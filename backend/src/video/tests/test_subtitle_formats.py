"""FFmpeg-native text formats become timed WebVTT in the real media queue."""

import asyncio

import pytest

from video.infrastructure.models import EncodedTrackRow
from video.public import CompleteUpload
from video.tests.test_publications import invoke
from video.tests.test_source_processing import process, source_environment
from video.tests.test_subtitle_jobs import ffmpeg, links, object_bytes

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.mark.parametrize("codec,suffix", [("ass", "mkv"), ("webvtt", "mkv"), ("mov_text", "mp4")])
def test_other_embedded_text_formats_keep_timing(
    backend_runtime, tmp_path, source_queue, codec, suffix
):
    text = tmp_path / "text.srt"
    text.write_text("1\n00:00:00,500 --> 00:00:01,500\nOther format\n")
    source = tmp_path / ("other." + suffix)
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=96x64:rate=24:duration=2",
        "-i",
        text,
        "-c:v",
        "mpeg4",
        "-c:s",
        codec,
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
            await process(container, resources, tmp_path / "other", source_queue)
            refs = await links(resources, upload.id)
            async with resources.sessions() as session:
                output = await session.get(EncodedTrackRow, refs[0].encoded_id)
            assert output.status == "ready", output.error_code
            data = await object_bytes(resources, output.artifact["prefix"] + "segment_000000.vtt")
            assert b"00:00:00.500 --> 00:00:01.500" in data and b"Other format" in data

    asyncio.run(exercise())


def test_missing_codec_descriptor_does_not_roll_back_ready_video(
    backend_runtime, tmp_path, source_queue
):
    from dataclasses import replace
    from unittest.mock import patch

    from video.infrastructure.source_inspector import S3SourceInspector

    text = tmp_path / "unknown.srt"
    text.write_text("1\n00:00:00,500 --> 00:00:01,500\nNative decoder\n")
    source = tmp_path / "unknown.mkv"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=size=32x48:rate=2:duration=2",
        "-i",
        text,
        "-c:v",
        "mpeg4",
        "-c:s",
        "srt",
        source,
    )
    original = S3SourceInspector.inspect

    async def without_name(inspector, claim):
        inspection = await original(inspector, claim)
        tracks = []
        for track in inspection.tracks:
            if track.properties.get("codec_type") == "subtitle":
                properties = dict(track.properties)
                properties.pop("codec_name", None)
                track = replace(track, properties=properties)
            tracks.append(track)
        return replace(inspection, tracks=tuple(tracks))

    async def exercise():
        async with source_environment(backend_runtime, source.read_bytes()) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            with patch.object(S3SourceInspector, "inspect", without_name):
                await process(container, resources, tmp_path / "decode", source_queue)
            refs = await links(resources, upload.id)
            async with resources.sessions() as session:
                assert (await session.get(EncodedTrackRow, refs[0].encoded_id)).status == "ready"
                assert (
                    await session.get(EncodedTrackRow, refs[0].video_encoded_id)
                ).status == "ready"

    asyncio.run(exercise())
