"""Search public identities inside the caller's transaction."""

from datetime import datetime
from typing import Protocol

from channel.domain.search import ChannelSearchHit, PlaylistSearchHit


class ChannelSearch(Protocol):
    async def find(
        self,
        query: str,
        before_at: datetime | None,
        before_id: int | None,
        since: datetime | None,
        until: datetime | None,
    ) -> tuple[ChannelSearchHit, ...]: ...


class FindChannels:
    def __init__(self, search: ChannelSearch) -> None:
        self.search = search

    async def __call__(
        self,
        query: str,
        before_at: datetime | None,
        before_id: int | None,
        since: datetime | None,
        until: datetime | None,
    ) -> tuple[ChannelSearchHit, ...]:
        return await self.search.find(query, before_at, before_id, since, until)


class PlaylistSearch(Protocol):
    async def find(
        self,
        query: str,
        sort: str,
        before_at: datetime | None,
        before_id: int | None,
        since: datetime | None,
        until: datetime | None,
    ) -> tuple[PlaylistSearchHit, ...]: ...


class FindPlaylists:
    def __init__(self, search: PlaylistSearch) -> None:
        self.search = search

    async def __call__(
        self,
        query: str,
        sort: str,
        before_at: datetime | None,
        before_id: int | None,
        since: datetime | None,
        until: datetime | None,
    ) -> tuple[PlaylistSearchHit, ...]:
        return await self.search.find(query, sort, before_at, before_id, since, until)
