"""Transfer policy; no SDK, HTTP, or shared-media ownership here."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

MAX_UPLOAD_SIZE = 5_000_000_000
PART_SIZE = 16 * 1024 * 1024
PARALLEL_PARTS = 4
URL_SECONDS = 15 * 60
IDLE_TIMEOUT = timedelta(hours=24)


class UploadError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StorageUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Transfer:
    id: int
    video_id: int
    object_key: str = field(repr=False)
    multipart_id: str | None = field(repr=False)
    size: int
    status: str
    activity_at: datetime
    original_released: bool = False

    @property
    def part_count(self) -> int:
        return (self.size + PART_SIZE - 1) // PART_SIZE

    def part_size(self, number: int) -> int:
        if not 1 <= number <= self.part_count:
            raise UploadError("invalid_part")
        return min(PART_SIZE, self.size - (number - 1) * PART_SIZE)


@dataclass(frozen=True)
class StoredPart:
    number: int
    size: int
    etag: str = field(repr=False)
    modified_at: datetime


@dataclass(frozen=True)
class StoredObject:
    size: int
    modified_at: datetime


@dataclass(frozen=True)
class PartLink:
    number: int
    size: int
    url: str = field(repr=False)


@dataclass(frozen=True)
class UploadPlan:
    id: int
    status: str
    size: int
    expires_at: datetime
    parts: tuple[PartLink, ...]
    part_size: int = PART_SIZE
    parallel_parts: int = PARALLEL_PARTS


def validate_size(value: int) -> None:
    if type(value) is not int or not 1 <= value <= MAX_UPLOAD_SIZE:
        raise UploadError("invalid_size")


def validate_parts(transfer: Transfer, parts: tuple[StoredPart, ...]) -> None:
    if len(parts) != transfer.part_count:
        raise UploadError("incomplete_upload")
    for number, part in enumerate(sorted(parts, key=lambda p: p.number), 1):
        if part.number != number or part.size != transfer.part_size(number):
            raise UploadError("invalid_parts")
