"""Search contract; raw query parameters never enter logs or error payloads."""

from datetime import datetime
from typing import Annotated, Literal

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from app.http.feed import FeedCardResponse, FeedChannelResponse
from app.http.videos import actor_id, video_problem
from common.http.errors import problem
from common.http.schemas import Identifier, JSONModel
from video.public import AuthenticationRequired, GetSearch, InvalidSearch, SearchQuery

HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie"}


class ChannelSearchResponse(JSONModel):
    id: Identifier
    name: str
    tag: str
    created_at: datetime
    updated_at: datetime


class PlaylistSearchResponse(JSONModel):
    id: Identifier
    title: str
    description: str
    created_at: datetime
    updated_at: datetime
    channel: FeedChannelResponse


class SearchItemResponse(JSONModel):
    type: Literal["video", "channel", "playlist"]
    video: FeedCardResponse | None = None
    channel: ChannelSearchResponse | None = None
    playlist: PlaylistSearchResponse | None = None


class SearchResponse(JSONModel):
    items: list[SearchItemResponse] = Field(max_length=20)
    next_cursor: str | None


def search_router() -> APIRouter:
    router = APIRouter(tags=["Search"], route_class=DishkaRoute)

    @router.get(
        "/search",
        response_model=SearchResponse,
        openapi_extra={"security": [{}, {"SessionCookie": []}]},
    )
    async def search(
        request: Request,
        response: Response,
        scenario: FromDishka[GetSearch],
        q: str = "",
        type: str | None = None,
        sort: str = "created",
        duration: str | None = None,
        period: str | None = None,
        cursor: str | None = None,
        feature: Annotated[list[str] | None, Query()] = None,
    ) -> SearchResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            if any(
                len(request.query_params.getlist(key)) > 1
                for key in ("q", "type", "sort", "duration", "period", "cursor")
            ):
                raise InvalidSearch
            page = await scenario(
                SearchQuery(q, type, sort, duration, period, tuple(feature or ())),
                cursor,
                account_id=actor_id(request),
            )
        except AuthenticationRequired as error:
            return video_problem(request, error)
        except InvalidSearch:
            return problem(
                request, 422, "invalid_search", "Invalid search parameters", headers=HEADERS
            )
        except Exception:
            return problem(
                request, 500, "internal_error", "An unexpected error occurred", headers=HEADERS
            )
        return SearchResponse(
            items=[
                SearchItemResponse(
                    type="video" if item.video else "channel" if item.channel else "playlist",
                    video=FeedCardResponse.from_value(item.video) if item.video else None,
                    channel=ChannelSearchResponse(
                        id=item.channel.id,
                        name=item.channel.name,
                        tag=item.channel.tag,
                        created_at=item.channel.created_at,
                        updated_at=item.channel.updated_at,
                    )
                    if item.channel
                    else None,
                    playlist=PlaylistSearchResponse(
                        id=item.playlist.id,
                        title=item.playlist.title,
                        description=item.playlist.description,
                        created_at=item.playlist.created_at,
                        updated_at=item.playlist.updated_at,
                        channel=FeedChannelResponse(
                            id=item.playlist.channel.id,
                            name=item.playlist.channel.name,
                            tag=item.playlist.channel.tag,
                        ),
                    )
                    if item.playlist
                    else None,
                )
                for item in page.items
            ],
            next_cursor=page.next_cursor,
        )

    return router
