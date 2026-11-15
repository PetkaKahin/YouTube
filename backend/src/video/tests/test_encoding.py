"""Approved ladder, shared work and real private AV1/AAC output."""

import asyncio
import copy
import json
import shutil
import subprocess
from contextlib import AsyncExitStack
from fractions import Fraction
from unittest.mock import patch

import pytest
from sqlalchemy import select

from video.application.encoding import RepairEncoding
from video.domain.encoding import make_plan, pair
from video.domain.processing import SourceInspectionError
from video.infrastructure.encoder import S3MediaEncoder
from video.infrastructure.models import (
    EncodedTrackRow,
    MediaPlanRow,
    PlanTrackRow,
)
from video.public import CompleteUpload, GetVideoSettings
from video.tests.test_publications import invoke
from video.tests.test_source_processing import process, source_environment, stored

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


def inventory(
    width=1920,
    height=1080,
    rate=Fraction(30),
    channels=2,
    hdr=False,
    offset=Fraction(0),
    sar=Fraction(1),
):
    props = {
        "codec_type": "video",
        "width": pair(Fraction(width)),
        "height": pair(Fraction(height)),
        "sample_aspect_ratio": pair(sar),
        "avg_frame_rate": pair(rate),
        "r_frame_rate": pair(rate),
        "pix_fmt": "yuv420p10le" if hdr else "yuv420p",
        "color_transfer": "smpte2084" if hdr else "bt709",
    }
    tracks = [
        {
            "stream_index": 0,
            "fingerprint": "a" * 64,
            "properties": props,
            "disposition": {"default": "1"},
            "presentation_offset": [0, 1],
            "presentation_duration": [13, 1],
        }
    ]
    if channels:
        tracks.append(
            {
                "stream_index": 1,
                "fingerprint": "b" * 64,
                "properties": {"codec_type": "audio", "channels": pair(Fraction(channels))},
                "disposition": {"default": "1"},
                "presentation_offset": pair(offset),
                "presentation_duration": [13, 1],
            }
        )
    return {"version": 3, "tracks": tracks}


def videos(plan):
    return [track for track in plan.tracks if track.spec["kind"] == "video"]


@pytest.mark.parametrize(
    "width,height,expected",
    [
        (1920, 1080, [360, 480, 720, 1080]),
        (3840, 2160, [360, 480, 720, 1080, 1440, 2160]),
        (1080, 1920, [360, 480, 720, 1080]),
        (320, 240, [240]),
        (32, 48, [32]),
        (1080, 1080, [360, 480, 720, 1080]),
    ],
)
def test_ladder_never_upscales_and_preserves_display_aspect(width, height, expected):
    plan = make_plan(inventory(width, height))
    tracks = videos(plan)
    assert [track.spec["quality"] for track in tracks] == expected
    for track in tracks:
        spec = track.spec
        assert spec["width"] <= width and spec["height"] <= height
        assert spec["width"] % 2 == spec["height"] % 2 == 0
        assert Fraction(spec["width"], spec["height"]) * Fraction(*spec["sar"]) == Fraction(
            width, height
        )


def test_anamorphic_rotation_and_primary_selection_are_preserved():
    payload = inventory(1440, 1080, sar=Fraction(4, 3))
    payload["tracks"][0]["properties"]["side_data"] = [
        {"values": {"key": "rotation", "value": [90, 1]}, "children": []}
    ]
    cover = copy.deepcopy(payload["tracks"][0])
    cover["stream_index"] = 2
    cover["disposition"] = {"default": "1", "attached_pic": "1"}
    alternate = copy.deepcopy(payload["tracks"][0])
    alternate["stream_index"] = 3
    alternate["disposition"] = {"default": "0"}
    payload["tracks"].extend([cover, alternate])
    plan = make_plan(payload)
    assert plan.video_index == 0 and plan.audio_index == 1
    for track in videos(plan):
        assert Fraction(track.spec["width"], track.spec["height"]) * Fraction(
            *track.spec["sar"]
        ) == Fraction(9, 16)
    payload["tracks"][0]["disposition"]["default"] = "0"
    alternate["disposition"]["default"] = "1"
    assert make_plan(payload).video_index == 3


@pytest.mark.parametrize(
    "rate",
    [
        Fraction(24000, 1001),
        Fraction(30000, 1001),
        Fraction(60000, 1001),
        Fraction(120),
        Fraction(31),
        Fraction(321, 10),
        Fraction(999983, 100),
    ],
)
def test_fps_is_capped_without_rounding_lower_fractional_rates(rate):
    for track in videos(make_plan(inventory(rate=rate))):
        limit = 30 if track.spec["quality"] <= 480 else 60
        actual = Fraction(*track.spec["fps"])
        if rate <= limit:
            assert actual == rate
        else:
            assert limit - Fraction(1, 6) < actual <= limit
        assert Fraction(track.spec["gop"]) / actual == Fraction(*track.spec["segment_duration"])


@pytest.mark.parametrize("channels", [0, 1, 2, 6])
def test_audio_profiles_and_hdr_do_not_duplicate_equivalent_work(channels):
    plan = make_plan(inventory(3840, 2160, channels=channels, hdr=True))
    assert [(item["quality"], item["range"]) for item in plan.variants] == [
        (360, "sdr"),
        (480, "sdr"),
        (720, "sdr"),
        (1080, "sdr"),
        (1080, "pq"),
        (1440, "sdr"),
        (1440, "pq"),
        (2160, "sdr"),
        (2160, "pq"),
    ]
    audio = [track for track in plan.tracks if track.spec["kind"] == "audio"]
    assert {track.spec["bitrate"] for track in audio} == (
        {32000, 48000, 80000, 120000}
        if channels == 1
        else {64000, 96000, 160000, 240000}
        if channels
        else set()
    )
    assert len(audio) == (4 if channels else 0)
    assert all(track.spec["pix_fmt"] == "yuv420p10le" for track in videos(plan))
    assert len({track.key for track in plan.tracks}) == len(plan.tracks)


def test_audio_sync_context_prevents_incompatible_reuse():
    first = make_plan(inventory(offset=Fraction(1, 2)))
    second = make_plan(inventory(offset=Fraction(-1, 2)))
    assert {track.key for track in videos(first)} == {track.key for track in videos(second)}
    assert {track.key for track in first.tracks if track.spec["kind"] == "audio"}.isdisjoint(
        {track.key for track in second.tracks if track.spec["kind"] == "audio"}
    )


@pytest.mark.parametrize(
    "size,encoder", [((32, 48), "libaom-av1"), ((96, 32), "libaom-av1"), ((64, 64), "libsvtav1")]
)
def test_small_frame_exception_stays_av1_without_enlargement(size, encoder):
    plan = make_plan(inventory(*size, channels=0))
    assert videos(plan)[0].spec["encoder"] == encoder
    assert (videos(plan)[0].spec["width"], videos(plan)[0].spec["height"]) == size


@pytest.mark.parametrize("change", ["version", "no_video", "bad_fps", "bad_size"])
def test_invalid_inventory_cannot_create_a_plan(change):
    payload = inventory()
    if change == "version":
        payload["version"] = 2
    elif change == "no_video":
        payload["tracks"] = payload["tracks"][1:]
    elif change == "bad_fps":
        payload["tracks"][0]["properties"].update(avg_frame_rate=[0, 1], r_frame_rate=[0, 1])
    else:
        payload["tracks"][0]["properties"]["width"] = [-1, 1]
    with pytest.raises(SourceInspectionError, match="^invalid_media_plan$"):
        make_plan(payload)


@pytest.fixture(scope="module")
def encoding_samples(tmp_path_factory):
    root = tmp_path_factory.mktemp("encoding-sources")
    paths = {}
    for name, size, sound in (("tiny", "32x48", False), ("standard", "96x64", True)):
        output = root / f"{name}.mp4"
        args = [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={size}:rate=24:duration=0.5",
        ]
        if sound:
            args += ["-f", "lavfi", "-i", "sine=sample_rate=48000:duration=0.5"]
        args += [
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            "-metadata",
            "title=private-must-not-survive",
            str(output),
        ]
        subprocess.run(args, check=True, capture_output=True, timeout=30)  # noqa: S603 -- synthetic fixture
        paths[name] = output.read_bytes()
    paths["single"] = b"P6\n1 1\n255\n\xff\x00\x00"
    return paths


@pytest.mark.parametrize("name", ["tiny", "standard", "single"])
def test_real_queue_encodes_private_av1_and_aac_without_deleting_original(
    backend_runtime,
    encoding_samples,
    name,
    tmp_path,
    source_queue,
):
    async def exercise():
        async with source_environment(backend_runtime, encoding_samples[name]) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            await process(container, resources, tmp_path, source_queue)
            async with resources.sessions() as session:
                plan = await session.get(MediaPlanRow, upload.id)
                outputs = (
                    await session.scalars(
                        select(EncodedTrackRow)
                        .join(PlanTrackRow, PlanTrackRow.encoded_id == EncodedTrackRow.id)
                        .where(PlanTrackRow.upload_id == upload.id)
                    )
                ).all()
            assert plan.status == "planned"
            assert len(outputs) == (2 if name == "standard" else 1)
            assert {output.status for output in outputs} == {"ready"}, [
                (output.status, output.error_code) for output in outputs
            ]
            for output in outputs:
                artifact = output.artifact
                assert (
                    artifact["codec"].startswith("av01.")
                    if output.spec["kind"] == "video"
                    else artifact["codec"] == "mp4a.40.2"
                )
                assert "private-must-not-survive" not in json.dumps(artifact)
                for item in artifact["files"]:
                    obj = await asyncio.to_thread(
                        resources.objects.get_object,
                        Bucket=resources.bucket,
                        Key=artifact["prefix"] + item["name"],
                    )
                    data = obj["Body"].read()
                    obj["Body"].close()
                    assert len(data) == item["size"]
                assert artifact["files"][-1]["name"] == "index.m3u8"
            after = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert after.processing.value == "ready"
            assert before.added_at == after.added_at and before.updated_at == after.updated_at
            assert after.visibility == "private" and after.publication == before.publication
            assert (await stored(resources, upload.id)).inspection["version"] == 3

    asyncio.run(exercise())


def test_duplicate_accounts_share_jobs_and_ready_outputs(
    backend_runtime, encoding_samples, tmp_path, source_queue
):
    async def exercise():
        async with AsyncExitStack() as stack:
            contexts = [
                await stack.enter_async_context(
                    source_environment(backend_runtime, encoding_samples["standard"])
                )
                for _ in range(2)
            ]
            for container, _, owner, draft, upload in contexts:
                await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            container, resources, *_ = contexts[0]
            calls = []
            original = S3MediaEncoder.encode

            async def counted(encoder, claim):
                if claim.spec["kind"] in {"audio", "video"}:
                    calls.append(claim.encoded_id)
                return await original(encoder, claim)

            with patch.object(S3MediaEncoder, "encode", counted):
                await process(container, resources, tmp_path, source_queue)
            assert len(calls) == len(set(calls)) <= 2
            async with resources.sessions() as session:
                groups = [
                    (
                        await session.scalars(
                            select(PlanTrackRow.encoded_id)
                            .where(PlanTrackRow.upload_id == context[-1].id)
                            .order_by(PlanTrackRow.encoded_id)
                        )
                    ).all()
                    for context in contexts
                ]
                assert groups[0] == groups[1] and len(groups[0]) == 2
                completed = (
                    await session.scalars(
                        select(EncodedTrackRow).where(EncodedTrackRow.id.in_(groups[0]))
                    )
                ).all()
            for output in completed:
                assert output.status == "ready"
                for item in output.artifact["files"]:
                    await asyncio.to_thread(
                        resources.objects.head_object,
                        Bucket=resources.bucket,
                        Key=output.artifact["prefix"] + item["name"],
                    )
            calls.clear()
            with patch.object(S3MediaEncoder, "encode", counted):
                await invoke(container, RepairEncoding)
                await process(container, resources, tmp_path, source_queue)
            assert calls == []

    asyncio.run(exercise())
