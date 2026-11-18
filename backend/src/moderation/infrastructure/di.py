"""Moderation owns orchestration; all ports share the request transaction."""

from dishka import (
    Provider,
    Scope,
    provide,  # pyright: ignore[reportUnknownVariableType] -- upstream decorator overloads
)

from moderation.application.reports import (
    DecideReport,
    GetReport,
    GetReportedMedia,
    GetReportedVideo,
    GetReports,
    ReportActions,
    Reports,
    ReportVideos,
    StaffActors,
    SubmitReport,
)
from moderation.infrastructure.adapters import UserStaffActors, VideoReports
from moderation.infrastructure.reports import SqlReports


class ModerationProvider(Provider):
    scope = Scope.REQUEST

    actors = provide(UserStaffActors, provides=StaffActors)
    reports = provide(SqlReports, provides=Reports)
    videos = provide(VideoReports, provides=ReportVideos)
    actions = provide(ReportActions)
    submit_report = provide(SubmitReport)
    get_reports = provide(GetReports)
    get_report = provide(GetReport)
    decide_report = provide(DecideReport)
    get_reported_video = provide(GetReportedVideo)
    get_reported_media = provide(GetReportedMedia)
