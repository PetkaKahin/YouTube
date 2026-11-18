"""Persist only the caller's active pair; account verification stays in User.public."""

from sqlalchemy import delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from channel.domain.pages import ChannelIdentity
from channel.domain.subscription_lists import SIZE, SubscriptionBoundary
from channel.domain.subscriptions import SubscriptionAuthenticationRequired
from channel.infrastructure.models import ChannelRow, SubscriptionRow
from user.public import AccountNotFound, GetAccount


class UserSubscriptionActors:
    def __init__(self, accounts: GetAccount) -> None:
        self.accounts = accounts

    async def require(self, account_id: int) -> None:
        try:
            await self.accounts(account_id)
        except AccountNotFound:
            raise SubscriptionAuthenticationRequired from None


class SqlSubscriptions:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def channels(
        self, subscriber_id: int, after: SubscriptionBoundary | None
    ) -> tuple[ChannelIdentity, ...]:
        query = (
            select(ChannelRow)
            .join(SubscriptionRow, SubscriptionRow.channel_id == ChannelRow.id)
            .where(SubscriptionRow.subscriber_id == subscriber_id)
        )
        if after is not None:
            query = query.where(
                tuple_(ChannelRow.created_at, ChannelRow.id) < (after.date, after.id)
            )
        rows = await self.session.scalars(
            query.order_by(ChannelRow.created_at.desc(), ChannelRow.id.desc()).limit(SIZE + 1)
        )
        return tuple(ChannelIdentity(row.id, row.owner_id, row.tag, row.created_at) for row in rows)

    async def owners(self, subscriber_id: int) -> tuple[int, ...]:
        rows = await self.session.scalars(
            select(ChannelRow.owner_id)
            .join(SubscriptionRow, SubscriptionRow.channel_id == ChannelRow.id)
            .where(SubscriptionRow.subscriber_id == subscriber_id)
        )
        return tuple(rows)

    async def get(self, subscriber_id: int, channel_id: int) -> bool:
        return (
            await self.session.scalar(
                select(SubscriptionRow.channel_id).where(
                    SubscriptionRow.subscriber_id == subscriber_id,
                    SubscriptionRow.channel_id == channel_id,
                )
            )
            is not None
        )

    async def set(self, subscriber_id: int, channel_id: int, subscribed: bool) -> None:
        if subscribed:
            await self.session.execute(
                insert(SubscriptionRow)
                .values(subscriber_id=subscriber_id, channel_id=channel_id)
                .on_conflict_do_nothing(
                    index_elements=[SubscriptionRow.subscriber_id, SubscriptionRow.channel_id]
                )
            )
        else:
            await self.session.execute(
                delete(SubscriptionRow).where(
                    SubscriptionRow.subscriber_id == subscriber_id,
                    SubscriptionRow.channel_id == channel_id,
                )
            )
