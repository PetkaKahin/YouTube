"""Playlist tables and adapters calling foreign modules only through public."""

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from channel.domain.playlists import (
    Playlist,
    PlaylistAuthenticationRequired,
    PlaylistBoundary,
    PlaylistMetadata,
    PlaylistNotFound,
    PlaylistVideo,
    PlaylistWatchProgress,
)
from channel.domain.profiles import PublicChannel
from channel.infrastructure.models import ChannelRow, PlaylistEntryRow, PlaylistRow
from user.public import AccountNotFound, GetAccount, GetPublicNames
from video.public import (
    AuthenticationRequired,
    GetPlaylistVideoCards,
    PlaylistVideoOwnership,
    ReadWatchProgress,
    RequirePlaylistVideo,
    VideoNotFound,
)


def value(row: PlaylistRow) -> Playlist:
    return Playlist(
        row.id,
        row.channel_id,
        row.title,
        row.description,
        row.visibility,
        row.created_at,
        row.updated_at,
    )


class UserPlaylistActors:
    def __init__(self, accounts: GetAccount) -> None:
        self.accounts = accounts

    async def require(self, account_id: int) -> None:
        try:
            await self.accounts(account_id)
        except AccountNotFound:
            raise PlaylistAuthenticationRequired from None


class VideoPlaylistAdapter:
    def __init__(
        self,
        ownership: PlaylistVideoOwnership,
        cards: GetPlaylistVideoCards,
        progress: ReadWatchProgress,
        viewing: RequirePlaylistVideo,
    ) -> None:
        self.ownership, self.views, self.positions = ownership, cards, progress
        self.viewing = viewing

    async def lock_viewable(self, account_id: int, video_id: int) -> None:
        try:
            await self.viewing(account_id, video_id)
        except AuthenticationRequired:
            raise PlaylistAuthenticationRequired from None
        except VideoNotFound:
            raise PlaylistNotFound from None

    async def progress(
        self, account_id: int | None, ids: tuple[int, ...]
    ) -> dict[int, PlaylistWatchProgress]:
        try:
            positions = await self.positions(account_id, ids)
        except AuthenticationRequired:
            raise PlaylistAuthenticationRequired from None
        return {
            identity: PlaylistWatchProgress(
                value.position_seconds, value.duration_seconds, value.updated_at
            )
            for identity, value in positions.items()
        }

    async def lock_owned(self, account_id: int, video_id: int) -> None:
        try:
            await self.ownership.lock(account_id, video_id)
        except AuthenticationRequired:
            raise PlaylistAuthenticationRequired from None
        except VideoNotFound:
            raise PlaylistNotFound from None

    async def touch(self, account_id: int, video_id: int) -> None:
        await self.ownership.touch(account_id, video_id)

    async def cards(self, account_id: int | None, ids: tuple[int, ...]) -> dict[int, PlaylistVideo]:
        try:
            cards = await self.views(account_id, ids)
        except AuthenticationRequired:
            raise PlaylistAuthenticationRequired from None
        return {
            identity: PlaylistVideo(
                card.id,
                card.title,
                card.added_at,
                card.duration_seconds,
                card.channel.id,
                card.channel.name,
                card.channel.tag,
                card.preview_ready,
            )
            for identity, card in cards.items()
        }


class SqlPlaylistStore:
    def __init__(self, session: AsyncSession, names: GetPublicNames) -> None:
        self.session, self.names = session, names

    async def channel(self, account_id: int, *, lock: bool = False) -> int | None:
        query = select(ChannelRow.id).where(ChannelRow.owner_id == account_id)
        return await self.session.scalar(query.with_for_update() if lock else query)

    async def create(self, channel_id: int, metadata: PlaylistMetadata) -> Playlist:
        row = PlaylistRow(
            channel_id=channel_id,
            title=metadata.title,
            description=metadata.description,
            visibility=metadata.visibility,
        )
        self.session.add(row)
        await self.session.flush()
        return value(row)

    async def get(self, playlist_id: int) -> Playlist | None:
        row = await self.session.get(PlaylistRow, playlist_id, populate_existing=True)
        return value(row) if row else None

    async def profile(self, channel_id: int) -> PublicChannel | None:
        row = await self.session.get(ChannelRow, channel_id, populate_existing=True)
        if row is None:
            return None
        name = (await self.names((row.owner_id,))).get(row.owner_id)
        return PublicChannel(row.id, name, row.tag) if name is not None else None

    async def owned(self, channel_id: int, after: PlaylistBoundary | None) -> tuple[Playlist, ...]:
        query = select(PlaylistRow).where(PlaylistRow.channel_id == channel_id)
        if after is not None:
            query = query.where(
                tuple_(PlaylistRow.created_at, PlaylistRow.id) < (after.date, after.id)
            )
        rows = (
            await self.session.scalars(
                query.order_by(PlaylistRow.created_at.desc(), PlaylistRow.id.desc()).limit(21)
            )
        ).all()
        return tuple(value(row) for row in rows)

    async def selected(self, channel_id: int, video_id: int) -> tuple[int, ...]:
        return tuple(
            (
                await self.session.scalars(
                    select(PlaylistEntryRow.playlist_id)
                    .join(PlaylistRow, PlaylistRow.id == PlaylistEntryRow.playlist_id)
                    .where(
                        PlaylistRow.channel_id == channel_id, PlaylistEntryRow.video_id == video_id
                    )
                    .order_by(PlaylistEntryRow.playlist_id)
                )
            ).all()
        )

    async def saved(self, channel_id: int, video_id: int) -> bool:
        return bool(
            await self.session.scalar(
                select(
                    select(PlaylistEntryRow.id)
                    .join(PlaylistRow, PlaylistRow.id == PlaylistEntryRow.playlist_id)
                    .where(
                        PlaylistRow.channel_id == channel_id, PlaylistEntryRow.video_id == video_id
                    )
                    .exists()
                )
            )
        )

    async def select_one(
        self, channel_id: int, playlist_id: int, video_id: int, selected: bool
    ) -> None:
        # The caller holds the channel lock, shared with Studio's complete replacement.
        owned = await self.session.scalar(
            select(PlaylistRow.id).where(
                PlaylistRow.id == playlist_id, PlaylistRow.channel_id == channel_id
            )
        )
        if owned is None:
            raise PlaylistNotFound
        if selected:
            changed = await self.session.scalar(
                insert(PlaylistEntryRow)
                .values(playlist_id=playlist_id, video_id=video_id)
                .on_conflict_do_nothing(
                    index_elements=[PlaylistEntryRow.playlist_id, PlaylistEntryRow.video_id]
                )
                .returning(PlaylistEntryRow.id)
            )
        else:
            changed = await self.session.scalar(
                delete(PlaylistEntryRow)
                .where(
                    PlaylistEntryRow.playlist_id == playlist_id,
                    PlaylistEntryRow.video_id == video_id,
                )
                .returning(PlaylistEntryRow.id)
            )
        if changed is not None:
            await self.session.execute(
                update(PlaylistRow)
                .where(PlaylistRow.id == playlist_id)
                .values(updated_at=func.clock_timestamp())
            )

    async def replace(self, channel_id: int, video_id: int, ids: tuple[int, ...]) -> bool:
        owned: set[int] = (
            set(
                (
                    await self.session.scalars(
                        select(PlaylistRow.id).where(
                            PlaylistRow.channel_id == channel_id, PlaylistRow.id.in_(ids)
                        )
                    )
                ).all()
            )
            if ids
            else set()
        )
        if owned != set(ids):
            raise PlaylistNotFound
        current = set(await self.selected(channel_id, video_id))
        added, removed = owned - current, current - owned
        if not added and not removed:
            return False
        if removed:
            await self.session.execute(
                delete(PlaylistEntryRow).where(
                    PlaylistEntryRow.video_id == video_id, PlaylistEntryRow.playlist_id.in_(removed)
                )
            )
        self.session.add_all(
            PlaylistEntryRow(playlist_id=identity, video_id=video_id) for identity in sorted(added)
        )
        await self.session.execute(
            update(PlaylistRow)
            .where(PlaylistRow.id.in_(added | removed))
            .values(updated_at=func.clock_timestamp())
        )
        await self.session.flush()
        return True

    async def entries(self, playlist_id: int, after: int) -> tuple[tuple[int, int], ...]:
        rows = await self.session.execute(
            select(PlaylistEntryRow.id, PlaylistEntryRow.video_id)
            .where(PlaylistEntryRow.playlist_id == playlist_id, PlaylistEntryRow.id > after)
            .order_by(PlaylistEntryRow.id)
            .limit(21)
        )
        return tuple((row.id, row.video_id) for row in rows)
