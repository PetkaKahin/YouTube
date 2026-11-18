"""A subscriber-scoped channel creation boundary, independent of follow time."""

import base64
import binascii
import re
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from channel.domain.pages import ChannelProfile

SIZE = 20
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
TOKEN = struct.Struct(">BBQqQ")


class InvalidSubscriptionCursor(Exception):
    pass


@dataclass(frozen=True)
class SubscriptionBoundary:
    date: datetime
    id: int

    def encode(self, actor: int) -> str:
        delta = self.date.astimezone(UTC) - EPOCH
        micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        return (
            base64.urlsafe_b64encode(TOKEN.pack(1, 67, actor, micros, self.id)).decode().rstrip("=")
        )

    @classmethod
    def decode(cls, token: str, actor: int) -> SubscriptionBoundary:
        if re.fullmatch(r"[A-Za-z0-9_-]{35}", token) is None:
            raise InvalidSubscriptionCursor
        try:
            version, purpose, scope, micros, identity = TOKEN.unpack(
                base64.b64decode(token + "=", altchars=b"-_", validate=True)
            )
            if version != 1 or purpose != 67 or scope != actor or not 0 < identity < 2**63:
                raise InvalidSubscriptionCursor
            result = cls(EPOCH + timedelta(microseconds=micros), identity)
            if result.encode(actor) != token:
                raise InvalidSubscriptionCursor
            return result
        except ValueError, OverflowError, struct.error, binascii.Error:
            raise InvalidSubscriptionCursor from None


@dataclass(frozen=True)
class SubscribedChannelPage:
    items: tuple[ChannelProfile, ...]
    next_cursor: str | None
