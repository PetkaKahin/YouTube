"""Publication transitions and fresh, publication-scoped media authorisation."""

import asyncio
from dataclasses import replace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from video.domain.delivery import ObjectDelivery, PlaylistDelivery, StoredMedia
from video.domain.publications import AuthenticationRequired, VideoNotFound
from video.infrastructure.models import PublicationRow
from video.infrastructure.publications import SqlPublications
from video.public import (
    CompleteUpload,
    CreateDraft,
    EditVideoSettings,
    GetMedia,
    GetVideoSettings,
    PublicationError,
    PublishVideo,
    SetPrivateAccess,
    VideoMetadata,
)
from video.tests import test_source_processing as support
from video.tests.test_access import published
from video.tests.test_publications import account, invoke
from video.tests.test_source_processing import source_environment

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.fixture(scope="module")
def small_video(tmp_path_factory):
    return support.small_video.__wrapped__(tmp_path_factory)


def test_publish_requires_accepted_upload_then_preserves_independent_states_and_dates(
    backend_runtime, small_video
):
    async def exercise():
        async with source_environment(backend_runtime, small_video) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            other = await account(container)
            original_settings = await invoke(container, GetVideoSettings, owner.id, draft.id)
            with pytest.raises(AuthenticationRequired):
                await invoke(container, PublishVideo, None, draft.id)
            for video_id in (draft.id, 2**62):
                with pytest.raises(VideoNotFound):
                    await invoke(container, PublishVideo, other.id, video_id)
            with pytest.raises(PublicationError):
                await invoke(container, PublishVideo, owner.id, draft.id)
            assert (
                await invoke(container, GetVideoSettings, owner.id, draft.id) == original_settings
            )
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            await invoke(container, SetPrivateAccess, owner.id, draft.id, other.email, True)
            async with resources.sessions.begin() as session:
                (await session.get(PublicationRow, draft.id)).moderation = "hidden"
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            with patch.object(resources.objects, "head_object", side_effect=AssertionError("I/O")):
                results = await asyncio.gather(
                    *[invoke(container, PublishVideo, owner.id, draft.id) for _ in range(4)]
                )
            assert all(item == results[0] for item in results)
            result = results[0]
            assert result.publication.value == "published"
            assert result.updated_at > before.updated_at
            assert (
                replace(result, publication=before.publication, updated_at=before.updated_at)
                == before
            )
            assert result.moderation.value == "hidden" and result.visibility.value == "private"
            for actor in (None, owner.id, other.id):
                with pytest.raises(VideoNotFound):
                    await invoke(container, GetMedia, actor, draft.id, None, "master.m3u8")

    asyncio.run(exercise())


def test_publish_rolls_back_after_storage_update(backend_runtime, small_video):
    async def exercise():
        async with source_environment(backend_runtime, small_video) as (
            container,
            _,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            original = SqlPublications.publish

            async def fail(repository, *args):
                await original(repository, *args)
                raise RuntimeError("lost commit")

            with patch.object(SqlPublications, "publish", fail), pytest.raises(RuntimeError):
                await invoke(container, PublishVideo, owner.id, draft.id)
            assert await invoke(container, GetVideoSettings, owner.id, draft.id) == before

    asyncio.run(exercise())


def test_all_files_check_live_rights_and_never_read_storage_in_api(
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
            other = await account(container)
            foreign = await invoke(
                container, CreateDraft, other.id, VideoMetadata("other", "", "public")
            )
            files = [
                (None, "master.m3u8"),
                ("v1", "index.m3u8"),
                ("v1", "init.mp4"),
                ("v1", "segment_000000.m4s"),
                (None, "preview.webp"),
            ]
            with (
                patch.object(resources.objects, "get_object", side_effect=AssertionError("I/O")),
                patch.object(resources.objects, "head_object", side_effect=AssertionError("I/O")),
            ):
                for track, name in files:
                    value = await invoke(container, GetMedia, owner.id, draft.id, track, name)
                    if isinstance(value, ObjectDelivery):
                        assert value.redirect.startswith("/_media_storage/")
                        assert "X-Amz-Signature=" in value.redirect
                        assert "X-Amz" not in repr(value)
                    else:
                        assert isinstance(value, PlaylistDelivery) and value.text.startswith(
                            "#EXTM3U"
                        )
                        assert not any(key in value.text for key in ("encoded/", "X-Amz", "rustfs"))
                    for actor in (None, other.id):
                        with pytest.raises(VideoNotFound):
                            await invoke(container, GetMedia, actor, draft.id, track, name)
                    with pytest.raises(VideoNotFound):
                        await invoke(container, GetMedia, other.id, foreign.id, track, name)
                for track, name in [
                    ("v1", "../init.mp4"),
                    ("v999", "init.mp4"),
                    ("v1", "segment_999999.m4s"),
                    ("1", "index.m3u8"),
                    (None, "http://rustfs:9000"),
                    ("v1", "init.mp4?x=1"),
                ]:
                    with pytest.raises(VideoNotFound):
                        await invoke(container, GetMedia, owner.id, draft.id, track, name)
                await invoke(container, SetPrivateAccess, owner.id, draft.id, other.email, True)
                for track, name in files:
                    await invoke(container, GetMedia, other.id, draft.id, track, name)
                await invoke(container, SetPrivateAccess, owner.id, draft.id, other.email, False)
                for track, name in files:
                    with pytest.raises(VideoNotFound):
                        await invoke(container, GetMedia, other.id, draft.id, track, name)
                await invoke(
                    container,
                    EditVideoSettings,
                    owner.id,
                    draft.id,
                    VideoMetadata("public", "", "public"),
                )
                for track, name in files:
                    await invoke(container, GetMedia, None, draft.id, track, name)
                async with resources.sessions.begin() as session:
                    row = await session.scalar(
                        select(PublicationRow).where(PublicationRow.id == draft.id)
                    )
                    row.moderation = "hidden"
                for track, name in files:
                    with pytest.raises(VideoNotFound):
                        await invoke(container, GetMedia, owner.id, draft.id, track, name)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "key",
    [
        "http://other/encoded/1/" + "a" * 32 + "/init.mp4",
        "encoded/1/" + "a" * 32 + "/../init.mp4",
        "encoded/1/" + "a" * 32 + "/init.mp4?x=1",
        "encoded/1/" + "a" * 32 + "/init.mp4\r\nLocation: http://other",
    ],
)
def test_storage_descriptor_rejects_injected_keys(key):
    with pytest.raises(ValueError):
        StoredMedia(key, 1, "video/mp4")
