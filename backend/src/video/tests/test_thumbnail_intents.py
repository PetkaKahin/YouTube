"""Actual uploads and owner requests remain ordered across delayed confirmations."""

import asyncio
from unittest.mock import patch

import pytest
from sqlalchemy import select

from video.infrastructure.models import ThumbnailChoiceRow, ThumbnailUploadRow
from video.infrastructure.thumbnail_jobs import SqlThumbnailJobs
from video.public import (
    CompleteThumbnailUpload,
    CompleteUpload,
    CreateDraft,
    GetThumbnails,
    GetVideoSettings,
    RepairThumbnails,
    SelectThumbnail,
    StartThumbnailUpload,
    VideoMetadata,
)
from video.tests.test_publications import account, invoke
from video.tests.test_source_processing import process, small_video, source_environment
from video.tests.test_thumbnails import image_samples as sample_fixture
from video.tests.test_thumbnails import upload_image

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.fixture(scope="module")
def images(tmp_path_factory):
    return sample_fixture.__wrapped__(tmp_path_factory)


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return small_video.__wrapped__(tmp_path_factory)


def test_late_transfer_confirmation_cannot_replace_manual_frame(
    backend_runtime, images, video, tmp_path, source_queue
):
    async def exercise():
        async with source_environment(backend_runtime, video) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            await process(container, resources, tmp_path / "video", source_queue)

            async def manual_choice():
                await invoke(container, SelectThumbnail, owner.id, draft.id, 1)

            image_id = await upload_image(
                container,
                resources,
                owner.id,
                draft.id,
                images["png"],
                before_complete=manual_choice,
            )
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            await process(container, resources, tmp_path / "image", source_queue)
            await invoke(container, CompleteThumbnailUpload, owner.id, draft.id, image_id)
            options = await invoke(container, GetThumbnails, owner.id, draft.id)
            assert options.selected == "generated" and options.slot == 1
            assert (
                await invoke(container, GetVideoSettings, owner.id, draft.id)
            ).updated_at == before.updated_at

    asyncio.run(exercise())


def test_overlapping_transfers_apply_only_the_latest_owner_intent(
    backend_runtime, images, tmp_path, source_queue
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("overlap", "", "private")
            )
            ids = []
            for extension in ("png", "jpg"):
                data = images[extension]
                plan = await invoke(container, StartThumbnailUpload, owner.id, draft.id, len(data))
                ids.append(plan.id)
                async with resources.sessions() as session:
                    row = await session.get(ThumbnailUploadRow, plan.id)
                    key, multipart = row.object_key, row.multipart_id
                await asyncio.to_thread(
                    resources.objects.upload_part,
                    Bucket=resources.bucket,
                    Key=key,
                    UploadId=multipart,
                    PartNumber=1,
                    Body=data,
                )
            assert ids[0] != ids[1]
            await invoke(container, CompleteThumbnailUpload, owner.id, draft.id, ids[1])
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            await invoke(container, CompleteThumbnailUpload, owner.id, draft.id, ids[0])
            await process(container, resources, tmp_path, source_queue)
            assert (await invoke(container, GetThumbnails, owner.id, draft.id)).selected == "custom"
            async with resources.sessions() as session:
                assert (await session.get(ThumbnailChoiceRow, draft.id)).requested_upload_id == ids[
                    1
                ]
            assert (
                await invoke(container, GetVideoSettings, owner.id, draft.id)
            ).updated_at == before.updated_at

    asyncio.run(exercise())


@pytest.mark.parametrize("choose_frame", [False, True])
def test_recovered_receipt_does_not_select_or_modify_dates_until_owner_confirmation(
    backend_runtime, images, video, tmp_path, source_queue, choose_frame
):
    async def exercise():
        async with source_environment(backend_runtime, video) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            await process(container, resources, tmp_path / "video", source_queue)
            with patch.object(
                SqlThumbnailJobs,
                "confirm_selection",
                side_effect=RuntimeError("interrupted before selection"),
            ):
                with pytest.raises(RuntimeError):
                    await upload_image(container, resources, owner.id, draft.id, images["bmp"])
            if choose_frame:
                await invoke(container, SelectThumbnail, owner.id, draft.id, 2)
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            await process(container, resources, tmp_path / "image", source_queue)
            await invoke(container, RepairThumbnails)
            options = await invoke(container, GetThumbnails, owner.id, draft.id)
            assert options.selected == "generated"
            assert (
                await invoke(container, GetVideoSettings, owner.id, draft.id)
            ).updated_at == before.updated_at
            async with resources.sessions() as session:
                image_id = await session.scalar(
                    select(ThumbnailUploadRow.id).where(ThumbnailUploadRow.video_id == draft.id)
                )
            await invoke(container, CompleteThumbnailUpload, owner.id, draft.id, image_id)
            options = await invoke(container, GetThumbnails, owner.id, draft.id)
            assert options.selected == ("generated" if choose_frame else "custom")

    asyncio.run(exercise())
