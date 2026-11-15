"""A canonical cursor is a sort boundary, never an authorisation credential."""

import base64
import binascii
import re
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from video.domain.publications import valid_identifier

FEED_SIZE = 20
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
CURSOR = struct.Struct(">BqQ")


class InvalidFeedCursor(Exception):
    pass


@dataclass(frozen=True)
class FeedPosition:
    added_at: datetime
    id: int

    def encode(self) -> str:
        delta = self.added_at.astimezone(UTC) - EPOCH
        micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        return base64.urlsafe_b64encode(CURSOR.pack(1, micros, self.id)).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, token: object) -> FeedPosition:
        if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{23}", token) is None:
            raise InvalidFeedCursor
        try:
            raw = base64.b64decode(token + "=", altchars=b"-_", validate=True)
            version, micros, identifier = CURSOR.unpack(raw)
            if version != 1 or not valid_identifier(identifier):
                raise InvalidFeedCursor
            value = cls(EPOCH + timedelta(microseconds=micros), identifier)
            if value.encode() != token:
                raise InvalidFeedCursor
            return value
        except ValueError, OverflowError, struct.error, binascii.Error:
            raise InvalidFeedCursor from None


CHANNEL_CURSOR = struct.Struct(">BQqQ")


@dataclass(frozen=True)
class ChannelFeedCursor:
    channel_id: int
    position: FeedPosition

    def encode(self) -> str:
        delta = self.position.added_at.astimezone(UTC) - EPOCH
        micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
        return (
            base64.urlsafe_b64encode(
                CHANNEL_CURSOR.pack(1, self.channel_id, micros, self.position.id)
            )
            .decode()
            .rstrip("=")
        )

    @classmethod
    def decode(cls, token: str, channel_id: int) -> ChannelFeedCursor:
        if re.fullmatch(r"[A-Za-z0-9_-]{34}", token) is None:
            raise InvalidFeedCursor
        try:
            version, scope, micros, identity = CHANNEL_CURSOR.unpack(
                base64.b64decode(token + "==", altchars=b"-_", validate=True)
            )
            if version != 1 or scope != channel_id or not valid_identifier(identity):
                raise InvalidFeedCursor
            result = cls(channel_id, FeedPosition(EPOCH + timedelta(microseconds=micros), identity))
            if result.encode() != token:
                raise InvalidFeedCursor
            return result
        except ValueError, OverflowError, struct.error, binascii.Error:
            raise InvalidFeedCursor from None


@dataclass(frozen=True)
class FeedAuthor:
    id: int
    name: str
    tag: str


@dataclass(frozen=True)
class FeedCandidate:
    position: FeedPosition
    owner_id: int
    title: str
    duration_seconds: float
    preview_ready: bool


@dataclass(frozen=True)
class FeedBatch:
    items: tuple[FeedCandidate, ...]
    after: FeedPosition | None
    exhausted: bool


@dataclass(frozen=True)
class FeedCard:
    id: int
    title: str
    added_at: datetime
    duration_seconds: float
    channel: FeedAuthor
    preview_ready: bool


@dataclass(frozen=True)
class FeedPage:
    items: tuple[FeedCard, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class FeedWindow:
    items: tuple[FeedCard, ...]
    after: FeedPosition | None
