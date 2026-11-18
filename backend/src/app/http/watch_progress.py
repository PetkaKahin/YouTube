"""Main-player positions; no implicit writes from playback or card reads."""

from dataclasses import asdict
from datetime import datetime

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field

from app.http.videos import actor_id, video_problem
from common.http.errors import problem
from common.http.schemas import Identifier, JSONModel
from video.public import (
    AuthenticationRequired,
    InvalidWatchProgress,
    SetWatchProgress,
    VideoNotFound,
    WatchProgress,
)


class WatchProgressBody(JSONModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    position_seconds: float = Field(ge=0, allow_inf_nan=False)


class WatchProgressResponse(JSONModel):
    position_seconds: float = Field(ge=0, le=3600, allow_inf_nan=False)
    duration_seconds: float = Field(gt=0, le=3600, allow_inf_nan=False)
    updated_at: datetime

    @classmethod
    def from_value(cls, value: WatchProgress) -> WatchProgressResponse:
        return cls.model_validate(asdict(value))


def watch_progress_router() -> APIRouter:
    router = APIRouter(prefix="/videos", tags=["Watch progress"], route_class=DishkaRoute)

    @router.put(
        "/{video_id}/progress",
        response_model=WatchProgressResponse,
        openapi_extra={"security": [{"SessionCookie": []}]},
    )
    async def write(
        video_id: Identifier,
        body: WatchProgressBody,
        request: Request,
        response: Response,
        scenario: FromDishka[SetWatchProgress],
    ) -> WatchProgressResponse | JSONResponse:
        response.headers.update({"Cache-Control": "no-store", "Vary": "Cookie"})
        try:
            if request.query_params:
                raise InvalidWatchProgress
            value = await scenario(actor_id(request), video_id, body.position_seconds)
        except AuthenticationRequired as error:
            return video_problem(request, error)
        except VideoNotFound as error:
            return video_problem(request, error)
        except InvalidWatchProgress:
            return problem(request, 422, "invalid_watch_progress", "Invalid watch progress")
        return WatchProgressResponse.from_value(value)

    return router
