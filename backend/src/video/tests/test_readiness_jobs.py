"""Readiness observes durable shared work, commit boundaries and complete media."""

import asyncio
from contextlib import AsyncExitStack
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from video.domain.processing import SourceInspectionError
from video.infrastructure.encoder import S3MediaEncoder
from video.infrastructure.models import EncodedTrackRow, MediaPlanRow, PublicationRow, UploadRow
from video.infrastructure.readiness import SqlProcessingViews
from video.public import CleanupOriginals, CompleteUpload, GetProcessing, GetVideoSettings
from video.tests import test_source_processing as support
from video.tests.test_publications import account, invoke
from video.tests.test_source_processing import process, source_environment
from video.tests.test_subtitle_jobs import ffmpeg

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.fixture(scope="module")
def small_video(tmp_path_factory):
    return support.small_video.__wrapped__(tmp_path_factory)


def test_actual_shared_packages_readiness_and_owner_boundaries(
    backend_runtime, small_video, tmp_path, source_queue
):
    async def exercise():
        async with AsyncExitStack() as stack:
            contexts = [
                await stack.enter_async_context(source_environment(backend_runtime, small_video))
                for _ in range(2)
            ]
            container, resources, owner, draft, upload = contexts[0]
            for container, _, user, publication, transfer in contexts:
                await invoke(container, CompleteUpload, user.id, publication.id, transfer.id)
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            await process(container, resources, tmp_path, source_queue)
            for container, _, user, publication, _transfer in contexts:
                value = await invoke(container, GetProcessing, user.id, publication.id)
                assert value.processing.value == "ready" and len(value.available_qualities) == 1
                assert value.upload == "uploaded" and not value.hd and not value.hdr
                settings = await invoke(container, GetVideoSettings, user.id, publication.id)
                assert settings.processing.value == "ready"
                assert (
                    settings.publication.value == "draft" and settings.visibility.value == "private"
                )
                async with resources.sessions() as session:
                    prepared = await SqlProcessingViews(session).prepared(publication.id, user.id)
                assert prepared.master.startswith("#EXTM3U\n")
                assert "av01." in prepared.master and "RESOLUTION=64x64" in prepared.master
            after = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert (before.added_at, before.updated_at) == (after.added_at, after.updated_at)
            async with resources.sessions() as session:
                assert (
                    await SqlProcessingViews(session).prepared(draft.id, contexts[1][2].id) is None
                )
                plan = await session.get(MediaPlanRow, upload.id)
                video_id = plan.config["variants"][0]["video"]
                track = await session.get(EncodedTrackRow, video_id)
                artifact = track.artifact
            # An uncommitted failure cannot replace a complete result seen by
            # another connection; rollback preserves it for all logical owners.
            async with resources.sessions() as writer:
                async with writer.begin():
                    row = await writer.get(EncodedTrackRow, video_id)
                    row.status, row.artifact, row.error_code = "failed", None, "encoding_failed"
                    await writer.flush()
                    assert (
                        await invoke(container, GetProcessing, owner.id, draft.id)
                    ).processing.value == "ready"
                    await writer.rollback()
            for committed_status in ("failed", "pending", "ready"):
                async with resources.sessions.begin() as writer:
                    row = await writer.get(EncodedTrackRow, video_id)
                    row.status = committed_status
                    row.artifact = artifact if committed_status == "ready" else None
                    row.error_code = "encoding_failed" if committed_status == "failed" else None
                for container, _, user, publication, _ in contexts:
                    value = await invoke(container, GetProcessing, user.id, publication.id)
                    assert (
                        value.processing.value
                        == {"failed": "failed", "pending": "processing", "ready": "ready"}[
                            committed_status
                        ]
                    )
            # State/assembly has no storage/cache I/O and changes no publications.
            with patch.object(
                resources.objects,
                "get_object",
                side_effect=AssertionError("unexpected object read"),
            ):
                assert (
                    await invoke(container, GetProcessing, owner.id, draft.id)
                ).processing.value == "ready"
            async with resources.sessions() as session:
                row = await session.get(PublicationRow, draft.id)
                assert row.publication == "draft" and row.processing == "processing"

    asyncio.run(exercise())


def test_readiness_query_failure_is_not_false_success(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            async with resources.sessions.begin() as session:
                await session.execute(text("SET LOCAL statement_timeout='1ms'"))
                # Deliberate transaction failure, then the real projection must
                # fail rather than return a cached ready value or a fallback.
                with pytest.raises(DBAPIError):
                    await session.execute(text("SELECT pg_sleep(0.05)"))
                with pytest.raises(DBAPIError):
                    await SqlProcessingViews(session).prepared(1, owner.id)
                await session.rollback()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "size,qualities,tone",
    [
        ("1280x720", [360, 480, 720], 440),
        ("3840x2160", [360, 480, 720, 1080, 1440, 2160], 880),
    ],
)
def test_real_partial_failure_keeps_low_video_and_waits_for_required_sound(
    backend_runtime, tmp_path, source_queue, size, qualities, tone
):
    source = tmp_path / "ladder.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={size}:rate=1:duration=1",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={tone}:sample_rate=48000:duration=1",
        "-c:v",
        "mpeg4",
        "-c:a",
        "aac",
        source,
    )

    async def exercise():
        async with source_environment(backend_runtime, source.read_bytes()) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            async with resources.sessions.begin() as session:
                row = await session.get(PublicationRow, draft.id)
                row.moderation = "hidden"
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            observations = []
            original = S3MediaEncoder.encode

            async def encode(encoder, claim):
                current = await invoke(container, GetProcessing, owner.id, draft.id)
                observations.append((claim.spec["kind"], claim.spec.get("quality"), current))
                if claim.spec["kind"] == "video" and claim.spec["quality"] >= 720:
                    assert current.processing.value == "processing"
                    assert [item.quality for item in current.available_qualities] == [360, 480]
                    raise SourceInspectionError("encoding_failed")
                return await original(encoder, claim)

            with patch.object(S3MediaEncoder, "encode", encode):
                await process(container, resources, tmp_path / "partial", source_queue)
            result = await invoke(container, GetProcessing, owner.id, draft.id)
            assert result.processing.value == "partial"
            assert [(item.quality, item.status) for item in result.qualities] == [
                (quality, "ready" if quality < 720 else "failed") for quality in qualities
            ]
            assert result.qualities[-1].error_code == "encoding_failed" and not result.hd
            assert any(
                kind == "audio" and not state.available_qualities for kind, _, state in observations
            )
            after = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert after.processing.value == "partial" and after.updated_at == before.updated_at
            assert after.publication.value == "draft" and after.moderation.value == "hidden"
            async with resources.sessions() as session:
                prepared = await SqlProcessingViews(session).prepared(draft.id, owner.id)
                plan = await session.get(MediaPlanRow, upload.id)
                failed_id = plan.config["variants"][-1]["video"]
            assert f"tracks/{failed_id}/" not in prepared.master
            assert "mp4a.40.2" in prepared.master
            assert prepared.master.count("#EXT-X-STREAM-INF:") == 2
            # Even terminal failures leave the source available for a later retry.
            await invoke(container, CleanupOriginals)
            async with resources.sessions() as session:
                receipt = await session.get(UploadRow, upload.id)
            assert receipt.original_deleted_at is None
            original_object = await asyncio.to_thread(
                resources.objects.head_object,
                Bucket=resources.bucket,
                Key=receipt.object_key,
            )
            assert original_object["ContentLength"] == len(source.read_bytes())
            assert [
                quality for kind, quality, _ in observations if kind == "video" and quality < 720
            ] == [360, 480]

    asyncio.run(exercise())
