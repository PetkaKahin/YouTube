"""The public home feed keeps pagination separate from media authorisation."""

from datetime import datetime

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from app.http.videos import actor_id, video_problem
from app.http.watch_progress import WatchProgressResponse
from common.http.errors import problem
from common.http.schemas import Identifier, JSONModel
from video.public import AuthenticationRequired, FeedCard, GetFeed, InvalidFeedCursor

HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie"}


class FeedChannelResponse(JSONModel):
    id: Identifier
    name: str
    tag: str


class FeedCardResponse(JSONModel):
    id: Identifier
    title: str
    added_at: datetime
    duration_seconds: float = Field(gt=0, le=3600)
    channel: FeedChannelResponse
    preview_url: str | None
    watch_progress: WatchProgressResponse | None

    @classmethod
    def from_value(cls, value: FeedCard) -> FeedCardResponse:
        return cls(
            id=value.id,
            title=value.title,
            added_at=value.added_at,
            duration_seconds=value.duration_seconds,
            channel=FeedChannelResponse(
                id=value.channel.id, name=value.channel.name, tag=value.channel.tag
            ),
            preview_url=f"/api/v1/videos/{value.id}/preview.webp" if value.preview_ready else None,
            watch_progress=WatchProgressResponse.from_value(value.watch_progress)
            if value.watch_progress is not None
            else None,
        )


class FeedResponse(JSONModel):
    items: list[FeedCardResponse]
    next_cursor: str | None


def feed_router() -> APIRouter:
    router = APIRouter(prefix="/videos", tags=["Feed"], route_class=DishkaRoute)

    @router.get(
        "/feed",
        response_model=FeedResponse,
        openapi_extra={"security": [{}, {"SessionCookie": []}]},
    )
    async def feed(
        request: Request,
        response: Response,
        scenario: FromDishka[GetFeed],
        cursor: str | None = None,
    ) -> FeedResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            if len(request.query_params.getlist("cursor")) > 1:
                raise InvalidFeedCursor
            page = await scenario(cursor, account_id=actor_id(request))
        except AuthenticationRequired as error:
            return video_problem(request, error)
        except InvalidFeedCursor:
            return problem(request, 422, "invalid_cursor", "Invalid feed cursor", headers=HEADERS)
        except Exception:
            return problem(
                request, 500, "internal_error", "An unexpected error occurred", headers=HEADERS
            )
        return FeedResponse(
            items=[FeedCardResponse.from_value(item) for item in page.items],
            next_cursor=page.next_cursor,
        )

    return router
