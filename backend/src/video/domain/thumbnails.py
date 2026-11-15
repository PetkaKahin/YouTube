"""Thumbnail policy, stable random positions, and public owner values."""

from dataclasses import dataclass, field
from fractions import Fraction
from hashlib import sha256
from typing import cast
from uuid import UUID

from video.domain.encoding import (
    PlannedTrack,
    integer,
    mapping,
    pair,
    primary,
    rational,
    side_fields,
)
from video.domain.processing import SourceInspectionError

PREPARE_THUMBNAIL_TASK = "video.prepare_thumbnail"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
THUMBNAIL_PROFILE = "thumbnail-webp-80-v1"


class ThumbnailError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def image_geometry(width: int, height: int, sar: Fraction = Fraction(1)) -> tuple[int, int]:
    if min(width, height) < 1 or sar <= 0:
        raise ValueError
    displayed_width = width * sar
    factor = min(Fraction(1), Fraction(1280) / displayed_width, Fraction(720, height))
    return max(1, int(displayed_width * factor)), max(1, int(height * factor))


def thumbnail_plan(payload: dict[str, object]) -> PlannedTrack:
    try:
        tracks = [mapping(item) for item in cast(list[object], payload["tracks"])]
        video = primary(tracks, "video")
        if video is None:
            raise ValueError
        properties = mapping(video["properties"])
        width, height = integer(properties["width"]), integer(properties["height"])
        value = properties.get("sample_aspect_ratio", [1, 1])
        sar = Fraction(1) if value in ("N/A", "0:1") else rational(value)
        side = side_fields(properties.get("side_data", []))
        rotation = rational(side.get("rotation", properties.get("rotate", [0, 1])))
        if rotation.denominator != 1 or rotation % 90:
            raise ValueError
        if rotation % 180:
            width, height, sar = height, width, 1 / sar
        width, height = image_geometry(width, height, sar)
        duration = rational(video["presentation_duration"])
        if not 0 < duration <= 3600:
            raise ValueError
        fingerprint = str(video["fingerprint"])
        positions: list[list[int]] = []
        for slot in range(3):
            entropy = int.from_bytes(
                sha256(f"{THUMBNAIL_PROFILE}:{fingerprint}:{slot}".encode()).digest()[:8]
            )
            # A separate interior point in each third, persisted through the profile identity.
            position = duration * (
                Fraction(slot, 3) + Fraction(1, 30) + Fraction(entropy % 8001, 30000)
            )
            positions.append(pair(position))
        spec: dict[str, object] = {
            "profile": THUMBNAIL_PROFILE,
            "kind": "thumbnail_set",
            "width": width,
            "height": height,
            "positions": positions,
            "duration": pair(duration),
            "input_hdr": properties.get("color_transfer") in {"smpte2084", "arib-std-b67"}
            or "dv_profile" in side,
        }
        return PlannedTrack(cast(int, video["stream_index"]), fingerprint, spec, -1)
    except KeyError, ValueError, TypeError, ZeroDivisionError:
        raise SourceInspectionError("invalid_media_plan") from None


@dataclass(frozen=True)
class ThumbnailClaim:
    upload_id: int
    token: UUID
    object_key: str = field(repr=False)
    size: int
    prefix: str = field(repr=False)


@dataclass(frozen=True)
class PreparedThumbnail:
    fingerprint: str
    object_key: str = field(repr=False)
    size: int
    width: int
    height: int


@dataclass(frozen=True)
class ThumbnailOptions:
    status: str
    selected: str
    slot: int
    candidates: tuple[str, ...]
    current_url: str | None
    custom_status: str | None
    error_code: str | None
