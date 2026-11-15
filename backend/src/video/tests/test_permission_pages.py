"""Bounded permission pages, opaque positions, revocation and real SQL rollback."""

import asyncio
import base64
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, select, text
from sqlalchemy.exc import SQLAlchemyError

from video.domain.permissions import TOKEN, InvalidPermissionCursor, PermissionCursor
from video.infrastructure.access import SqlPrivateGrants
from video.infrastructure.models import PrivateGrantRow
from video.public import (
    AuthenticationRequired,
    CreateDraft,
    GetPrivateAccess,
    GetVideoSettings,
    SetPrivateAccess,
    VideoMetadata,
    VideoNotFound,
)
from video.tests.test_access import account
from video.tests.test_publications import invoke


def test_cursor_scope_canonicality_and_numeric_contents():
    value = PermissionCursor(2**63 - 1, 2**63 - 1, 2**63 - 1)
    token = value.encode()
    assert (
        len(token) == 35 and PermissionCursor.decode(token, value.video_id, value.owner_id) == value
    )
    assert TOKEN.unpack(base64.urlsafe_b64decode(token + "=")) == (
        1,
        1,
        value.video_id,
        value.owner_id,
        value.id,
    )
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    noncanonical = token[:-1] + alphabet[alphabet.index(token[-1]) + 1]
    for invalid in ("", token + "=", token[:-1], "x" * 10000, "';--", noncanonical):
        with pytest.raises(InvalidPermissionCursor):
            PermissionCursor.decode(invalid, value.video_id, value.owner_id)
    for video, owner in ((1, value.owner_id), (value.video_id, 1)):
        with pytest.raises(InvalidPermissionCursor):
            PermissionCursor.decode(token, video, owner)


@pytest.mark.parametrize("version,kind,identity", [(2, 1, 1), (1, 2, 1), (1, 1, 0), (1, 1, 2**63)])
def test_forged_header_and_bigint_boundaries_rejected(version, kind, identity):
    token = base64.urlsafe_b64encode(TOKEN.pack(version, kind, 1, 2, identity)).decode().rstrip("=")
    with pytest.raises(InvalidPermissionCursor):
        PermissionCursor.decode(token, 1, 2)


@pytest.mark.parametrize("count", [0, 20, 21, 41])
def test_page_boundaries_order_no_email_in_cursor_and_bounded_sql(backend_runtime, count):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("private", "", "private")
            )
            addresses = [f"grant{index:03d}@example.com" for index in range(count)]
            for index, email in enumerate(addresses):
                changed = await invoke(container, SetPrivateAccess, owner.id, draft.id, email, True)
                assert changed.emails == tuple(reversed(addresses[: index + 1]))[:20]
                assert bool(changed.next_cursor) == (index >= 20)
            statements = []

            def capture(_, __, statement, parameters, *args):
                if "FROM video_private_grants" in statement:
                    assert "LIMIT" in statement and 21 in parameters.values()
                    statements.append(statement)

            cursor = None
            values = []
            pages = []
            event.listen(resources.engine.sync_engine, "before_cursor_execute", capture)
            try:
                while True:
                    page = await invoke(container, GetPrivateAccess, owner.id, draft.id, cursor)
                    pages.append(page)
                    values.extend(page.emails)
                    cursor = page.next_cursor
                    if cursor is None:
                        break
                    assert all(
                        email.encode() not in base64.urlsafe_b64decode(cursor + "=")
                        for email in addresses
                    )
                    assert all(email not in repr(page) for email in addresses)
            finally:
                event.remove(resources.engine.sync_engine, "before_cursor_execute", capture)
            assert values == list(reversed(addresses))
            assert [len(page.emails) for page in pages] == (
                [0] if count == 0 else [20] * (count // 20) + ([count % 20] if count % 20 else [])
            )
            assert len(statements) == len(pages)

    asyncio.run(exercise())


def test_noop_deleted_boundary_new_insert_and_regrant(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, _):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("private", "", "private")
            )
            addresses = [f"ordered{index:03d}@example.com" for index in range(41)]
            for email in addresses:
                await invoke(container, SetPrivateAccess, owner.id, draft.id, email, True)
            first = await invoke(container, GetPrivateAccess, owner.id, draft.id)
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert (
                await invoke(
                    container,
                    SetPrivateAccess,
                    owner.id,
                    draft.id,
                    " " + addresses[0].upper() + " ",
                    True,
                )
                == first
            )
            assert await invoke(container, GetVideoSettings, owner.id, draft.id) == before
            await invoke(container, SetPrivateAccess, owner.id, draft.id, first.emails[-1], False)
            await invoke(container, SetPrivateAccess, owner.id, draft.id, "new@example.com", True)
            second = await invoke(
                container, GetPrivateAccess, owner.id, draft.id, first.next_cursor
            )
            assert second.emails == tuple(reversed(addresses[:21]))[:20] and second.next_cursor
            await invoke(container, SetPrivateAccess, owner.id, draft.id, addresses[0], False)
            last = await invoke(container, GetPrivateAccess, owner.id, draft.id, second.next_cursor)
            assert last.emails == () and last.next_cursor is None
            regrant = await invoke(
                container, SetPrivateAccess, owner.id, draft.id, addresses[0], True
            )
            assert regrant.emails[:2] == (addresses[0], "new@example.com")
            assert (
                await invoke(
                    container, SetPrivateAccess, owner.id, draft.id, "absent@example.com", False
                )
                == regrant
            )
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            original = SqlPrivateGrants.page

            async def fail(repository, *args):
                await original(repository, *args)
                raise SQLAlchemyError("private page failure")

            with patch.object(SqlPrivateGrants, "page", fail):
                with pytest.raises(SQLAlchemyError):
                    await invoke(
                        container, SetPrivateAccess, owner.id, draft.id, addresses[0], False
                    )
            assert await invoke(container, GetVideoSettings, owner.id, draft.id) == before
            assert await invoke(container, GetPrivateAccess, owner.id, draft.id) == regrant

    asyncio.run(exercise())


def test_owner_check_precedes_decode_and_cursor_is_bound_to_video(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, _):
            owner, other = await account(container), await account(container)
            drafts = [
                await invoke(
                    container, CreateDraft, owner.id, VideoMetadata("private", "", "private")
                )
                for _ in range(2)
            ]
            with patch.object(
                PermissionCursor, "decode", side_effect=AssertionError("must authorize first")
            ):
                with pytest.raises(AuthenticationRequired):
                    await invoke(container, GetPrivateAccess, None, drafts[0].id, "bad")
                with pytest.raises(VideoNotFound):
                    await invoke(container, GetPrivateAccess, other.id, drafts[0].id, "bad")
            for token in (
                PermissionCursor(drafts[1].id, owner.id, 1).encode(),
                PermissionCursor(drafts[0].id, other.id, 1).encode(),
            ):
                with pytest.raises(InvalidPermissionCursor):
                    await invoke(container, GetPrivateAccess, owner.id, drafts[0].id, token)

    asyncio.run(exercise())


def test_migration_preserves_legacy_grants_order_and_sequence(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("private", "", "private")
            )
            spec = importlib.util.spec_from_file_location(
                "permission_positions",
                Path(__file__).parents[3] / "migrations/versions/0019_permission_positions.py",
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            def migrate(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    module.downgrade()
                    connection.execute(
                        text(
                            "INSERT INTO video_private_grants (video_id,email) VALUES "
                            "(:id,'c@example.com'),(:id,'a@example.com'),(:id,'b@example.com')"
                        ),
                        {"id": draft.id},
                    )
                    module.upgrade()

            async with resources.engine.begin() as connection:
                await connection.run_sync(migrate)
            page = await invoke(container, GetPrivateAccess, owner.id, draft.id)
            assert (
                page.emails == ("a@example.com", "b@example.com", "c@example.com")
                and page.next_cursor is None
            )
            async with resources.sessions() as session:
                before = (await session.execute(select(PrivateGrantRow.id))).scalars().all()
            changed = await invoke(
                container, SetPrivateAccess, owner.id, draft.id, "z@example.com", True
            )
            assert changed.emails == ("z@example.com", *page.emails)
            async with resources.sessions() as session:
                latest = await session.scalar(
                    select(PrivateGrantRow.id).where(
                        PrivateGrantRow.video_id == draft.id,
                        PrivateGrantRow.email == "z@example.com",
                    )
                )
                assert latest > max(before)

    asyncio.run(exercise())
