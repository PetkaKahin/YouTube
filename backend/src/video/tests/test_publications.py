"""Video scenarios and persistence against an isolated migrated PostgreSQL database."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from common.infrastructure.transactions import SqlTransactions
from user.public import CreateAccount, NewAccount
from video.application.ports import VideoActors
from video.application.publications import CreateDraft, EditVideoSettings, GetVideoSettings
from video.domain.publications import (
    AuthenticationRequired,
    ModerationState,
    ProcessingState,
    PublicationState,
    UploadState,
    VideoError,
    VideoMetadata,
    VideoNotFound,
)
from video.infrastructure.models import PublicationRow
from video.infrastructure.publications import SqlPublications


async def account(container):
    async with container() as operation:
        session = await operation.get(AsyncSession)
        async with session.begin():
            return await (await operation.get(CreateAccount))(
                NewAccount("Иван", uuid4().hex + "@example.com", "abcdef", "abcdef")
            )


async def invoke(container, scenario_type, *args):
    async with container() as operation:
        return await (await operation.get(scenario_type))(*args)


@pytest.mark.parametrize("publication", ["draft", "published"])
def test_create_and_edit_preserve_independent_states_and_idempotent_timestamp(
    backend_runtime, publication
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("  Черновик  ", "", "private")
            )
            assert draft.title == "Черновик" and draft.owner_id == owner.id
            assert draft.publication is PublicationState.DRAFT
            assert draft.upload is UploadState.PENDING
            assert draft.processing is ProcessingState.PENDING
            assert draft.moderation is ModerationState.VISIBLE
            assert draft.added_at is None
            assert draft.created_at == draft.updated_at
            async with resources.sessions.begin() as session:
                await session.execute(
                    update(PublicationRow)
                    .where(PublicationRow.id == draft.id)
                    .values(
                        upload="uploaded",
                        processing="processing",
                        moderation="hidden",
                        publication=publication,
                        added_at=draft.created_at,
                    )
                )
            first = await invoke(
                container,
                EditVideoSettings,
                owner.id,
                draft.id,
                VideoMetadata("Новое название", " текст\r\nс переносами\n ", "public"),
            )
            assert first.title == "Новое название"
            assert first.description == " текст\r\nс переносами\n "
            assert first.visibility.value == "public"
            assert first.updated_at > draft.updated_at
            assert first.created_at == draft.created_at
            assert first.added_at == draft.created_at
            assert first.moderation is ModerationState.HIDDEN
            assert first.publication.value == publication
            assert first.upload is UploadState.UPLOADED
            assert first.processing is ProcessingState.PROCESSING
            again = await invoke(
                container,
                EditVideoSettings,
                owner.id,
                draft.id,
                VideoMetadata(" \tНовое название\n", first.description, "public"),
            )
            assert again == first
            assert await invoke(container, GetVideoSettings, owner.id, draft.id) == first

    asyncio.run(exercise())


def test_foreign_or_missing_publication_is_indistinguishable_and_unmodified(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, _):
            owner, stranger = await account(container), await account(container)
            data = VideoMetadata("private marker", "not disclosed", "private")
            draft = await invoke(container, CreateDraft, owner.id, data)
            for video_id in (draft.id, 2**62):
                with pytest.raises(VideoNotFound):
                    await invoke(container, GetVideoSettings, stranger.id, video_id)
                with pytest.raises(VideoNotFound):
                    await invoke(container, EditVideoSettings, stranger.id, video_id, data)
            assert await invoke(container, GetVideoSettings, owner.id, draft.id) == draft
            for scenario_type, args in (
                (CreateDraft, (data,)),
                (GetVideoSettings, (draft.id,)),
                (EditVideoSettings, (draft.id, data)),
            ):
                with pytest.raises(AuthenticationRequired):
                    await invoke(container, scenario_type, 2**62, *args)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "changed",
    [
        {"title": ""},
        {"title": "a" * 101},
        {"description": "a" * 5001},
        {"visibility": "unlisted"},
        {"publication": "ready"},
        {"upload": "ready"},
        {"processing": "published"},
        {"moderation": "public"},
        {"owner_id": 2**62},
    ],
)
def test_database_constraints_reject_invalid_rows_without_changes(backend_runtime, changed):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("test", "", "public")
            )
            with pytest.raises(IntegrityError):
                async with resources.sessions.begin() as session:
                    await session.execute(
                        update(PublicationRow)
                        .where(PublicationRow.id == draft.id)
                        .values(**changed)
                    )
            assert await invoke(container, GetVideoSettings, owner.id, draft.id) == draft

    asyncio.run(exercise())


def test_invalid_metadata_rolls_back_and_next_operation_is_usable(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            async with container() as operation:
                scenario = await operation.get(CreateDraft)
                with pytest.raises(VideoError):
                    await scenario(owner.id, VideoMetadata("", "", "private"))
                valid = await scenario(owner.id, VideoMetadata("valid", "", "private"))
                session = await operation.get(AsyncSession)
                assert not session.in_transaction()
            async with resources.sessions() as session:
                assert (
                    await session.scalars(
                        select(PublicationRow).where(PublicationRow.owner_id == owner.id)
                    )
                ).one().id == valid.id

    asyncio.run(exercise())


@pytest.mark.parametrize("scenario_type", [CreateDraft, EditVideoSettings])
def test_failure_after_real_insert_or_update_rolls_back(backend_runtime, scenario_type):
    class BrokenPublications(SqlPublications):
        async def create(self, owner_id, metadata):
            await super().create(owner_id, metadata)
            raise RuntimeError("artificial failure after insert")

        async def update_metadata(self, value):
            await super().update_metadata(value)
            raise RuntimeError("artificial failure after update")

    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            data = VideoMetadata("original", "", "private")
            draft = await invoke(container, CreateDraft, owner.id, data)
            async with container() as operation:
                session = await operation.get(AsyncSession)
                scenario = scenario_type(
                    await operation.get(VideoActors),
                    BrokenPublications(session),
                    SqlTransactions(session),
                )
                args = (VideoMetadata("new title", "new description", "public"),)
                if scenario_type is EditVideoSettings:
                    args = (draft.id, *args)
                with pytest.raises(RuntimeError):
                    await scenario(owner.id, *args)
                assert not session.in_transaction()
            assert await invoke(container, GetVideoSettings, owner.id, draft.id) == draft
            async with resources.sessions() as session:
                assert (
                    len(
                        list(
                            await session.scalars(
                                select(PublicationRow).where(PublicationRow.owner_id == owner.id)
                            )
                        )
                    )
                    == 1
                )

    asyncio.run(exercise())


async def wait_for_waiters(engine, blocker):
    async with asyncio.timeout(10), engine.connect() as observer:
        await observer.execution_options(isolation_level="AUTOCOMMIT")
        while not await observer.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                "AND :blocker = ANY(pg_blocking_pids(pid))"
            ),
            {"blocker": blocker},
        ):
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("commit", [False, True])
def test_edit_waits_for_row_lock_and_uses_latest_committed_metadata(backend_runtime, commit):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("original", "", "private")
            )
            requested = VideoMetadata("serialized", "different", "public")
            async with resources.sessions() as first:
                await first.begin()
                blocker = await first.scalar(text("SELECT pg_backend_pid()"))
                repository = SqlPublications(first)
                current = await repository.owned(draft.id, owner.id, lock=True)
                staged = await repository.update_metadata(
                    replace(
                        current,
                        title=requested.title,
                        description=requested.description,
                        visibility=type(current.visibility).PUBLIC,
                    )
                )
                waiting = asyncio.create_task(
                    invoke(container, EditVideoSettings, owner.id, draft.id, requested)
                )
                try:
                    await wait_for_waiters(resources.engine, blocker)
                    assert not waiting.done()
                    # Uncommitted metadata never reaches an independent read.
                    assert await invoke(container, GetVideoSettings, owner.id, draft.id) == draft
                    if commit:
                        await first.commit()
                    else:
                        await first.rollback()
                    result = await asyncio.wait_for(waiting, 10)
                    assert result.title == requested.title
                    assert (
                        result.updated_at == staged.updated_at
                        if commit
                        else (result.updated_at > staged.updated_at)
                    )
                    assert result.owner_id == draft.owner_id
                    assert result.publication is PublicationState.DRAFT
                finally:
                    await first.rollback()
                    if not waiting.done():
                        waiting.cancel()
                    await asyncio.gather(waiting, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("cancel", [False, True])
def test_cancelled_or_lost_connection_transaction_releases_lock_without_partial_change(
    backend_runtime, cancel
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            data = VideoMetadata("original", "", "private")
            draft = await invoke(container, CreateDraft, owner.id, data)
            async with resources.sessions() as holder:
                await holder.begin()
                blocker = await holder.scalar(text("SELECT pg_backend_pid()"))
                await SqlPublications(holder).owned(draft.id, owner.id, lock=True)
                waiting = asyncio.create_task(
                    invoke(
                        container,
                        EditVideoSettings,
                        owner.id,
                        draft.id,
                        VideoMetadata("must rollback", "", "public"),
                    )
                )
                try:
                    await wait_for_waiters(resources.engine, blocker)
                    if cancel:
                        waiting.cancel()
                    else:
                        async with resources.engine.begin() as observer:
                            pid = await observer.scalar(
                                text(
                                    "SELECT pid FROM pg_stat_activity "
                                    "WHERE datname=current_database() "
                                    "AND :blocker=ANY(pg_blocking_pids(pid))"
                                ),
                                {"blocker": blocker},
                            )
                            assert await observer.scalar(
                                text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
                            )
                    result = (
                        await asyncio.wait_for(asyncio.gather(waiting, return_exceptions=True), 10)
                    )[0]
                    assert isinstance(result, BaseException)
                finally:
                    await holder.rollback()
                    if not waiting.done():
                        waiting.cancel()
                    await asyncio.gather(waiting, return_exceptions=True)
            assert await invoke(container, GetVideoSettings, owner.id, draft.id) == draft
            # A later valid edit confirms rollback and pool recovery.
            result = await invoke(
                container,
                EditVideoSettings,
                owner.id,
                draft.id,
                VideoMetadata("after recovery", "", "private"),
            )
            assert result.title == "after recovery"

    asyncio.run(exercise())
