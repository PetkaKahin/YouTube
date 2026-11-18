"""Publication-scoped delivery descriptors; temporary storage details stay private."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

MEDIA_OBJECT_KEY = (
    r"(?:encoded/[1-9][0-9]*/[0-9a-f]{32}|prepared/[0-9a-f]{64}/[0-9a-f]{64})/"
    r"(?:init\.mp4|segment_[0-9]{6}\.(?:m4s|vtt)|(?:candidate_[0-2]|image)\.webp)"
)


@dataclass(frozen=True)
class StoredMedia:
    key: str = field(repr=False)
    size: int
    media_type: str

    def __post_init__(self) -> None:
        if (
            re.fullmatch(
                MEDIA_OBJECT_KEY,
                self.key,
            )
            is None
            or type(self.size) is not int
            or self.size <= 0
        ):
            raise ValueError("Invalid prepared media object")


@dataclass(frozen=True)
class MediaPackage:
    playlist: str
    files: Mapping[str, StoredMedia] = field(repr=False)


@dataclass(frozen=True)
class MediaDelivery:
    master: str
    tracks: Mapping[str, MediaPackage] = field(repr=False)
    preview: StoredMedia | None = field(default=None, repr=False)


@dataclass(frozen=True)
class PlaylistDelivery:
    text: str
    media_type: str = "application/vnd.apple.mpegurl"


@dataclass(frozen=True)
class ObjectDelivery:
    redirect: str = field(repr=False)
    media_type: str


class PublicationError(Exception):
    pass
