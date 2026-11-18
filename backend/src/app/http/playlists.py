"""Playlist controls and viewing; media URLs retain the publication's own checks."""

from dataclasses import asdict
from datetime import datetime

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field, StrictBool

from app.http.feed import FeedCardResponse, FeedChannelResponse
from app.http.videos import actor_id
from app.http.watch_progress import WatchProgressResponse
from channel.public import (
    CreatePlaylist,
    GetMyPlaylists,
    GetPlaylist,
    GetSavedPlaylists,
    Playlist,
    PlaylistAuthenticationRequired,
    PlaylistError,
    PlaylistMetadata,
    PlaylistNotFound,
    SetSavedPlaylist,
    SetVideoPlaylists,
)
from common.http.errors import problem
from common.http.schemas import Identifier, JSONModel

HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie"}
ERRORS = (PlaylistAuthenticationRequired, PlaylistNotFound, PlaylistError)


class PlaylistBody(JSONModel):
    title: str
    description: str = ""
    visibility: str
    video_id: Identifier | None = None


class PlaylistResponse(JSONModel):
    id: Identifier
    channel_id: Identifier
    title: str
    description: str
    visibility: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_value(cls, value: Playlist) -> PlaylistResponse:
        return cls.model_validate(asdict(value))


class PlaylistOptionResponse(PlaylistResponse):
    selected: bool


class PlaylistListResponse(JSONModel):
    items: list[PlaylistOptionResponse] = Field(max_length=20)
    next_cursor: str | None


class SavedPlaylistListResponse(PlaylistListResponse):
    saved: bool


class SavedSelectionBody(JSONModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    selected: StrictBool


class SavedSelectionResponse(JSONModel):
    playlist_id: Identifier
    video_id: Identifier
    selected: bool
    saved: bool


class SelectionBody(JSONModel):
    playlist_ids: list[Identifier]


class SelectionResponse(JSONModel):
    playlist_ids: list[Identifier]


class PlaylistViewResponse(JSONModel):
    playlist: PlaylistResponse
    channel: FeedChannelResponse
    items: list[FeedCardResponse] = Field(max_length=20)
    next_cursor: str | None


def playlist_problem(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, ERRORS):
        return problem(
            request, 500, "internal_error", "An unexpected error occurred", headers=HEADERS
        )
    if isinstance(error, PlaylistAuthenticationRequired):
        return problem(request, 401, "unauthenticated", "Authentication required", headers=HEADERS)
    if isinstance(error, PlaylistNotFound):
        return problem(request, 404, "not_found", "Playlist or video not found", headers=HEADERS)
    code = str(error)
    return problem(
        request,
        422,
        code
        if code in {"invalid_metadata", "invalid_cursor", "invalid_playlist_ids"}
        else "invalid_playlist",
        "Invalid playlist request",
        headers=HEADERS,
    )


def check_cursor(request: Request) -> None:
    if len(request.query_params.getlist("cursor")) > 1:
        raise PlaylistError("invalid_cursor")


def playlists_router() -> APIRouter:
    router = APIRouter(tags=["Playlists"], route_class=DishkaRoute)
    owner_security: dict[str, list[dict[str, list[str]]]] = {"security": [{"SessionCookie": []}]}

    @router.post(
        "/playlists", response_model=PlaylistResponse, status_code=201, openapi_extra=owner_security
    )
    async def create(
        request: Request,
        response: Response,
        body: PlaylistBody,
        scenario: FromDishka[CreatePlaylist],
    ) -> PlaylistResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            return PlaylistResponse.from_value(
                await scenario(
                    actor_id(request),
                    PlaylistMetadata(body.title, body.description, body.visibility),
                    body.video_id,
                )
            )
        except Exception as error:
            return playlist_problem(request, error)

    async def listing(
        request: Request,
        response: Response,
        scenario: GetMyPlaylists,
        cursor: str | None,
        video_id: int | None = None,
    ) -> PlaylistListResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            check_cursor(request)
            value = await scenario(actor_id(request), cursor, video_id)
            return PlaylistListResponse(
                items=[
                    PlaylistOptionResponse(**asdict(item.playlist), selected=item.selected)
                    for item in value.items
                ],
                next_cursor=value.next_cursor,
            )
        except Exception as error:
            return playlist_problem(request, error)

    @router.get(
        "/videos/{video_id}/saved-playlists",
        response_model=SavedPlaylistListResponse,
        openapi_extra=owner_security,
    )
    async def saved_playlists(
        video_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[GetSavedPlaylists],
        cursor: str | None = None,
    ) -> SavedPlaylistListResponse | JSONResponse:
        response.headers.update(HEADERS)
        if any(
            key != "cursor" or len(request.query_params.getlist(key)) > 1
            for key in request.query_params
        ):
            cursor = ""  # Authentication precedes invalid cursor handling inside the scenario.
        try:
            page = await scenario(actor_id(request), video_id, cursor)
            return SavedPlaylistListResponse(
                items=[
                    PlaylistOptionResponse(**asdict(item.playlist), selected=item.selected)
                    for item in page.items
                ],
                next_cursor=page.next_cursor,
                saved=page.saved,
            )
        except Exception as error:
            return playlist_problem(request, error)

    @router.put(
        "/playlists/{playlist_id}/videos/{video_id}",
        response_model=SavedSelectionResponse,
        openapi_extra=owner_security,
    )
    async def saved_selection(
        playlist_id: Identifier,
        video_id: Identifier,
        body: SavedSelectionBody,
        request: Request,
        response: Response,
        scenario: FromDishka[SetSavedPlaylist],
    ) -> SavedSelectionResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            value = await scenario(actor_id(request), playlist_id, video_id, body.selected)
            return SavedSelectionResponse.model_validate(asdict(value))
        except Exception as error:
            return playlist_problem(request, error)

    @router.get(
        "/playlists/mine", response_model=PlaylistListResponse, openapi_extra=owner_security
    )
    async def mine(
        request: Request,
        response: Response,
        scenario: FromDishka[GetMyPlaylists],
        cursor: str | None = None,
    ) -> PlaylistListResponse | JSONResponse:
        return await listing(request, response, scenario, cursor)

    @router.get(
        "/videos/{video_id}/playlists",
        response_model=PlaylistListResponse,
        openapi_extra=owner_security,
    )
    async def choices(
        video_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[GetMyPlaylists],
        cursor: str | None = None,
    ) -> PlaylistListResponse | JSONResponse:
        return await listing(request, response, scenario, cursor, video_id)

    @router.put(
        "/videos/{video_id}/playlists",
        response_model=SelectionResponse,
        openapi_extra=owner_security,
    )
    async def select(
        video_id: Identifier,
        request: Request,
        response: Response,
        body: SelectionBody,
        scenario: FromDishka[SetVideoPlaylists],
    ) -> SelectionResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            return SelectionResponse(
                playlist_ids=list(
                    await scenario(actor_id(request), video_id, tuple(body.playlist_ids))
                )
            )
        except Exception as error:
            return playlist_problem(request, error)

    @router.get(
        "/playlists/{playlist_id}",
        response_model=PlaylistViewResponse,
        openapi_extra={"security": [{}, {"SessionCookie": []}]},
    )
    async def view(
        playlist_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[GetPlaylist],
        cursor: str | None = None,
    ) -> PlaylistViewResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            check_cursor(request)
            value = await scenario(actor_id(request), playlist_id, cursor)
            return PlaylistViewResponse(
                playlist=PlaylistResponse.from_value(value.playlist),
                channel=FeedChannelResponse(
                    id=value.channel.id, name=value.channel.name, tag=value.channel.tag
                ),
                items=[
                    FeedCardResponse(
                        id=card.id,
                        title=card.title,
                        added_at=card.added_at,
                        duration_seconds=card.duration_seconds,
                        channel=FeedChannelResponse(
                            id=card.channel_id, name=card.channel_name, tag=card.channel_tag
                        ),
                        preview_url=f"/api/v1/videos/{card.id}/preview.webp"
                        if card.preview_ready
                        else None,
                        watch_progress=WatchProgressResponse.model_validate(
                            asdict(card.watch_progress)
                        )
                        if card.watch_progress is not None
                        else None,
                    )
                    for card in value.items
                ],
                next_cursor=value.next_cursor,
            )
        except Exception as error:
            return playlist_problem(request, error)

    return router
