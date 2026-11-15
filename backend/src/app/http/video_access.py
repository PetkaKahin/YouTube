"""Private email permissions and ordinary viewing of authorised metadata."""

from dataclasses import asdict
from datetime import datetime

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field, StrictBool

from app.http.videos import actor_id, video_problem
from common.http.errors import problem
from common.http.schemas import Identifier, JSONModel
from video.public import (
    AuthenticationRequired,
    GetPrivateAccess,
    GetViewableVideo,
    InvalidPermissionCursor,
    SetPrivateAccess,
    VideoError,
    VideoNotFound,
    VideoView,
)


class PermissionBody(JSONModel):
    email: str
    allowed: StrictBool


class PermissionsResponse(JSONModel):
    emails: list[str] = Field(max_length=20)
    next_cursor: str | None


def permission_problem(request: Request, error: Exception) -> JSONResponse:
    headers = {"Cache-Control": "no-store", "Vary": "Cookie"}
    if isinstance(error, (AuthenticationRequired, VideoNotFound, VideoError)):
        return video_problem(request, error)
    if isinstance(error, InvalidPermissionCursor):
        return problem(request, 422, "invalid_cursor", "Invalid permission cursor", headers=headers)
    return problem(request, 500, "internal_error", "An unexpected error occurred", headers=headers)


class ReadyQualityResponse(JSONModel):
    quality: int
    range: str


class VideoViewResponse(JSONModel):
    id: Identifier
    title: str
    description: str
    visibility: str
    added_at: datetime | None
    updated_at: datetime
    qualities: list[ReadyQualityResponse]
    hd: bool
    uhd: bool
    hdr: bool
    uhd_hdr: bool
    hls_url: str
    preview_url: str

    @classmethod
    def from_value(cls, value: VideoView) -> VideoViewResponse:
        payload = asdict(value)
        payload["qualities"] = [asdict(item) for item in value.qualities]
        payload["hls_url"] = f"/api/v1/videos/{value.id}/hls/master.m3u8"
        payload["preview_url"] = f"/api/v1/videos/{value.id}/preview.webp"
        return cls.model_validate(payload)


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Cookie"


def video_access_router() -> APIRouter:
    router = APIRouter(prefix="/videos", tags=["Video access"], route_class=DishkaRoute)
    owner_security: dict[str, list[dict[str, list[str]]]] = {"security": [{"SessionCookie": []}]}

    @router.get(
        "/{video_id}/permissions", response_model=PermissionsResponse, openapi_extra=owner_security
    )
    async def permissions(
        video_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[GetPrivateAccess],
        cursor: str | None = None,
    ) -> PermissionsResponse | JSONResponse:
        no_store(response)
        try:
            if len(request.query_params.getlist("cursor")) > 1:
                raise InvalidPermissionCursor
            page = await scenario(actor_id(request), video_id, cursor)
        except Exception as error:
            return permission_problem(request, error)
        return PermissionsResponse(emails=list(page.emails), next_cursor=page.next_cursor)

    @router.put(
        "/{video_id}/permissions", response_model=PermissionsResponse, openapi_extra=owner_security
    )
    async def set_permission(
        video_id: Identifier,
        body: PermissionBody,
        request: Request,
        response: Response,
        scenario: FromDishka[SetPrivateAccess],
    ) -> PermissionsResponse | JSONResponse:
        no_store(response)
        try:
            page = await scenario(actor_id(request), video_id, body.email, body.allowed)
        except Exception as error:
            return permission_problem(request, error)
        return PermissionsResponse(emails=list(page.emails), next_cursor=page.next_cursor)

    @router.get(
        "/{video_id}",
        response_model=VideoViewResponse,
        openapi_extra={"security": [{}, {"SessionCookie": []}]},
    )
    async def view(
        video_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[GetViewableVideo],
    ) -> VideoViewResponse | JSONResponse:
        no_store(response)
        try:
            value = await scenario(actor_id(request), video_id)
        except (AuthenticationRequired, VideoNotFound) as error:
            return video_problem(request, error)
        return VideoViewResponse.from_value(value)

    return router
