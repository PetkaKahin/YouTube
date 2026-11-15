"""Completeness, partial failure and safe master assembly from committed manifests."""

import copy
from dataclasses import asdict

import pytest

from video.domain.publications import ProcessingState
from video.domain.readiness import ProcessingStatus, QualityStatus
from video.infrastructure.readiness import prepare


def output(encoded_id, kind="video", quality=360, color="sdr", durations=None):
    durations = durations or [[6, 1], [1, 1]]
    extension = "vtt" if kind == "subtitle" else "m4s"
    names = [f"segment_{index:06d}.{extension}" for index in range(len(durations))]
    names += (["init.mp4"] if kind != "subtitle" else []) + ["index.m3u8"]
    return {
        "id": encoded_id,
        "status": "ready",
        "error_code": None,
        "spec": {
            "kind": kind,
            "quality": quality,
            "range": color,
            "width": 640,
            "height": 360,
            "fps": [30, 1],
        },
        "artifact": {
            "prefix": f"encoded/{encoded_id}/{'a' * 32}/",
            "kind": kind,
            "files": [{"name": name, "size": 100, "sha256": "b" * 64} for name in names],
            "segments": durations,
            "target_duration": 6,
            "presentation_origin": [0, 1],
            "configuration": "1190" if kind == "audio" else "81004c00",
            "metadata": {
                "codec_name": "av1",
                "width": 640,
                "height": 360,
                "verified_frame_rate": "30",
                "color_primaries": "bt2020" if color == "pq" else "bt709",
                "color_transfer": "smpte2084" if color == "pq" else "bt709",
                "color_space": "bt2020nc" if color == "pq" else "bt709",
                "color_range": "tv",
            },
        },
    }


def snapshot():
    return {
        "upload": "uploaded",
        "processing": "processing",
        "source": {"status": "inspected"},
        "plan": {
            "status": "planned",
            "config": {
                "variants": [
                    {"quality": 360, "range": "sdr", "video": 1, "audio": 3},
                    {"quality": 1080, "range": "pq", "video": 2, "audio": 3},
                ],
                "thumbnail": 4,
            },
        },
        "outputs": [
            output(1),
            output(2, quality=1080, color="pq"),
            output(3, "audio"),
            {"id": 4, "status": "failed", "error_code": "thumbnail_failed"},
        ],
        "subtitles": [],
    }


@pytest.mark.parametrize(
    "statuses,expected,ready",
    [
        (("ready", "pending", "ready"), "processing", [360]),
        (("ready", "running", "ready"), "processing", [360]),
        (("ready", "failed", "ready"), "partial", [360]),
        (("failed", "failed", "ready"), "failed", []),
        (("ready", "ready", "failed"), "failed", []),
        (("ready", "ready", "pending"), "processing", []),
        (("ready", "ready", "ready"), "ready", [360, 1080]),
        (("failed", "ready", "pending"), "processing", []),
    ],
)
def test_quality_dependencies_and_overall_state(statuses, expected, ready):
    data = snapshot()
    for item, status in zip(data["outputs"][:3], statuses, strict=True):
        item["status"] = status
        if status != "ready":
            item["artifact"] = None
    result = prepare(data)
    assert result.status.processing.value == expected
    assert [item.quality for item in result.status.available_qualities] == ready
    assert (result.master is not None) == bool(ready)
    assert result.status.thumbnail.status == "failed"
    if result.master:
        assert "mp4a.40.2" in result.master and "av01." in result.master
        assert "FRAME-RATE=30.000" in result.master
        assert "encoded/" not in result.master and "http" not in result.master
        for index, item in enumerate(data["outputs"][:2], 1):
            assert (f"tracks/{index}/index.m3u8" in result.master) == (
                item["spec"]["quality"] in ready
            )
    serialized = str(asdict(result.status))
    assert not any(
        name in serialized for name in ("artifact", "prefix", "encoded_id", "job_id", "sha256")
    )


@pytest.mark.parametrize(
    "damage",
    [
        "missing_file",
        "zero_size",
        "digest",
        "path",
        "prefix",
        "configuration",
        "color",
        "fps",
        "duration",
        "duplicate_file",
        "wrong_audio",
    ],
)
def test_invalid_descriptor_never_opens_a_quality(damage):
    data = snapshot()
    artifact = data["outputs"][1]["artifact"]
    if damage == "missing_file":
        artifact["files"].pop()
    elif damage == "zero_size":
        artifact["files"][0]["size"] = 0
    elif damage == "digest":
        artifact["files"][0]["sha256"] = "bad"
    elif damage == "path":
        artifact["files"][0]["name"] = "../../secret"
    elif damage == "prefix":
        artifact["prefix"] = "https://metadata.internal/"
    elif damage == "configuration":
        artifact["configuration"] = "abcd"
    elif damage == "color":
        artifact["metadata"]["color_transfer"] = "bt709"
    elif damage == "fps":
        artifact["metadata"]["verified_frame_rate"] = "60"
    elif damage == "duration":
        artifact["segments"][0] = [1, 0]
    elif damage == "duplicate_file":
        artifact["files"].append(copy.deepcopy(artifact["files"][0]))
    else:
        data["outputs"][2]["artifact"]["configuration"] = "abcd"
    result = prepare(data)
    assert result.status.qualities[1].status == "failed"
    assert result.status.qualities[1].error_code == "invalid_encoded_media"
    assert result.status.processing.value == ("failed" if damage == "wrong_audio" else "partial")
    if result.master:
        assert "tracks/2/" not in result.master and "tracks/1/" in result.master


def test_video_without_sound_does_not_wait_for_nonexistent_aac():
    data = snapshot()
    data["plan"]["config"]["variants"] = [data["plan"]["config"]["variants"][0]]
    data["plan"]["config"]["variants"][0]["audio"] = None
    data["outputs"] = data["outputs"][:1]
    result = prepare(data)
    assert result.status.processing.value == "ready"
    assert "mp4a" not in result.master and "TYPE=AUDIO" not in result.master


@pytest.mark.parametrize(
    "qualities,expected",
    [
        ([(2160, "sdr"), (1080, "pq")], (True, True, True, False)),
        ([(2160, "pq")], (True, True, True, True)),
        ([(720, "sdr")], (True, False, False, False)),
        ([(480, "sdr")], (False, False, False, False)),
    ],
)
def test_flags_use_ready_results_and_4k_hdr_requires_one_variant(qualities, expected):
    value = ProcessingStatus(
        "uploaded",
        ProcessingState.READY,
        qualities=tuple(QualityStatus(q, color, "ready") for q, color in qualities)
        + (QualityStatus(2160, "pq", "pending"),),
    )
    assert (value.hd, value.uhd, value.hdr, value.uhd_hdr) == expected


def test_variant_specific_subtitle_groups_keep_their_own_timeline_and_sanitize_language():
    data = snapshot()
    data["outputs"].extend(
        [output(5, "subtitle"), output(6, "subtitle", durations=[[5, 1], [2, 1]])]
    )
    data["outputs"][1]["artifact"]["segments"] = [[5, 1], [2, 1]]
    data["subtitles"] = [
        {
            "stream_index": 7,
            "video_encoded_id": video_id,
            "encoded_id": encoded_id,
            "status": "queued",
            "language": '"\nURI="https://internal/',
        }
        for video_id, encoded_id in [(1, 5), (2, 6)]
    ]
    result = prepare(data)
    assert result.status.processing.value == "ready"
    assert {item.status for item in result.status.subtitles} == {"ready"}
    assert 'GROUP-ID="subtitles"' in result.master and 'GROUP-ID="subtitles-1"' in result.master
    assert "tracks/5/index.m3u8" in result.master and "tracks/6/index.m3u8" in result.master
    assert "https://" not in result.master and "LANGUAGE" not in result.master
    data["outputs"][-1]["artifact"]["presentation_origin"] = [1, 1]
    result = prepare(data)
    assert result.status.processing.value == "ready"
    assert result.status.subtitles[1].error_code == "invalid_subtitle_timeline"
    assert "tracks/6/" not in result.master


@pytest.mark.parametrize("stage", ["source", "plan"])
def test_initial_failure_has_safe_reason_and_no_master(stage):
    data = snapshot()
    data["plan"]["status"] = "failed" if stage == "plan" else "pending"
    data[stage] = {"status": "failed", "error_code": "postgresql://private-secret"}
    result = prepare(data)
    assert result.status.processing.value == "failed" and result.master is None
    assert result.status.error_code == "inspection_failed"
