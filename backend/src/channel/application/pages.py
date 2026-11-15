"""Public channel reads; discovery never inherits the visitor's private grants."""

from typing import Protocol

from channel.domain.pages import (
    ChannelIdentity,
    ChannelNotFound,
    ChannelPlaylistPage,
    ChannelProfile,
)
from channel.domain.playlists import SIZE, Playlist, PlaylistBoundary
from channel.domain.registration import RegistrationError, validate_tag
from common.contracts.transactions import Transactions


class ChannelPages(Protocol):
    async def find(self, tag: str) -> ChannelIdentity | None: ...

    async def name(self, owner_id: int) -> str | None: ...

    async def playlists(
        self, channel_id: int, after: PlaylistBoundary | None
    ) -> tuple[Playlist, ...]: ...


class FindChannel:
    """Resolve a tag inside the caller's transaction; no commit or private access."""

    def __init__(self, pages: ChannelPages) -> None:
        self.pages = pages

    async def __call__(self, tag: str) -> ChannelIdentity:
        try:
            tag = validate_tag(tag)
        except RegistrationError:
            raise ChannelNotFound from None
        result = await self.pages.find(tag)
        if result is None:
            raise ChannelNotFound
        return result


class GetChannel:
    def __init__(self, find: FindChannel, pages: ChannelPages, transactions: Transactions) -> None:
        self.find, self.pages, self.transactions = find, pages, transactions

    async def __call__(self, tag: str) -> ChannelProfile:
        async with self.transactions.begin():
            identity = await self.find(tag)
            name = await self.pages.name(identity.owner_id)
            if name is None:
                raise ChannelNotFound
            return ChannelProfile(identity.id, name, identity.tag, identity.created_at)


class GetChannelPlaylists:
    def __init__(self, find: FindChannel, pages: ChannelPages, transactions: Transactions) -> None:
        self.find, self.pages, self.transactions = find, pages, transactions

    async def __call__(self, tag: str, cursor: str | None = None) -> ChannelPlaylistPage:
        async with self.transactions.begin():
            channel = await self.find(tag)
            after = (
                PlaylistBoundary.decode(cursor, 4, channel.id, 0) if cursor is not None else None
            )
            rows = await self.pages.playlists(channel.id, after)
            return ChannelPlaylistPage(
                rows[:SIZE],
                PlaylistBoundary(rows[SIZE - 1].created_at, rows[SIZE - 1].id).encode(
                    4, channel.id, 0
                )
                if len(rows) > SIZE
                else None,
            )
