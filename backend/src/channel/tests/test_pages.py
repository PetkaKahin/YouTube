"""Public profiles and public playlist pages never use viewer-specific rights."""

import asyncio
from unittest.mock import patch

import pytest
from sqlalchemy import update

from channel.domain.pages import ChannelNotFound
from channel.domain.playlists import PlaylistBoundary, PlaylistError, PlaylistMetadata
from channel.infrastructure.models import PlaylistRow
from channel.infrastructure.pages import SqlChannelPages
from channel.public import (
    CreatePlaylist,
    GetChannel,
    GetChannelPlaylists,
    GetMyPlaylists,
    SetVideoPlaylists,
)
from channel.tests.test_playlists import author, invoke
from video.public import CreateDraft, VideoMetadata


@pytest.mark.parametrize(
    "tag", ["", "xx", "x" * 31, "@@name", "<script>", "';--", "../secrets", "https://host"]
)
def test_invalid_tag_is_not_found_before_database_read(backend_runtime, tag):
    async def exercise():
        async with backend_runtime() as (container, _):
            with patch.object(
                SqlChannelPages, "find", side_effect=AssertionError("SQL must not run")
            ):
                with pytest.raises(ChannelNotFound):
                    await invoke(container, GetChannel, tag)

    asyncio.run(exercise())


def test_empty_channel_case_prefix_and_duplicate_names(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, _):
            first, second = await author(container), await author(container)
            values = [
                await invoke(container, GetChannel, tag)
                for tag in (first.tag, first.tag.upper(), "@" + first.tag.upper())
            ]
            assert values[0] == values[1] == values[2]
            assert values[0].id == first.channel_id and values[0].name == "Автор"
            other = await invoke(container, GetChannel, second.tag)
            assert other.name == values[0].name and other.id != values[0].id
            assert (await invoke(container, GetChannelPlaylists, first.tag)).items == ()
            with pytest.raises(ChannelNotFound):
                await invoke(container, GetChannel, "missing-channel")

    asyncio.run(exercise())


def test_playlist_pages_equal_dates_foreign_scope_noop_and_fresh_visibility(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner, other = await author(container), await author(container)
            rows = []
            for index in range(85):
                row = await invoke(
                    container,
                    CreatePlaylist,
                    owner.account_id,
                    PlaylistMetadata(
                        "public name",
                        "",
                        "public" if index < 41 else ("private" if index % 2 else "unlisted"),
                    ),
                )
                if index < 41:
                    rows.append(row)
            await invoke(
                container,
                CreatePlaylist,
                other.account_id,
                PlaylistMetadata("foreign", "", "public"),
            )
            async with resources.sessions.begin() as session:
                await session.execute(
                    update(PlaylistRow)
                    .where(PlaylistRow.channel_id == owner.channel_id)
                    .values(created_at=rows[0].created_at)
                )
            pages = []
            cursor = None
            for _ in range(3):
                page = await invoke(container, GetChannelPlaylists, "@" + owner.tag.upper(), cursor)
                pages.append(page)
                cursor = page.next_cursor
            assert [len(page.items) for page in pages] == [20, 20, 1]
            assert [row.id for page in pages for row in page.items] == [
                row.id for row in reversed(rows)
            ]
            assert cursor is None
            with pytest.raises(PlaylistError):
                await invoke(container, GetChannelPlaylists, other.tag, pages[0].next_cursor)
            owner_page = await invoke(container, GetMyPlaylists, owner.account_id)
            with pytest.raises(PlaylistError):
                await invoke(container, GetChannelPlaylists, owner.tag, owner_page.next_cursor)
            with pytest.raises(PlaylistError):
                await invoke(container, GetMyPlaylists, owner.account_id, pages[0].next_cursor)
            # Altering membership changes updated_at, never the public list order.
            draft = await invoke(
                container, CreateDraft, owner.account_id, VideoMetadata("secret", "", "private")
            )
            await invoke(container, SetVideoPlaylists, owner.account_id, draft.id, (rows[0].id,))
            fresh_first = await invoke(container, GetChannelPlaylists, owner.tag)
            assert fresh_first == pages[0]
            async with resources.sessions.begin() as session:
                await session.execute(
                    update(PlaylistRow)
                    .where(PlaylistRow.id == rows[0].id)
                    .values(visibility="unlisted")
                )
            last = await invoke(container, GetChannelPlaylists, owner.tag, pages[1].next_cursor)
            assert last.items == () and last.next_cursor is None
            for token in (
                "",
                "x" * 10000,
                pages[0].next_cursor + "=",
                PlaylistBoundary(rows[0].created_at, rows[0].id).encode(3, owner.channel_id, 0),
            ):
                with pytest.raises(PlaylistError):
                    await invoke(container, GetChannelPlaylists, owner.tag, token)

    asyncio.run(exercise())
