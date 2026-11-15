"""Real embedded subtitle extraction, shared packages and independent failures."""

import asyncio
import shutil
import subprocess
from fractions import Fraction
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select

from video.application.encoding import RepairEncoding
from video.infrastructure.encoder import S3MediaEncoder
from video.infrastructure.models import (
    EncodedTrackRow,
    EncodingAttemptRow,
    MediaPlanRow,
    SubtitleLinkRow,
)
from video.public import CleanupOriginals, CompleteUpload, GetVideoSettings
from video.tests.test_publications import invoke
from video.tests.test_source_processing import process, source_environment

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


def ffmpeg(*args):
    subprocess.run(  # noqa: S603 -- fixed tools and generated fixtures
        [shutil.which("ffmpeg"), "-v", "error", "-nostdin", *map(str, args)],
        check=True,
        capture_output=True,
        timeout=90,
    )  # noqa: S603 -- generated fixtures


@pytest.fixture(scope="module")
def subtitled(tmp_path_factory):
    directory = tmp_path_factory.mktemp("embedded-subtitles")
    text = directory / "captions.srt"
    text.write_text(
        "1\n00:00:05,500 --> 00:00:06,500\nПривет <i>мир</i>\n\n"
        "2\n00:00:12,000 --> 00:00:12,500\n<script>bad</script>\n",
        encoding="utf-8",
    )
    source = directory / "source.mkv"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1280x720:rate=1:duration=13",
        "-i",
        text,
        "-map",
        "0:v",
        "-map",
        "1:s",
        "-c:v",
        "mpeg4",
        "-c:s",
        "srt",
        "-metadata:s:s:0",
        "language=rus",
        "-output_ts_offset",
        "5",
        source,
    )
    tagged = directory / "tagged.mkv"
    ffmpeg(
        "-i",
        source,
        "-copyts",
        "-map",
        "0",
        "-c",
        "copy",
        "-metadata:s:s:0",
        "language=eng",
        tagged,
    )
    return source.read_bytes(), tagged.read_bytes()


async def links(resources, upload_id):
    async with resources.sessions() as session:
        return (
            await session.scalars(
                select(SubtitleLinkRow).where(SubtitleLinkRow.upload_id == upload_id)
            )
        ).all()


async def object_bytes(resources, key):
    response = await asyncio.to_thread(
        resources.objects.get_object, Bucket=resources.bucket, Key=key
    )
    try:
        return response["Body"].read()
    finally:
        response["Body"].close()


def test_real_timeline_shared_across_qualities_accounts_and_languages(
    backend_runtime, subtitled, tmp_path, source_queue
):
    async def exercise():
        identities = []
        for index, data in enumerate(subtitled):
            async with source_environment(backend_runtime, data) as (
                container,
                resources,
                owner,
                draft,
                upload,
            ):
                await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
                before = await invoke(container, GetVideoSettings, owner.id, draft.id)
                await process(container, resources, tmp_path / str(index), source_queue)
                references = await links(resources, upload.id)
                assert len(references) == 3 and {ref.status for ref in references} == {"queued"}
                assert len({ref.encoded_id for ref in references}) == 1
                assert {ref.language for ref in references} == {"rus" if index == 0 else "eng"}
                identities.append(references[0].encoded_id)
                async with resources.sessions() as session:
                    output = await session.get(EncodedTrackRow, references[0].encoded_id)
                assert output.status == "ready", output.error_code
                artifact = output.artifact
                first = await object_bytes(resources, artifact["prefix"] + "segment_000000.vtt")
                second = await object_bytes(resources, artifact["prefix"] + "segment_000001.vtt")
                for segment in (first, second):
                    assert "00:00:05.500 --> 00:00:06.500" in segment.decode()
                    assert "Привет <i>мир</i>" in segment.decode()
                    clock = round(Fraction(*artifact["presentation_origin"]) * 90000) % (1 << 33)
                    assert f"MPEGTS:{clock}".encode() in segment
                after = await invoke(container, GetVideoSettings, owner.id, draft.id)
                assert after.updated_at == before.updated_at and after.processing.value == "ready"
        assert identities[0] == identities[1]

    asyncio.run(exercise())


def test_legacy_clock_is_recovered_without_reencoding_video(
    backend_runtime, subtitled, tmp_path, source_queue
):
    async def exercise():
        async with source_environment(backend_runtime, subtitled[0]) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            await process(container, resources, tmp_path / "first", source_queue)
            await invoke(container, CleanupOriginals)
            references = await links(resources, upload.id)
            async with resources.sessions.begin() as session:
                await session.execute(
                    delete(SubtitleLinkRow).where(SubtitleLinkRow.upload_id == upload.id)
                )
                plan = await session.get(MediaPlanRow, upload.id)
                config = dict(plan.config)
                config.pop("subtitles_inventoried")
                plan.config = config
                for ref in references:
                    video = await session.get(EncodedTrackRow, ref.video_encoded_id)
                    artifact = dict(video.artifact)
                    artifact.pop("presentation_origin", None)
                    video.artifact = artifact
            await invoke(container, RepairEncoding)
            kinds = []
            original = S3MediaEncoder.encode

            async def counted(encoder, claim):
                kinds.append(claim.spec["kind"])
                return await original(encoder, claim)

            with patch.object(S3MediaEncoder, "encode", counted):
                await process(container, resources, tmp_path / "clock", source_queue)
            assert kinds and set(kinds) == {"subtitle_clock"}
            assert all(ref.status == "queued" for ref in await links(resources, upload.id))
            async with resources.sessions() as session:
                for ref in await links(resources, upload.id):
                    assert (await session.get(EncodedTrackRow, ref.encoded_id)).status == "ready"

    asyncio.run(exercise())


def test_cues_outside_video_are_empty_without_a_package(backend_runtime, tmp_path, source_queue):
    text = tmp_path / "outside.srt"
    text.write_text("1\n00:00:20,000 --> 00:00:21,000\noutside\n")
    source = tmp_path / "outside.mkv"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=size=32x48:rate=1:duration=1",
        "-i",
        text,
        "-c:v",
        "mpeg4",
        "-c:s",
        "srt",
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
            await process(container, resources, tmp_path / "empty", source_queue)
            refs = await links(resources, upload.id)
            assert len(refs) == 1
            async with resources.sessions() as session:
                output = await session.get(EncodedTrackRow, refs[0].encoded_id)
                assert output.status == "empty" and output.artifact is None and output.token is None
                assert not (
                    await session.scalars(
                        select(EncodingAttemptRow).where(EncodingAttemptRow.encoded_id == output.id)
                    )
                ).all()

    asyncio.run(exercise())
