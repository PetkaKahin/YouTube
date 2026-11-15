"""Lost-worker recovery and a concurrent queue-generation fence for encoded media."""

import asyncio
from unittest.mock import patch
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from common.infrastructure.job_error_model import JobErrorRow
from common.infrastructure.job_errors import DurableJobErrors, ErrorSpool, PostgresJobErrorStore
from video.application.encoding import EncodingJobs, RepairEncoding
from video.domain.processing import TemporarySourceError
from video.infrastructure.encoder import S3MediaEncoder
from video.infrastructure.models import EncodingAttemptRow, UploadRow
from video.public import CompleteUpload, GetVideoSettings
from video.tests import test_encoding_reliability as support
from video.tests.test_publications import invoke
from video.tests.test_source_processing import process, source_environment

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.fixture
def encoding_samples(tmp_path_factory, tmp_path):
    return support.encoding_samples.__wrapped__(tmp_path_factory, tmp_path)


@pytest.mark.parametrize("attempt", [1, 2])
def test_lost_encoder_worker_recovers_or_finishes_budget(
    backend_runtime, encoding_samples, tmp_path, source_queue, recover_media, attempt
):
    async def exercise():
        async with source_environment(backend_runtime, encoding_samples["tiny"]) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            with patch.object(S3MediaEncoder, "encode", side_effect=TemporarySourceError()):
                await process(container, resources, tmp_path / "first", source_queue)
            output = await support.encoded(resources, upload.id)
            async with container() as operation:
                session = await operation.get(AsyncSession)
                async with session.begin():
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
                        {"id": output.job_id, "attempt": attempt, "worker": worker},
                    )
                    await (await operation.get(EncodingJobs)).claim(
                        output.id, output.job_id, attempt
                    )
            queue = source_queue(container)
            spool = ErrorSpool(tmp_path / "recovery")
            journal = DurableJobErrors(spool, PostgresJobErrorStore(resources.sessions))
            async with queue.open_async():
                await recover_media(queue, resources, journal, spool)
            await invoke(container, RepairEncoding)
            if attempt == 1:
                async with resources.sessions.begin() as session:
                    await session.execute(
                        text("UPDATE procrastinate_jobs SET scheduled_at=now() WHERE id=:id"),
                        {"id": output.job_id},
                    )
                await process(container, resources, tmp_path / "again", source_queue)
                assert (await support.encoded(resources, upload.id)).status == "ready"
            else:
                after = await support.encoded(resources, upload.id)
                assert after.status == "failed" and after.token is None and after.artifact is None
                assert (
                    await invoke(container, GetVideoSettings, owner.id, draft.id)
                ).processing.value == "failed"
            async with resources.sessions() as session:
                assert (
                    await session.scalar(
                        select(JobErrorRow.attempt).where(
                            JobErrorRow.job_id == output.job_id, JobErrorRow.reason == "worker_lost"
                        )
                    )
                    == attempt + 1
                )

    asyncio.run(exercise())


def test_recovery_in_flight_blocks_then_rejects_late_output(
    backend_runtime, encoding_samples, tmp_path, source_queue
):
    async def exercise():
        async with source_environment(backend_runtime, encoding_samples["tiny"]) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            with patch.object(S3MediaEncoder, "encode", side_effect=TemporarySourceError()):
                await process(container, resources, tmp_path, source_queue)
            output = await support.encoded(resources, upload.id)
            async with container() as operation:
                session = await operation.get(AsyncSession)
                async with session.begin():
                    await session.execute(
                        text("UPDATE procrastinate_jobs SET status='doing' WHERE id=:id"),
                        {"id": output.job_id},
                    )
                    claim = await (await operation.get(EncodingJobs)).claim(
                        output.id, output.job_id, 1
                    )
            started = asyncio.Event()
            worker_pid = None

            async def finish():
                nonlocal worker_pid
                async with container() as operation:
                    session = await operation.get(AsyncSession)
                    async with session.begin():
                        await session.execute(text("SET LOCAL statement_timeout='5s'"))
                        worker_pid = await session.scalar(text("SELECT pg_backend_pid()"))
                        started.set()
                        return await (await operation.get(EncodingJobs)).finish(
                            output.id, claim.token, {"prefix": claim.prefix}
                        )

            async with resources.sessions.begin() as recovering:
                await recovering.execute(
                    text("UPDATE procrastinate_jobs SET status='todo',attempts=2 WHERE id=:id"),
                    {"id": output.job_id},
                )
                task = asyncio.create_task(finish())
                try:
                    await started.wait()
                    async with resources.sessions() as observer:
                        for _ in range(100):
                            blocked = await observer.scalar(
                                text("SELECT cardinality(pg_blocking_pids(:pid))>0"),
                                {"pid": worker_pid},
                            )
                            if blocked or task.done():
                                break
                            await asyncio.sleep(0.01)
                    assert blocked and not task.done()
                except BaseException:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise
            assert not await asyncio.wait_for(task, timeout=5)
            assert (await support.encoded(resources, upload.id)).artifact is None

    asyncio.run(exercise())


def test_expired_orphan_cleanup_keeps_committed_media_and_original(
    backend_runtime, encoding_samples, tmp_path, source_queue, monkeypatch
):
    monkeypatch.setenv("MEDIA_ATTEMPT_SECONDS", "3600")

    async def exercise():
        async with source_environment(backend_runtime, encoding_samples["tiny"]) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            with patch.object(S3MediaEncoder, "encode", side_effect=TemporarySourceError()):
                await process(container, resources, tmp_path / "first", source_queue)
            output = await support.encoded(resources, upload.id)
            async with resources.sessions.begin() as session:
                obsolete = (
                    await session.scalars(
                        select(EncodingAttemptRow).where(EncodingAttemptRow.encoded_id == output.id)
                    )
                ).one()
                prefix, token = obsolete.prefix, obsolete.token
                assert (obsolete.expires_at - obsolete.created_at).total_seconds() == 3660
                await session.execute(
                    text("UPDATE procrastinate_jobs SET scheduled_at=now() WHERE id=:id"),
                    {"id": output.job_id},
                )
            await process(container, resources, tmp_path / "again", source_queue)
            ready = await support.encoded(resources, upload.id)
            assert ready.status == "ready"
            await asyncio.to_thread(
                resources.objects.put_object,
                Bucket=resources.bucket,
                Key=prefix + "orphan.bin",
                Body=b"partial",
            )
            monkeypatch.setenv("MEDIA_ATTEMPT_SECONDS", "1")
            await invoke(container, RepairEncoding)
            assert (
                await asyncio.to_thread(
                    resources.objects.head_object,
                    Bucket=resources.bucket,
                    Key=prefix + "orphan.bin",
                )
            )["ContentLength"] == 7
            async with resources.sessions.begin() as session:
                # A legacy/ambiguous successful-attempt record must still protect its package.
                session.add(
                    EncodingAttemptRow(
                        token=UUID(ready.artifact["prefix"].split("/")[-2]),
                        encoded_id=ready.id,
                        prefix=ready.artifact["prefix"],
                    )
                )
                await session.flush()
                await session.execute(
                    text(
                        "UPDATE video_encoding_attempts SET expires_at=now()-interval '1 minute' "
                        "WHERE encoded_id=:id"
                    ),
                    {"id": ready.id},
                )
            await invoke(container, RepairEncoding)
            page = await asyncio.to_thread(
                resources.objects.list_objects_v2, Bucket=resources.bucket, Prefix=prefix
            )
            assert page.get("KeyCount", 0) == 0
            async with resources.sessions() as session:
                assert await session.get(EncodingAttemptRow, token) is None
                original = await session.get(UploadRow, upload.id)
                info = await asyncio.to_thread(
                    resources.objects.head_object, Bucket=resources.bucket, Key=original.object_key
                )
                assert info["ContentLength"] == len(encoding_samples["tiny"])
            for item in ready.artifact["files"]:
                info = await asyncio.to_thread(
                    resources.objects.head_object,
                    Bucket=resources.bucket,
                    Key=ready.artifact["prefix"] + item["name"],
                )
                assert info["ContentLength"] == item["size"]

    asyncio.run(exercise())
