"""HTTP settings controller; files and viewing are separate Video use cases."""

from datetime import datetime
from typing import Literal

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator

from common.http.errors import problem
from common.http.schemas import FieldError, Identifier, JSONModel
from video.public import (
    AuthenticationRequired,
    CreateDraft,
    EditVideoSettings,
    GetVideoSettings,
    VideoError,
    VideoMetadata,
    VideoNotFound,
    VideoSettings,
)


class VideoSettingsBody(JSONModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=5000)
    visibility: Literal["public", "private"]

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    def metadata(self) -> VideoMetadata:
        return VideoMetadata(self.title, self.description, self.visibility)


class DraftBody(VideoSettingsBody):
    description: str = Field(default="", max_length=5000)


class VideoSettingsResponse(JSONModel):
    id: Identifier
    owner_id: Identifier
    title: str
    description: str
    visibility: str
    publication: str
    upload: str
    processing: str
    moderation: str
    added_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_value(cls, value: VideoSettings) -> VideoSettingsResponse:
        return cls(
            id=value.id,
            owner_id=value.owner_id,
            title=value.title,
            description=value.description,
            visibility=value.visibility.value,
            publication=value.publication.value,
            upload=value.upload.value,
            processing=value.processing.value,
            moderation=value.moderation.value,
            added_at=value.added_at,
            created_at=value.created_at,
            updated_at=value.updated_at,
        )


def actor_id(request: Request) -> int | None:
    value = request.session.get("user_id")
    # Reject strings, booleans and overflow even for corrupted server-side sessions.
    return value if type(value) is int and 0 < value < 2**63 else None


def video_problem(
    request: Request, error: AuthenticationRequired | VideoNotFound | VideoError
) -> JSONResponse:
    headers = {"Cache-Control": "no-store", "Vary": "Cookie"}
    if isinstance(error, AuthenticationRequired):
        return problem(request, 401, "unauthenticated", "Authentication required", headers=headers)
    if isinstance(error, VideoNotFound):
        return problem(request, 404, "not_found", "Video not found", headers=headers)
    return problem(
        request,
        422,
        "invalid_video_metadata",
        "Video metadata is invalid",
        [FieldError(location=["body", error.field], code=error.code)],
        headers=headers,
    )


def videos_router() -> APIRouter:
    router = APIRouter(
        prefix="/videos",
        tags=["Videos"],
        route_class=DishkaRoute,
    )
    security: dict[str, list[dict[str, list[str]]]] = {"security": [{"SessionCookie": []}]}

    @router.post("", status_code=201, response_model=VideoSettingsResponse, openapi_extra=security)
    async def create(
        body: DraftBody, request: Request, response: Response, scenario: FromDishka[CreateDraft]
    ) -> VideoSettingsResponse | JSONResponse:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Vary"] = "Cookie"
        try:
            value = await scenario(actor_id(request), body.metadata())
        except (AuthenticationRequired, VideoError) as error:
            return video_problem(request, error)
        return VideoSettingsResponse.from_value(value)

    @router.get(
        "/{video_id}/settings", response_model=VideoSettingsResponse, openapi_extra=security
    )
    async def settings(
        video_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[GetVideoSettings],
    ) -> VideoSettingsResponse | JSONResponse:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Vary"] = "Cookie"
        try:
            value = await scenario(actor_id(request), video_id)
        except (AuthenticationRequired, VideoNotFound) as error:
            return video_problem(request, error)
        return VideoSettingsResponse.from_value(value)

    @router.put(
        "/{video_id}/settings", response_model=VideoSettingsResponse, openapi_extra=security
    )
    async def edit(
        video_id: Identifier,
        body: VideoSettingsBody,
        request: Request,
        response: Response,
        scenario: FromDishka[EditVideoSettings],
    ) -> VideoSettingsResponse | JSONResponse:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Vary"] = "Cookie"
        try:
            value = await scenario(actor_id(request), video_id, body.metadata())
        except (AuthenticationRequired, VideoNotFound, VideoError) as error:
            return video_problem(request, error)
        return VideoSettingsResponse.from_value(value)

    return router
