"""Video like state and totals use the same ordinary viewing permissions as metadata."""

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
    GetVideoLike,
    InvalidLike,
    SetVideoLike,
    VideoNotFound,
)

HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie"}


class LikeBody(JSONModel):
    liked: StrictBool


class LikeResponse(JSONModel):
    liked: bool
    like_count: int = Field(ge=0)
    can_like: bool


def like_problem(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, (AuthenticationRequired, VideoNotFound)):
        return video_problem(request, error)
    if isinstance(error, InvalidLike):
        return problem(request, 422, "invalid_like", "Invalid like request", headers=HEADERS)
    return problem(request, 500, "internal_error", "An unexpected error occurred", headers=HEADERS)


def video_likes_router() -> APIRouter:
    router = APIRouter(prefix="/videos", tags=["Video likes"], route_class=DishkaRoute)

    @router.get(
        "/{video_id}/like",
        response_model=LikeResponse,
        openapi_extra={"security": [{}, {"SessionCookie": []}]},
    )
    async def state(
        video_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[GetVideoLike],
    ) -> LikeResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            value = await scenario(actor_id(request), video_id)
            return LikeResponse(
                liked=value.liked, like_count=value.like_count, can_like=value.can_like
            )
        except Exception as error:
            return like_problem(request, error)

    @router.put(
        "/{video_id}/like",
        response_model=LikeResponse,
        openapi_extra={"security": [{"SessionCookie": []}]},
    )
    async def set_state(
        video_id: Identifier,
        body: LikeBody,
        request: Request,
        response: Response,
        scenario: FromDishka[SetVideoLike],
    ) -> LikeResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            value = await scenario(actor_id(request), video_id, body.liked)
            return LikeResponse(
                liked=value.liked, like_count=value.like_count, can_like=value.can_like
            )
        except Exception as error:
            return like_problem(request, error)

    return router
