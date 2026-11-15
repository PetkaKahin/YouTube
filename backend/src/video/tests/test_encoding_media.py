"""Real color conversion, audio alignment and independent fractional HLS clocks."""

import asyncio
import json
import shutil
import struct
import subprocess
from fractions import Fraction
from uuid import uuid4

import pytest
from sqlalchemy import select

from common.contracts.storage import ObjectBucket
from video.domain.encoding import EncodingClaim, make_plan
from video.infrastructure.encoder import S3MediaEncoder
from video.infrastructure.models import EncodedTrackRow, PlanTrackRow, UploadRow
from video.public import CompleteUpload, GetThumbnailImage, GetThumbnails
from video.tests.test_encoding import inventory, videos
from video.tests.test_publications import invoke
from video.tests.test_source_processing import process, source_environment

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


def run_ffmpeg(*args):
    return subprocess.run(  # noqa: S603 -- fixed tools and generated local paths
        [shutil.which("ffmpeg"), "-v", "error", "-nostdin", *map(str, args)],
        check=True,
        capture_output=True,
        timeout=90,
    )  # noqa: S603 -- generated fixture/verified server output


async def local_package(resources, artifact, directory):
    directory.mkdir(parents=True, exist_ok=True)
    for item in artifact["files"]:
        response = await asyncio.to_thread(
            resources.objects.get_object,
            Bucket=resources.bucket,
            Key=artifact["prefix"] + item["name"],
        )
        body = response["Body"]
        try:
            (directory / item["name"]).write_bytes(body.read())
        finally:
            body.close()
    return directory / "index.m3u8"


def test_real_hdr_plans_and_encodes_hdr10_and_sdr_on_cpu(backend_runtime, tmp_path, source_queue):
    source = tmp_path / "hdr.mkv"
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1920x1080:rate=2:duration=0.5,format=yuv420p10le,setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc",
        "-c:v",
        "ffv1",
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
            await process(container, resources, tmp_path / "spool", source_queue)
            async with resources.sessions() as session:
                outputs = (
                    await session.scalars(
                        select(EncodedTrackRow)
                        .join(PlanTrackRow, PlanTrackRow.encoded_id == EncodedTrackRow.id)
                        .where(PlanTrackRow.upload_id == upload.id)
                    )
                ).all()
            assert len(outputs) == 5 and all(output.status == "ready" for output in outputs), [
                (output.spec, output.error_code) for output in outputs
            ]
            frames = {}
            for output in outputs:
                assert output.artifact["metadata"]["pix_fmt"] == "yuv420p10le"
                if output.spec["quality"] == 1080:
                    playlist = await local_package(
                        resources, output.artifact, tmp_path / output.spec["range"]
                    )
                    frames[output.spec["range"]] = run_ffmpeg(
                        "-i",
                        playlist,
                        "-frames:v",
                        "1",
                        "-pix_fmt",
                        "yuv420p10le",
                        "-f",
                        "rawvideo",
                        "-",
                    ).stdout
            assert len(frames["sdr"]) == len(frames["pq"]) == 1920 * 1080 * 3
            assert frames["sdr"] != frames["pq"]
            options = await invoke(container, GetThumbnails, owner.id, draft.id)
            assert options.status == "ready" and len(options.candidates) == 3
            assert (await invoke(container, GetThumbnailImage, owner.id, draft.id))[:4] == b"RIFF"

    asyncio.run(exercise())


def test_delayed_audio_keeps_its_position_after_separate_encoding(
    backend_runtime, tmp_path, source_queue
):
    source = tmp_path / "delay.mp4"
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=size=96x64:rate=24:duration=3",
        "-itsoffset",
        "1",
        "-f",
        "lavfi",
        "-i",
        "sine=sample_rate=48000:duration=1",
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
            await process(container, resources, tmp_path / "spool", source_queue)
            async with resources.sessions() as session:
                outputs = (
                    await session.scalars(
                        select(EncodedTrackRow).where(
                            EncodedTrackRow.source_upload_id == upload.id,
                            EncodedTrackRow.spec["kind"].astext.in_(("video", "audio")),
                        )
                    )
                ).all()
            assert all(output.status == "ready" for output in outputs), [
                (output.status, output.error_code) for output in outputs
            ]
            audio = next(output for output in outputs if output.spec["kind"] == "audio")
            playlist = await local_package(resources, audio.artifact, tmp_path / "audio")
            pcm = run_ffmpeg("-i", playlist, "-ac", "1", "-ar", "48000", "-f", "f32le", "-").stdout
            samples = struct.unpack("<" + "f" * (len(pcm) // 4), pcm)
            activity = [
                sum(value * value for value in samples[start : start + 480]) / 480
                for start in range(0, len(samples) - 480, 480)
            ]
            first = next(index / 100 for index, power in enumerate(activity) if power > 0.001)
            last = max(index / 100 for index, power in enumerate(activity) if power > 0.001)
            assert 0.95 <= first <= 1.05 and 1.95 <= last <= 2.05
            assert abs(len(samples) / 48000 - 3) < 0.05

    asyncio.run(exercise())


def test_fractional_frame_caps_keep_independent_segment_boundaries(backend_runtime, tmp_path):
    source = tmp_path / "cadence.mp4"
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=96x64:rate=60000/1001:duration=13",
        "-c:v",
        "mpeg4",
        source,
    )
    profiles = [
        track.spec
        for track in videos(make_plan(inventory(1280, 720, rate=Fraction(60000, 1001), channels=0)))
    ]
    # Qualify both clocks at a small size; the domain tests check real ladder geometry.
    profiles = [
        {**profiles[0], "width": 96, "height": 64, "sar": [1, 1]},
        {**profiles[-1], "width": 96, "height": 64, "sar": [1, 1]},
    ]

    async def exercise():
        async with source_environment(backend_runtime, source.read_bytes()) as (
            _,
            resources,
            _,
            _,
            upload,
        ):
            async with resources.sessions() as session:
                row = await session.get(UploadRow, upload.id)
                key = row.object_key
            encoder = S3MediaEncoder(resources.objects, ObjectBucket(resources.bucket))
            prefixes = []
            boundaries = []
            try:
                for index, spec in enumerate(profiles):
                    token = uuid4()
                    prefix = f"encoded/900000{index}/{token.hex}/"
                    prefixes.append(prefix)
                    artifact = await encoder.encode(
                        EncodingClaim(
                            9000000 + index, token, key, len(source.read_bytes()), 0, spec, prefix
                        )
                    )
                    directory = tmp_path / f"cadence-{index}"
                    await local_package(resources, artifact, directory)
                    starts = []
                    for segment in sorted(directory.glob("segment_*.m4s")):
                        joined = directory / "part.mp4"
                        joined.write_bytes(
                            (directory / "init.mp4").read_bytes() + segment.read_bytes()
                        )
                        result = subprocess.run(  # noqa: S603 -- verified generated file
                            [
                                shutil.which("ffprobe"),
                                "-v",
                                "error",
                                "-show_frames",
                                "-show_entries",
                                "frame=key_frame,best_effort_timestamp_time",
                                "-of",
                                "json",
                                str(joined),
                            ],
                            check=True,
                            capture_output=True,
                            timeout=30,
                        )  # noqa: S603 -- verified generated file
                        frames = json.loads(result.stdout)["frames"]
                        assert frames[0]["key_frame"] == 1
                        starts.append(Fraction(frames[0]["best_effort_timestamp_time"]))
                    boundaries.append(starts)
                assert len(boundaries[0]) == len(boundaries[1]) == 3
                assert all(
                    abs(left - right) < Fraction(1, 1000000)
                    for left, right in zip(*boundaries, strict=True)
                ), boundaries
            finally:
                for prefix in prefixes:
                    await encoder.discard(prefix)

    asyncio.run(exercise())


def test_low_resolution_text_and_dark_gradient_remain_distinguishable(
    backend_runtime, tmp_path, source_queue
):
    width, height = 160, 90
    pixels = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            shade = 12 if y < 40 else 8 + x * 180 // width
            start = (y * width + x) * 3
            pixels[start : start + 3] = bytes([shade]) * 3
    glyphs = (
        ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
        ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
        ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    )
    stroke_centers = []
    for index, glyph in enumerate(glyphs):
        for row, line in enumerate(glyph):
            for col, bit in enumerate(line):
                if bit == "1":
                    left, top = 8 + index * 18 + col * 3, 8 + row * 3
                    stroke_centers.append((left + 1, top + 1))
                    for dy in range(3):
                        for dx in range(3):
                            start = ((top + dy) * width + left + dx) * 3
                            pixels[start : start + 3] = b"\xf0\xf0\xf0"
    data = f"P6\n{width} {height}\n255\n".encode() + pixels

    async def exercise():
        async with source_environment(backend_runtime, data) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            await process(container, resources, tmp_path / "spool", source_queue)
            async with resources.sessions() as session:
                output = (
                    await session.scalars(
                        select(EncodedTrackRow).where(
                            EncodedTrackRow.source_upload_id == upload.id,
                            EncodedTrackRow.spec["kind"].astext == "video",
                        )
                    )
                ).one()
            assert output.status == "ready" and output.spec["crf"] == 34
            assert output.spec["width"] == width and output.spec["height"] == height
            playlist = await local_package(resources, output.artifact, tmp_path / "readability")
            rendered = run_ffmpeg(
                "-i", playlist, "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"
            ).stdout

            def light(x, y):
                return sum(rendered[(y * width + x) * 3 : (y * width + x) * 3 + 3]) / 3

            assert min(light(x, y) for x, y in stroke_centers) > 160
            assert light(70, 20) < 60
            gradient = [light(x, 60) for x in range(0, width, 10)]
            assert (
                max(gradient) - min(gradient) > 100
                and len({int(value) // 8 for value in gradient}) >= 8
            )
            assert sum(item["size"] for item in output.artifact["files"]) < len(data)

    asyncio.run(exercise())
