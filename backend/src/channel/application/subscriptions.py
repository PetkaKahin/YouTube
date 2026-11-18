"""Authenticated target-state subscription commands; no channel or video edits."""

from typing import Protocol

from channel.application.pages import FindChannel
from channel.domain.pages import ChannelIdentity
from channel.domain.subscription_lists import SubscriptionBoundary
from channel.domain.subscriptions import (
    SubscriptionAuthenticationRequired,
    SubscriptionError,
    SubscriptionState,
)
from common.contracts.transactions import Transactions


class SubscriptionActors(Protocol):
    async def require(self, account_id: int) -> None: ...


class Subscriptions(Protocol):
    async def channels(
        self, subscriber_id: int, after: SubscriptionBoundary | None
    ) -> tuple[ChannelIdentity, ...]: ...
    async def owners(self, subscriber_id: int) -> tuple[int, ...]: ...

    async def get(self, subscriber_id: int, channel_id: int) -> bool: ...

    async def set(self, subscriber_id: int, channel_id: int, subscribed: bool) -> None: ...


class SubscriptionActions:
    def __init__(
        self,
        actors: SubscriptionActors,
        find: FindChannel,
        store: Subscriptions,
        transactions: Transactions,
    ) -> None:
        self.actors, self.find, self.store, self.transactions = actors, find, store, transactions

    async def actor(self, account_id: int | None) -> int:
        if type(account_id) is not int or not 0 < account_id < 2**63:
            raise SubscriptionAuthenticationRequired
        await self.actors.require(account_id)
        return account_id


class GetSubscription:
    def __init__(self, actions: SubscriptionActions) -> None:
        self.actions = actions

    async def __call__(self, account_id: int | None, tag: str) -> SubscriptionState:
        async with self.actions.transactions.begin():
            actor = await self.actions.actor(account_id)
            channel = await self.actions.find(tag)
            if channel.owner_id == actor:
                return SubscriptionState(False, False)
            return SubscriptionState(await self.actions.store.get(actor, channel.id), True)


class SetSubscription:
    def __init__(self, actions: SubscriptionActions) -> None:
        self.actions = actions

    async def __call__(
        self, account_id: int | None, tag: str, subscribed: object
    ) -> SubscriptionState:
        async with self.actions.transactions.begin():
            actor = await self.actions.actor(account_id)
            channel = await self.actions.find(tag)
            if channel.owner_id == actor:
                raise SubscriptionError("self_subscription")
            if type(subscribed) is not bool:
                raise SubscriptionError("invalid_subscription")
            await self.actions.store.set(actor, channel.id, subscribed)
            return SubscriptionState(subscribed, True)
