"""Like pairs, exact counters and permission changes on real prepared media."""

import asyncio
import copy
from unittest.mock import patch

import pytest
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.exc import IntegrityError

from user.public import SetRole
from video.infrastructure.likes import SqlVideoLikes
from video.infrastructure.models import LikeRow, MediaPlanRow, PrivateGrantRow, PublicationRow
from video.public import (
    AuthenticationRequired,
    GetVideoLike,
    GetVideoSettings,
    InvalidLike,
    SetPrivateAccess,
    SetVideoLike,
    VideoNotFound,
)
from video.tests import test_feed as feed
from video.tests.test_publications import account, invoke, wait_for_waiters

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.fixture(scope="module")
def media_seed(backend_runtime, tmp_path_factory, source_queue):
    return feed.feed_seed.__wrapped__(backend_runtime, tmp_path_factory, source_queue)


async def rows(resources, video_id):
    async with resources.sessions() as session:
        return await session.scalar(
            select(func.count()).select_from(LikeRow).where(LikeRow.video_id == video_id)
        )


def test_guest_owner_peers_totals_dates_and_shared_media_scope(backend_runtime, media_seed):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = media_seed[0]
            first, second = await feed.clone(resources, media_seed, count=2)
            video = first[0]
            peer = await account(container)
            before = await invoke(container, GetVideoSettings, owner.account_id, video)
            guest = await invoke(container, GetVideoLike, None, video)
            assert not guest.liked and guest.like_count == 0 and not guest.can_like
            with pytest.raises(AuthenticationRequired):
                await invoke(container, SetVideoLike, None, video, True)
            for desired, count in (
                (False, 0),
                (True, 1),
                (True, 1),
                (False, 0),
                (False, 0),
                (True, 1),
            ):
                state = await invoke(container, SetVideoLike, owner.account_id, video, desired)
                assert state.liked is desired and state.like_count == count and state.can_like
                assert await invoke(container, GetVideoLike, owner.account_id, video) == state
            peer_state = await invoke(container, SetVideoLike, peer.id, video, True)
            assert peer_state.liked and peer_state.like_count == 2
            guest = await invoke(container, GetVideoLike, None, video)
            assert not guest.liked and guest.like_count == 2 and not guest.can_like
            assert (await invoke(container, GetVideoLike, peer.id, second[0])).like_count == 0
            assert await rows(resources, video) == 2 and await rows(resources, second[0]) == 0
            assert await invoke(container, GetVideoSettings, owner.account_id, video) == before

    asyncio.run(exercise())


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_private_likes_survive_access_loss_but_no_state_or_mutation_leaks(
    backend_runtime, media_seed, role
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = media_seed[0]
            video = (await feed.clone(resources, media_seed))[0][0]
            viewer = await account(container)
            await invoke(container, SetRole, viewer.email, role)
            await invoke(container, SetVideoLike, viewer.id, video, True)
            async with resources.sessions.begin() as session:
                (await session.get(PublicationRow, video)).visibility = "private"
            for actor in (None, viewer.id):
                with pytest.raises(VideoNotFound):
                    await invoke(container, GetVideoLike, actor, video)
            for desired in (True, False):
                with pytest.raises(VideoNotFound):
                    await invoke(container, SetVideoLike, viewer.id, video, desired)
            assert (await invoke(container, GetVideoLike, owner.account_id, video)).like_count == 1
            await invoke(container, SetPrivateAccess, owner.account_id, video, viewer.email, True)
            state = await invoke(container, GetVideoLike, viewer.id, video)
            assert state.liked and state.like_count == 1 and state.can_like
            assert await invoke(container, SetVideoLike, viewer.id, video, True) == state
            for field, value in (("moderation", "hidden"), ("publication", "draft")):
                async with resources.sessions.begin() as session:
                    publication = await session.get(PublicationRow, video)
                    previous = getattr(publication, field)
                    setattr(publication, field, value)
                for actor in (owner.account_id, viewer.id):
                    with pytest.raises(VideoNotFound):
                        await invoke(container, GetVideoLike, actor, video)
                    with pytest.raises(VideoNotFound):
                        await invoke(container, SetVideoLike, actor, video, False)
                async with resources.sessions.begin() as session:
                    setattr(await session.get(PublicationRow, video), field, previous)
            assert await rows(resources, video) == 1
            await invoke(container, SetPrivateAccess, owner.account_id, video, viewer.email, False)
            with pytest.raises(VideoNotFound):
                await invoke(container, SetVideoLike, viewer.id, video, False)
            assert await rows(resources, video) == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_strict_target_and_actor_validation(backend_runtime, media_seed, value):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = media_seed[0]
            video = (await feed.clone(resources, media_seed))[0][0]
            with pytest.raises(InvalidLike):
                await invoke(container, SetVideoLike, owner.account_id, video, value)
            assert await rows(resources, video) == 0
            with pytest.raises(AuthenticationRequired):
                await invoke(container, SetVideoLike, False, video, True)
            with pytest.raises(AuthenticationRequired):
                await invoke(container, GetVideoLike, 2**62, video)
            with pytest.raises(VideoNotFound):
                await invoke(container, SetVideoLike, owner.account_id, 2**62, True)

    asyncio.run(exercise())


def test_parallel_pairs_exact_totals_other_publications_and_constraints(
    backend_runtime, media_seed
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            video, other = [item[0] for item in await feed.clone(resources, media_seed, count=2)]
            owner = media_seed[0].account_id
            repeated = await asyncio.gather(
                *[invoke(container, SetVideoLike, owner, video, True) for _ in range(8)]
            )
            assert all(result.liked and result.like_count == 1 for result in repeated)
            peers = [await account(container) for _ in range(8)]
            added = await asyncio.gather(
                *[invoke(container, SetVideoLike, peer.id, video, True) for peer in peers]
            )
            assert all(result.liked for result in added) and sorted(
                result.like_count for result in added
            ) == list(range(2, 10))
            assert (await invoke(container, GetVideoLike, None, video)).like_count == 9
            await invoke(container, SetVideoLike, owner, other, True)
            removed = await asyncio.gather(
                *[invoke(container, SetVideoLike, peer.id, video, False) for peer in peers]
            )
            assert not any(result.liked for result in removed) and sorted(
                result.like_count for result in removed
            ) == list(range(1, 9))
            desired = [True, False] * 8
            mixed = await asyncio.gather(
                *[invoke(container, SetVideoLike, owner, video, value) for value in desired]
            )
            assert [result.liked for result in mixed] == desired
            assert all(result.like_count == int(result.liked) for result in mixed)
            state = await invoke(container, GetVideoLike, owner, video)
            assert state.like_count == await rows(resources, video) and state.like_count in (0, 1)
            await invoke(container, SetVideoLike, owner, video, True)
            for target, actor in ((video, owner), (2**62, owner), (video, 2**62)):
                with pytest.raises(IntegrityError):
                    async with resources.sessions.begin() as session:
                        await session.execute(
                            insert(LikeRow).values(video_id=target, account_id=actor)
                        )
            assert (await invoke(container, GetVideoLike, owner, other)).like_count == 1

    asyncio.run(exercise())


def test_ready_aac_dependency_rollback_and_valkey_loss(backend_runtime, media_seed):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = media_seed[0].account_id
            video, upload = (await feed.clone(resources, media_seed))[0]
            await invoke(container, SetVideoLike, owner, video, True)
            async with resources.sessions.begin() as session:
                plan = await session.get(MediaPlanRow, upload)
                config = copy.deepcopy(plan.config)
                invalid = copy.deepcopy(config)
                invalid["variants"][0]["audio"] = 2**62
                plan.config = invalid
            with pytest.raises(VideoNotFound):
                await invoke(container, GetVideoLike, None, video)
            with pytest.raises(VideoNotFound):
                await invoke(container, SetVideoLike, owner, video, False)
            async with resources.sessions.begin() as session:
                (await session.get(MediaPlanRow, upload)).config = config
            before = await invoke(container, GetVideoSettings, owner, video)
            original = SqlVideoLikes.totals

            async def fail(repository, *args):
                await original(repository, *args)
                raise RuntimeError("synthetic aggregate failure")

            with patch.object(SqlVideoLikes, "totals", fail):
                with pytest.raises(RuntimeError):
                    await invoke(container, SetVideoLike, owner, video, False)
            assert (await invoke(container, GetVideoLike, owner, video)).liked
            with patch(
                "redis.asyncio.Redis.execute_command",
                side_effect=ConnectionError("synthetic outage"),
            ):
                assert (await invoke(container, GetVideoLike, None, video)).like_count == 1
                assert (await invoke(container, SetVideoLike, owner, video, False)).like_count == 0
            assert await invoke(container, GetVideoSettings, owner, video) == before

    asyncio.run(exercise())


@pytest.mark.parametrize("change", ["permission", "visibility", "hidden"])
@pytest.mark.parametrize("commit", [False, True])
def test_pending_mutation_rechecks_access_after_publication_lock(
    backend_runtime, media_seed, change, commit
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = media_seed[0].account_id
            viewer = await account(container)
            video = (
                await feed.clone(
                    resources,
                    media_seed,
                    visibility="private" if change == "permission" else "public",
                )
            )[0][0]
            if change == "permission":
                await invoke(container, SetPrivateAccess, owner, video, viewer.email, True)
            async with resources.sessions() as blocker:
                await blocker.begin()
                pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
                publication = await blocker.scalar(
                    select(PublicationRow).where(PublicationRow.id == video).with_for_update()
                )
                if change == "permission":
                    await blocker.execute(
                        delete(PrivateGrantRow).where(
                            PrivateGrantRow.video_id == video, PrivateGrantRow.email == viewer.email
                        )
                    )
                elif change == "visibility":
                    publication.visibility = "private"
                else:
                    publication.moderation = "hidden"
                await blocker.flush()
                pending = asyncio.create_task(
                    invoke(container, SetVideoLike, viewer.id, video, True)
                )
                await wait_for_waiters(resources.engine, pid)
                assert not pending.done()
                await blocker.commit() if commit else await blocker.rollback()
                if commit:
                    with pytest.raises(VideoNotFound):
                        await pending
                else:
                    state = await pending
                    assert state.liked and state.like_count == 1
            assert await rows(resources, video) == (0 if commit else 1)

    asyncio.run(exercise())
