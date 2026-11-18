"""Ordinary report submission and separately authorized, report-bound staff routes."""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from app.http.video_access import VideoViewResponse
from app.http.videos import actor_id
from common.http.errors import problem
from common.http.schemas import Identifier, JSONModel
from moderation.public import (
    DecideReport,
    DecisionCommand,
    GetReport,
    GetReportedMedia,
    GetReportedVideo,
    GetReports,
    InvalidReport,
    InvalidReportCursor,
    ModerationAuthenticationRequired,
    ModerationForbidden,
    Report,
    ReportConflict,
    ReportDecision,
    ReportedVideo,
    ReportNotFound,
    ReviewMediaUnavailable,
    ReviewPlaylist,
    SubmitReport,
)

ReasonValue = Literal["spam", "harassment", "violence", "sexual_content", "copyright", "other"]
HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie", "X-Content-Type-Options": "nosniff"}
SECURITY: dict[str, object] = {"security": [{"SessionCookie": []}]}


class ReportBody(JSONModel):
    reason: ReasonValue


class ReportAccepted(JSONModel):
    accepted: Literal[True] = True


class DismissDecisionBody(JSONModel):
    revision: Identifier
    action: Literal["dismiss"]


class HideDecisionBody(JSONModel):
    revision: Identifier
    action: Literal["hide"]
    reason: ReasonValue


DecisionBody = Annotated[DismissDecisionBody | HideDecisionBody, Field(discriminator="action")]


class ReportDecisionResponse(JSONModel):
    revision: Identifier
    actor_id: Identifier
    action: str
    reason: str | None
    created_at: datetime

    @classmethod
    def from_value(cls, value: ReportDecision) -> ReportDecisionResponse:
        return cls(
            revision=value.revision,
            actor_id=value.actor_id,
            action=value.action,
            reason=value.reason.value if value.reason is not None else None,
            created_at=value.created_at,
        )


class ReportResponse(JSONModel):
    id: Identifier
    video_id: Identifier
    reporter_id: Identifier
    revision: Identifier
    reason: str
    state: str
    submitted_at: datetime
    created_at: datetime
    decision: ReportDecisionResponse | None

    @classmethod
    def from_value(cls, value: Report) -> ReportResponse:
        return cls(
            id=value.id,
            video_id=value.video_id,
            reporter_id=value.reporter_id,
            revision=value.revision,
            reason=value.reason.value,
            state=value.state,
            submitted_at=value.submitted_at,
            created_at=value.created_at,
            decision=ReportDecisionResponse.from_value(value.decision) if value.decision else None,
        )


class ReportsResponse(JSONModel):
    items: list[ReportResponse] = Field(max_length=20)
    next_cursor: str | None


def moderation_problem(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, ModerationAuthenticationRequired):
        return problem(request, 401, "unauthenticated", "Authentication required", headers=HEADERS)
    if isinstance(error, ModerationForbidden):
        return problem(request, 403, "forbidden", "Staff role required", headers=HEADERS)
    if isinstance(error, ReportNotFound):
        return problem(request, 404, "not_found", "Report or material not found", headers=HEADERS)
    if isinstance(error, ReportConflict):
        return problem(
            request, 409, "report_conflict", "Report revision or decision changed", headers=HEADERS
        )
    if isinstance(error, InvalidReportCursor):
        return problem(request, 422, "invalid_cursor", "Invalid report cursor", headers=HEADERS)
    if isinstance(error, InvalidReport):
        return problem(request, 422, "invalid_report", "Invalid report request", headers=HEADERS)
    if isinstance(error, ReviewMediaUnavailable):
        return problem(request, 503, "media_unavailable", "Media is unavailable", headers=HEADERS)
    return problem(request, 500, "internal_error", "An unexpected error occurred", headers=HEADERS)


def material_response(report_id: int, value: ReportedVideo) -> VideoViewResponse:
    payload = asdict(value)
    payload["qualities"] = [asdict(item) for item in value.qualities]
    base = f"/api/v1/moderation/reports/{report_id}"
    payload["hls_url"] = base + "/hls/master.m3u8"
    payload["preview_url"] = base + "/preview.webp"
    return VideoViewResponse.model_validate(payload)


async def deliver_report(
    request: Request, report_id: int, track: str | None, filename: str, scenario: GetReportedMedia
) -> Response:
    try:
        value = await scenario(actor_id(request), report_id, track, filename, method=request.method)
        if isinstance(value, ReviewPlaylist):
            body = value.text.encode("utf-8")
            return Response(
                content=b"" if request.method == "HEAD" else body,
                media_type=value.media_type,
                headers={**HEADERS, "Content-Length": str(len(body))},
            )
        return Response(
            headers={**HEADERS, "X-Accel-Redirect": value.redirect}, media_type=value.media_type
        )
    except Exception as error:
        return moderation_problem(request, error)


def moderation_router() -> APIRouter:
    router = APIRouter(tags=["Moderation"], route_class=DishkaRoute)

    @router.post(
        "/videos/{video_id}/reports",
        status_code=202,
        response_model=ReportAccepted,
        openapi_extra=SECURITY,
    )
    async def submit(
        video_id: Identifier,
        body: ReportBody,
        request: Request,
        response: Response,
        scenario: FromDishka[SubmitReport],
    ) -> ReportAccepted | JSONResponse:
        response.headers.update(HEADERS)
        try:
            await scenario(actor_id(request), video_id, body.reason)
            return ReportAccepted()
        except Exception as error:
            return moderation_problem(request, error)

    @router.get("/moderation/reports", response_model=ReportsResponse, openapi_extra=SECURITY)
    async def reports(
        request: Request,
        response: Response,
        scenario: FromDishka[GetReports],
        cursor: str | None = None,
    ) -> ReportsResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            if len(request.query_params.getlist("cursor")) > 1:
                raise InvalidReportCursor
            page = await scenario(actor_id(request), cursor)
            return ReportsResponse(
                items=[ReportResponse.from_value(item) for item in page.items],
                next_cursor=page.next_cursor,
            )
        except Exception as error:
            return moderation_problem(request, error)

    @router.get(
        "/moderation/reports/{report_id}", response_model=ReportResponse, openapi_extra=SECURITY
    )
    async def report(
        report_id: Identifier, request: Request, response: Response, scenario: FromDishka[GetReport]
    ) -> ReportResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            return ReportResponse.from_value(await scenario(actor_id(request), report_id))
        except Exception as error:
            return moderation_problem(request, error)

    @router.put(
        "/moderation/reports/{report_id}/decision",
        response_model=ReportResponse,
        openapi_extra=SECURITY,
    )
    async def decide(
        report_id: Identifier,
        body: DecisionBody,
        request: Request,
        response: Response,
        scenario: FromDishka[DecideReport],
    ) -> ReportResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            value = await scenario(
                actor_id(request),
                report_id,
                DecisionCommand(
                    body.revision,
                    body.action,
                    body.reason if isinstance(body, HideDecisionBody) else None,
                ),
            )
            return ReportResponse.from_value(value)
        except Exception as error:
            return moderation_problem(request, error)

    @router.get(
        "/moderation/reports/{report_id}/video",
        response_model=VideoViewResponse,
        openapi_extra=SECURITY,
    )
    async def reported_video(
        report_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[GetReportedVideo],
    ) -> VideoViewResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            return material_response(report_id, await scenario(actor_id(request), report_id))
        except Exception as error:
            return moderation_problem(request, error)

    @router.head("/moderation/reports/{report_id}/hls/master.m3u8", include_in_schema=False)
    @router.get(
        "/moderation/reports/{report_id}/hls/master.m3u8",
        response_class=Response,
        openapi_extra=SECURITY,
        responses={200: {"content": {"application/vnd.apple.mpegurl": {}}}},
    )
    async def master(
        report_id: Identifier, request: Request, scenario: FromDishka[GetReportedMedia]
    ) -> Response:
        return await deliver_report(request, report_id, None, "master.m3u8", scenario)

    @router.head("/moderation/reports/{report_id}/preview.webp", include_in_schema=False)
    @router.get(
        "/moderation/reports/{report_id}/preview.webp",
        response_class=Response,
        openapi_extra=SECURITY,
        responses={200: {"content": {"image/webp": {}}}},
    )
    async def preview(
        report_id: Identifier, request: Request, scenario: FromDishka[GetReportedMedia]
    ) -> Response:
        return await deliver_report(request, report_id, None, "preview.webp", scenario)

    @router.head(
        "/moderation/reports/{report_id}/hls/tracks/{track}/{filename}", include_in_schema=False
    )
    @router.get(
        "/moderation/reports/{report_id}/hls/tracks/{track}/{filename}",
        response_class=Response,
        openapi_extra=SECURITY,
        responses={
            200: {
                "content": {
                    kind: {}
                    for kind in (
                        "application/vnd.apple.mpegurl",
                        "video/mp4",
                        "audio/mp4",
                        "text/vtt",
                    )
                }
            }
        },
    )
    async def track_file(
        report_id: Identifier,
        track: str,
        filename: str,
        request: Request,
        scenario: FromDishka[GetReportedMedia],
    ) -> Response:
        return await deliver_report(request, report_id, track, filename, scenario)

    return router
