"""Full frame retention, display aspect, safe transforms and real queued rotation."""

import asyncio
import copy
import json
import shutil
import subprocess
from fractions import Fraction
from math import hypot

import pytest
from sqlalchemy import select

from video.domain.encoding import make_plan
from video.domain.orientation import AffineOrientation, display_geometry, stretch
from video.domain.processing import SourceInspectionError
from video.domain.thumbnails import thumbnail_plan
from video.infrastructure.models import EncodedTrackRow, PlanTrackRow
from video.infrastructure.orientation import input_orientation, orientation_filter
from video.public import CompleteUpload, GetThumbnailImage, GetThumbnails, GetVideoSettings
from video.tests.test_encoding import inventory, videos
from video.tests.test_encoding_media import local_package, run_ffmpeg
from video.tests.test_publications import invoke
from video.tests.test_source_processing import process, source_environment
from video.tests.test_thumbnails import upload_image

pytestmark = pytest.mark.usefixtures("video_media_cleanup")

MATRIX_45 = "00000000: 46340 -46340 0 00000001: 46340 46340 0 00000002: 0 0 1073741824"


def oriented_payload(width=1920, height=1080, *, angle=Fraction(45), sar=Fraction(1)):
    payload = inventory(width, height, channels=0, sar=sar)
    payload["tracks"][0]["properties"]["side_data"] = [
        {"values": {"rotation": [angle.numerator, angle.denominator]}}
    ]
    return payload


@pytest.mark.parametrize("angle", [Fraction(45), Fraction(-45), Fraction(273, 2), Fraction(725)])
@pytest.mark.parametrize("sar", [Fraction(1), Fraction(4, 3), Fraction(3, 4)])
def test_full_canvas_preserves_source_resolution_cap_and_shared_thumbnail_transform(angle, sar):
    payload = oriented_payload(angle=angle, sar=sar)
    plan = make_plan(payload)
    thumbnail = thumbnail_plan(payload)
    original = make_plan(inventory(1920, 1080, channels=0, sar=sar))
    assert {item["quality"] for item in plan.variants} <= {
        item["quality"] for item in original.variants
    }
    for track in videos(plan):
        transform = AffineOrientation.parse(track.spec["orientation"])
        assert transform.spec == thumbnail.spec["orientation"]
        assert stretch(transform.matrix) <= 1 + 1e-12
        w, h = transform.bounds
        assert track.spec["width"] <= w and track.spec["height"] <= h
        assert Fraction(track.spec["width"], track.spec["height"]) * Fraction(
            *track.spec["sar"]
        ) == Fraction(w, h)


def test_padding_alone_cannot_promote_a_sub360_source_to_360p():
    plan = make_plan(oriented_payload(320, 240))
    assert len(plan.variants) == 1 and plan.variants[0]["quality"] != 360
    assert videos(plan)[0].spec["width"] <= 398


def test_rotated_1080p_does_not_gain_1440p_from_black_corners():
    assert [item["quality"] for item in make_plan(oriented_payload()).variants] == [
        360,
        480,
        720,
        1080,
    ]


def test_native_display_matrix_is_used_including_a_reflection():
    geometry = display_geometry(128, 72, Fraction(4, 3), Fraction(45), MATRIX_45)
    a, b, c, d = geometry.orientation.matrix
    assert a > 0 and b > 0 and c < 0 and d > 0
    assert a / b == -c / d == Fraction(4, 3)
    mirrored = display_geometry(
        128,
        72,
        Fraction(1),
        Fraction(0),
        "00000000: -65536 0 0 00000001: 0 65536 0 00000002: 0 0 1073741824",
    )
    assert mirrored.orientation.matrix == (-1, 0, 0, 1)


def test_cardinal_profiles_keep_their_identity_and_new_transforms_change_it():
    for angle in (0, 90, 180, 270):
        payload = oriented_payload(angle=Fraction(angle))
        plain = make_plan(payload)
        assert all("orientation" not in track.spec for track in plain.tracks)
        coefficients = {
            0: (65536, 0, 0, 65536),
            90: (0, -65536, 65536, 0),
            180: (-65536, 0, 0, -65536),
            270: (0, 65536, -65536, 0),
        }[angle]
        a, c, b, d = coefficients
        payload["tracks"][0]["properties"]["side_data"][0]["values"]["displaymatrix"] = (
            f"00000000: {a} {c} 0 00000001: {b} {d} 0 00000002: 0 0 1073741824"
        )
        assert [item.key for item in make_plan(payload).tracks] == [
            item.key for item in plain.tracks
        ]
    first = videos(make_plan(oriented_payload(angle=Fraction(45))))[0]
    second = videos(make_plan(oriented_payload(angle=Fraction(-45))))[0]
    assert first.key != second.key


@pytest.mark.parametrize(
    "matrix",
    [
        "movie;movie=https://invalid.example/image",
        MATRIX_45.replace("46340 -46340 0", "46340 -46340 1"),
        MATRIX_45.replace("46340 -46340 0", "0 0 0").replace("46340 46340 0", "0 0 0"),
        MATRIX_45.replace("1073741824", "0"),
        MATRIX_45.replace("46340", "2147483648"),
    ],
)
def test_invalid_matrices_fail_before_a_plan_or_filter_can_be_created(matrix):
    payload = oriented_payload()
    payload["tracks"][0]["properties"]["side_data"][0]["values"]["displaymatrix"] = matrix
    for planner in (make_plan, thumbnail_plan):
        with pytest.raises(SourceInspectionError, match="^invalid_media_plan$"):
            planner(payload)


@pytest.mark.parametrize("change", ["expression", "upscale", "singular", "version", "zero"])
def test_corrupt_persisted_transforms_cannot_reach_ffmpeg(change):
    spec = copy.deepcopy(display_geometry(128, 72, Fraction(1), Fraction(45)).orientation.spec)
    if change == "expression":
        spec["matrix"][0] = "1, movie=bad"
    elif change == "upscale":
        spec["matrix"] = [[2, 1], [0, 1], [0, 1], [2, 1]]
    elif change == "singular":
        spec["matrix"] = [[1, 1]] * 4
    elif change == "zero":
        spec["width"] = 0
    else:
        spec["profile"] = "affine-v2"
    for renderer in (orientation_filter, lambda value: input_orientation(value, 0)):
        with pytest.raises(ValueError):
            renderer(spec)


COLORS = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))
CENTERS = ((9, 9), (119, 9), (9, 63), (119, 63))


def corner_image():
    result = bytearray(128 * 72 * 3)
    for color, (cx, cy) in zip(COLORS, CENTERS, strict=True):
        for y in range(cy - 8, cy + 8):
            for x in range(cx - 8, cx + 8):
                result[(y * 128 + x) * 3 : (y * 128 + x + 1) * 3] = bytes(color)
    return b"P6\n128 72\n255\n" + result


def colored_points(frame, width, color):
    return [
        (index // 3 % width, index // 3 // width)
        for index in range(0, len(frame), 3)
        if all(
            value > 170 if expected else value < 80
            for value, expected in zip(frame[index : index + 3], color, strict=True)
        )
    ]


@pytest.mark.parametrize("sar", [Fraction(1), Fraction(4, 3), Fraction(3, 4)])
@pytest.mark.parametrize("angle", [Fraction(45), Fraction(-45), Fraction(31, 2)])
def test_real_affine_render_retains_every_corner_at_its_display_position(tmp_path, sar, angle):
    source = tmp_path / "corners.ppm"
    source.write_bytes(corner_image())
    orientation = display_geometry(128, 72, sar, angle).orientation
    w, h = orientation.bounds
    output = run_ffmpeg(
        "-filter_threads",
        "1",
        "-i",
        source,
        "-vf",
        orientation_filter(orientation.spec) + "format=rgb24",
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-",
    ).stdout
    assert len(output) == w * h * 3
    a, b, c, d = map(float, orientation.matrix)
    for color, (x, y) in zip(COLORS, CENTERS, strict=True):
        points = colored_points(output, w, color)
        # A full 16x16 marker, reduced only by the source's non-square pixel geometry.
        assert 256 * abs(a * d - b * c) * 0.75 <= len(points) <= 270
        actual_x = sum(x for x, _ in points) / len(points)
        actual_y = sum(y for _, y in points) / len(points)
        expected_x = w / 2 + a * (x - 64) + b * (y - 36)
        expected_y = h / 2 + c * (x - 64) + d * (y - 36)
        assert hypot(actual_x - expected_x, actual_y - expected_y) < 2


@pytest.mark.parametrize("hdr", [False, True])
def test_real_queue_rotates_video_and_all_thumbnail_paths_without_double_rotation(
    backend_runtime, tmp_path, source_queue, hdr
):
    image = tmp_path / "corners.ppm"
    image.write_bytes(corner_image())
    base, source = tmp_path / "base.mov", tmp_path / "rotated.mov"
    run_ffmpeg(
        "-loop",
        "1",
        "-framerate",
        "4",
        "-i",
        image,
        "-t",
        "0.5",
        "-vf",
        "format=yuv420p10le,setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc"
        if hdr
        else "format=rgb24",
        "-c:v",
        "v210" if hdr else "png",
        base,
    )
    run_ffmpeg("-display_rotation:0", "45", "-i", base, "-c", "copy", source)

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
            settings = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert settings.processing.value == "ready"
            async with resources.sessions() as session:
                outputs = (
                    await session.scalars(
                        select(EncodedTrackRow)
                        .join(PlanTrackRow, PlanTrackRow.encoded_id == EncodedTrackRow.id)
                        .where(PlanTrackRow.upload_id == upload.id)
                    )
                ).all()
            assert len(outputs) == 1 and outputs[0].status == "ready"
            output = outputs[0]
            assert "orientation" in output.spec
            assert output.spec["input_hdr"] is hdr
            assert output.artifact["metadata"]["pix_fmt"] == ("yuv420p10le" if hdr else "yuv420p")
            local = await local_package(resources, output.artifact, tmp_path / "encoded")
            probe = subprocess.run(  # noqa: S603 -- verified server HLS in isolated fixture
                [
                    shutil.which("ffprobe"),
                    "-v",
                    "error",
                    "-show_streams",
                    "-of",
                    "json",
                    str(local),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            stream = json.loads(probe.stdout)["streams"][0]
            assert not any("rotation" in item for item in stream.get("side_data_list", []))
            frame = run_ffmpeg(
                "-i", local, "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"
            ).stdout
            w, h = output.spec["width"], output.spec["height"]
            assert len(frame) == w * h * 3
            if not hdr:
                transform = AffineOrientation.parse(output.spec["orientation"])
                a, b, c, d = map(float, transform.matrix)
                for color, (x, y) in zip(COLORS, CENTERS, strict=True):
                    points = colored_points(frame, w, color)
                    assert len(points) > 130
                    actual_x = sum(x for x, _ in points) / len(points)
                    actual_y = sum(y for _, y in points) / len(points)
                    assert (
                        hypot(
                            actual_x - (w / 2 + a * (x - 64) + b * (y - 36)),
                            actual_y - (h / 2 + c * (x - 64) + d * (y - 36)),
                        )
                        < 3
                    )
            options = await invoke(container, GetThumbnails, owner.id, draft.id)
            assert options.status == "ready" and len(options.candidates) == 3
            thumb = tmp_path / "generated.webp"
            thumb.write_bytes(await invoke(container, GetThumbnailImage, owner.id, draft.id))
            thumb_frame = run_ffmpeg(
                "-i", thumb, "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"
            ).stdout
            if not hdr:
                for color in COLORS:
                    assert len(colored_points(thumb_frame, 144, color)) > 130
            # A single-frame container exercises the same geometry for an owner's image.
            still, rotated_still = tmp_path / "still.mov", tmp_path / "rotated-still.mov"
            run_ffmpeg("-i", image, "-frames:v", "1", "-c:v", "png", still)
            run_ffmpeg("-display_rotation:0", "45", "-i", still, "-c", "copy", rotated_still)
            await upload_image(container, resources, owner.id, draft.id, rotated_still.read_bytes())
            await process(container, resources, tmp_path / "custom-spool", source_queue)
            custom = await invoke(container, GetThumbnails, owner.id, draft.id)
            assert custom.custom_status == "ready" and custom.selected == "custom"
            own_image = tmp_path / "custom.webp"
            own_image.write_bytes(await invoke(container, GetThumbnailImage, owner.id, draft.id))
            own_frame = run_ffmpeg(
                "-i", own_image, "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"
            ).stdout
            assert len(own_frame) == 144 * 144 * 3
            for color in COLORS:
                assert len(colored_points(own_frame, 144, color)) > 130
            thumb.write_bytes(await invoke(container, GetThumbnailImage, owner.id, draft.id))
            custom_frame = run_ffmpeg(
                "-i", thumb, "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"
            ).stdout
            for color in COLORS:
                assert len(colored_points(custom_frame, 144, color)) > 130

    asyncio.run(exercise())
