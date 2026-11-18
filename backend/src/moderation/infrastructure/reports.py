"""Only Moderation tables are queried here; caller holds video and account locks."""

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from moderation.domain.reports import (
    SIZE,
    DecisionCommand,
    Reason,
    Report,
    ReportCursor,
    ReportDecision,
)
from moderation.infrastructure.models import DecisionRow, ReportRow, ReviewAccessRow


def report_value(row: ReportRow, decision: DecisionRow | None = None) -> Report:
    return Report(
        row.id,
        row.video_id,
        row.reporter_id,
        row.revision,
        Reason(row.reason),
        row.state,
        row.submitted_at,
        row.created_at,
        ReportDecision(
            decision.revision,
            decision.actor_id,
            decision.action,
            Reason(decision.reason) if decision.reason is not None else None,
            decision.created_at,
        )
        if decision is not None
        else None,
    )


class SqlReports:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def submit(self, account_id: int, video_id: int, reason: Reason) -> None:
        await self.session.execute(
            insert(ReportRow)
            .values(reporter_id=account_id, video_id=video_id, reason=reason.value)
            .on_conflict_do_update(
                index_elements=[ReportRow.reporter_id, ReportRow.video_id],
                set_={
                    "reason": reason.value,
                    "state": "pending",
                    "revision": ReportRow.revision + 1,
                    "submitted_at": func.clock_timestamp(),
                },
            )
        )

    async def get(self, report_id: int, *, lock: str | None = None) -> Report | None:
        statement = (
            select(ReportRow)
            .where(ReportRow.id == report_id)
            .execution_options(populate_existing=True)
        )
        if lock is not None:
            if lock not in ("share", "update"):
                raise ValueError("Invalid report lock")
            statement = statement.with_for_update(read=lock == "share")
        row = await self.session.scalar(statement)
        if row is None:
            return None
        decision = (
            await self.session.get(DecisionRow, (row.id, row.revision), populate_existing=True)
            if row.state == "reviewed"
            else None
        )
        return report_value(row, decision)

    async def page(self, after: ReportCursor | None) -> tuple[Report, ...]:
        statement = select(ReportRow).where(ReportRow.state == "pending")
        if after is not None:
            statement = statement.where(
                tuple_(ReportRow.submitted_at, ReportRow.id) > tuple_(after.submitted_at, after.id)
            )
        rows = await self.session.scalars(
            statement.order_by(ReportRow.submitted_at, ReportRow.id).limit(SIZE + 1)
        )
        return tuple(report_value(row) for row in rows)

    async def decide(self, report: Report, actor_id: int, command: DecisionCommand) -> Report:
        self.session.add(
            DecisionRow(
                report_id=report.id,
                revision=report.revision,
                actor_id=actor_id,
                action=command.action,
                reason=str(command.reason) if command.reason is not None else None,
            )
        )
        await self.session.execute(
            update(ReportRow)
            .where(ReportRow.id == report.id, ReportRow.revision == report.revision)
            .values(state="reviewed")
        )
        await self.session.flush()
        result = await self.get(report.id)
        if result is None:
            raise RuntimeError("Report disappeared during decision")
        return result

    async def audit(self, report: Report, actor_id: int, resource: str) -> None:
        self.session.add(
            ReviewAccessRow(
                report_id=report.id,
                revision=report.revision,
                actor_id=actor_id,
                reason=report.reason.value,
                resource=resource,
            )
        )
        await self.session.flush()
