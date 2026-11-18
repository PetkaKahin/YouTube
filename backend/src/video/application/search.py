"""Merge independently filtered public sources before pagination."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from common.contracts.transactions import Transactions
from video.application.watch_progress import ReadWatchProgress
from video.domain.search import (
    SEARCH_SIZE,
    SearchCursor,
    SearchItem,
    SearchPage,
    SearchPosition,
    SearchQuery,
)


class SearchVideos(Protocol):
    async def get(
        self, query: SearchQuery, after: SearchPosition | None, anchor: datetime
    ) -> tuple[SearchItem, ...]: ...


class SearchChannels(Protocol):
    async def get(
        self, query: SearchQuery, after: SearchPosition | None, anchor: datetime
    ) -> tuple[SearchItem, ...]: ...


class SearchPlaylists(Protocol):
    async def get(
        self, query: SearchQuery, after: SearchPosition | None, anchor: datetime
    ) -> tuple[SearchItem, ...]: ...


class GetSearch:
    def __init__(
        self,
        videos: SearchVideos,
        channels: SearchChannels,
        playlists: SearchPlaylists,
        transactions: Transactions,
        progress: ReadWatchProgress,
    ) -> None:
        self.videos, self.channels, self.transactions = videos, channels, transactions
        self.playlists = playlists
        self.progress = progress

    async def __call__(
        self, query: SearchQuery, cursor: str | None = None, *, account_id: int | None = None
    ) -> SearchPage:
        query = query.normalized()
        continuation = SearchCursor.decode(cursor, query) if cursor is not None else None
        anchor = continuation.anchor if continuation else datetime.now(UTC)
        after = continuation.position if continuation else None
        async with self.transactions.begin():
            videos = (
                await self.videos.get(query, after, anchor) if query.kind in {None, "video"} else ()
            )
            channels = (
                await self.channels.get(query, after, anchor)
                if query.kind in {None, "channel"}
                else ()
            )
            playlists = (
                await self.playlists.get(query, after, anchor)
                if query.kind in {None, "playlist"}
                else ()
            )
            items = sorted(
                (*videos, *channels, *playlists),
                key=lambda item: (item.position.date, item.position.rank, item.position.id),
                reverse=True,
            )
            selected = items[:SEARCH_SIZE]
            positions = await self.progress(
                account_id, tuple(item.video.id for item in selected if item.video is not None)
            )
            return SearchPage(
                tuple(
                    replace(
                        item, video=replace(item.video, watch_progress=positions.get(item.video.id))
                    )
                    if item.video is not None
                    else item
                    for item in selected
                ),
                SearchCursor(items[SEARCH_SIZE - 1].position, anchor).encode(query)
                if len(items) > SEARCH_SIZE
                else None,
            )
