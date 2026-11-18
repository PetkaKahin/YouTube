"""Own subscription pages and owner IDs for composing the video feed."""

from channel.application.profiles import GetPublicChannels
from channel.application.subscriptions import SubscriptionActions
from channel.domain.pages import ChannelProfile
from channel.domain.subscription_lists import SIZE, SubscribedChannelPage, SubscriptionBoundary


class GetSubscribedChannels:
    def __init__(self, actions: SubscriptionActions, profiles: GetPublicChannels) -> None:
        self.actions, self.profiles = actions, profiles

    async def __call__(
        self, account_id: int | None, cursor: str | None = None
    ) -> SubscribedChannelPage:
        async with self.actions.transactions.begin():
            actor = await self.actions.actor(account_id)
            after = SubscriptionBoundary.decode(cursor, actor) if cursor is not None else None
            items: list[ChannelProfile] = []
            while True:
                rows = await self.actions.store.channels(actor, after)
                profiles = await self.profiles(tuple(row.owner_id for row in rows))
                for row in rows:
                    profile = profiles.get(row.owner_id)
                    if profile is not None:
                        items.append(
                            ChannelProfile(profile.id, profile.name, profile.tag, row.created_at)
                        )
                    if len(items) > SIZE:
                        last = items[SIZE - 1]
                        return SubscribedChannelPage(
                            tuple(items[:SIZE]),
                            SubscriptionBoundary(last.created_at, last.id).encode(actor),
                        )
                if len(rows) < SIZE + 1:
                    return SubscribedChannelPage(tuple(items), None)
                after = SubscriptionBoundary(rows[-1].created_at, rows[-1].id)


class GetSubscribedOwners:
    """Participates in the calling Video transaction; exposes only subscribed owner IDs."""

    def __init__(self, actions: SubscriptionActions) -> None:
        self.actions = actions

    async def __call__(self, account_id: int | None) -> tuple[int, ...]:
        actor = await self.actions.actor(account_id)
        return await self.actions.store.owners(actor)
