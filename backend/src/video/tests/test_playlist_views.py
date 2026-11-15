"""Playlist contents use the same live rights as direct playback, before pagination."""

import asyncio
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from channel.public import (
    CreatePlaylist,
    GetPlaylist,
    PlaylistMetadata,
    PlaylistNotFound,
    Register,
    Registration,
    SetVideoPlaylists,
)
from video.infrastructure.models import PublicationRow
from video.public import SetPrivateAccess
from video.tests import test_feed as support
from video.tests.test_publications import invoke

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.fixture(scope="module")
def feed_seed(backend_runtime, tmp_path_factory, source_queue):
    return support.feed_seed.__wrapped__(backend_runtime, tmp_path_factory, source_queue)


async def recipient(container):
    token = uuid4().hex
    return await invoke(
        container,
        Register,
        Registration("Получатель", token + "@example.com", "t" + token[:28], "abcdef", "abcdef"),
    ), token + "@example.com"


@pytest.mark.parametrize("visibility", ["public", "unlisted", "private"])
def test_playlist_visibility_never_overrides_video_privacy_and_hidden_state(
    backend_runtime, feed_seed, visibility
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = feed_seed[0].account_id
            invited, email = await recipient(container)
            stranger, _ = await recipient(container)
            pairs = await support.clone(resources, feed_seed, count=4)
            public, private, hidden, draft = [identity for identity, _ in pairs]
            async with resources.sessions.begin() as session:
                (await session.get(PublicationRow, private)).visibility = "private"
                (await session.get(PublicationRow, hidden)).moderation = "hidden"
                (await session.get(PublicationRow, draft)).publication = "draft"
            playlist = await invoke(
                container, CreatePlaylist, owner, PlaylistMetadata("ordered", "", visibility)
            )
            for video_id, _ in pairs:
                await invoke(container, SetVideoPlaylists, owner, video_id, (playlist.id,))
            assert [
                item.id for item in (await invoke(container, GetPlaylist, owner, playlist.id)).items
            ] == [public, private]
            for actor in (None, invited.account_id, stranger.account_id):
                if visibility == "private":
                    with pytest.raises(PlaylistNotFound):
                        await invoke(container, GetPlaylist, actor, playlist.id)
                else:
                    assert [
                        item.id
                        for item in (await invoke(container, GetPlaylist, actor, playlist.id)).items
                    ] == [public]
            await invoke(container, SetPrivateAccess, owner, private, email, True)
            if visibility != "private":
                assert [
                    item.id
                    for item in (
                        await invoke(container, GetPlaylist, invited.account_id, playlist.id)
                    ).items
                ] == [public, private]
            await invoke(container, SetPrivateAccess, owner, private, email, False)
            if visibility != "private":
                assert [
                    item.id
                    for item in (
                        await invoke(container, GetPlaylist, invited.account_id, playlist.id)
                    ).items
                ] == [public]
            async with resources.sessions.begin() as session:
                (await session.get(PublicationRow, public)).moderation = "hidden"
            assert [
                item.id for item in (await invoke(container, GetPlaylist, owner, playlist.id)).items
            ] == [private]

    asyncio.run(exercise())


def test_inaccessible_entries_are_filtered_before_page_and_continuation(backend_runtime, feed_seed):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = feed_seed[0].account_id
            playlist = await invoke(
                container, CreatePlaylist, owner, PlaylistMetadata("p", "", "public")
            )
            pairs = await support.clone(resources, feed_seed, count=64)
            for identity, _ in pairs:
                await invoke(container, SetVideoPlaylists, owner, identity, (playlist.id,))
            hidden = [identity for identity, _ in pairs[:43]]
            async with resources.sessions.begin() as session:
                rows = (
                    await session.scalars(
                        select(PublicationRow).where(PublicationRow.id.in_(hidden))
                    )
                ).all()
                for row in rows:
                    row.visibility = "private"
            with (
                patch.object(
                    resources.objects, "get_object", side_effect=AssertionError("API read")
                ),
                patch.object(
                    resources.objects, "head_object", side_effect=AssertionError("API HEAD")
                ),
            ):
                first = await invoke(container, GetPlaylist, None, playlist.id)
                assert [item.id for item in first.items] == [
                    identity for identity, _ in pairs[43:63]
                ]
                assert first.next_cursor
                second = await invoke(container, GetPlaylist, None, playlist.id, first.next_cursor)
                assert [item.id for item in second.items] == [
                    pairs[-1][0]
                ] and second.next_cursor is None
            # Already known continuation does not survive a fresh privacy change.
            async with resources.sessions.begin() as session:
                (await session.get(PublicationRow, pairs[-1][0])).visibility = "private"
            assert (
                await invoke(container, GetPlaylist, None, playlist.id, first.next_cursor)
            ).items == ()
            other = await invoke(
                container, CreatePlaylist, owner, PlaylistMetadata("other", "", "public")
            )
            from channel.public import PlaylistError

            with pytest.raises(PlaylistError):
                await invoke(container, GetPlaylist, None, other.id, first.next_cursor)

    asyncio.run(exercise())


def test_reinserted_video_moves_to_end_without_changing_other_membership(
    backend_runtime, feed_seed
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = feed_seed[0].account_id
            first = await invoke(
                container, CreatePlaylist, owner, PlaylistMetadata("first", "", "unlisted")
            )
            second = await invoke(
                container, CreatePlaylist, owner, PlaylistMetadata("second", "", "public")
            )
            pairs = await support.clone(resources, feed_seed, count=2)
            a, b = [identity for identity, _ in pairs]
            await invoke(container, SetVideoPlaylists, owner, a, (first.id, second.id))
            await invoke(container, SetVideoPlaylists, owner, b, (first.id, second.id))
            await invoke(container, SetVideoPlaylists, owner, a, (second.id,))
            await invoke(container, SetVideoPlaylists, owner, a, (first.id, second.id))
            assert [
                card.id for card in (await invoke(container, GetPlaylist, None, first.id)).items
            ] == [b, a]
            assert [
                card.id for card in (await invoke(container, GetPlaylist, None, second.id)).items
            ] == [a, b]

    asyncio.run(exercise())
