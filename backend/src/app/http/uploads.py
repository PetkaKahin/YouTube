"""Upload control API; raw content goes through Nginx straight to RustFS."""

from datetime import datetime

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from app.http.videos import actor_id, video_problem
from common.http.errors import problem
from common.http.schemas import Identifier, JSONModel
from video.public import (
    AuthenticationRequired,
    CompleteUpload,
    RefreshUpload,
    StartUpload,
    StorageUnavailable,
    UploadError,
    UploadPlan,
    VideoNotFound,
)


class StartUploadBody(JSONModel):
    size: int = Field(ge=1, le=5_000_000_000)


class UploadPartResponse(JSONModel):
    number: int
    size: int
    url: str = Field(repr=False)


class UploadResponse(JSONModel):
    id: Identifier
    status: str
    size: int
    part_size: int
    parallel_parts: int
    expires_at: datetime
    parts: list[UploadPartResponse]

    @classmethod
    def from_value(cls, value: UploadPlan) -> UploadResponse:
        return cls(
            id=value.id,
            status=value.status,
            size=value.size,
            part_size=value.part_size,
            parallel_parts=value.parallel_parts,
            expires_at=value.expires_at,
            parts=[
                UploadPartResponse(number=p.number, size=p.size, url=p.url) for p in value.parts
            ],
        )


def upload_problem(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, (AuthenticationRequired, VideoNotFound)):
        return video_problem(request, error)
    headers = {"Cache-Control": "no-store", "Vary": "Cookie"}
    if isinstance(error, StorageUnavailable):
        return problem(
            request,
            503,
            "storage_unavailable",
            "Storage is temporarily unavailable",
            headers=headers,
        )
    if isinstance(error, UploadError):
        return problem(
            request,
            410 if error.code == "upload_expired" else 409,
            error.code,
            "Upload could not be completed",
            headers=headers,
        )
    raise error


def upload_router() -> APIRouter:
    router = APIRouter(prefix="/videos", tags=["Uploads"], route_class=DishkaRoute)
    security: dict[str, list[dict[str, list[str]]]] = {"security": [{"SessionCookie": []}]}

    @router.post("/{video_id}/upload", response_model=UploadResponse, openapi_extra=security)
    async def start(
        video_id: Identifier,
        body: StartUploadBody,
        request: Request,
        response: Response,
        scenario: FromDishka[StartUpload],
    ) -> UploadResponse | JSONResponse:
        response.headers["Cache-Control"], response.headers["Vary"] = "no-store", "Cookie"
        try:
            return UploadResponse.from_value(await scenario(actor_id(request), video_id, body.size))
        except (AuthenticationRequired, VideoNotFound, StorageUnavailable, UploadError) as error:
            return upload_problem(request, error)

    @router.post(
        "/{video_id}/upload/{upload_id}/refresh",
        response_model=UploadResponse,
        openapi_extra=security,
    )
    async def refresh(
        video_id: Identifier,
        upload_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[RefreshUpload],
    ) -> UploadResponse | JSONResponse:
        response.headers["Cache-Control"], response.headers["Vary"] = "no-store", "Cookie"
        try:
            return UploadResponse.from_value(await scenario(actor_id(request), video_id, upload_id))
        except (AuthenticationRequired, VideoNotFound, StorageUnavailable, UploadError) as error:
            return upload_problem(request, error)

    @router.post(
        "/{video_id}/upload/{upload_id}/complete",
        response_model=UploadResponse,
        openapi_extra=security,
    )
    async def complete(
        video_id: Identifier,
        upload_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[CompleteUpload],
    ) -> UploadResponse | JSONResponse:
        response.headers["Cache-Control"], response.headers["Vary"] = "no-store", "Cookie"
        try:
            return UploadResponse.from_value(await scenario(actor_id(request), video_id, upload_id))
        except (AuthenticationRequired, VideoNotFound, StorageUnavailable, UploadError) as error:
            return upload_problem(request, error)

    return router
