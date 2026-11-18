"""Discussion values, strict text validation and scoped canonical sort boundaries."""

import base64
import binascii
import re
import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

SIZE = 20
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
TOKEN = struct.Struct(">BBQQqQ")


class CommentAuthenticationRequired(Exception):
    pass


class DiscussionNotFound(Exception):
    pass


class InvalidComment(Exception):
    pass


class InvalidCommentCursor(Exception):
    pass


class CommentSort(StrEnum):
    NEWEST = "newest"
    POPULAR = "popular"


def valid_id(value: object) -> bool:
    return type(value) is int and 0 < value < 2**63


def validate_text(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 2000
        or not value.strip()
        or "\0" in value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise InvalidComment
    return value


@dataclass(frozen=True)
class CommentRecord:
    id: int
    video_id: int
    author_id: int
    text: str = field(repr=False)
    created_at: datetime
    liked: bool = False
    like_count: int = 0


@dataclass(frozen=True)
class CommentItem:
    id: int
    author_name: str
    text: str = field(repr=False)
    created_at: datetime
    liked: bool
    like_count: int


@dataclass(frozen=True)
class CommentPage:
    items: tuple[CommentItem, ...]
    next_cursor: str | None
    can_comment: bool


@dataclass(frozen=True)
class CommentLikeState:
    liked: bool
    like_count: int
    can_like: bool


@dataclass(frozen=True)
class CommentCursor:
    video_id: int
    sort: CommentSort
    like_count: int
    created_at: datetime
    id: int

    def encode(self) -> str:
        delta = self.created_at.astimezone(UTC) - EPOCH
        micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        return (
            base64.urlsafe_b64encode(
                TOKEN.pack(
                    1,
                    int(self.sort == CommentSort.POPULAR),
                    self.video_id,
                    self.like_count,
                    micros,
                    self.id,
                )
            )
            .decode()
            .rstrip("=")
        )

    @classmethod
    def decode(cls, token: object, video_id: int, sort: CommentSort) -> CommentCursor:
        if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{46}", token) is None:
            raise InvalidCommentCursor
        try:
            version, kind, video, count, micros, identity = TOKEN.unpack(
                base64.b64decode(token + "==", altchars=b"-_", validate=True)
            )
            if (
                (version, kind, video) != (1, int(sort == CommentSort.POPULAR), video_id)
                or not valid_id(identity)
                or count >= 2**63
                or (sort == CommentSort.NEWEST and count != 0)
            ):
                raise InvalidCommentCursor
            result = cls(video_id, sort, count, EPOCH + timedelta(microseconds=micros), identity)
            if result.encode() != token:
                raise InvalidCommentCursor
            return result
        except ValueError, OverflowError, struct.error, binascii.Error:
            raise InvalidCommentCursor from None
