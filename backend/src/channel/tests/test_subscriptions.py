"""Active-pair integrity, target-state concurrency, ownership and transaction rollback."""

import asyncio
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from channel.infrastructure.models import SubscriptionRow
from channel.infrastructure.subscriptions import SqlSubscriptions
from channel.public import (
    ChannelNotFound,
    CreatePlaylist,
    GetChannel,
    GetPlaylist,
    GetSubscription,
    PlaylistMetadata,
    SetSubscription,
    SubscriptionAuthenticationRequired,
    SubscriptionError,
)
from channel.tests.test_playlists import author, invoke
from common.contracts.transactions import Transactions
from user.public import CreateAccount, GetAccount, NewAccount, SetRole
from video.public import CreateDraft, GetVideoSettings, VideoMetadata


async def count(resources, subscriber, channel):
    async with resources.sessions() as session:
        return await session.scalar(
            select(func.count())
            .select_from(SubscriptionRow)
            .where(
                SubscriptionRow.subscriber_id == subscriber,
                SubscriptionRow.channel_id == channel,
            )
        )


def test_state_self_channel_and_account_ids_differ_from_channel_ids(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, resources):
            async with container() as operation:
                async with (await operation.get(Transactions)).begin():
                    await (await operation.get(CreateAccount))(
                        NewAccount("Пропуск", uuid4().hex + "@example.com", "abcdef", "abcdef")
                    )
            owner, subscriber = await author(container), await author(container)
            assert owner.account_id != owner.channel_id
            for tag in (owner.tag, owner.tag.upper(), "@" + owner.tag.upper()):
                self_state = await invoke(container, GetSubscription, owner.account_id, tag)
                assert not self_state.subscribed and not self_state.can_subscribe
                for desired in (True, False):
                    with pytest.raises(SubscriptionError, match="self_subscription"):
                        await invoke(container, SetSubscription, owner.account_id, tag, desired)
            assert await count(resources, owner.account_id, owner.channel_id) == 0
            for desired in (False, True, True, False, False):
                result = await invoke(
                    container,
                    SetSubscription,
                    subscriber.account_id,
                    "@" + owner.tag.upper(),
                    desired,
                )
                assert result.subscribed is desired and result.can_subscribe
                assert (
                    await invoke(container, GetSubscription, subscriber.account_id, owner.tag)
                    == result
                )
                assert await count(resources, subscriber.account_id, owner.channel_id) == int(
                    desired
                )

    asyncio.run(exercise())


@pytest.mark.parametrize("actor", [None, False, 0, -1, "1", 2**63, 2**62])
def test_invalid_or_absent_actor_never_reaches_channel_lookup(backend_runtime, actor):
    async def exercise():
        async with backend_runtime() as (container, _):
            for scenario, args in ((GetSubscription, ()), (SetSubscription, (True,))):
                with pytest.raises(SubscriptionAuthenticationRequired):
                    await invoke(container, scenario, actor, "missing-channel", *args)

    asyncio.run(exercise())


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_state_must_be_actual_boolean(backend_runtime, value):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner, subscriber = await author(container), await author(container)
            with pytest.raises(SubscriptionError, match="invalid_subscription"):
                await invoke(container, SetSubscription, subscriber.account_id, owner.tag, value)
            assert await count(resources, subscriber.account_id, owner.channel_id) == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_roles_do_not_allow_self_and_target_is_tag_not_name(backend_runtime, role):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner, other = await author(container), await author(container)
            account = await invoke(container, GetAccount, owner.account_id)
            await invoke(container, SetRole, account.email, role)
            assert (await invoke(container, GetChannel, owner.tag)).name == (
                await invoke(container, GetChannel, other.tag)
            ).name
            with pytest.raises(SubscriptionError, match="self_subscription"):
                await invoke(container, SetSubscription, owner.account_id, owner.tag, True)
            assert (
                await invoke(container, SetSubscription, owner.account_id, other.tag, True)
            ).subscribed
            assert await count(resources, owner.account_id, other.channel_id) == 1
            assert not (
                await invoke(container, GetSubscription, other.account_id, owner.tag)
            ).subscribed

    asyncio.run(exercise())


def test_parallel_repeats_mixed_targets_foreign_pairs_and_constraints(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner, first, second = (
                await author(container),
                await author(container),
                await author(container),
            )
            responses = await asyncio.gather(
                *[
                    invoke(container, SetSubscription, first.account_id, owner.tag, True)
                    for _ in range(8)
                ]
            )
            assert all(result.subscribed and result.can_subscribe for result in responses)
            assert await count(resources, first.account_id, owner.channel_id) == 1
            await invoke(container, SetSubscription, second.account_id, owner.tag, True)
            responses = await asyncio.gather(
                *[
                    invoke(container, SetSubscription, first.account_id, owner.tag, False)
                    for _ in range(8)
                ]
            )
            assert all(not result.subscribed and result.can_subscribe for result in responses)
            assert await count(resources, first.account_id, owner.channel_id) == 0
            desired = [True, False] * 8
            responses = await asyncio.gather(
                *[
                    invoke(container, SetSubscription, first.account_id, owner.tag, value)
                    for value in desired
                ]
            )
            assert [result.subscribed for result in responses] == desired
            rows = await count(resources, first.account_id, owner.channel_id)
            assert rows in (0, 1) and (
                await invoke(container, GetSubscription, first.account_id, owner.tag)
            ).subscribed == bool(rows)
            await invoke(container, SetSubscription, first.account_id, owner.tag, False)
            assert await count(resources, first.account_id, owner.channel_id) == 0
            assert await count(resources, second.account_id, owner.channel_id) == 1
            for subscriber, channel in (
                (second.account_id, owner.channel_id),
                (2**62, owner.channel_id),
                (first.account_id, 2**62),
            ):
                with pytest.raises(IntegrityError):
                    async with resources.sessions.begin() as session:
                        await session.execute(
                            insert(SubscriptionRow).values(
                                subscriber_id=subscriber, channel_id=channel
                            )
                        )
            assert await count(resources, second.account_id, owner.channel_id) == 1

    asyncio.run(exercise())


def test_read_dates_rollback_and_no_cache_dependency(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner, subscriber = await author(container), await author(container)
            video = await invoke(
                container, CreateDraft, owner.account_id, VideoMetadata("draft", "", "private")
            )
            listing = await invoke(
                container, CreatePlaylist, owner.account_id, PlaylistMetadata("list", "", "private")
            )
            profile = await invoke(container, GetChannel, owner.tag)
            playlist = await invoke(container, GetPlaylist, owner.account_id, listing.id)
            original = SqlSubscriptions.set

            async def fail(repository, *args):
                await original(repository, *args)
                raise RuntimeError("synthetic lost commit")

            for current in (False, True):
                await invoke(container, SetSubscription, subscriber.account_id, owner.tag, current)
                with patch.object(SqlSubscriptions, "set", fail):
                    with pytest.raises(RuntimeError):
                        await invoke(
                            container,
                            SetSubscription,
                            subscriber.account_id,
                            owner.tag,
                            not current,
                        )
                assert (
                    await invoke(container, GetSubscription, subscriber.account_id, owner.tag)
                ).subscribed is current
            with patch(
                "redis.asyncio.Redis.execute_command",
                side_effect=ConnectionError("synthetic outage"),
            ):
                assert (
                    await invoke(container, GetSubscription, subscriber.account_id, owner.tag)
                ).subscribed
                assert not (
                    await invoke(
                        container, SetSubscription, subscriber.account_id, owner.tag, False
                    )
                ).subscribed
            assert await invoke(container, GetChannel, owner.tag) == profile
            assert await invoke(container, GetPlaylist, owner.account_id, listing.id) == playlist
            assert await invoke(container, GetVideoSettings, owner.account_id, video.id) == video
            for tag in ("missing-channel", "';DROP--", "../secrets"):
                with pytest.raises(ChannelNotFound):
                    await invoke(container, SetSubscription, subscriber.account_id, tag, True)
            assert await count(resources, subscriber.account_id, owner.channel_id) == 0

    asyncio.run(exercise())
