"""Public channel metadata and two independently paginated public lists."""

from datetime import datetime

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field, StrictBool

from app.http.feed import FeedCardResponse, FeedResponse
from app.http.playlists import PlaylistResponse
from app.http.videos import actor_id
from channel.public import (
    ChannelNotFound,
    GetChannel,
    GetChannelPlaylists,
    GetSubscription,
    PlaylistError,
    SetSubscription,
    SubscriptionAuthenticationRequired,
    SubscriptionError,
)
from common.http.errors import problem
from common.http.schemas import Identifier, JSONModel
from video.public import GetChannelFeed, InvalidFeedCursor, VideoNotFound

HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie"}


class ChannelResponse(JSONModel):
    id: Identifier
    name: str
    tag: str
    created_at: datetime
    updated_at: datetime


class ChannelPlaylistsResponse(JSONModel):
    items: list[PlaylistResponse] = Field(max_length=20)
    next_cursor: str | None


class SubscriptionBody(JSONModel):
    subscribed: StrictBool


class SubscriptionResponse(JSONModel):
    subscribed: bool
    can_subscribe: bool


def channel_problem(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, SubscriptionAuthenticationRequired):
        return problem(request, 401, "unauthenticated", "Authentication required", headers=HEADERS)
    if isinstance(error, SubscriptionError):
        code = str(error)
        return problem(
            request,
            422,
            code
            if code in {"self_subscription", "invalid_subscription"}
            else "invalid_subscription",
            "Invalid subscription request",
            headers=HEADERS,
        )
    if isinstance(error, (ChannelNotFound, VideoNotFound)):
        return problem(request, 404, "not_found", "Channel not found", headers=HEADERS)
    if isinstance(error, (InvalidFeedCursor, PlaylistError)):
        return problem(request, 422, "invalid_cursor", "Invalid channel cursor", headers=HEADERS)
    return problem(request, 500, "internal_error", "An unexpected error occurred", headers=HEADERS)


def check_cursor(request: Request) -> None:
    if len(request.query_params.getlist("cursor")) > 1:
        raise InvalidFeedCursor


def channels_router() -> APIRouter:
    router = APIRouter(prefix="/channels", tags=["Channels"], route_class=DishkaRoute)
    security: dict[str, list[dict[str, list[str]]]] = {"security": [{}, {"SessionCookie": []}]}
    subscription_security: dict[str, list[dict[str, list[str]]]] = {
        "security": [{"SessionCookie": []}]
    }

    @router.get("/{tag}", response_model=ChannelResponse, openapi_extra=security)
    async def channel(
        tag: str, request: Request, response: Response, scenario: FromDishka[GetChannel]
    ) -> ChannelResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            value = await scenario(tag)
            return ChannelResponse(
                id=value.id,
                name=value.name,
                tag=value.tag,
                created_at=value.created_at,
                updated_at=value.created_at,
            )
        except Exception as error:
            return channel_problem(request, error)

    @router.get("/{tag}/videos", response_model=FeedResponse, openapi_extra=security)
    async def videos(
        tag: str,
        request: Request,
        response: Response,
        scenario: FromDishka[GetChannelFeed],
        cursor: str | None = None,
    ) -> FeedResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            check_cursor(request)
            page = await scenario(tag, cursor)
            return FeedResponse(
                items=[FeedCardResponse.from_value(item) for item in page.items],
                next_cursor=page.next_cursor,
            )
        except Exception as error:
            return channel_problem(request, error)

    @router.get("/{tag}/playlists", response_model=ChannelPlaylistsResponse, openapi_extra=security)
    async def playlists(
        tag: str,
        request: Request,
        response: Response,
        scenario: FromDishka[GetChannelPlaylists],
        cursor: str | None = None,
    ) -> ChannelPlaylistsResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            check_cursor(request)
            page = await scenario(tag, cursor)
            return ChannelPlaylistsResponse(
                items=[PlaylistResponse.from_value(item) for item in page.items],
                next_cursor=page.next_cursor,
            )
        except Exception as error:
            return channel_problem(request, error)

    @router.get(
        "/{tag}/subscription",
        response_model=SubscriptionResponse,
        openapi_extra=subscription_security,
    )
    async def subscription(
        tag: str,
        request: Request,
        response: Response,
        scenario: FromDishka[GetSubscription],
    ) -> SubscriptionResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            value = await scenario(actor_id(request), tag)
            return SubscriptionResponse(
                subscribed=value.subscribed, can_subscribe=value.can_subscribe
            )
        except Exception as error:
            return channel_problem(request, error)

    @router.put(
        "/{tag}/subscription",
        response_model=SubscriptionResponse,
        openapi_extra=subscription_security,
    )
    async def set_subscription(
        tag: str,
        body: SubscriptionBody,
        request: Request,
        response: Response,
        scenario: FromDishka[SetSubscription],
    ) -> SubscriptionResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            value = await scenario(actor_id(request), tag, body.subscribed)
            return SubscriptionResponse(
                subscribed=value.subscribed, can_subscribe=value.can_subscribe
            )
        except Exception as error:
            return channel_problem(request, error)

    return router
