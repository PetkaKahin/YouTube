"""Actual PostgreSQL/RustFS/gateway transfer, uncertain outcomes, and ownership races."""

import asyncio
import http.client
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from anyio import to_thread
from sqlalchemy import select, text, update

from common.contracts.storage import ObjectBucket
from video.application.uploads import CompleteUpload, ExpireUploads, RefreshUpload, StartUpload
from video.domain.publications import VideoMetadata, VideoNotFound
from video.domain.uploads import PART_SIZE, StorageUnavailable, UploadError, validate_size
from video.infrastructure.models import UploadRow
from video.infrastructure.multipart import S3Multipart
from video.infrastructure.uploads import SqlUploads, transfer_value
from video.public import CreateDraft, GetVideoSettings
from video.tests.test_publications import account, invoke, wait_for_waiters


def put_part(link, data, *, method="PUT", host="nginx", headers=None):
    # Never let a failing HTTP exception print a signed URL or raw storage error body.
    connection = http.client.HTTPConnection(host, 8080 if host == "nginx" else 9000, timeout=15)
    try:
        connection.request(method, link, body=data, headers=headers or {})
        response = connection.getresponse()
        response.read()
        return response.status
    except Exception:
        raise RuntimeError("Synthetic upload request failed") from None
    finally:
        connection.close()


@asynccontextmanager
async def environment(backend_runtime):
    async with backend_runtime() as (container, resources):
        owner = await account(container)
        draft = await invoke(
            container, CreateDraft, owner.id, VideoMetadata("source", "", "private")
        )
        storage = S3Multipart(resources.objects, ObjectBucket(resources.bucket))
        try:
            yield container, resources, owner, draft, storage
        finally:
            async with resources.sessions() as session:
                transfers = [
                    transfer_value(row)
                    for row in await session.scalars(
                        select(UploadRow).where(UploadRow.video_id == draft.id)
                    )
                ]
            for transfer in transfers:
                await storage.discard(transfer)


async def transfer(resources, upload_id):
    async with resources.sessions() as session:
        return transfer_value(await session.get(UploadRow, upload_id))


def test_real_multipart_transfer_refresh_and_idempotent_complete(backend_runtime):
    async def exercise():
        async with environment(backend_runtime) as (container, resources, owner, draft, storage):
            plan = await invoke(container, StartUpload, owner.id, draft.id, PART_SIZE + 3)
            assert plan.part_size == PART_SIZE and plan.parallel_parts == 4
            assert [link.size for link in plan.parts] == [PART_SIZE, 3]
            assert 890 <= (plan.expires_at - datetime.now(UTC)).total_seconds() <= 900
            assert await to_thread.run_sync(put_part, plan.parts[0].url, b"a" * PART_SIZE) == 200
            refreshed = await invoke(container, RefreshUpload, owner.id, draft.id, plan.id)
            assert [link.number for link in refreshed.parts] == [2]
            with pytest.raises(UploadError, match="incomplete_upload"):
                await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert before.added_at is None and before.publication.value == "draft"
            assert await to_thread.run_sync(put_part, refreshed.parts[0].url, b"end") == 200
            result = await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            assert result.status == "uploaded" and not result.parts
            value = await invoke(container, GetVideoSettings, owner.id, draft.id)
            stored = await storage.head(await transfer(resources, plan.id))
            assert value.added_at == stored.modified_at
            assert value.upload.value == "uploaded" and value.processing.value == "pending"
            assert value.updated_at == draft.updated_at and value.visibility.value == "private"
            assert (
                await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            ).status == "uploaded"
            assert await invoke(container, GetVideoSettings, owner.id, draft.id) == value
            assert (
                await invoke(container, StartUpload, owner.id, draft.id, PART_SIZE + 3)
            ).parts == ()

    asyncio.run(exercise())


def test_concurrent_start_and_complete_have_one_transfer_and_date(backend_runtime):
    async def exercise():
        async with environment(backend_runtime) as (container, resources, owner, draft, storage):
            first, second = await asyncio.gather(
                *[invoke(container, StartUpload, owner.id, draft.id, 3) for _ in range(2)]
            )
            assert first.id == second.id
            pending = await transfer(resources, first.id)
            assert len(await storage.existing(pending)) == 1
            assert await to_thread.run_sync(put_part, first.parts[0].url, b"abc") == 200
            results = await asyncio.gather(
                *[invoke(container, CompleteUpload, owner.id, draft.id, first.id) for _ in range(2)]
            )
            assert all(result.status == "uploaded" for result in results)
            saved = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert saved.added_at == (await storage.head(pending)).modified_at

    asyncio.run(exercise())


@pytest.mark.parametrize("target", ["activate", "complete"])
def test_storage_success_then_failed_commit_is_recovered_without_duplicate(
    backend_runtime, monkeypatch, target
):
    async def exercise():
        async with environment(backend_runtime) as (container, resources, owner, draft, storage):
            original = getattr(SqlUploads, target)

            async def failed_commit(self, *args):
                await original(self, *args)
                raise RuntimeError("artificial failure before transaction commit")

            if target == "activate":
                with monkeypatch.context() as patch:
                    patch.setattr(SqlUploads, target, failed_commit)
                    with pytest.raises(RuntimeError):
                        await invoke(container, StartUpload, owner.id, draft.id, 3)
                async with resources.sessions() as session:
                    pending = transfer_value(
                        (
                            await session.scalars(
                                select(UploadRow).where(UploadRow.video_id == draft.id)
                            )
                        ).one()
                    )
                assert pending.status == "creating" and pending.multipart_id is None
                assert len(await storage.existing(pending)) == 1
                recovered = await invoke(container, StartUpload, owner.id, draft.id, 3)
                assert recovered.id == pending.id
                assert len(await storage.existing(pending)) == 1
            else:
                plan = await invoke(container, StartUpload, owner.id, draft.id, 3)
                assert await to_thread.run_sync(put_part, plan.parts[0].url, b"abc") == 200
                with monkeypatch.context() as patch:
                    patch.setattr(SqlUploads, target, failed_commit)
                    with pytest.raises(RuntimeError):
                        await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
                pending = await transfer(resources, plan.id)
                stored = await storage.head(pending)
                assert stored is not None and pending.status == "uploading"
                assert (
                    await invoke(container, GetVideoSettings, owner.id, draft.id)
                ).added_at is None
                assert (
                    await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
                ).status == "uploaded"
                assert (
                    await invoke(container, GetVideoSettings, owner.id, draft.id)
                ).added_at == stored.modified_at

    asyncio.run(exercise())


@pytest.mark.parametrize("activity", ["idle", "recent_part", "completed"])
def test_expiry_preserves_active_parts_or_completed_source(backend_runtime, activity):
    async def exercise():
        async with environment(backend_runtime) as (container, resources, owner, draft, storage):
            plan = await invoke(container, StartUpload, owner.id, draft.id, 3)
            if activity != "idle":
                assert await to_thread.run_sync(put_part, plan.parts[0].url, b"abc") == 200
            if activity == "completed":
                await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            async with resources.sessions.begin() as session:
                await session.execute(
                    update(UploadRow)
                    .where(UploadRow.id == plan.id)
                    .values(activity_at=datetime.now(UTC) - timedelta(hours=25))
                )
            await invoke(container, ExpireUploads)
            pending = await transfer(resources, plan.id)
            if activity == "idle":
                assert pending.status == "expired" and await storage.existing(pending) == ()
                assert await storage.head(pending) is None
                with pytest.raises(UploadError, match="upload_expired"):
                    await invoke(container, RefreshUpload, owner.id, draft.id, plan.id)
                fresh = await invoke(container, StartUpload, owner.id, draft.id, 4)
                assert fresh.id != plan.id
            else:
                assert pending.status == ("uploaded" if activity == "completed" else "uploading")
            saved = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert saved.title == draft.title and saved.updated_at == draft.updated_at

    asyncio.run(exercise())


def test_complete_and_cleanup_serialize_on_real_database_lock(backend_runtime):
    async def exercise():
        async with environment(backend_runtime) as (container, resources, owner, draft, _):
            plan = await invoke(container, StartUpload, owner.id, draft.id, 3)
            assert await to_thread.run_sync(put_part, plan.parts[0].url, b"abc") == 200
            async with resources.sessions.begin() as session:
                await session.execute(
                    update(UploadRow)
                    .where(UploadRow.id == plan.id)
                    .values(activity_at=datetime.now(UTC) - timedelta(hours=25))
                )
            async with resources.sessions() as holder:
                await holder.begin()
                blocker = await holder.scalar(text("SELECT pg_backend_pid()"))
                await holder.execute(
                    text("SELECT id FROM video_publications WHERE id=:id FOR UPDATE"),
                    {"id": draft.id},
                )
                waiting = asyncio.create_task(
                    invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
                )
                try:
                    await wait_for_waiters(resources.engine, blocker)
                    assert await invoke(container, ExpireUploads) == 0
                    await holder.commit()
                    assert (await waiting).status == "uploaded"
                finally:
                    await holder.rollback()
                    if not waiting.done():
                        waiting.cancel()
                    await asyncio.gather(waiting, return_exceptions=True)

    asyncio.run(exercise())


def test_failed_expiry_can_resume_without_losing_metadata(backend_runtime, monkeypatch):
    async def exercise():
        async with environment(backend_runtime) as (container, resources, owner, draft, _):
            plan = await invoke(container, StartUpload, owner.id, draft.id, 3)
            async with resources.sessions.begin() as session:
                await session.execute(
                    update(UploadRow)
                    .where(UploadRow.id == plan.id)
                    .values(activity_at=datetime.now(UTC) - timedelta(hours=25))
                )

            async def unavailable(self, pending):
                raise StorageUnavailable

            with monkeypatch.context() as patch:
                patch.setattr(S3Multipart, "discard", unavailable)
                with pytest.raises(StorageUnavailable):
                    await invoke(container, ExpireUploads)
            assert (await transfer(resources, plan.id)).status == "uploading"
            assert await invoke(container, ExpireUploads) == 1
            assert (await transfer(resources, plan.id)).status == "expired"
            saved = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert saved.title == draft.title and saved.updated_at == draft.updated_at

    asyncio.run(exercise())


def test_wrong_actual_object_size_never_becomes_uploaded_and_is_cleaned(backend_runtime):
    async def exercise():
        async with environment(backend_runtime) as (container, resources, owner, draft, storage):
            plan = await invoke(container, StartUpload, owner.id, draft.id, 3)
            pending = await transfer(resources, plan.id)
            # Privileged test setup bypasses the signed public gateway deliberately.
            objects = resources.objects
            part = await to_thread.run_sync(
                lambda: objects.upload_part(
                    Bucket=resources.bucket,
                    Key=pending.object_key,
                    UploadId=pending.multipart_id,
                    PartNumber=1,
                    Body=b"four",
                )
            )
            with pytest.raises(UploadError, match="invalid_parts"):
                await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            await to_thread.run_sync(
                lambda: objects.complete_multipart_upload(
                    Bucket=resources.bucket,
                    Key=pending.object_key,
                    UploadId=pending.multipart_id,
                    MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": part["ETag"]}]},
                )
            )
            with pytest.raises(UploadError, match="invalid_size"):
                await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            assert (await invoke(container, GetVideoSettings, owner.id, draft.id)).added_at is None
            async with resources.sessions.begin() as session:
                await session.execute(
                    update(UploadRow)
                    .where(UploadRow.id == plan.id)
                    .values(activity_at=datetime.now(UTC) - timedelta(hours=25))
                )
            await invoke(container, ExpireUploads)
            assert await storage.head(pending) is None

    asyncio.run(exercise())


@pytest.mark.parametrize("method", [StartUpload, RefreshUpload, CompleteUpload])
def test_stranger_cannot_control_uploads_or_receive_links(backend_runtime, method):
    async def exercise():
        async with environment(backend_runtime) as (container, _, owner, draft, _):
            stranger = await account(container)
            plan = await invoke(container, StartUpload, owner.id, draft.id, 3)
            with pytest.raises(VideoNotFound):
                await invoke(
                    container,
                    method,
                    stranger.id,
                    draft.id,
                    3 if method is StartUpload else plan.id,
                )

    asyncio.run(exercise())


@pytest.mark.parametrize("mutation", ["size", "part", "key", "expired", "method"])
def test_signature_rejects_tampered_size_target_method_and_expiry(
    backend_runtime, monkeypatch, mutation
):
    async def exercise():
        async with environment(backend_runtime) as (container, resources, owner, draft, storage):
            plan = await invoke(container, StartUpload, owner.id, draft.id, 3)
            if mutation == "expired":
                with monkeypatch.context() as patch:
                    patch.setattr(
                        "botocore.auth.get_current_datetime",
                        lambda: datetime.now(UTC) - timedelta(minutes=16),
                    )
                    expired_url = await storage.sign(await transfer(resources, plan.id), 1)
                    plan = replace(plan, parts=(replace(plan.parts[0], url=expired_url),))
            link = plan.parts[0].url
            data, method = b"abc", "PUT"
            if mutation == "size":
                data = b"abcd"
            if mutation == "part":
                parsed = urlsplit(link)
                query = parse_qs(parsed.query)
                query["partNumber"] = ["2"]
                link = parsed.path + "?" + urlencode(query, doseq=True)
            if mutation == "key":
                link = link.replace("/incoming/", "/other/")
            if mutation == "method":
                method = "GET"
            status = await to_thread.run_sync(lambda: put_part(link, data, method=method))
            assert status == 403
            assert await storage.parts(await transfer(resources, plan.id)) == ()
            fresh = await invoke(container, RefreshUpload, owner.id, draft.id, plan.id)
            assert await to_thread.run_sync(put_part, fresh.parts[0].url, b"abc") == 200

    asyncio.run(exercise())


@pytest.mark.parametrize("size", [0, -1, True, "3", 5_000_000_001])
def test_invalid_sizes_rejected_in_domain(size):
    with pytest.raises(UploadError, match="invalid_size"):
        validate_size(size)


def test_maximum_size_plan_and_resize_conflict(backend_runtime):
    async def exercise():
        async with environment(backend_runtime) as (container, _, owner, draft, _):
            plan = await invoke(container, StartUpload, owner.id, draft.id, 5_000_000_000)
            assert len(plan.parts) == 299 and sum(p.size for p in plan.parts) == 5_000_000_000
            with pytest.raises(UploadError, match="upload_size_conflict"):
                await invoke(container, StartUpload, owner.id, draft.id, 3)

    asyncio.run(exercise())


def test_storage_failure_does_not_claim_uploaded_or_destroy_durable_intent(
    backend_runtime, monkeypatch
):
    async def exercise():
        async with environment(backend_runtime) as (container, resources, owner, draft, _):

            async def unavailable(self, pending):
                raise StorageUnavailable

            with monkeypatch.context() as patch:
                patch.setattr(S3Multipart, "initiate", unavailable)
                with pytest.raises(StorageUnavailable):
                    await invoke(container, StartUpload, owner.id, draft.id, 3)
            async with resources.sessions() as session:
                pending = transfer_value(
                    (
                        await session.scalars(
                            select(UploadRow).where(UploadRow.video_id == draft.id)
                        )
                    ).one()
                )
            assert pending.status == "creating"
            assert (await invoke(container, GetVideoSettings, owner.id, draft.id)).added_at is None
            plan = await invoke(container, StartUpload, owner.id, draft.id, 3)
            assert plan.id == pending.id and plan.status == "uploading"

    asyncio.run(exercise())
