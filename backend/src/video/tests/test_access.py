"""Email grants, immutable Casbin rules and real PostgreSQL authorisation snapshots."""

import asyncio
from contextlib import asynccontextmanager
from itertools import product
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from channel.public import Register, Registration
from user.public import CreateAccount, GetAccount, NewAccount, SetRole
from video.domain.access import ViewConditions, Viewer
from video.domain.processing import SourceInspectionError
from video.domain.publications import (
    AuthenticationRequired,
    VideoError,
    VideoMetadata,
    VideoNotFound,
)
from video.infrastructure.access import SqlPrivateGrants
from video.infrastructure.encoder import S3MediaEncoder
from video.infrastructure.models import PublicationRow
from video.infrastructure.viewing_policy import create_viewing_policy
from video.public import (
    CompleteUpload,
    CreateDraft,
    EditVideoSettings,
    GetMedia,
    GetPrivateAccess,
    GetVideoSettings,
    GetViewableVideo,
    PublishVideo,
    SetPrivateAccess,
)
from video.tests import test_source_processing as support
from video.tests.test_originals import ready
from video.tests.test_publications import account, invoke, wait_for_waiters
from video.tests.test_source_processing import process, source_environment
from video.tests.test_subtitle_jobs import ffmpeg

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.fixture(autouse=True)
def registered_source_owner(monkeypatch):
    async def create_owner(container):
        token = uuid4().hex
        registered = await invoke(
            container,
            Register,
            Registration("Иван", token + "@example.com", "t" + token[:28], "abcdef", "abcdef"),
        )
        return await invoke(container, GetAccount, registered.account_id)

    monkeypatch.setattr(support, "account", create_owner)


@pytest.fixture(scope="module")
def small_video(tmp_path_factory):
    return support.small_video.__wrapped__(tmp_path_factory)


@pytest.mark.parametrize(
    "public,published,visible,playable,subject",
    list(
        product(
            [False, True],
            [False, True],
            [False, True],
            [False, True],
            ["guest", "owner", "invited", "stranger"],
        )
    ),
)
def test_real_casbin_policy_matrix(public, published, visible, playable, subject):
    viewer = Viewer(None if subject == "guest" else 1 if subject == "owner" else 2)
    conditions = ViewConditions(1, published, visible, playable, public, subject == "invited")
    expected = published and visible and playable and (public or subject in {"owner", "invited"})
    assert create_viewing_policy().allows(viewer, conditions) == expected


@asynccontextmanager
async def published(backend_runtime, data, tmp_path, source_queue):
    async with ready(backend_runtime, data, tmp_path, source_queue) as context:
        container, _, owner, draft, _ = context
        await invoke(container, PublishVideo, owner.id, draft.id)
        yield context


def test_permission_before_registration_normalization_revocation_and_independent_dates(
    backend_runtime, small_video, tmp_path, source_queue
):
    async def exercise():
        async with published(backend_runtime, small_video, tmp_path, source_queue) as (
            container,
            resources,
            owner,
            draft,
            _,
        ):
            email = uuid4().hex + "@example.com"
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert (await invoke(container, GetPrivateAccess, owner.id, draft.id)).emails == ()
            assert (
                await invoke(
                    container, SetPrivateAccess, owner.id, draft.id, " " + email.upper() + " ", True
                )
            ).emails == (email,)
            changed = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert changed.updated_at > before.updated_at and changed.added_at == before.added_at
            async with container() as operation:
                session = await operation.get(AsyncSession)
                async with session.begin():
                    recipient = await (await operation.get(CreateAccount))(
                        NewAccount("Получатель", email.upper(), "abcdef", "abcdef")
                    )
            assert (
                await invoke(container, SetPrivateAccess, owner.id, draft.id, email, True)
            ).emails == (email,)
            assert (
                await invoke(container, GetVideoSettings, owner.id, draft.id)
            ).updated_at == changed.updated_at
            assert (
                await invoke(container, GetViewableVideo, recipient.id, draft.id)
            ).id == draft.id
            with pytest.raises(VideoNotFound):
                await invoke(container, GetViewableVideo, None, draft.id)
            assert (await invoke(container, GetViewableVideo, owner.id, draft.id)).qualities
            assert (
                await invoke(container, SetPrivateAccess, owner.id, draft.id, email, False)
            ).emails == ()
            revoked = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert revoked.updated_at > changed.updated_at
            assert (
                await invoke(container, SetPrivateAccess, owner.id, draft.id, email.upper(), False)
            ).emails == ()
            assert (
                await invoke(container, GetVideoSettings, owner.id, draft.id)
            ).updated_at == revoked.updated_at
            with pytest.raises(VideoNotFound):
                await invoke(container, GetViewableVideo, recipient.id, draft.id)
            assert (await invoke(container, GetViewableVideo, owner.id, draft.id)).id == draft.id

    asyncio.run(exercise())


def test_grants_never_resolve_the_recipient_account(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, _):
            owner = await account(container)
            recipient = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("private", "", "private")
            )
            calls = []
            original = GetAccount.__call__

            async def tracked(service, account_id):
                calls.append(account_id)
                return await original(service, account_id)

            with patch.object(GetAccount, "__call__", tracked):
                known = (
                    await invoke(
                        container, SetPrivateAccess, owner.id, draft.id, recipient.email, True
                    )
                ).emails
                missing = (
                    await invoke(
                        container,
                        SetPrivateAccess,
                        owner.id,
                        draft.id,
                        uuid4().hex + "@example.com",
                        True,
                    )
                ).emails
                assert (
                    await invoke(container, GetPrivateAccess, owner.id, draft.id)
                ).emails == missing
            assert calls and set(calls) == {owner.id}
            assert len(known) == 1 and len(missing) == 2

    asyncio.run(exercise())


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_staff_role_cannot_manage_foreign_grants_or_view_private_video(
    backend_runtime, small_video, tmp_path, source_queue, role
):
    async def exercise():
        async with published(backend_runtime, small_video, tmp_path, source_queue) as (
            container,
            _,
            owner,
            draft,
            _,
        ):
            other = await account(container)
            await invoke(container, SetRole, other.email, role)
            for video_id in (draft.id, 2**62):
                with pytest.raises(VideoNotFound):
                    await invoke(container, GetPrivateAccess, other.id, video_id)
                with pytest.raises(VideoNotFound):
                    await invoke(container, SetPrivateAccess, other.id, video_id, other.email, True)
                with pytest.raises(VideoNotFound):
                    await invoke(container, GetViewableVideo, other.id, video_id)
            assert (await invoke(container, GetPrivateAccess, owner.id, draft.id)).emails == ()

    asyncio.run(exercise())


def test_live_visibility_hidden_and_draft_rules_without_cached_authority(
    backend_runtime, small_video, tmp_path, source_queue
):
    async def exercise():
        async with published(backend_runtime, small_video, tmp_path, source_queue) as (
            container,
            resources,
            owner,
            draft,
            _,
        ):
            invited = await account(container)
            await invoke(container, SetPrivateAccess, owner.id, draft.id, invited.email, True)
            await invoke(
                container,
                EditVideoSettings,
                owner.id,
                draft.id,
                VideoMetadata("public", "", "public"),
            )
            assert (await invoke(container, GetViewableVideo, None, draft.id)).title == "public"
            await invoke(
                container,
                EditVideoSettings,
                owner.id,
                draft.id,
                VideoMetadata("private", "", "private"),
            )
            with pytest.raises(VideoNotFound):
                await invoke(container, GetViewableVideo, None, draft.id)
            assert (await invoke(container, GetPrivateAccess, owner.id, draft.id)).emails == (
                invited.email,
            )
            assert (await invoke(container, GetViewableVideo, invited.id, draft.id)).id == draft.id
            for field, denied, restored in (
                ("moderation", "hidden", "visible"),
                ("publication", "draft", "published"),
            ):
                async with resources.sessions.begin() as session:
                    setattr(await session.get(PublicationRow, draft.id), field, denied)
                for actor in (None, owner.id, invited.id):
                    with pytest.raises(VideoNotFound):
                        await invoke(container, GetViewableVideo, actor, draft.id)
                assert (
                    await invoke(container, GetVideoSettings, owner.id, draft.id)
                ).id == draft.id
                async with resources.sessions.begin() as session:
                    setattr(await session.get(PublicationRow, draft.id), field, restored)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        "not-email",
        "x@example.com\nBcc: other@example.com",
        "'; DROP TABLE video_publications;--",
        "<script>@example.com",
        "a\x00@example.com",
        "a\ud800@example.com",
    ],
)
def test_invalid_email_rolls_back_without_changes(backend_runtime, value):
    async def exercise():
        async with backend_runtime() as (container, _):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("v", "", "private")
            )
            with pytest.raises(VideoError):
                await invoke(container, SetPrivateAccess, owner.id, draft.id, value, True)
            assert (await invoke(container, GetPrivateAccess, owner.id, draft.id)).emails == ()
            assert await invoke(container, GetVideoSettings, owner.id, draft.id) == draft
            with pytest.raises(AuthenticationRequired):
                await invoke(container, GetPrivateAccess, None, draft.id)

    asyncio.run(exercise())


def test_parallel_duplicate_grants_and_rollback_after_real_change(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, _):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("v", "", "private")
            )
            email = uuid4().hex + "@example.com"
            results = await asyncio.gather(
                *[
                    invoke(container, SetPrivateAccess, owner.id, draft.id, email, True)
                    for _ in range(8)
                ]
            )
            assert all(
                result.emails == (email,) and result.next_cursor is None for result in results
            )
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            original = SqlPrivateGrants.set

            async def fail(repository, *args):
                await original(repository, *args)
                raise RuntimeError("synthetic lost commit")

            with patch.object(SqlPrivateGrants, "set", fail):
                with pytest.raises(RuntimeError):
                    await invoke(container, SetPrivateAccess, owner.id, draft.id, email, False)
            assert (await invoke(container, GetPrivateAccess, owner.id, draft.id)).emails == (
                email,
            )
            assert await invoke(container, GetVideoSettings, owner.id, draft.id) == before

    asyncio.run(exercise())


@pytest.mark.parametrize("commit", [False, True])
def test_grant_changes_wait_for_publication_lock_and_observe_commit_or_rollback(
    backend_runtime, commit
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("v", "", "private")
            )
            async with resources.sessions() as blocker:
                await blocker.begin()
                pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
                await blocker.scalar(
                    select(PublicationRow).where(PublicationRow.id == draft.id).with_for_update()
                )
                # The blocked command must serialize with metadata edits.
                (await blocker.get(PublicationRow, draft.id)).title = (
                    "committed" if commit else "rolled back"
                )
                await blocker.flush()
                pending = asyncio.create_task(
                    invoke(
                        container, SetPrivateAccess, owner.id, draft.id, "test@example.com", True
                    )
                )
                await wait_for_waiters(resources.engine, pid)
                assert not pending.done()
                await blocker.commit() if commit else await blocker.rollback()
                result = await pending
                assert result.emails == ("test@example.com",) and result.next_cursor is None
            settings = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert settings.title == ("committed" if commit else "v")

    asyncio.run(exercise())


def test_failed_or_pending_video_never_exposes_metadata(backend_runtime, small_video):
    async def exercise():
        async with backend_runtime() as (container, _):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("private-marker", "", "public")
            )
            with pytest.raises(VideoNotFound):
                await invoke(container, GetViewableVideo, None, draft.id)

    asyncio.run(exercise())


def test_early_low_quality_remains_viewable_after_high_quality_failure(
    backend_runtime, tmp_path, source_queue
):
    video = tmp_path / "early.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=1:duration=1", "-c:v", "mpeg4", video)

    async def exercise():
        async with source_environment(backend_runtime, video.read_bytes()) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            recipient = await account(container)
            await invoke(container, SetPrivateAccess, owner.id, draft.id, recipient.email, True)
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            await invoke(container, PublishVideo, owner.id, draft.id)
            original = S3MediaEncoder.encode
            observations = []

            async def encode(encoder, claim):
                if claim.spec.get("quality") == 720:
                    value = await invoke(container, GetViewableVideo, recipient.id, draft.id)
                    observations.append([item.quality for item in value.qualities])
                    assert observations[-1] == [360, 480]
                    master = await invoke(
                        container, GetMedia, recipient.id, draft.id, None, "master.m3u8"
                    )
                    assert "tracks/v1/index.m3u8" in master.text
                    assert "tracks/v2/index.m3u8" in master.text
                    assert "tracks/v3/index.m3u8" not in master.text
                    await invoke(container, GetMedia, recipient.id, draft.id, "v1", "init.mp4")
                    with pytest.raises(VideoNotFound):
                        await invoke(container, GetMedia, recipient.id, draft.id, "v3", "init.mp4")
                    with pytest.raises(VideoNotFound):
                        await invoke(container, GetViewableVideo, None, draft.id)
                    raise SourceInspectionError("encoding_failed")
                return await original(encoder, claim)

            with patch.object(S3MediaEncoder, "encode", encode):
                await process(container, resources, tmp_path, source_queue)
            value = await invoke(container, GetViewableVideo, recipient.id, draft.id)
            assert observations == [[360, 480]]
            assert [item.quality for item in value.qualities] == [360, 480] and not value.hd

    asyncio.run(exercise())


@pytest.mark.parametrize("email", ["Иван@пример.рф", "x'--@example.com"])
def test_unicode_and_sql_punctuation_use_exact_account_normalization(
    backend_runtime, small_video, tmp_path, source_queue, email
):
    async def exercise():
        async with published(backend_runtime, small_video, tmp_path, source_queue) as (
            container,
            _,
            owner,
            draft,
            _,
        ):
            async with container() as operation:
                session = await operation.get(AsyncSession)
                async with session.begin():
                    recipient = await (await operation.get(CreateAccount))(
                        NewAccount("Получатель", email, "abcdef", "abcdef")
                    )
            assert (
                await invoke(container, SetPrivateAccess, owner.id, draft.id, email, True)
            ).emails == (recipient.email,)
            assert (
                await invoke(container, GetViewableVideo, recipient.id, draft.id)
            ).id == draft.id
            stranger = await account(container)
            with pytest.raises(VideoNotFound):
                await invoke(container, GetViewableVideo, stranger.id, draft.id)

    asyncio.run(exercise())
