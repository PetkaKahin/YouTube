"""Owner-scoped publication lists; a cursor is only a stable sort boundary."""

import base64
import binascii
import re
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from video.domain.publications import PublicationState, VideoSettings, valid_identifier

SIZE = 20
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
TOKEN = struct.Struct(">BBBQqQ")


class InvalidVideoList(Exception):
    pass


@dataclass(frozen=True)
class OwnedVideoBoundary:
    created_at: datetime
    id: int

    def encode(self, owner: int, publication: PublicationState) -> str:
        delta = self.created_at.astimezone(UTC) - EPOCH
        micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        mode = 1 if publication is PublicationState.DRAFT else 2
        return (
            base64.urlsafe_b64encode(TOKEN.pack(1, 86, mode, owner, micros, self.id))
            .decode("ascii")
            .rstrip("=")
        )

    @classmethod
    def decode(cls, token: str, owner: int, publication: PublicationState) -> OwnedVideoBoundary:
        if re.fullmatch(r"[A-Za-z0-9_-]{36}", token) is None:
            raise InvalidVideoList
        try:
            version, purpose, mode, scope, micros, identity = TOKEN.unpack(
                base64.b64decode(token, altchars=b"-_", validate=True)
            )
            if (
                version != 1
                or purpose != 86
                or mode != (1 if publication is PublicationState.DRAFT else 2)
                or scope != owner
                or not valid_identifier(identity)
            ):
                raise InvalidVideoList
            result = cls(EPOCH + timedelta(microseconds=micros), identity)
            if result.encode(owner, publication) != token:
                raise InvalidVideoList
            return result
        except ValueError, OverflowError, struct.error, binascii.Error:
            raise InvalidVideoList from None


@dataclass(frozen=True)
class VideoSettingsPage:
    items: tuple[VideoSettings, ...]
    next_cursor: str | None
