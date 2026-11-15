"""Permission cursors contain numeric boundaries, never private addresses."""

import base64
import binascii
import re
import struct
from dataclasses import dataclass, field

from video.domain.publications import valid_identifier

SIZE = 20
TOKEN = struct.Struct(">BBQQQ")


class InvalidPermissionCursor(Exception):
    pass


@dataclass(frozen=True)
class PermissionGrant:
    id: int
    email: str = field(repr=False)


@dataclass(frozen=True)
class PermissionPage:
    emails: tuple[str, ...] = field(repr=False)
    next_cursor: str | None


@dataclass(frozen=True)
class PermissionCursor:
    video_id: int
    owner_id: int
    id: int

    def encode(self) -> str:
        return (
            base64.urlsafe_b64encode(TOKEN.pack(1, 1, self.video_id, self.owner_id, self.id))
            .decode()
            .rstrip("=")
        )

    @classmethod
    def decode(cls, token: str, video_id: int, owner_id: int) -> PermissionCursor:
        if re.fullmatch(r"[A-Za-z0-9_-]{35}", token) is None:
            raise InvalidPermissionCursor
        try:
            version, kind, video, owner, identity = TOKEN.unpack(
                base64.b64decode(token + "=", altchars=b"-_", validate=True)
            )
            if (version, kind, video, owner) != (1, 1, video_id, owner_id) or not valid_identifier(
                identity
            ):
                raise InvalidPermissionCursor
            result = cls(video_id, owner_id, identity)
            if result.encode() != token:
                raise InvalidPermissionCursor
            return result
        except ValueError, struct.error, binascii.Error:
            raise InvalidPermissionCursor from None
