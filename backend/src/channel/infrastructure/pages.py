"""Read only Channel tables; display names come from User.public."""

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from channel.domain.pages import ChannelIdentity
from channel.domain.playlists import Playlist, PlaylistBoundary
from channel.infrastructure.models import ChannelRow, PlaylistRow
from channel.infrastructure.playlists import value
from user.public import GetPublicNames


class SqlChannelPages:
    def __init__(self, session: AsyncSession, names: GetPublicNames) -> None:
        self.session, self.names = session, names

    async def find(self, tag: str) -> ChannelIdentity | None:
        row = await self.session.scalar(select(ChannelRow).where(func.lower(ChannelRow.tag) == tag))
        return ChannelIdentity(row.id, row.owner_id, row.tag, row.created_at) if row else None

    async def name(self, owner_id: int) -> str | None:
        return (await self.names((owner_id,))).get(owner_id)

    async def playlists(
        self, channel_id: int, after: PlaylistBoundary | None
    ) -> tuple[Playlist, ...]:
        statement = select(PlaylistRow).where(
            PlaylistRow.channel_id == channel_id, PlaylistRow.visibility == "public"
        )
        if after is not None:
            statement = statement.where(
                tuple_(PlaylistRow.created_at, PlaylistRow.id) < (after.date, after.id)
            )
        rows = (
            await self.session.scalars(
                statement.order_by(PlaylistRow.created_at.desc(), PlaylistRow.id.desc()).limit(21)
            )
        ).all()
        return tuple(value(row) for row in rows)
