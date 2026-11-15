"""Persist only the caller's active pair; account verification stays in User.public."""

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from channel.domain.subscriptions import SubscriptionAuthenticationRequired
from channel.infrastructure.models import SubscriptionRow
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
