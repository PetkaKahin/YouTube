"""Original cleanup uses durable intent, complete dependencies and exact private keys."""

import asyncio
import copy
from contextlib import asynccontextmanager
from unittest.mock import patch
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from sqlalchemy import delete, select, text

from common.infrastructure.job_error_model import JobErrorRow
from common.infrastructure.job_errors import DurableJobErrors, ErrorSpool, PostgresJobErrorStore
from common.infrastructure.job_monitor import error_history_middleware
from video.domain.encoding import PlannedTrack
from video.domain.originals import OriginalDeletion
from video.domain.processing import SourceInspectionError
from video.domain.uploads import StorageUnavailable
from video.infrastructure.encoding_jobs import ensure_output
from video.infrastructure.models import (
    EncodedTrackRow,
    MediaPlanRow,
    PublicationRow,
    SourceProcessingRow,
    SubtitleLinkRow,
    UploadRow,
)
from video.infrastructure.original_guard import require_original
from video.infrastructure.originals import SqlOriginalDeletions
from video.public import (
    CleanupOriginals,
    CompleteUpload,
    GetProcessing,
    GetVideoSettings,
    RefreshUpload,
)
from video.tests import test_source_processing as support
from video.tests.test_publications import invoke
from video.tests.test_source_processing import process, source_environment
from video.tests.test_subtitle_jobs import ffmpeg

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.fixture(scope="module")
def small_video(tmp_path_factory):
    return support.small_video.__wrapped__(tmp_path_factory)


@asynccontextmanager
async def ready(backend_runtime, data, tmp_path, source_queue):
    async with source_environment(backend_runtime, data) as context:
        container, resources, owner, draft, upload = context
        await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
        await process(container, resources, tmp_path, source_queue)
        assert (
            await invoke(container, GetProcessing, owner.id, draft.id)
        ).processing.value == "ready"
        yield context


async def stored(resources, upload_id):
    async with resources.sessions() as session:
        return await session.get(UploadRow, upload_id)


async def absent(resources, key):
    try:
        await asyncio.to_thread(resources.objects.head_object, Bucket=resources.bucket, Key=key)
    except ClientError as error:
        assert error.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}
    else:
        raise AssertionError("Temporary source still exists")


def test_ready_original_deleted_but_receipt_fingerprints_and_shared_packages_survive(
    backend_runtime, small_video, tmp_path, source_queue
):
    async def exercise():
        async with ready(backend_runtime, small_video, tmp_path, source_queue) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            async with resources.sessions.begin() as session:
                publication = await session.get(PublicationRow, draft.id)
                publication.moderation = "hidden"
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            source_before = await stored(resources, upload.id)
            assert await invoke(container, CleanupOriginals) >= 1
            row = await stored(resources, upload.id)
            assert row.original_cleanup_started_at and row.original_deleted_at
            await absent(resources, row.object_key)
            for operation in (CompleteUpload, RefreshUpload):
                with patch.object(
                    resources.objects,
                    "head_object",
                    side_effect=AssertionError("Deleted source must not be probed"),
                ):
                    receipt = await invoke(container, operation, owner.id, draft.id, upload.id)
                assert receipt.status == "uploaded" and receipt.parts == ()
            assert await invoke(container, CleanupOriginals) == 0
            assert (
                await stored(resources, upload.id)
            ).original_deleted_at == row.original_deleted_at
            after = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert before == after and after.moderation.value == "hidden"
            async with resources.sessions() as session:
                inspection = await session.get(SourceProcessingRow, upload.id)
                assert inspection.content_id is not None and inspection.inspection["fingerprint"]
                artifacts = (
                    await session.scalars(
                        select(EncodedTrackRow.artifact).where(
                            EncodedTrackRow.source_upload_id == upload.id
                        )
                    )
                ).all()
            for artifact in artifacts:
                for item in artifact["files"]:
                    result = await asyncio.to_thread(
                        resources.objects.head_object,
                        Bucket=resources.bucket,
                        Key=artifact["prefix"] + item["name"],
                    )
                    assert result["ContentLength"] == item["size"]
            assert source_before.status == row.status == "uploaded"
            # A later account reuses the outputs after the first source is gone.
            async with ready(
                backend_runtime, small_video, tmp_path / "duplicate", source_queue
            ) as (other_container, _, other, other_draft, other_upload):
                assert (
                    await invoke(other_container, GetProcessing, other.id, other_draft.id)
                ).available_qualities
                assert await invoke(other_container, CleanupOriginals) == 1
                await absent(resources, (await stored(resources, other_upload.id)).object_key)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "blocker",
    [
        "pending",
        "running",
        "failed",
        "bad_manifest",
        "bad_thumbnail",
        "missing_subtitle",
        "unsupported_subtitle",
        "native_job",
        "old_inspection",
    ],
)
def test_unfinished_failed_or_unverified_dependencies_keep_original(
    backend_runtime, small_video, tmp_path, source_queue, blocker
):
    async def exercise():
        async with ready(backend_runtime, small_video, tmp_path, source_queue) as (
            container,
            resources,
            _,
            _,
            upload,
        ):
            async with resources.sessions() as session:
                plan = await session.get(MediaPlanRow, upload.id)
                output_id = (
                    plan.config["thumbnail"]
                    if blocker == "bad_thumbnail"
                    else plan.config["variants"][0]["video"]
                )
                output = await session.get(EncodedTrackRow, output_id)
                backup = {
                    key: copy.deepcopy(getattr(output, key))
                    for key in ("status", "token", "artifact")
                }
                source = await session.get(SourceProcessingRow, upload.id)
                inspection = copy.deepcopy(source.inspection)
                job_id = output.job_id
            try:
                async with resources.sessions.begin() as session:
                    row = await session.get(EncodedTrackRow, output_id)
                    if blocker in {"pending", "running", "failed"}:
                        row.status, row.artifact = blocker, None
                        row.token = uuid4() if blocker == "running" else None
                    elif blocker in {"bad_manifest", "bad_thumbnail"}:
                        row.artifact = {**row.artifact, "files": []}
                    elif blocker == "native_job":
                        await session.execute(
                            text("UPDATE procrastinate_jobs SET status='todo' WHERE id=:id"),
                            {"id": job_id},
                        )
                    else:
                        source = await session.get(SourceProcessingRow, upload.id)
                        data = copy.deepcopy(source.inspection)
                        if blocker == "old_inspection":
                            data["version"] = 2
                        else:
                            data["tracks"].append(
                                {"stream_index": 42, "properties": {"codec_type": "subtitle"}}
                            )
                            if blocker == "unsupported_subtitle":
                                session.add(
                                    SubtitleLinkRow(
                                        upload_id=upload.id,
                                        stream_index=42,
                                        video_encoded_id=output_id,
                                        status="failed",
                                        language="und",
                                        disposition={},
                                        error_code="unsupported_subtitle",
                                    )
                                )
                        source.inspection = data
                await invoke(container, CleanupOriginals)
                row = await stored(resources, upload.id)
                assert row.original_cleanup_started_at is None and row.original_deleted_at is None
                result = await asyncio.to_thread(
                    resources.objects.head_object, Bucket=resources.bucket, Key=row.object_key
                )
                assert result["ContentLength"] == len(small_video)
            finally:
                async with resources.sessions.begin() as session:
                    row = await session.get(EncodedTrackRow, output_id)
                    for key, value in backup.items():
                        setattr(row, key, value)
                    (await session.get(SourceProcessingRow, upload.id)).inspection = inspection
                    await session.execute(
                        delete(SubtitleLinkRow).where(
                            SubtitleLinkRow.upload_id == upload.id,
                            SubtitleLinkRow.stream_index == 42,
                        )
                    )
                    await session.execute(
                        text("UPDATE procrastinate_jobs SET status='succeeded' WHERE id=:id"),
                        {"id": job_id},
                    )

    asyncio.run(exercise())


def test_foreign_shared_work_using_this_source_prevents_deletion(
    backend_runtime, small_video, tmp_path, source_queue
):
    async def exercise():
        async with ready(backend_runtime, small_video, tmp_path, source_queue) as (
            container,
            resources,
            _,
            _,
            upload,
        ):
            async with resources.sessions.begin() as session:
                plan = await session.get(MediaPlanRow, upload.id)
                track = await session.get(EncodedTrackRow, plan.config["variants"][0]["video"])
                other_spec = {**track.spec, "crf": 33}
                output_id = await ensure_output(
                    session,
                    PlannedTrack(0, "f" * 64, other_spec, 0),
                    track.track_id,
                    upload_id=upload.id,
                )
            try:
                await invoke(container, CleanupOriginals)
                assert (await stored(resources, upload.id)).original_cleanup_started_at is None
            finally:
                async with resources.sessions.begin() as session:
                    await session.execute(
                        delete(EncodedTrackRow).where(EncodedTrackRow.id == output_id)
                    )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "phase", ["intent_rollback", "storage", "lost_delete_response", "finish_rollback"]
)
def test_uncertain_deletion_recovers_from_durable_intent(
    backend_runtime, small_video, tmp_path, source_queue, phase
):
    async def exercise():
        async with ready(backend_runtime, small_video, tmp_path, source_queue) as (
            container,
            resources,
            _,
            _,
            upload,
        ):
            key = (await stored(resources, upload.id)).object_key
            if phase == "intent_rollback":
                async with resources.sessions() as session:
                    async with session.begin():
                        assert await SqlOriginalDeletions(session).prepare(upload.id)
                        await session.rollback()
                assert (await stored(resources, upload.id)).original_cleanup_started_at is None
                assert resources.objects.head_object(Bucket=resources.bucket, Key=key)[
                    "ContentLength"
                ]
            elif phase in {"storage", "lost_delete_response"}:
                original = resources.objects.delete_object

                def fail(**kwargs):
                    if kwargs["Key"] == key:
                        if phase == "lost_delete_response":
                            original(**kwargs)
                        raise EndpointConnectionError(endpoint_url="http://synthetic.invalid")
                    return original(**kwargs)

                with patch.object(resources.objects, "delete_object", side_effect=fail):
                    with pytest.raises(StorageUnavailable):
                        await invoke(container, CleanupOriginals)
                row = await stored(resources, upload.id)
                assert row.original_cleanup_started_at and row.original_deleted_at is None
                if phase == "lost_delete_response":
                    await absent(resources, key)
            else:
                finish = SqlOriginalDeletions.finish

                async def fail_after_update(repository, upload_id):
                    await finish(repository, upload_id)
                    raise RuntimeError("synthetic lost commit")

                with patch.object(SqlOriginalDeletions, "finish", fail_after_update):
                    with pytest.raises(RuntimeError, match="synthetic"):
                        await invoke(container, CleanupOriginals)
                assert (await stored(resources, upload.id)).original_deleted_at is None
                await absent(resources, key)
            await invoke(container, CleanupOriginals)
            assert (await stored(resources, upload.id)).original_deleted_at
            await absent(resources, key)

    asyncio.run(exercise())


def test_admission_lock_and_durable_release_block_late_original_work(
    backend_runtime, small_video, tmp_path, source_queue
):
    async def exercise():
        async with ready(backend_runtime, small_video, tmp_path, source_queue) as (
            container,
            resources,
            _,
            _,
            upload,
        ):
            async with resources.sessions.begin() as admission:
                await require_original(admission, upload.id)
                async with resources.sessions.begin() as cleanup:
                    assert await SqlOriginalDeletions(cleanup).prepare(upload.id) is None
            async with resources.sessions.begin() as cleanup:
                assert await SqlOriginalDeletions(cleanup).prepare(upload.id)
            async with resources.sessions.begin() as admission:
                with pytest.raises(SourceInspectionError, match="source_not_uploaded"):
                    await require_original(admission, upload.id)
            await invoke(container, CleanupOriginals)

    asyncio.run(exercise())


def test_empty_subtitle_is_successful_for_cleanup(backend_runtime, tmp_path, source_queue):
    captions = tmp_path / "outside.srt"
    captions.write_text("1\n00:00:20,000 --> 00:00:21,000\noutside\n")
    video = tmp_path / "empty.mkv"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=size=32x48:rate=1:duration=1",
        "-i",
        captions,
        "-c:v",
        "mpeg4",
        "-c:s",
        "srt",
        video,
    )

    async def exercise():
        async with ready(backend_runtime, video.read_bytes(), tmp_path, source_queue) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            assert {
                item.status
                for item in (await invoke(container, GetProcessing, owner.id, draft.id)).subtitles
            } == {"empty"}
            await invoke(container, CleanupOriginals)
            assert (await stored(resources, upload.id)).original_deleted_at

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "key",
    [
        "encoded/1/" + "a" * 32 + "/",
        "incoming/../../private",
        "https://internal/",
        "incoming/" + "a" * 32 + "\n",
    ],
)
def test_original_key_rejects_paths_and_other_media(key):
    with pytest.raises(ValueError):
        OriginalDeletion(1, key)


def test_native_cleanup_job_deduplicates_and_journals_storage_failure(
    backend_runtime, small_video, tmp_path, source_queue, original_cleanup_queue, enqueue_originals
):
    async def exercise():
        async with ready(backend_runtime, small_video, tmp_path, source_queue) as (
            container,
            resources,
            _,
            _,
            upload,
        ):
            queue = original_cleanup_queue(container)
            async with queue.open_async():
                await enqueue_originals(queue)
                await enqueue_originals(queue)
                async with resources.sessions() as session:
                    jobs = (
                        await session.execute(
                            text(
                                "SELECT id FROM procrastinate_jobs "
                                "WHERE task_name='video.cleanup_originals' AND status='todo'"
                            )
                        )
                    ).all()
                assert len(jobs) == 1
                job_id = jobs[0][0]
                journal = DurableJobErrors(
                    ErrorSpool(tmp_path / "journal"), PostgresJobErrorStore(resources.sessions)
                )
                with patch.object(
                    resources.objects,
                    "delete_object",
                    side_effect=EndpointConnectionError(endpoint_url="http://synthetic.invalid"),
                ):
                    await queue.run_worker_async(
                        queues=["maintenance"],
                        concurrency=1,
                        wait=False,
                        listen_notify=False,
                        install_signal_handlers=False,
                        worker_middleware=[error_history_middleware(journal)],
                    )
                async with resources.sessions() as session:
                    assert (
                        await session.scalar(
                            select(JobErrorRow.job_id).where(JobErrorRow.job_id == job_id)
                        )
                        == job_id
                    )
                assert (await stored(resources, upload.id)).original_cleanup_started_at
                assert (await stored(resources, upload.id)).original_deleted_at is None
            await invoke(container, CleanupOriginals)

    asyncio.run(exercise())
