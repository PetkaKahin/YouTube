"""Playlist values and pagination boundaries; no authority is stored in cursors."""

import base64
import binascii
import re
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

SIZE = 20
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
TOKEN = struct.Struct(">BBQQqQ")


class PlaylistError(Exception):
    pass


class PlaylistNotFound(Exception):
    pass


class PlaylistAuthenticationRequired(Exception):
    pass


def identifier(value: object) -> bool:
    return type(value) is int and 0 < value < 2**63


@dataclass(frozen=True)
class PlaylistMetadata:
    title: str
    description: str
    visibility: str

    def normalized(self) -> PlaylistMetadata:
        title = self.title.strip()
        description = self.description.replace("\r\n", "\n").replace("\r", "\n")
        if (
            not 1 <= len(title) <= 100
            or len(description) > 5000
            or self.visibility not in {"public", "unlisted", "private"}
            or any(c == "\x00" or 0xD800 <= ord(c) <= 0xDFFF for c in title + description)
        ):
            raise PlaylistError("invalid_metadata")
        return PlaylistMetadata(title, description, self.visibility)


@dataclass(frozen=True)
class Playlist:
    id: int
    channel_id: int
    title: str
    description: str
    visibility: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PlaylistOption:
    playlist: Playlist
    selected: bool


@dataclass(frozen=True)
class PlaylistList:
    items: tuple[PlaylistOption, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class PlaylistVideo:
    id: int
    title: str
    added_at: datetime
    duration_seconds: float
    channel_id: int
    channel_name: str
    channel_tag: str
    preview_ready: bool


@dataclass(frozen=True)
class PlaylistView:
    playlist: Playlist
    items: tuple[PlaylistVideo, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class PlaylistBoundary:
    date: datetime
    id: int

    def encode(self, mode: int, scope: int, owner: int) -> str:
        delta = self.date.astimezone(UTC) - EPOCH
        micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
        return (
            base64.urlsafe_b64encode(TOKEN.pack(1, mode, scope, owner, micros, self.id))
            .decode()
            .rstrip("=")
        )

    @classmethod
    def decode(cls, token: str, mode: int, scope: int, owner: int) -> PlaylistBoundary:
        if re.fullmatch(r"[A-Za-z0-9_-]{46}", token) is None:
            raise PlaylistError("invalid_cursor")
        try:
            version, actual_mode, actual_scope, actual_owner, micros, identity = TOKEN.unpack(
                base64.b64decode(token + "==", altchars=b"-_", validate=True)
            )
            if (version, actual_mode, actual_scope, actual_owner) != (
                1,
                mode,
                scope,
                owner,
            ) or not identifier(identity):
                raise PlaylistError("invalid_cursor")
            value = cls(EPOCH + timedelta(microseconds=micros), identity)
            if value.encode(mode, scope, owner) != token:
                raise PlaylistError("invalid_cursor")
            return value
        except ValueError, OverflowError, struct.error, binascii.Error:
            raise PlaylistError("invalid_cursor") from None
