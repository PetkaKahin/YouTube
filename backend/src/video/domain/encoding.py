"""Pure primary-track selection, geometry and approved encoding profiles."""

import json
import re
from dataclasses import dataclass, field
from fractions import Fraction
from hashlib import sha256
from math import ceil
from typing import cast
from uuid import UUID

from video.domain.orientation import display_geometry
from video.domain.processing import SourceInspectionError

PLAN_TASK = "video.plan_media"
ENCODE_TASK = "video.encode_track"
PROFILE = "av1-aac-v1"


def pair(value: Fraction) -> list[int]:
    return [value.numerator, value.denominator]


def rational(value: object) -> Fraction:
    if isinstance(value, list):
        parts = cast(list[object], value)
        if len(parts) == 2 and type(parts[0]) is int and type(parts[1]) is int:
            return Fraction(parts[0], parts[1])
    if isinstance(value, str):
        return Fraction(value.replace(":", "/"))
    raise ValueError


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError
    return cast(dict[str, object], value)


def integer(value: object) -> int:
    result = rational(value)
    if result.denominator != 1:
        raise ValueError
    return result.numerator


def signature(track: str, spec: dict[str, object]) -> str:
    return sha256(
        json.dumps(
            {"track": track, "spec": spec}, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class PlannedTrack:
    stream_index: int
    fingerprint: str
    spec: dict[str, object]
    priority: int

    @property
    def key(self) -> str:
        return signature(self.fingerprint, self.spec)


@dataclass(frozen=True)
class EncodingPlan:
    tracks: tuple[PlannedTrack, ...]
    variants: tuple[dict[str, object], ...]
    video_index: int
    audio_index: int | None


@dataclass(frozen=True)
class EncodingClaim:
    encoded_id: int
    token: UUID
    object_key: str = field(repr=False)
    size: int
    stream_index: int
    spec: dict[str, object]
    prefix: str = field(repr=False)


def primary(tracks: list[dict[str, object]], kind: str) -> dict[str, object] | None:
    candidates = [
        track
        for track in tracks
        if mapping(track["properties"]).get("codec_type") == kind
        and not (kind == "video" and mapping(track["disposition"]).get("attached_pic") == "1")
    ]
    return (
        min(
            candidates,
            key=lambda track: (
                mapping(track["disposition"]).get("default") != "1",
                int(cast(int, track["stream_index"])),
            ),
        )
        if candidates
        else None
    )


def side_fields(value: object) -> dict[str, object]:
    """Extract only server-generated side_datum fields, never construct a filter from text."""
    result: dict[str, object] = {}
    if isinstance(value, list):
        for item in cast(list[object], value):
            result.update(side_fields(item))
    elif isinstance(value, dict):
        item = cast(dict[str, object], value)
        attrs = mapping(item.get("values", {}))
        if isinstance(attrs.get("key"), str):
            result[cast(str, attrs["key"])] = attrs.get("value")
        result.update(attrs)
        result.update(side_fields(item.get("children", [])))
    return result


def make_plan(payload: dict[str, object]) -> EncodingPlan:
    try:
        return _make_plan(payload)
    except KeyError, ValueError, TypeError, ZeroDivisionError, OverflowError:
        raise SourceInspectionError("invalid_media_plan") from None


def _make_plan(payload: dict[str, object]) -> EncodingPlan:
    if payload.get("version") != 3 or not isinstance(payload.get("tracks"), list):
        raise ValueError
    tracks = [mapping(value) for value in cast(list[object], payload["tracks"])]
    video, audio = primary(tracks, "video"), primary(tracks, "audio")
    if video is None:
        raise ValueError
    properties = mapping(video["properties"])
    width, height = integer(properties["width"]), integer(properties["height"])
    sar_value = properties.get("sample_aspect_ratio", [1, 1])
    sar = Fraction(1) if sar_value in ("N/A", "0:1") else rational(sar_value)
    if sar <= 0:
        sar = Fraction(1)
    side = side_fields(properties.get("side_data", []))
    rotation = rational(side.get("rotation", properties.get("rotate", [0, 1])))
    source_w, source_h = width * sar, Fraction(height)
    geometry = display_geometry(width, height, sar, rotation, side.get("displaymatrix"))
    width, height, sar = geometry.width, geometry.height, geometry.sar
    frame_rate = (
        Fraction(0)
        if properties.get("avg_frame_rate") == "0/0"
        else rational(properties.get("avg_frame_rate", [0, 1]))
    )
    if frame_rate <= 0:
        frame_rate = rational(properties["r_frame_rate"])
    duration = rational(video["presentation_duration"])
    if min(width, height) < 1 or frame_rate <= 0 or not 0 < duration <= 3600:
        raise ValueError
    hdr = properties.get("color_transfer") in {"smpte2084", "arib-std-b67"} or "dv_profile" in side
    fmt = str(properties.get("pix_fmt", ""))
    depth = integer(properties.get("bits_per_raw_sample", [0, 1]))
    high_depth = (
        hdr
        or depth > 8
        or bool(re.search(r"(?:p|f|gray|xyz|rgb|bgr|rgba|bgra)(?:9|10|12|14|16|32|48|64)", fmt))
        or fmt.startswith(("p010", "p012", "p016"))
    )
    geometries: list[tuple[int, int, int]] = []
    displayed_w, displayed_h = Fraction(width) * sar, Fraction(height)
    for quality, bound_w, bound_h in (
        (360, 640, 360),
        (480, 854, 480),
        (720, 1280, 720),
        (1080, 1920, 1080),
        (1440, 2560, 1440),
        (2160, 3840, 2160),
    ):
        source_bound_w, source_bound_h = bound_w, bound_h
        if source_h > source_w:
            source_bound_w, source_bound_h = source_bound_h, source_bound_w
        eligible = min(source_bound_w / source_w, source_bound_h / source_h) <= 1
        if displayed_h > displayed_w:
            bound_w, bound_h = bound_h, bound_w
        scale = min(Fraction(bound_w) / displayed_w, Fraction(bound_h) / displayed_h)
        if scale <= 1 and (geometry.orientation is None or eligible):
            w, h = int(width * scale) // 2 * 2, int(height * scale) // 2 * 2
            if min(w, h) >= 2:
                geometries.append((quality, w, h))
    if not geometries:
        geometries = [
            (max(1, min(int(displayed_w), height)), max(1, width // 2 * 2), max(1, height // 2 * 2))
        ]
    planned: dict[str, PlannedTrack] = {}
    variants: list[dict[str, object]] = []
    highest_rate = min(
        frame_rate, max(30 if quality <= 480 else 60 for quality, _, _ in geometries)
    )
    segment_duration = Fraction(ceil(6 * highest_rate), 1) / highest_rate
    for rank, (quality, w, h) in enumerate(geometries):
        output_sar = displayed_w / displayed_h * Fraction(h, w)
        audio_key: str | None = None
        if audio is not None:
            channels = min(2, integer(mapping(audio["properties"])["channels"]))
            if channels <= 0:
                raise ValueError
            bitrate = (
                (
                    64000
                    if quality <= 360
                    else 96000
                    if quality <= 480
                    else 160000
                    if quality <= 720
                    else 240000
                )
                * channels
                // 2
            )
            spec: dict[str, object] = {
                "profile": PROFILE,
                "kind": "audio",
                "channels": channels,
                "bitrate": bitrate,
                "sample_rate": 48000,
                "duration": pair(duration),
                "offset": pair(
                    rational(audio["presentation_offset"]) - rational(video["presentation_offset"])
                ),
                "segment_duration": pair(segment_duration),
            }
            output = PlannedTrack(
                cast(int, audio["stream_index"]), str(audio["fingerprint"]), spec, -rank * 2
            )
            audio_key = output.key
            planned.setdefault(output.key, output)
        for color in ("sdr", "pq") if hdr and quality >= 1080 else ("sdr",):
            cap = 30 if quality <= 480 else 60
            output_rate = (
                frame_rate
                if frame_rate <= cap
                else Fraction(int(segment_duration * cap), 1) / segment_duration
            )
            spec = {
                "profile": PROFILE,
                "kind": "video",
                "encoder": "libaom-av1" if min(w, h) < 64 else "libsvtav1",
                "quality": quality,
                "width": w,
                "height": h,
                "sar": pair(output_sar),
                "fps": pair(output_rate),
                "gop": int(segment_duration * output_rate),
                "segment_duration": pair(segment_duration),
                "fps_cap": 30 if quality <= 480 else 60,
                "input_hdr": hdr,
                "range": color,
                "pix_fmt": "yuv420p10le" if high_depth else "yuv420p",
                "crf": 34
                if quality <= 360
                else 32
                if quality <= 480
                else 30
                if quality <= 720
                else 28,
                "duration": pair(duration),
            }
            if geometry.orientation is not None:
                spec["orientation"] = geometry.orientation.spec
            output = PlannedTrack(
                cast(int, video["stream_index"]), str(video["fingerprint"]), spec, -rank * 2 - 1
            )
            planned.setdefault(output.key, output)
            variants.append(
                {"quality": quality, "range": color, "video": output.key, "audio": audio_key}
            )
    return EncodingPlan(
        tuple(planned.values()),
        tuple(variants),
        cast(int, video["stream_index"]),
        cast(int, audio["stream_index"]) if audio is not None else None,
    )
