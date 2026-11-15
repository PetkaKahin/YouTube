"""Playlist changes are atomic; ordered contents retain independent video rights."""

from typing import Protocol

from channel.domain.playlists import (
    EPOCH,
    SIZE,
    Playlist,
    PlaylistAuthenticationRequired,
    PlaylistBoundary,
    PlaylistError,
    PlaylistList,
    PlaylistMetadata,
    PlaylistNotFound,
    PlaylistOption,
    PlaylistVideo,
    PlaylistView,
    identifier,
)
from common.contracts.transactions import Transactions


class PlaylistActors(Protocol):
    async def require(self, account_id: int) -> None: ...


class PlaylistStore(Protocol):
    async def channel(self, account_id: int, *, lock: bool = False) -> int | None: ...
    async def create(self, channel_id: int, metadata: PlaylistMetadata) -> Playlist: ...
    async def get(self, playlist_id: int) -> Playlist | None: ...
    async def owned(
        self, channel_id: int, after: PlaylistBoundary | None
    ) -> tuple[Playlist, ...]: ...
    async def selected(self, channel_id: int, video_id: int) -> tuple[int, ...]: ...
    async def replace(self, channel_id: int, video_id: int, ids: tuple[int, ...]) -> bool: ...
    async def entries(self, playlist_id: int, after: int) -> tuple[tuple[int, int], ...]: ...


class PlaylistVideos(Protocol):
    async def lock_owned(self, account_id: int, video_id: int) -> None: ...
    async def touch(self, account_id: int, video_id: int) -> None: ...
    async def cards(
        self, account_id: int | None, ids: tuple[int, ...]
    ) -> dict[int, PlaylistVideo]: ...


class PlaylistActions:
    def __init__(
        self,
        actors: PlaylistActors,
        store: PlaylistStore,
        videos: PlaylistVideos,
        transactions: Transactions,
    ) -> None:
        self.actors, self.store, self.videos, self.transactions = (
            actors,
            store,
            videos,
            transactions,
        )

    async def owner(self, account_id: int | None, *, lock: bool = False) -> tuple[int, int]:
        if not identifier(account_id):
            raise PlaylistAuthenticationRequired
        if account_id is None:
            raise PlaylistAuthenticationRequired
        await self.actors.require(account_id)
        channel = await self.store.channel(account_id, lock=lock)
        if channel is None:
            raise PlaylistNotFound
        return account_id, channel


class CreatePlaylist:
    def __init__(self, actions: PlaylistActions) -> None:
        self.actions = actions

    async def __call__(self, account_id: int | None, metadata: PlaylistMetadata) -> Playlist:
        async with self.actions.transactions.begin():
            _, channel = await self.actions.owner(account_id, lock=True)
            return await self.actions.store.create(channel, metadata.normalized())


class GetMyPlaylists:
    def __init__(self, actions: PlaylistActions) -> None:
        self.actions = actions

    async def __call__(
        self, account_id: int | None, cursor: str | None = None, video_id: int | None = None
    ) -> PlaylistList:
        async with self.actions.transactions.begin():
            actor, channel = await self.actions.owner(account_id)
            if video_id is not None:
                await self.actions.videos.lock_owned(actor, video_id)
            mode, scope = (2, video_id) if video_id is not None else (1, channel)
            after = (
                PlaylistBoundary.decode(cursor, mode, scope, actor) if cursor is not None else None
            )
            rows = await self.actions.store.owned(channel, after)
            selected: set[int] = (
                set(await self.actions.store.selected(channel, video_id))
                if video_id is not None
                else set()
            )
            return PlaylistList(
                tuple(PlaylistOption(row, row.id in selected) for row in rows[:SIZE]),
                PlaylistBoundary(rows[SIZE - 1].created_at, rows[SIZE - 1].id).encode(
                    mode, scope, actor
                )
                if len(rows) > SIZE
                else None,
            )


class SetVideoPlaylists:
    def __init__(self, actions: PlaylistActions) -> None:
        self.actions = actions

    async def __call__(
        self, account_id: int | None, video_id: int, ids: tuple[int, ...]
    ) -> tuple[int, ...]:
        async with self.actions.transactions.begin():
            actor, _ = await self.actions.owner(account_id)
            await self.actions.videos.lock_owned(actor, video_id)
            _, channel = await self.actions.owner(actor, lock=True)
            if any(not identifier(value) for value in ids):
                raise PlaylistError("invalid_playlist_ids")
            normalized = tuple(sorted(set(ids)))
            if await self.actions.store.replace(channel, video_id, normalized):
                await self.actions.videos.touch(actor, video_id)
            return normalized


class GetPlaylist:
    def __init__(self, actions: PlaylistActions) -> None:
        self.actions = actions

    async def __call__(
        self, account_id: int | None, playlist_id: int, cursor: str | None = None
    ) -> PlaylistView:
        async with self.actions.transactions.begin():
            value = await self.actions.store.get(playlist_id) if identifier(playlist_id) else None
            if value is None:
                raise PlaylistNotFound
            if value.visibility == "private":
                if not identifier(account_id) or account_id is None:
                    raise PlaylistNotFound
                await self.actions.actors.require(account_id)
                if await self.actions.store.channel(account_id) != value.channel_id:
                    raise PlaylistNotFound
            after = (
                PlaylistBoundary.decode(cursor, 3, playlist_id, 0).id if cursor is not None else 0
            )
            visible: list[tuple[int, PlaylistVideo]] = []
            while True:
                entries = await self.actions.store.entries(playlist_id, after)
                cards = (
                    await self.actions.videos.cards(
                        account_id, tuple(video for _, video in entries)
                    )
                    if entries
                    else {}
                )
                for entry, video in entries:
                    if video in cards:
                        visible.append((entry, cards[video]))
                    if len(visible) > SIZE:
                        return PlaylistView(
                            value,
                            tuple(card for _, card in visible[:SIZE]),
                            PlaylistBoundary(EPOCH, visible[SIZE - 1][0]).encode(3, playlist_id, 0),
                        )
                if len(entries) < SIZE + 1:
                    return PlaylistView(value, tuple(card for _, card in visible), None)
                after = entries[-1][0]
