"""Public playlist discovery does not inspect or grant access to its contents."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import update

from channel.domain.playlists import PlaylistMetadata
from channel.infrastructure.models import PlaylistRow
from channel.public import CreatePlaylist, FindPlaylists, SetVideoPlaylists
from channel.tests.test_playlists import author, invoke
from user.public import GetPublicNames
from video.public import CreateDraft, GetSearch, SearchQuery, VideoMetadata


@pytest.mark.parametrize("needle", ["тЕсТ", "%", "_", "!", "\\", "<script>", "';--"])
def test_title_literal_case_and_injection(backend_runtime, needle):
    async def exercise():
        async with backend_runtime() as (container, _):
            owner = await author(container)
            row = await invoke(
                container,
                CreatePlaylist,
                owner.account_id,
                PlaylistMetadata("Тест % _ ! \\ <script> ';--", "", "public"),
            )
            # No wildcard expansion, description or identity matching.
            await invoke(
                container,
                CreatePlaylist,
                owner.account_id,
                PlaylistMetadata("ordinary", needle, "public"),
            )
            result = await invoke(container, GetSearch, SearchQuery(needle, "playlist"))
            # Other parametrized cases also created the same literal title.
            assert row.id in [item.playlist.id for item in result.items]
            assert all(item.playlist.title == row.title for item in result.items)
            assert result.next_cursor is None
            assert (
                await invoke(container, GetSearch, SearchQuery(owner.tag, "playlist"))
            ).items == ()

    asyncio.run(exercise())


@pytest.mark.parametrize("sort", ["created", "updated"])
def test_public_filter_before_pages_and_fresh_visibility(backend_runtime, sort):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await author(container)
            title = uuid4().hex
            public = []
            for index in range(85):
                row = await invoke(
                    container,
                    CreatePlaylist,
                    owner.account_id,
                    PlaylistMetadata(
                        title,
                        "",
                        "public" if index < 41 else ("private" if index % 2 else "unlisted"),
                    ),
                )
                if index < 41:
                    public.append(row)
            date = public[0].created_at
            async with resources.sessions.begin() as session:
                await session.execute(
                    update(PlaylistRow)
                    .where(PlaylistRow.channel_id == owner.channel_id)
                    .values(created_at=date, updated_at=date)
                )
            query = SearchQuery(
                title, "playlist", sort, "long", features=("4k", "hdr", "subtitles")
            )
            first = await invoke(container, GetSearch, query)
            second = await invoke(container, GetSearch, query, first.next_cursor)
            third = await invoke(container, GetSearch, query, second.next_cursor)
            assert [len(page.items) for page in (first, second, third)] == [20, 20, 1]
            assert [item.playlist.id for page in (first, second, third) for item in page.items] == [
                row.id for row in reversed(public)
            ]
            assert third.next_cursor is None
            async with resources.sessions.begin() as session:
                await session.execute(
                    update(PlaylistRow)
                    .where(PlaylistRow.id == public[0].id)
                    .values(visibility="unlisted")
                )
            fresh = await invoke(container, GetSearch, query, second.next_cursor)
            assert fresh.items == () and fresh.next_cursor is None

    asyncio.run(exercise())


@pytest.mark.parametrize("period", ["today", "week", "month", "year"])
def test_period_filters_creation_even_when_sorted_by_update(backend_runtime, period):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await author(container)
            anchor = datetime(2026, 4, 15, 10, tzinfo=UTC)
            start = SearchQuery("x", period=period).start(anchor)
            title = uuid4().hex
            rows = []
            for date in (
                start - timedelta(microseconds=1),
                start,
                anchor,
                anchor + timedelta(microseconds=1),
            ):
                row = await invoke(
                    container,
                    CreatePlaylist,
                    owner.account_id,
                    PlaylistMetadata(title, "", "public"),
                )
                rows.append(row)
                async with resources.sessions.begin() as session:
                    await session.execute(
                        update(PlaylistRow)
                        .where(PlaylistRow.id == row.id)
                        .values(created_at=date, updated_at=anchor)
                    )
            hits = await invoke(
                container, FindPlaylists, title, "updated", None, None, start, anchor
            )
            assert [hit.playlist.id for hit in hits] == [rows[2].id, rows[1].id]

    asyncio.run(exercise())


def test_real_membership_update_changes_search_order_and_noop_keeps_cursor(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, _):
            owner = await author(container)
            title = uuid4().hex
            first, second = [
                await invoke(
                    container,
                    CreatePlaylist,
                    owner.account_id,
                    PlaylistMetadata(title, "", "public"),
                )
                for _ in range(2)
            ]
            video = await invoke(
                container,
                CreateDraft,
                owner.account_id,
                VideoMetadata("secret draft", "", "private"),
            )
            await invoke(container, SetVideoPlaylists, owner.account_id, video.id, (first.id,))
            query = SearchQuery(
                title, "playlist", "updated", "long", features=("4k", "hdr", "subtitles")
            )
            before = await invoke(container, GetSearch, query)
            assert [item.playlist.id for item in before.items] == [first.id, second.id]
            # A draft with no source does not influence discovery or video filters.
            await invoke(
                container, SetVideoPlaylists, owner.account_id, video.id, (first.id, first.id)
            )
            assert await invoke(container, GetSearch, query) == before
            created = await invoke(container, GetSearch, SearchQuery(title, "playlist"))
            assert [item.playlist.id for item in created.items] == [second.id, first.id]

    asyncio.run(exercise())


def test_public_names_batched_and_no_content_reads(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, _):
            owners = [await author(container) for _ in range(3)]
            title = uuid4().hex
            for index in range(21):
                await invoke(
                    container,
                    CreatePlaylist,
                    owners[index % 3].account_id,
                    PlaylistMetadata(title, "", "public"),
                )
            original = GetPublicNames.__call__
            batches = []

            async def capture(scenario, identifiers):
                batches.append(identifiers)
                return await original(scenario, identifiers)

            with patch.object(GetPublicNames, "__call__", capture):
                result = await invoke(container, GetSearch, SearchQuery(title, "playlist"))
            assert len(result.items) == 20 and result.next_cursor
            assert len(batches) == 1 and set(batches[0]) == {owner.account_id for owner in owners}
            assert len(batches[0]) == 3

    asyncio.run(exercise())
