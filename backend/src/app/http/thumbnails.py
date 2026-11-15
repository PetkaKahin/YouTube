"""Owner-only preview control; bytes are always authorized through the publication."""

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from app.http.uploads import UploadResponse, upload_problem
from app.http.videos import actor_id
from common.http.errors import problem
from common.http.schemas import Identifier, JSONModel
from video.public import (
    AuthenticationRequired,
    CompleteThumbnailUpload,
    GetThumbnailDelivery,
    GetThumbnails,
    RefreshThumbnailUpload,
    SelectThumbnail,
    SourceInspectionError,
    StartThumbnailUpload,
    StorageUnavailable,
    ThumbnailError,
    ThumbnailOptions,
    UploadError,
    VideoNotFound,
)

HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie", "X-Content-Type-Options": "nosniff"}
ERRORS = (
    AuthenticationRequired,
    VideoNotFound,
    StorageUnavailable,
    UploadError,
    ThumbnailError,
    SourceInspectionError,
)


class ThumbnailResponse(JSONModel):
    status: str
    selected: str
    slot: int
    candidates: list[str]
    current_url: str | None
    custom_status: str | None
    error_code: str | None

    @classmethod
    def from_value(cls, value: ThumbnailOptions) -> ThumbnailResponse:
        return cls(
            status=value.status,
            selected=value.selected,
            slot=value.slot,
            candidates=list(value.candidates),
            current_url=value.current_url,
            custom_status=value.custom_status,
            error_code=value.error_code,
        )


class SelectionBody(JSONModel):
    slot: int = Field(ge=0, le=2)


class ImageUploadBody(JSONModel):
    size: int = Field(ge=1, le=10 * 1024 * 1024)


def thumbnail_problem(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, ThumbnailError):
        return problem(
            request,
            422 if error.code == "image_size_limit" else 409,
            error.code,
            "Thumbnail could not be changed",
            headers=HEADERS,
        )
    if isinstance(error, SourceInspectionError):
        return problem(
            request, 503, "thumbnail_unavailable", "Thumbnail is unavailable", headers=HEADERS
        )
    return upload_problem(request, error)


def thumbnails_router() -> APIRouter:
    router = APIRouter(
        prefix="/videos/{video_id}/thumbnail", tags=["Thumbnails"], route_class=DishkaRoute
    )
    security: dict[str, list[dict[str, list[str]]]] = {"security": [{"SessionCookie": []}]}

    @router.get("", response_model=ThumbnailResponse, openapi_extra=security)
    async def options(
        video_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[GetThumbnails],
    ) -> ThumbnailResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            return ThumbnailResponse.from_value(await scenario(actor_id(request), video_id))
        except ERRORS as error:
            return thumbnail_problem(request, error)

    @router.put("", response_model=ThumbnailResponse, openapi_extra=security)
    async def select(
        video_id: Identifier,
        body: SelectionBody,
        request: Request,
        response: Response,
        scenario: FromDishka[SelectThumbnail],
    ) -> ThumbnailResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            return ThumbnailResponse.from_value(
                await scenario(actor_id(request), video_id, body.slot)
            )
        except ERRORS as error:
            return thumbnail_problem(request, error)

    @router.head("/image", include_in_schema=False)
    @router.get(
        "/image",
        openapi_extra=security,
        response_class=Response,
        responses={200: {"content": {"image/webp": {}}}},
    )
    async def current(
        video_id: Identifier, request: Request, scenario: FromDishka[GetThumbnailDelivery]
    ) -> Response:
        try:
            value = await scenario(actor_id(request), video_id, method=request.method)
            return Response(
                media_type=value.media_type, headers={**HEADERS, "X-Accel-Redirect": value.redirect}
            )
        except ERRORS as error:
            return thumbnail_problem(request, error)

    @router.head("/generated/{slot}", include_in_schema=False)
    @router.get(
        "/generated/{slot}",
        openapi_extra=security,
        response_class=Response,
        responses={200: {"content": {"image/webp": {}}}},
    )
    async def candidate(
        video_id: Identifier,
        slot: int,
        request: Request,
        scenario: FromDishka[GetThumbnailDelivery],
    ) -> Response:
        try:
            value = await scenario(actor_id(request), video_id, slot=slot, method=request.method)
            return Response(
                media_type=value.media_type, headers={**HEADERS, "X-Accel-Redirect": value.redirect}
            )
        except ERRORS as error:
            return thumbnail_problem(request, error)

    @router.post("/upload", response_model=UploadResponse, openapi_extra=security)
    async def start(
        video_id: Identifier,
        body: ImageUploadBody,
        request: Request,
        response: Response,
        scenario: FromDishka[StartThumbnailUpload],
    ) -> UploadResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            return UploadResponse.from_value(await scenario(actor_id(request), video_id, body.size))
        except ERRORS as error:
            return thumbnail_problem(request, error)

    @router.post(
        "/upload/{upload_id}/refresh", response_model=UploadResponse, openapi_extra=security
    )
    async def refresh(
        video_id: Identifier,
        upload_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[RefreshThumbnailUpload],
    ) -> UploadResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            return UploadResponse.from_value(await scenario(actor_id(request), video_id, upload_id))
        except ERRORS as error:
            return thumbnail_problem(request, error)

    @router.post(
        "/upload/{upload_id}/complete", response_model=UploadResponse, openapi_extra=security
    )
    async def complete(
        video_id: Identifier,
        upload_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[CompleteThumbnailUpload],
    ) -> UploadResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            return UploadResponse.from_value(await scenario(actor_id(request), video_id, upload_id))
        except ERRORS as error:
            return thumbnail_problem(request, error)

    return router
