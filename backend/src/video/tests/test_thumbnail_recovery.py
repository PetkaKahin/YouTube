"""Lost image preparation, stale tokens and orphan pixels use real queue generations."""

import asyncio
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from common.infrastructure.job_errors import DurableJobErrors, ErrorSpool, PostgresJobErrorStore
from video.application.thumbnails import ThumbnailJobs
from video.domain.processing import TemporarySourceError
from video.domain.thumbnails import PreparedThumbnail
from video.infrastructure.models import (
    ThumbnailPreparationRow,
    ThumbnailUploadRow,
)
from video.infrastructure.thumbnail_media import S3ThumbnailProcessor
from video.public import (
    CreateDraft,
    GetThumbnails,
    RepairThumbnails,
    VideoMetadata,
)
from video.tests.test_publications import account, invoke
from video.tests.test_source_processing import process
from video.tests.test_thumbnails import image_samples as sample_fixture
from video.tests.test_thumbnails import upload_image

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.fixture(scope="module")
def images(tmp_path_factory):
    return sample_fixture.__wrapped__(tmp_path_factory)


@pytest.mark.parametrize("attempt", [1, 2])
def test_lost_preparation_fences_late_result_and_recovers_budget(
    backend_runtime, images, tmp_path, source_queue, recover_media, attempt
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("recovery", "", "private")
            )
            upload_id = await upload_image(container, resources, owner.id, draft.id, images["png"])
            with patch.object(S3ThumbnailProcessor, "prepare", side_effect=TemporarySourceError()):
                await process(container, resources, tmp_path / "first", source_queue)
            async with container() as operation:
                session = await operation.get(AsyncSession)
                async with session.begin():
                    row = await session.get(ThumbnailUploadRow, upload_id)
                    job_id = row.job_id
                    worker = await session.scalar(
                        text(
                            "INSERT INTO procrastinate_workers(last_heartbeat) "
                            "VALUES(now()-interval '61 seconds') RETURNING id"
                        )
                    )
                    await session.execute(
                        text(
                            "UPDATE procrastinate_jobs SET status='doing',attempts=:attempt,"
                            "worker_id=:worker WHERE id=:id"
                        ),
                        {"id": job_id, "attempt": attempt, "worker": worker},
                    )
                    claim = await (await operation.get(ThumbnailJobs)).claim(
                        upload_id, job_id, attempt
                    )
            queue = source_queue(container)
            spool = ErrorSpool(tmp_path / "recovery")
            journal = DurableJobErrors(spool, PostgresJobErrorStore(resources.sessions))
            async with queue.open_async():
                await recover_media(queue, resources, journal, spool)
            async with container() as operation:
                session = await operation.get(AsyncSession)
                async with session.begin():
                    assert not await (await operation.get(ThumbnailJobs)).finish(
                        upload_id,
                        claim.token,
                        PreparedThumbnail("a" * 64, claim.prefix + "pixels.rgba", 4, 1, 1),
                    )
            await invoke(container, RepairThumbnails)
            if attempt == 1:
                async with resources.sessions.begin() as session:
                    await session.execute(
                        text("UPDATE procrastinate_jobs SET scheduled_at=now() WHERE id=:id"),
                        {"id": job_id},
                    )
                await process(container, resources, tmp_path / "retry", source_queue)
                assert (
                    await invoke(container, GetThumbnails, owner.id, draft.id)
                ).custom_status == "ready"
            else:
                assert (
                    await invoke(container, GetThumbnails, owner.id, draft.id)
                ).custom_status == "failed"

    asyncio.run(exercise())


def test_uncertain_pixel_upload_is_not_published_and_can_be_cleaned_and_retried(
    backend_runtime, images, tmp_path, source_queue
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("partial", "", "private")
            )
            upload_id = await upload_image(container, resources, owner.id, draft.id, images["bmp"])
            original_put = resources.objects.put_object

            def lost_response(**kwargs):
                result = original_put(**kwargs)
                if kwargs["Key"].startswith("thumbnail-pixels/"):
                    raise ClientError({"Error": {"Code": "ServiceUnavailable"}}, "PutObject")
                return result

            with patch.object(resources.objects, "put_object", side_effect=lost_response):
                await process(container, resources, tmp_path / "failed", source_queue)
            options = await invoke(container, GetThumbnails, owner.id, draft.id)
            assert options.current_url is None and options.custom_status == "uploaded"
            async with resources.sessions.begin() as session:
                row = await session.get(ThumbnailUploadRow, upload_id)
                key, job_id = row.object_key, row.job_id
                prefixes = (
                    await session.scalars(
                        select(ThumbnailPreparationRow.prefix).where(
                            ThumbnailPreparationRow.upload_id == upload_id
                        )
                    )
                ).all()
                await session.execute(
                    text(
                        "UPDATE video_thumbnail_preparations "
                        "SET expires_at=now()-interval '1 minute' WHERE upload_id=:id"
                    ),
                    {"id": upload_id},
                )
                await session.execute(
                    text("UPDATE procrastinate_jobs SET scheduled_at=now() WHERE id=:id"),
                    {"id": job_id},
                )
            await invoke(container, RepairThumbnails)
            for prefix in prefixes:
                page = await asyncio.to_thread(
                    resources.objects.list_objects_v2, Bucket=resources.bucket, Prefix=prefix
                )
                assert page.get("KeyCount", 0) == 0
            assert (
                await asyncio.to_thread(
                    resources.objects.head_object, Bucket=resources.bucket, Key=key
                )
            )["ContentLength"] == len(images["bmp"])
            await process(container, resources, tmp_path / "retry", source_queue)
            assert (
                await invoke(container, GetThumbnails, owner.id, draft.id)
            ).custom_status == "ready"

    asyncio.run(exercise())
