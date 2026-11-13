"""Publication identity and metadata, independent of storage and transport."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum


class Visibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


class PublicationState(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


class UploadState(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"


class ProcessingState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"


class ModerationState(StrEnum):
    VISIBLE = "visible"
    HIDDEN = "hidden"


class VideoError(Exception):
    def __init__(self, field: str, code: str) -> None:
        super().__init__(code)
        self.field = field
        self.code = code


class AuthenticationRequired(Exception):
    pass


class VideoNotFound(Exception):
    """Same public result for a missing publication and somebody else's settings."""


def valid_identifier(value: object) -> bool:
    return type(value) is int and 0 < value < 2**63


def validate_text(value: str, field: str) -> None:
    if any(char == "\x00" or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise VideoError(field, "invalid_characters")


@dataclass(frozen=True)
class VideoMetadata:
    title: str
    description: str
    visibility: str

    def normalized(self) -> VideoMetadata:
        title = self.title.strip()
        if not 1 <= len(title) <= 100:
            raise VideoError("title", "invalid_length")
        if len(self.description) > 5000:
            raise VideoError("description", "invalid_length")
        validate_text(title, "title")
        validate_text(self.description, "description")
        try:
            visibility = Visibility(self.visibility)
        except ValueError:
            raise VideoError("visibility", "invalid_visibility") from None
        return VideoMetadata(title, self.description, visibility.value)


@dataclass(frozen=True)
class VideoSettings:
    id: int
    owner_id: int
    title: str
    description: str
    visibility: Visibility
    publication: PublicationState
    upload: UploadState
    processing: ProcessingState
    moderation: ModerationState
    added_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def edit(self, metadata: VideoMetadata) -> VideoSettings:
        return replace(
            self,
            title=metadata.title,
            description=metadata.description,
            visibility=Visibility(metadata.visibility),
        )
