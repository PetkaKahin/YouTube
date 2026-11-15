"""Search values, literal matching and versioned context-bound pagination."""

import base64
import binascii
import json
import re
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from hashlib import sha256
from zoneinfo import ZoneInfo

from video.domain.feed import EPOCH, FeedAuthor, FeedCard
from video.domain.publications import valid_identifier

SEARCH_SIZE = 20
TOKEN = struct.Struct(">BqBQq32s")
MOSCOW = ZoneInfo("Europe/Moscow")


class InvalidSearch(Exception):
    pass


@dataclass(frozen=True)
class SearchQuery:
    text: str
    kind: str | None = None
    sort: str = "created"
    duration: str | None = None
    period: str | None = None
    features: tuple[str, ...] = ()

    def normalized(self) -> SearchQuery:
        value = self.text.strip()
        if (
            not 1 <= len(value) <= 100
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)
            or self.kind not in {None, "video", "channel", "playlist"}
            or self.sort not in {"created", "updated"}
            or self.duration not in {None, "short", "medium", "long"}
            or self.period not in {None, "today", "week", "month", "year"}
            or any(item not in {"hd", "4k", "hdr", "subtitles"} for item in self.features)
        ):
            raise InvalidSearch
        return SearchQuery(
            value,
            self.kind,
            self.sort,
            self.duration,
            self.period,
            tuple(sorted(set(self.features))),
        )

    def digest(self) -> bytes:
        return sha256(
            json.dumps(
                [
                    self.text.lower(),
                    self.kind,
                    self.sort,
                    self.duration,
                    self.period,
                    self.features,
                ],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode()
        ).digest()

    def start(self, anchor: datetime) -> datetime | None:
        if self.period is None:
            return None
        local = anchor.astimezone(MOSCOW).replace(hour=0, minute=0, second=0, microsecond=0)
        if self.period == "week":
            local -= timedelta(days=local.weekday())
        elif self.period == "month":
            local = local.replace(day=1)
        elif self.period == "year":
            local = local.replace(month=1, day=1)
        return local.astimezone(UTC)

    def duration_matches(self, value: Fraction) -> bool:
        return (
            self.duration is None
            or (self.duration == "short" and value < 180)
            or (self.duration == "medium" and 180 <= value <= 1200)
            or (self.duration == "long" and value > 1200)
        )


def micros(value: datetime) -> int:
    delta = value.astimezone(UTC) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


@dataclass(frozen=True)
class SearchPosition:
    date: datetime
    kind: int  # Stable cursor codes: video=1, channel=0, playlist=2.
    id: int

    @property
    def rank(self) -> int:
        return {1: 2, 0: 1, 2: 0}[self.kind]

    def source_id(self, kind: int) -> int | None:
        if kind == self.kind:
            return self.id
        return None if {1: 2, 0: 1, 2: 0}[kind] < self.rank else 0


@dataclass(frozen=True)
class SearchCursor:
    position: SearchPosition
    anchor: datetime

    def encode(self, query: SearchQuery) -> str:
        return (
            base64.urlsafe_b64encode(
                TOKEN.pack(
                    1,
                    micros(self.position.date),
                    self.position.kind,
                    self.position.id,
                    micros(self.anchor),
                    query.digest(),
                )
            )
            .decode()
            .rstrip("=")
        )

    @classmethod
    def decode(cls, token: str, query: SearchQuery) -> SearchCursor:
        if re.fullmatch(r"[A-Za-z0-9_-]{78}", token) is None:
            raise InvalidSearch
        try:
            version, date, kind, identifier, anchor, digest = TOKEN.unpack(
                base64.b64decode(token + "==", altchars=b"-_", validate=True)
            )
            if (
                version != 1
                or kind not in {0, 1, 2}
                or not valid_identifier(identifier)
                or digest != query.digest()
            ):
                raise InvalidSearch
            value = cls(
                SearchPosition(EPOCH + timedelta(microseconds=date), kind, identifier),
                EPOCH + timedelta(microseconds=anchor),
            )
            # A forged boundary may decode as a datetime yet overflow during
            # conversion to Moscow or a period start. Reject before any SQL.
            query.start(value.anchor)
            if value.encode(query) != token:
                raise InvalidSearch
            return value
        except ValueError, OverflowError, struct.error, binascii.Error:
            raise InvalidSearch from None


@dataclass(frozen=True)
class SearchChannel:
    id: int
    name: str
    tag: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SearchPlaylist:
    id: int
    title: str
    description: str
    created_at: datetime
    updated_at: datetime
    channel: FeedAuthor


@dataclass(frozen=True)
class SearchItem:
    position: SearchPosition
    video: FeedCard | None = None
    channel: SearchChannel | None = None
    playlist: SearchPlaylist | None = None


@dataclass(frozen=True)
class SearchPage:
    items: tuple[SearchItem, ...]
    next_cursor: str | None
