"""Watch saves edit a single own pair after ordinary viewing, independently of Studio."""

from channel.application.playlists import PlaylistActions
from channel.domain.playlists import (
    SIZE,
    PlaylistBoundary,
    PlaylistError,
    PlaylistNotFound,
    PlaylistOption,
    SavedPlaylistList,
    SavedPlaylistSelection,
    identifier,
)

SAVED_CURSOR_MODE = 5


class GetSavedPlaylists:
    def __init__(self, actions: PlaylistActions) -> None:
        self.actions = actions

    async def __call__(
        self, account_id: int | None, video_id: int, cursor: str | None = None
    ) -> SavedPlaylistList:
        async with self.actions.transactions.begin():
            actor, channel = await self.actions.owner(account_id)
            await self.actions.videos.lock_viewable(actor, video_id)
            after = (
                PlaylistBoundary.decode(cursor, SAVED_CURSOR_MODE, video_id, actor)
                if cursor is not None
                else None
            )
            rows = await self.actions.store.owned(channel, after)
            # Membership is read once for the whole own set, never per playlist.
            selected = set(await self.actions.store.selected(channel, video_id))
            return SavedPlaylistList(
                tuple(PlaylistOption(row, row.id in selected) for row in rows[:SIZE]),
                PlaylistBoundary(rows[SIZE - 1].created_at, rows[SIZE - 1].id).encode(
                    SAVED_CURSOR_MODE, video_id, actor
                )
                if len(rows) > SIZE
                else None,
                bool(selected),
            )


class SetSavedPlaylist:
    def __init__(self, actions: PlaylistActions) -> None:
        self.actions = actions

    async def __call__(
        self, account_id: int | None, playlist_id: int, video_id: int, selected: object
    ) -> SavedPlaylistSelection:
        async with self.actions.transactions.begin():
            actor, _ = await self.actions.owner(account_id)
            await self.actions.videos.lock_viewable(actor, video_id)
            _, channel = await self.actions.owner(actor, lock=True)
            if not identifier(playlist_id):
                raise PlaylistNotFound
            if type(selected) is not bool:
                raise PlaylistError("invalid_selection")
            await self.actions.store.select_one(channel, playlist_id, video_id, selected)
            return SavedPlaylistSelection(
                playlist_id, video_id, selected, await self.actions.store.saved(channel, video_id)
            )
