"""Personal list purpose and exact account-bound composite boundaries."""

import base64
import binascii
import re
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from video.domain.publications import valid_identifier

SIZE = 20
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
TOKEN = struct.Struct(">BBBQqQ")


class LibraryKind(StrEnum):
    SUBSCRIPTIONS = "subscriptions"
    LIKED = "liked"
    HISTORY = "history"


CODES = {LibraryKind.SUBSCRIPTIONS: 1, LibraryKind.LIKED: 2, LibraryKind.HISTORY: 3}


class InvalidLibraryCursor(Exception):
    pass


@dataclass(frozen=True)
class LibraryBoundary:
    date: datetime
    id: int

    def encode(self, actor: int, kind: LibraryKind) -> str:
        delta = self.date.astimezone(UTC) - EPOCH
        micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        return (
            base64.urlsafe_b64encode(TOKEN.pack(1, 76, CODES[kind], actor, micros, self.id))
            .decode()
            .rstrip("=")
        )

    @classmethod
    def decode(cls, token: str, actor: int, kind: LibraryKind) -> LibraryBoundary:
        if re.fullmatch(r"[A-Za-z0-9_-]{36}", token) is None:
            raise InvalidLibraryCursor
        try:
            version, purpose, mode, scope, micros, identity = TOKEN.unpack(
                base64.b64decode(token, altchars=b"-_", validate=True)
            )
            if (
                version != 1
                or purpose != 76
                or mode != CODES[kind]
                or scope != actor
                or not valid_identifier(identity)
            ):
                raise InvalidLibraryCursor
            result = cls(EPOCH + timedelta(microseconds=micros), identity)
            if result.encode(actor, kind) != token:
                raise InvalidLibraryCursor
            return result
        except ValueError, OverflowError, struct.error, binascii.Error:
            raise InvalidLibraryCursor from None


@dataclass(frozen=True)
class LibraryCandidate:
    boundary: LibraryBoundary
    owner_id: int
