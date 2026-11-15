"""Playlist invariants, atomic selection and concurrency on a migrated database."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from channel.domain.playlists import (
    PlaylistAuthenticationRequired,
    PlaylistBoundary,
    PlaylistError,
    PlaylistMetadata,
    PlaylistNotFound,
)
from channel.infrastructure.models import PlaylistEntryRow, PlaylistRow
from channel.infrastructure.playlists import SqlPlaylistStore
from channel.public import (
    CreatePlaylist,
    GetMyPlaylists,
    GetPlaylist,
    Register,
    Registration,
    SetVideoPlaylists,
)
from user.public import GetAccount, SetRole
from video.public import CreateDraft, GetVideoSettings, PlaylistVideoOwnership, VideoMetadata


async def invoke(container, scenario, *args):
    async with container() as operation:
        return await (await operation.get(scenario))(*args)


async def author(container):
    token = uuid4().hex
    return await invoke(
        container,
        Register,
        Registration("Автор", token + "@example.com", "t" + token[:28], "abcdef", "abcdef"),
    )


@pytest.mark.parametrize(
    "title,description",
    [("x", ""), ("Я" * 100, "a" * 5000), (" <script> ", "' DROP TABLE users;--\r\nnext")],
)
def test_metadata_normalizes_as_text(title, description):
    data = PlaylistMetadata(title, description, "unlisted").normalized()
    assert data.title == title.strip() and data.description == description.replace("\r\n", "\n")


@pytest.mark.parametrize(
    "title,description,visibility",
    [
        ("", "", "public"),
        (" \n ", "", "public"),
        ("x" * 101, "", "public"),
        ("x", "a" * 5001, "public"),
        ("x\x00", "", "public"),
        ("x", "\ud800", "public"),
        ("x", "", "unknown"),
    ],
)
def test_invalid_metadata(title, description, visibility):
    with pytest.raises(PlaylistError):
        PlaylistMetadata(title, description, visibility).normalized()


def test_cursor_is_canonical_and_bound_to_mode_object_and_owner():
    value = PlaylistBoundary(datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC), 2**63 - 1)
    token = value.encode(1, 10, 20)
    assert len(token) == 46 and PlaylistBoundary.decode(token, 1, 10, 20) == value
    for context in ((2, 10, 20), (1, 11, 20), (1, 10, 21)):
        with pytest.raises(PlaylistError):
            PlaylistBoundary.decode(token, *context)
    for malformed in ("", token + "=", "x" * 10000, "../secret", "' OR true--"):
        with pytest.raises(PlaylistError):
            PlaylistBoundary.decode(malformed, 1, 10, 20)


def test_owner_pages_equal_dates_all_modes_and_foreign_scope(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner, other = await author(container), await author(container)
            rows = [
                await invoke(
                    container,
                    CreatePlaylist,
                    owner.account_id,
                    PlaylistMetadata(
                        f"List {index}", "", ("public", "private", "unlisted")[index % 3]
                    ),
                )
                for index in range(41)
            ]
            date = rows[0].created_at
            async with resources.sessions.begin() as session:
                await session.execute(
                    update(PlaylistRow)
                    .where(PlaylistRow.channel_id == owner.channel_id)
                    .values(created_at=date)
                )
            first = await invoke(container, GetMyPlaylists, owner.account_id)
            second = await invoke(container, GetMyPlaylists, owner.account_id, first.next_cursor)
            third = await invoke(container, GetMyPlaylists, owner.account_id, second.next_cursor)
            assert [len(page.items) for page in (first, second, third)] == [20, 20, 1]
            assert [item.playlist.id for page in (first, second, third) for item in page.items] == [
                row.id for row in reversed(rows)
            ]
            assert third.next_cursor is None
            with pytest.raises(PlaylistError):
                await invoke(container, GetMyPlaylists, other.account_id, first.next_cursor)
            with pytest.raises(PlaylistAuthenticationRequired):
                await invoke(container, GetMyPlaylists, None)
            assert (await invoke(container, GetMyPlaylists, other.account_id)).items == ()

    asyncio.run(exercise())


def test_selection_any_number_noop_dates_independent_links_and_reinsert_position(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await author(container)
            video = await invoke(
                container, CreateDraft, owner.account_id, VideoMetadata("draft", "", "private")
            )
            other = await invoke(
                container, CreateDraft, owner.account_id, VideoMetadata("second", "", "private")
            )
            rows = [
                await invoke(
                    container,
                    CreatePlaylist,
                    owner.account_id,
                    PlaylistMetadata(f"List {index}", "", "private"),
                )
                for index in range(25)
            ]
            ids = tuple(row.id for row in rows)
            assert (
                await invoke(container, SetVideoPlaylists, owner.account_id, video.id, ids + ids)
                == ids
            )
            changed = await invoke(container, GetVideoSettings, owner.account_id, video.id)
            assert changed.updated_at > video.updated_at and changed.added_at == video.added_at
            async with resources.sessions() as session:
                entries = (
                    await session.scalars(
                        select(PlaylistEntryRow)
                        .where(PlaylistEntryRow.video_id == video.id)
                        .order_by(PlaylistEntryRow.id)
                    )
                ).all()
                positions = [row.id for row in entries]
                dates = (
                    await session.scalars(
                        select(PlaylistRow.updated_at)
                        .where(PlaylistRow.id.in_(ids))
                        .order_by(PlaylistRow.id)
                    )
                ).all()
            assert len(positions) == 25
            assert (
                await invoke(
                    container, SetVideoPlaylists, owner.account_id, video.id, tuple(reversed(ids))
                )
                == ids
            )
            assert await invoke(container, GetVideoSettings, owner.account_id, video.id) == changed
            await invoke(container, SetVideoPlaylists, owner.account_id, other.id, (ids[0],))
            await invoke(container, SetVideoPlaylists, owner.account_id, video.id, ids[1:])
            await invoke(container, SetVideoPlaylists, owner.account_id, video.id, ids)
            async with resources.sessions() as session:
                order = (
                    await session.scalars(
                        select(PlaylistEntryRow.video_id)
                        .where(PlaylistEntryRow.playlist_id == ids[0])
                        .order_by(PlaylistEntryRow.id)
                    )
                ).all()
                unchanged = (
                    await session.scalars(
                        select(PlaylistRow.updated_at)
                        .where(PlaylistRow.id.in_(ids[1:]))
                        .order_by(PlaylistRow.id)
                    )
                ).all()
            assert order == [other.id, video.id] and unchanged == dates[1:]
            options = await invoke(container, GetMyPlaylists, owner.account_id, None, video.id)
            assert len(options.items) == 20 and all(item.selected for item in options.items)
            assert await invoke(container, SetVideoPlaylists, owner.account_id, video.id, ()) == ()
            async with resources.sessions() as session:
                assert (
                    await session.scalars(
                        select(PlaylistEntryRow.video_id).where(
                            PlaylistEntryRow.playlist_id == ids[0]
                        )
                    )
                ).all() == [other.id]

    asyncio.run(exercise())


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_foreign_video_and_playlist_cannot_be_changed_by_role(backend_runtime, role):
    async def exercise():
        async with backend_runtime() as (container, _):
            owner, other = await author(container), await author(container)
            account = await invoke(container, GetAccount, other.account_id)
            await invoke(container, SetRole, account.email, role)
            video = await invoke(
                container, CreateDraft, owner.account_id, VideoMetadata("own", "", "private")
            )
            foreign = await invoke(
                container,
                CreatePlaylist,
                other.account_id,
                PlaylistMetadata("foreign", "", "private"),
            )
            owned = await invoke(
                container, CreatePlaylist, owner.account_id, PlaylistMetadata("owned", "", "public")
            )
            for actor, ids in (
                (other.account_id, (foreign.id,)),
                (owner.account_id, (owned.id, foreign.id)),
            ):
                with pytest.raises(PlaylistNotFound):
                    await invoke(container, SetVideoPlaylists, actor, video.id, ids)
            assert await invoke(container, GetVideoSettings, owner.account_id, video.id) == video
            assert not any(
                item.selected
                for item in (
                    await invoke(container, GetMyPlaylists, owner.account_id, None, video.id)
                ).items
            )
            with pytest.raises(PlaylistNotFound):
                await invoke(container, GetPlaylist, other.account_id, foreign.id + 2**32)

    asyncio.run(exercise())


def test_concurrent_repeated_selection_and_cross_module_rollback(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await author(container)
            video = await invoke(
                container, CreateDraft, owner.account_id, VideoMetadata("v", "", "private")
            )
            playlist = await invoke(
                container, CreatePlaylist, owner.account_id, PlaylistMetadata("p", "", "public")
            )
            results = await asyncio.gather(
                *[
                    invoke(container, SetVideoPlaylists, owner.account_id, video.id, (playlist.id,))
                    for _ in range(8)
                ]
            )
            assert all(value == (playlist.id,) for value in results)
            before = await invoke(container, GetVideoSettings, owner.account_id, video.id)
            metadata = (
                await invoke(container, GetPlaylist, owner.account_id, playlist.id)
            ).playlist
            original = PlaylistVideoOwnership.touch

            async def fail(service, *args):
                await original(service, *args)
                raise RuntimeError("lost commit")

            with patch.object(PlaylistVideoOwnership, "touch", fail), pytest.raises(RuntimeError):
                await invoke(container, SetVideoPlaylists, owner.account_id, video.id, ())
            assert await invoke(container, GetVideoSettings, owner.account_id, video.id) == before
            assert (
                await invoke(container, GetPlaylist, owner.account_id, playlist.id)
            ).playlist == metadata
            async with resources.sessions() as session:
                assert (
                    len(
                        (
                            await session.scalars(
                                select(PlaylistEntryRow).where(
                                    PlaylistEntryRow.video_id == video.id
                                )
                            )
                        ).all()
                    )
                    == 1
                )

    asyncio.run(exercise())


def test_creation_failure_rolls_back(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, _):
            owner = await author(container)
            original = SqlPlaylistStore.create

            async def fail(store, *args):
                await original(store, *args)
                raise RuntimeError("lost commit")

            with patch.object(SqlPlaylistStore, "create", fail), pytest.raises(RuntimeError):
                await invoke(
                    container, CreatePlaylist, owner.account_id, PlaylistMetadata("p", "", "public")
                )
            assert (await invoke(container, GetMyPlaylists, owner.account_id)).items == ()

    asyncio.run(exercise())
