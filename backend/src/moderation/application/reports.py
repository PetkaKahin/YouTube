"""Report context, role locks and Video commands share one request transaction."""

from typing import Protocol

from common.contracts.transactions import Transactions
from moderation.domain.reports import (
    SIZE,
    DecisionCommand,
    Reason,
    Report,
    ReportConflict,
    ReportCursor,
    ReportedVideo,
    ReportNotFound,
    ReportPage,
    ReviewObject,
    ReviewPlaylist,
    actor_id,
    valid_id,
)


class StaffActors(Protocol):
    async def require(self, account_id: int | None) -> int: ...


class Reports(Protocol):
    async def submit(self, account_id: int, video_id: int, reason: Reason) -> None: ...

    async def get(self, report_id: int, *, lock: str | None = None) -> Report | None: ...

    async def page(self, after: ReportCursor | None) -> tuple[Report, ...]: ...

    async def decide(self, report: Report, actor_id: int, command: DecisionCommand) -> Report: ...

    async def audit(self, report: Report, actor_id: int, resource: str) -> None: ...


class ReportVideos(Protocol):
    async def reportable(self, account_id: int, video_id: int) -> None: ...

    async def lock(self, account_id: int, video_id: int, *, exclusive: bool = False) -> None: ...

    async def hide(self, account_id: int, video_id: int) -> None: ...

    async def view(self, account_id: int, video_id: int) -> ReportedVideo: ...

    async def media(
        self, account_id: int, video_id: int, track: str | None, filename: str, method: str
    ) -> ReviewPlaylist | ReviewObject: ...


class ReportActions:
    def __init__(
        self,
        actors: StaffActors,
        reports: Reports,
        videos: ReportVideos,
        transactions: Transactions,
    ) -> None:
        self.actors, self.reports, self.videos, self.transactions = (
            actors,
            reports,
            videos,
            transactions,
        )

    async def get(self, report_id: int, *, lock: str | None = None) -> Report:
        if not valid_id(report_id):
            raise ReportNotFound
        report = await self.reports.get(report_id, lock=lock)
        if report is None:
            raise ReportNotFound
        return report

    async def staff_report(
        self,
        account_id: int | None,
        report_id: int,
        *,
        exclusive: bool = False,
        pending: bool = False,
    ) -> tuple[int, Report]:
        actor = await self.actors.require(account_id)
        initial = await self.get(report_id)
        await self.videos.lock(actor, initial.video_id, exclusive=exclusive)
        report = await self.get(report_id, lock="update" if exclusive else "share")
        if pending and report.state != "pending":
            raise ReportNotFound
        return actor, report


class SubmitReport:
    def __init__(self, actions: ReportActions) -> None:
        self.actions = actions

    async def __call__(self, account_id: int | None, video_id: int, reason: object) -> None:
        async with self.actions.transactions.begin():
            actor = actor_id(account_id)
            if not valid_id(video_id):
                raise ReportNotFound
            await self.actions.videos.reportable(actor, video_id)
            await self.actions.reports.submit(actor, video_id, Reason.parse(reason))


class GetReports:
    def __init__(self, actions: ReportActions) -> None:
        self.actions = actions

    async def __call__(self, account_id: int | None, cursor: str | None = None) -> ReportPage:
        async with self.actions.transactions.begin():
            await self.actions.actors.require(account_id)
            after = ReportCursor.decode(cursor) if cursor is not None else None
            rows = await self.actions.reports.page(after)
            last = rows[SIZE - 1] if len(rows) > SIZE else None
            return ReportPage(
                rows[:SIZE], ReportCursor(last.submitted_at, last.id).encode() if last else None
            )


class GetReport:
    def __init__(self, actions: ReportActions) -> None:
        self.actions = actions

    async def __call__(self, account_id: int | None, report_id: int) -> Report:
        async with self.actions.transactions.begin():
            await self.actions.actors.require(account_id)
            return await self.actions.get(report_id)


class DecideReport:
    def __init__(self, actions: ReportActions) -> None:
        self.actions = actions

    async def __call__(
        self, account_id: int | None, report_id: int, command: DecisionCommand
    ) -> Report:
        async with self.actions.transactions.begin():
            actor, report = await self.actions.staff_report(account_id, report_id, exclusive=True)
            desired = command.validated()
            if report.revision != desired.revision:
                raise ReportConflict
            if report.state == "reviewed":
                previous = report.decision
                if previous is None or (previous.action, previous.reason) != (
                    desired.action,
                    desired.reason,
                ):
                    raise ReportConflict
                return report
            if report.state != "pending":
                raise ReportConflict
            if desired.action == "hide":
                await self.actions.videos.hide(actor, report.video_id)
            return await self.actions.reports.decide(report, actor, desired)


class GetReportedVideo:
    def __init__(self, actions: ReportActions) -> None:
        self.actions = actions

    async def __call__(self, account_id: int | None, report_id: int) -> ReportedVideo:
        async with self.actions.transactions.begin():
            actor, report = await self.actions.staff_report(account_id, report_id, pending=True)
            value = await self.actions.videos.view(actor, report.video_id)
            await self.actions.reports.audit(report, actor, "video")
            return value


class GetReportedMedia:
    def __init__(self, actions: ReportActions) -> None:
        self.actions = actions

    async def __call__(
        self,
        account_id: int | None,
        report_id: int,
        track: str | None,
        filename: str,
        *,
        method: str = "GET",
    ) -> ReviewPlaylist | ReviewObject:
        async with self.actions.transactions.begin():
            actor, report = await self.actions.staff_report(account_id, report_id, pending=True)
            if method not in ("GET", "HEAD"):
                raise ReportNotFound
            value = await self.actions.videos.media(actor, report.video_id, track, filename, method)
            resource = (
                "track"
                if track is not None
                else "master"
                if filename == "master.m3u8"
                else "preview"
            )
            await self.actions.reports.audit(report, actor, resource)
            return value
