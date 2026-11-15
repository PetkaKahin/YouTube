"""Fault injection uses real queue state, PostgreSQL locks and private RustFS objects."""

import asyncio
import itertools
import shutil
import subprocess
from contextlib import AsyncExitStack
from unittest.mock import patch

import pytest
from botocore.exceptions import EndpointConnectionError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from common.contracts.storage import ObjectBucket
from common.infrastructure.job_error_model import JobErrorRow
from video.application.encoding import EncodingJobs, PlanMedia
from video.application.processing import SourceJobs
from video.domain.processing import TemporarySourceError
from video.infrastructure.encoder import S3MediaEncoder
from video.infrastructure.media_resources import MediaResourceError
from video.infrastructure.models import (
    EncodedTrackRow,
    EncodingAttemptRow,
    MediaPlanRow,
    PlanTrackRow,
)
from video.infrastructure.source_inspector import S3SourceInspector
from video.public import CompleteUpload, GetVideoSettings
from video.tests import test_encoding as encoding_support
from video.tests.test_publications import invoke
from video.tests.test_source_processing import process, source_environment, stored

pytestmark = pytest.mark.usefixtures("video_media_cleanup")

_sample_sequence = itertools.count(1)


@pytest.fixture
def encoding_samples(tmp_path_factory, tmp_path):
    assets = encoding_support.encoding_samples.__wrapped__(tmp_path_factory)
    source = tmp_path / "fault-source.mp4"
    target = tmp_path / "retimed.mp4"
    source.write_bytes(assets["tiny"])
    subprocess.run(  # noqa: S603 -- fixed encoder and unique synthetic timing
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-itsscale",
            f"1.{next(_sample_sequence):02d}",
            "-i",
            str(source),
            "-c",
            "copy",
            str(target),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return {**assets, "tiny": target.read_bytes()}


async def encoded(resources, upload_id):
    async with resources.sessions() as session:
        return (
            await session.scalars(
                select(EncodedTrackRow).where(
                    EncodedTrackRow.source_upload_id == upload_id,
                    EncodedTrackRow.spec["kind"].astext == "video",
                )
            )
        ).one()


def test_transient_encode_error_retries_and_retains_history(
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
            output = await encoded(resources, upload.id)
            assert output.status == "pending" and output.error_code == "source_unavailable"
            async with resources.sessions.begin() as session:
                await session.execute(
                    text("UPDATE procrastinate_jobs SET scheduled_at=now() WHERE id=:id"),
                    {"id": output.job_id},
                )
            await process(container, resources, tmp_path, source_queue)
            assert (await encoded(resources, upload.id)).status == "ready"
            async with resources.sessions() as session:
                errors = (
                    await session.scalars(
                        select(JobErrorRow).where(JobErrorRow.job_id == output.job_id)
                    )
                ).all()
                assert (
                    len(errors) == 1
                    and errors[0].attempt == 1
                    and errors[0].error_code == "source_unavailable"
                )

    asyncio.run(exercise())


def test_resource_failure_is_terminal_and_does_not_change_visibility(
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
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            with patch.object(
                S3MediaEncoder, "_encode", side_effect=MediaResourceError("workspace_limit")
            ):
                await process(container, resources, tmp_path, source_queue)
            output = await encoded(resources, upload.id)
            assert output.status == "failed" and output.error_code == "workspace_limit"
            after = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert after.processing.value == "failed" and after.visibility == before.visibility
            assert after.updated_at == before.updated_at and after.added_at == before.added_at
            async with resources.sessions() as session:
                assert (
                    await session.scalar(
                        select(JobErrorRow.error_code).where(JobErrorRow.job_id == output.job_id)
                    )
                    == "workspace_limit"
                )
                assert (
                    await session.scalar(
                        text("SELECT status::text FROM procrastinate_jobs WHERE id=:id"),
                        {"id": output.job_id},
                    )
                    == "failed"
                )

    asyncio.run(exercise())


def test_partial_object_upload_is_removed_and_never_becomes_ready(
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
            original = resources.objects.put_object
            writes = 0

            def put(**kwargs):
                nonlocal writes
                if kwargs["Key"].endswith((".mp4", ".m4s", ".m3u8")):
                    writes += 1
                    if writes == 2:
                        raise EndpointConnectionError(endpoint_url="http://unavailable.invalid")
                return original(**kwargs)

            with patch.object(resources.objects, "put_object", side_effect=put):
                await process(container, resources, tmp_path, source_queue)
            output = await encoded(resources, upload.id)
            assert output.status == "pending" and output.artifact is None
            async with resources.sessions() as session:
                prefix = await session.scalar(
                    select(EncodingAttemptRow.prefix).where(
                        EncodingAttemptRow.encoded_id == output.id
                    )
                )
            page = await asyncio.to_thread(
                resources.objects.list_objects_v2, Bucket=resources.bucket, Prefix=prefix
            )
            assert page.get("KeyCount", 0) == 0

    asyncio.run(exercise())


def test_concurrent_plans_enqueue_one_shared_output(backend_runtime, encoding_samples):
    async def exercise():
        async with AsyncExitStack() as stack:
            contexts = [
                await stack.enter_async_context(
                    source_environment(backend_runtime, encoding_samples["tiny"])
                )
                for _ in range(2)
            ]
            for container, resources, owner, draft, upload in contexts:
                await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
                source = await stored(resources, upload.id)
                async with container() as operation:
                    session = await operation.get(AsyncSession)
                    async with session.begin():
                        await session.execute(
                            text("UPDATE procrastinate_jobs SET status='doing' WHERE id=:id"),
                            {"id": source.job_id},
                        )
                        claim = await (await operation.get(SourceJobs)).claim(
                            upload.id, source.job_id, 0
                        )
                result = await S3SourceInspector(
                    resources.objects, ObjectBucket(resources.bucket)
                ).inspect(claim)
                async with container() as operation:
                    session = await operation.get(AsyncSession)
                    async with session.begin():
                        await (await operation.get(SourceJobs)).finish(
                            upload.id, claim.token, result
                        )

            async def plan(context):
                container, resources, _, _, upload = context
                async with resources.sessions.begin() as session:
                    row = await session.get(MediaPlanRow, upload.id)
                    job_id = row.job_id
                    await session.execute(
                        text("UPDATE procrastinate_jobs SET status='doing' WHERE id=:id"),
                        {"id": job_id},
                    )
                async with container() as operation:
                    await (await operation.get(PlanMedia))(upload.id, job_id, attempt=0)

            await asyncio.wait_for(
                asyncio.gather(*(plan(context) for context in contexts)), timeout=15
            )
            resources = contexts[0][1]
            async with resources.sessions() as session:
                refs = (
                    await session.scalars(
                        select(PlanTrackRow).where(
                            PlanTrackRow.upload_id.in_([context[-1].id for context in contexts])
                        )
                    )
                ).all()
                assert len(refs) == 2 and refs[0].encoded_id == refs[1].encoded_id
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(EncodedTrackRow)
                        .where(EncodedTrackRow.id == refs[0].encoded_id)
                    )
                    == 1
                )

    asyncio.run(exercise())


def test_queue_recovery_fences_late_encoded_result(
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
            output = await encoded(resources, upload.id)
            async with container() as operation:
                session = await operation.get(AsyncSession)
                jobs = await operation.get(EncodingJobs)
                async with session.begin():
                    await session.execute(
                        text("UPDATE procrastinate_jobs SET status='doing' WHERE id=:id"),
                        {"id": output.job_id},
                    )
                    claim = await jobs.claim(output.id, output.job_id, 1)
                async with resources.sessions.begin() as recovering:
                    await recovering.execute(
                        text("UPDATE procrastinate_jobs SET status='todo',attempts=2 WHERE id=:id"),
                        {"id": output.job_id},
                    )
                async with session.begin():
                    assert not await jobs.finish(output.id, claim.token, {"prefix": claim.prefix})
                    await jobs.fail(output.id, claim.token, "invalid_media", terminal=True)
            after = await encoded(resources, upload.id)
            assert (
                after.status == "running" and after.token == claim.token and after.artifact is None
            )

    asyncio.run(exercise())
