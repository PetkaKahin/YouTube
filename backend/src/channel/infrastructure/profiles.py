"""Batch channel identity and names through User.public, without foreign joins."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from channel.domain.profiles import PublicChannel
from channel.infrastructure.models import ChannelRow
from user.public import GetPublicNames


class SqlPublicChannels:
    def __init__(self, session: AsyncSession, names: GetPublicNames) -> None:
        self.session, self.names = session, names

    async def get(self, owner_ids: tuple[int, ...]) -> dict[int, PublicChannel]:
        rows = (
            await self.session.execute(
                select(ChannelRow.id, ChannelRow.owner_id, ChannelRow.tag).where(
                    ChannelRow.owner_id.in_(owner_ids)
                )
            )
        ).all()
        names = await self.names(tuple(row.owner_id for row in rows))
        return {
            row.owner_id: PublicChannel(row.id, names[row.owner_id], row.tag)
            for row in rows
            if row.owner_id in names
        }
