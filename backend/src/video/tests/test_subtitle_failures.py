"""A failed or unsupported subtitle never blocks other tracks or ready video."""

import asyncio
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

import pytest
from botocore.exceptions import EndpointConnectionError
from sqlalchemy import select, text

from common.infrastructure.job_error_model import JobErrorRow
from video.infrastructure.models import EncodedTrackRow, EncodingAttemptRow, UploadRow
from video.infrastructure.source_inspector import S3SourceInspector
from video.public import CompleteUpload, GetVideoSettings
from video.tests.test_publications import invoke
from video.tests.test_source_processing import process, source_environment
from video.tests.test_subtitle_jobs import ffmpeg

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.fixture
def captions(tmp_path):
    for name in ("good", "other"):
        (tmp_path / (name + ".srt")).write_text(
            f"1\n00:00:00,500 --> 00:00:01,500\n{name}-{uuid4().hex}\n"
        )
    source = tmp_path / "two.mkv"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=96x64:rate=2:duration=2",
        "-i",
        tmp_path / "good.srt",
        "-i",
        tmp_path / "other.srt",
        "-map",
        "0:v",
        "-map",
        "1:s",
        "-map",
        "2:s",
        "-c:v",
        "mpeg4",
        "-c:s",
        "srt",
        source,
    )
    return source.read_bytes()


async def outputs(resources, upload_id):
    async with resources.sessions() as session:
        return (
            await session.scalars(
                select(EncodedTrackRow).where(
                    EncodedTrackRow.source_upload_id == upload_id,
                    EncodedTrackRow.spec["kind"].astext == "subtitle",
                )
            )
        ).all()


def test_partial_webvtt_upload_is_private_cleaned_and_retried_independently(
    backend_runtime, captions, tmp_path, source_queue
):
    async def exercise():
        async with source_environment(backend_runtime, captions) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            put = resources.objects.put_object
            failed_ids = []

            def fail_once(**kwargs):
                if kwargs["Key"].endswith(".vtt") and not failed_ids:
                    failed_ids.append(int(kwargs["Key"].split("/")[1]))
                    raise EndpointConnectionError(endpoint_url="http://synthetic.invalid")
                return put(**kwargs)

            with patch.object(resources.objects, "put_object", side_effect=fail_once):
                await process(container, resources, tmp_path / "failed", source_queue)
            tracks = await outputs(resources, upload.id)
            assert sorted(track.status for track in tracks) == ["pending", "ready"]
            failed = next(track for track in tracks if track.id == failed_ids[0])
            async with resources.sessions.begin() as session:
                prefix = await session.scalar(
                    select(EncodingAttemptRow.prefix).where(
                        EncodingAttemptRow.encoded_id == failed.id
                    )
                )
                await session.execute(
                    text("UPDATE procrastinate_jobs SET scheduled_at=now() WHERE id=:id"),
                    {"id": failed.job_id},
                )
            page = await asyncio.to_thread(
                resources.objects.list_objects_v2, Bucket=resources.bucket, Prefix=prefix
            )
            assert page.get("KeyCount", 0) == 0
            await process(container, resources, tmp_path / "retry", source_queue)
            assert all(track.status == "ready" for track in await outputs(resources, upload.id))
            async with resources.sessions() as session:
                assert (
                    await session.scalar(
                        select(JobErrorRow.error_code).where(JobErrorRow.job_id == failed.job_id)
                    )
                    == "source_unavailable"
                )
            assert (
                await invoke(container, GetVideoSettings, owner.id, draft.id)
            ).processing.value == "ready"

    asyncio.run(exercise())


def test_bitmap_policy_is_journaled_without_conversion_and_other_tracks_succeed(
    backend_runtime, captions, tmp_path, source_queue
):
    original = S3SourceInspector.inspect

    async def bitmap_inventory(inspector, claim):
        result = await original(inspector, claim)
        return replace(
            result,
            tracks=tuple(
                replace(track, properties={**track.properties, "codec_name": "hdmv_pgs_subtitle"})
                if track.stream_index == 2
                else track
                for track in result.tracks
            ),
        )

    async def exercise():
        async with source_environment(backend_runtime, captions) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            with patch.object(S3SourceInspector, "inspect", bitmap_inventory):
                await process(container, resources, tmp_path, source_queue)
            tracks = await outputs(resources, upload.id)
            assert sorted(track.status for track in tracks) == ["failed", "ready"]
            failed = next(track for track in tracks if track.status == "failed")
            assert failed.error_code == "unsupported_subtitle"
            async with resources.sessions() as session:
                assert (
                    await session.scalar(
                        select(JobErrorRow.error_code).where(JobErrorRow.job_id == failed.job_id)
                    )
                    == "unsupported_subtitle"
                )
                source = await session.get(UploadRow, upload.id)
                assert (
                    await asyncio.to_thread(
                        resources.objects.head_object,
                        Bucket=resources.bucket,
                        Key=source.object_key,
                    )
                )["ContentLength"] == len(captions)
            assert (
                await invoke(container, GetVideoSettings, owner.id, draft.id)
            ).processing.value == "ready"

    asyncio.run(exercise())
