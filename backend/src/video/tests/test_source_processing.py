"""Real source admission, SQL rollback/concurrency, RustFS and Procrastinate execution."""

import asyncio
import json
import shutil
import subprocess
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest.mock import patch

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from common.infrastructure.job_error_model import JobErrorRow
from common.infrastructure.job_errors import DurableJobErrors, ErrorSpool, PostgresJobErrorStore
from common.infrastructure.job_monitor import error_history_middleware
from video.application.processing import InspectSource, RepairSourceProcessing, SourceJobs
from video.application.uploads import Uploads
from video.domain.processing import SourceInspection, SourceInspectionError, TemporarySourceError
from video.domain.publications import VideoMetadata
from video.infrastructure.models import (
    SourceProcessingRow,
    UploadRow,
)
from video.infrastructure.source_inspector import S3SourceInspector
from video.public import MEDIA_QUEUE, CompleteUpload, CreateDraft, GetVideoSettings, StartUpload
from video.tests.test_publications import account, invoke

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.fixture(scope="module")
def small_video(tmp_path_factory):
    directory = tmp_path_factory.mktemp("source-media")
    output = directory / "sample.mp4"
    subprocess.run(  # noqa: S603 -- fixed FFmpeg and generated local fixture
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=24:duration=0.5",
            "-c:v",
            "mpeg4",
            "-metadata",
            "title=private-title-must-not-be-stored",
            str(output),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    return output.read_bytes()


@asynccontextmanager
async def source_environment(backend_runtime, data):
    async with backend_runtime() as (container, resources):
        owner = await account(container)
        draft = await invoke(
            container, CreateDraft, owner.id, VideoMetadata("private", "", "private")
        )
        plan = await invoke(container, StartUpload, owner.id, draft.id, len(data))
        async with resources.sessions() as session:
            upload = (await session.scalars(select(UploadRow).where(UploadRow.id == plan.id))).one()
            key, multipart = upload.object_key, upload.multipart_id
        await asyncio.to_thread(
            resources.objects.abort_multipart_upload,
            Bucket=resources.bucket,
            Key=key,
            UploadId=multipart,
        )
        await asyncio.to_thread(
            resources.objects.put_object, Bucket=resources.bucket, Key=key, Body=data
        )
        yield container, resources, owner, draft, plan


async def stored(resources, upload_id):
    async with resources.sessions() as session:
        return await session.get(SourceProcessingRow, upload_id)


async def process(container, resources, tmp_path, source_queue):
    queue = source_queue(container)
    journal = DurableJobErrors(ErrorSpool(tmp_path), PostgresJobErrorStore(resources.sessions))
    async with queue.open_async():
        await queue.run_worker_async(
            queues=[MEDIA_QUEUE],
            concurrency=1,
            wait=False,
            listen_notify=False,
            install_signal_handlers=False,
            worker_middleware=[error_history_middleware(journal)],
        )


def test_authoritative_completion_and_concurrent_repeats_create_one_durable_job(
    backend_runtime, small_video
):
    async def exercise():
        async with source_environment(backend_runtime, small_video) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await asyncio.gather(
                *[invoke(container, CompleteUpload, owner.id, draft.id, plan.id) for _ in range(3)]
            )
            source = await stored(resources, plan.id)
            assert source is not None and source.status == "pending"
            async with resources.sessions() as session:
                jobs = (
                    await session.execute(
                        text("SELECT id,args FROM procrastinate_jobs WHERE lock=:lock"),
                        {"lock": f"video.source:{plan.id}"},
                    )
                ).all()
            assert jobs == [(source.job_id, {"upload_id": plan.id})]
            assert (
                await invoke(container, GetVideoSettings, owner.id, draft.id)
            ).processing.value == "pending"

    asyncio.run(exercise())


def test_queue_failure_rolls_back_completion_but_refresh_recovers_existing_object(
    backend_runtime, small_video
):
    async def exercise():
        async with source_environment(backend_runtime, small_video) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):

            def fail(connection, cursor, statement, parameters, context, many):
                if "procrastinate_defer_jobs_v1" in statement:
                    raise RuntimeError("synthetic queue write failure")

            event.listen(resources.engine.sync_engine, "before_cursor_execute", fail)
            try:
                with pytest.raises(RuntimeError):
                    await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            finally:
                event.remove(resources.engine.sync_engine, "before_cursor_execute", fail)
            assert await stored(resources, plan.id) is None
            assert (
                await invoke(container, GetVideoSettings, owner.id, draft.id)
            ).upload.value == "uploading"
            await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            assert (await stored(resources, plan.id)).status == "pending"

    asyncio.run(exercise())


def test_inspection_runs_from_rustfs_and_does_not_publish_or_delete_original(
    backend_runtime, small_video, tmp_path, source_queue
):
    async def exercise():
        async with source_environment(backend_runtime, small_video) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            await process(container, resources, tmp_path, source_queue)
            source = await stored(resources, plan.id)
            assert source.status == "inspected" and source.token is None
            assert {
                key: source.inspection[key]
                for key in ("version", "codec", "width", "height", "duration_num", "duration_den")
            } == {
                "version": 3,
                "codec": "mpeg4",
                "width": 64,
                "height": 64,
                "duration_num": 1,
                "duration_den": 2,
            }
            assert source.content_id is not None and len(source.inspection["tracks"]) == 1
            assert "private-title" not in json.dumps(source.inspection)
            after = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert after.processing.value == "ready" and after.publication == before.publication
            assert after.visibility == before.visibility and after.moderation == before.moderation
            assert after.updated_at == before.updated_at and after.added_at == before.added_at
            async with container() as operation:
                with patch.object(
                    S3SourceInspector, "inspect", side_effect=AssertionError("must not re-inspect")
                ):
                    await (await operation.get(InspectSource))(
                        plan.id, source.job_id, attempt=0, last_attempt=False
                    )
            async with resources.sessions() as session:
                key = (await session.get(UploadRow, plan.id)).object_key
            content = await asyncio.to_thread(
                resources.objects.get_object, Bucket=resources.bucket, Key=key
            )
            assert content["Body"].read() == small_video
            content["Body"].close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "data,code", [(b"not media at all", "invalid_media"), (b"RIFFbad", "invalid_media")]
)
def test_bad_input_records_failure_without_retries_or_losing_upload(
    backend_runtime, tmp_path, data, code, source_queue
):
    async def exercise():
        async with source_environment(backend_runtime, data) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            await process(container, resources, tmp_path, source_queue)
            source = await stored(resources, plan.id)
            assert source.status == "failed" and source.error_code == code
            async with resources.sessions() as session:
                assert (
                    await session.scalar(
                        select(JobErrorRow.error_code).where(JobErrorRow.job_id == source.job_id)
                    )
                    == code
                )
                assert (
                    await session.scalar(
                        select(JobErrorRow.job_id).where(JobErrorRow.job_id == source.job_id)
                    )
                    == source.job_id
                )
                assert (
                    await session.scalar(
                        text("SELECT status::text FROM procrastinate_jobs WHERE id=:id"),
                        {"id": source.job_id},
                    )
                    == "failed"
                )
            settings = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert settings.processing.value == "failed" and settings.upload.value == "uploaded"

    asyncio.run(exercise())


def test_transient_source_failure_retries_and_retains_every_attempt(
    backend_runtime, small_video, tmp_path, source_queue
):
    async def exercise():
        async with source_environment(backend_runtime, small_video) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            with patch.object(S3SourceInspector, "inspect", side_effect=TemporarySourceError()):
                await process(container, resources, tmp_path, source_queue)
            source = await stored(resources, plan.id)
            assert source.status == "pending" and source.error_code == "source_unavailable"
            async with resources.sessions.begin() as session:
                await session.execute(
                    text("UPDATE procrastinate_jobs SET scheduled_at=now() WHERE id=:id"),
                    {"id": source.job_id},
                )
            await process(container, resources, tmp_path, source_queue)
            assert (await stored(resources, plan.id)).status == "inspected"
            async with resources.sessions() as session:
                assert (
                    await session.scalar(
                        select(JobErrorRow.attempt).where(JobErrorRow.job_id == source.job_id)
                    )
                    == 1
                )

    asyncio.run(exercise())


def test_legacy_confirmation_is_repaired_once_and_unconfirmed_upload_is_ignored(
    backend_runtime, small_video
):
    async def exercise():
        async with source_environment(backend_runtime, small_video) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await invoke(container, RepairSourceProcessing)
            assert await stored(resources, plan.id) is None
            async with container() as operation:
                session = await operation.get(AsyncSession)
                async with session.begin():
                    uploads = await operation.get(Uploads)
                    transfer = await uploads.get(draft.id, plan.id)
                    from video.domain.uploads import StoredObject

                    await uploads.complete(
                        transfer, StoredObject(len(small_video), datetime.now(UTC))
                    )
            await asyncio.gather(
                invoke(container, RepairSourceProcessing), invoke(container, RepairSourceProcessing)
            )
            assert (await stored(resources, plan.id)).status == "pending"
            assert await invoke(container, RepairSourceProcessing) == 0

    asyncio.run(exercise())


def test_stale_run_token_cannot_overwrite_reclaimed_source(backend_runtime, small_video):
    async def exercise():
        async with source_environment(backend_runtime, small_video) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            source = await stored(resources, plan.id)
            async with container() as operation:
                session, jobs = await operation.get(AsyncSession), await operation.get(SourceJobs)
                async with session.begin():
                    await session.execute(
                        text("UPDATE procrastinate_jobs SET status='doing' WHERE id=:id"),
                        {"id": source.job_id},
                    )
                    first = await jobs.claim(plan.id, source.job_id, 0)
                async with session.begin():
                    second = await jobs.claim(plan.id, source.job_id, 0)
                async with session.begin():
                    await jobs.finish(
                        plan.id, first.token, SourceInspection("bad", 1, 1, Fraction(1))
                    )
                    await jobs.fail(plan.id, first.token, "invalid_media", terminal=True)
                assert (await stored(resources, plan.id)).token == second.token
                async with session.begin():
                    await jobs.finish(
                        plan.id, second.token, SourceInspection("mpeg4", 64, 64, Fraction(1, 2))
                    )
            assert (await stored(resources, plan.id)).inspection["width"] == 64

    asyncio.run(exercise())


@pytest.mark.parametrize("attempts", [0, 2])
def test_lost_media_worker_is_recovered_or_marked_failed(
    backend_runtime, small_video, tmp_path, attempts, source_queue, recover_media
):
    async def exercise():
        async with source_environment(backend_runtime, small_video) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            source = await stored(resources, plan.id)
            queue = source_queue(container)
            spool = ErrorSpool(tmp_path)
            journal = DurableJobErrors(spool, PostgresJobErrorStore(resources.sessions))
            async with queue.open_async():
                async with resources.sessions.begin() as session:
                    worker = await session.scalar(
                        text(
                            "INSERT INTO procrastinate_workers(last_heartbeat) "
                            "VALUES (now()-interval '61 seconds') RETURNING id"
                        )
                    )
                    await session.execute(
                        text(
                            "UPDATE procrastinate_jobs SET status='doing',worker_id=:worker,"
                            "attempts=:attempts WHERE id=:id"
                        ),
                        {"worker": worker, "attempts": attempts, "id": source.job_id},
                    )
                await recover_media(queue, resources, journal, spool)
            await invoke(container, RepairSourceProcessing)
            async with resources.sessions() as session:
                assert (
                    await session.scalar(
                        select(JobErrorRow.reason).where(JobErrorRow.job_id == source.job_id)
                    )
                    == "worker_lost"
                )
                status = await session.scalar(
                    text("SELECT status::text FROM procrastinate_jobs WHERE id=:id"),
                    {"id": source.job_id},
                )
            assert status == ("todo" if attempts == 0 else "failed")
            assert (await stored(resources, plan.id)).status == (
                "pending" if attempts == 0 else "failed"
            )

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["local", "http"])
def test_uploaded_playlist_cannot_open_other_local_files_or_request_urls(
    backend_runtime, small_video, tmp_path, source_queue, kind
):
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(small_video)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    target = tmp_path / "other.mp4"
    target.write_bytes(small_video)
    reference = str(target) if kind == "local" else f"http://127.0.0.1:{server.server_port}/video"
    data = f"ffconcat version 1.0\nfile '{reference}'\n".encode()

    async def exercise():
        async with source_environment(backend_runtime, data) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            await process(container, resources, tmp_path / "history", source_queue)
            result = await stored(resources, plan.id)
            assert result.status == "failed" and result.error_code == "invalid_media"
            assert hits == [] and target.read_bytes() == small_video

    try:
        asyncio.run(exercise())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_audio_only_file_is_not_accepted_as_video(backend_runtime, tmp_path, source_queue):
    audio = tmp_path / "audio.wav"
    subprocess.run(  # noqa: S603 -- fixed local synthetic fixture command
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=0.5",
            str(audio),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )

    async def exercise():
        async with source_environment(backend_runtime, audio.read_bytes()) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            await process(container, resources, tmp_path / "history", source_queue)
            result = await stored(resources, plan.id)
            assert result.status == "failed" and result.error_code == "no_video"

    asyncio.run(exercise())


def test_resource_failure_is_journaled_and_not_automatically_retried(
    backend_runtime, small_video, tmp_path, source_queue
):
    async def exercise():
        async with source_environment(backend_runtime, small_video) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            with patch.object(
                S3SourceInspector, "inspect", side_effect=SourceInspectionError("workspace_limit")
            ):
                await process(container, resources, tmp_path, source_queue)
            result = await stored(resources, plan.id)
            assert result.status == "failed"
            async with resources.sessions() as session:
                assert (
                    await session.scalar(
                        select(JobErrorRow.error_code).where(JobErrorRow.job_id == result.job_id)
                    )
                    == "workspace_limit"
                )
                assert (
                    await session.scalar(
                        text("SELECT attempts FROM procrastinate_jobs WHERE id=:id"),
                        {"id": result.job_id},
                    )
                    == 1
                )

    asyncio.run(exercise())


def test_object_size_changed_after_completion_fails_without_publishing(
    backend_runtime, small_video, tmp_path, source_queue
):
    async def exercise():
        async with source_environment(backend_runtime, small_video) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            async with resources.sessions() as session:
                key = (await session.get(UploadRow, plan.id)).object_key
            await asyncio.to_thread(
                resources.objects.put_object, Bucket=resources.bucket, Key=key, Body=b"changed"
            )
            await process(container, resources, tmp_path, source_queue)
            result = await stored(resources, plan.id)
            assert result.status == "failed" and result.error_code == "source_size_changed"

    asyncio.run(exercise())


def test_late_result_is_fenced_after_queue_recovery_even_before_next_claim(
    backend_runtime, small_video
):
    async def exercise():
        async with source_environment(backend_runtime, small_video) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            row = await stored(resources, plan.id)
            async with container() as operation:
                session, jobs = await operation.get(AsyncSession), await operation.get(SourceJobs)
                async with session.begin():
                    await session.execute(
                        text("UPDATE procrastinate_jobs SET status='doing' WHERE id=:id"),
                        {"id": row.job_id},
                    )
                    old = await jobs.claim(plan.id, row.job_id, 0)
                async with session.begin():
                    await session.execute(
                        text("UPDATE procrastinate_jobs SET status='todo',attempts=1 WHERE id=:id"),
                        {"id": row.job_id},
                    )
                async with session.begin():
                    await jobs.finish(
                        plan.id, old.token, SourceInspection("old", 1, 1, Fraction(1))
                    )
                    await jobs.fail(plan.id, old.token, "invalid_media", terminal=True)
                unchanged = await stored(resources, plan.id)
                assert unchanged.status == "inspecting" and unchanged.inspection is None
                async with session.begin():
                    await session.execute(
                        text("UPDATE procrastinate_jobs SET status='doing' WHERE id=:id"),
                        {"id": row.job_id},
                    )
                    assert await jobs.claim(plan.id, row.job_id, 0) is None
                    current = await jobs.claim(plan.id, row.job_id, 1)
                    assert current is not None and current.token != old.token

    asyncio.run(exercise())
