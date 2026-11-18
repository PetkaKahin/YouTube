"""Safe technical administration values, fixed registry and opaque cursors."""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import cast

QUEUES = {
    "video.inspect_source": "media",
    "video.plan_media": "media",
    "video.encode_track": "media",
    "video.prepare_thumbnail": "media",
    "video.expire_uploads": "maintenance",
    "video.cleanup_originals": "maintenance",
}


class JobAdminAuthenticationRequired(Exception):
    pass


class JobAdminForbidden(Exception):
    pass


class JobAdminNotFound(Exception):
    pass


class InvalidJobRequest(Exception):
    pass


class InvalidJobCursor(Exception):
    pass


def valid_id(value: object) -> bool:
    return type(value) is int and 0 < value < 2**63


def selection_ids(value: tuple[int, ...] | None) -> list[int] | None:
    if value is None:
        return None
    if not value or any(not valid_id(item) for item in value):
        raise InvalidJobRequest
    return sorted(set(value))


@dataclass(frozen=True)
class ErrorCursor:
    at: datetime
    id: int

    def encode(self) -> str:
        return (
            base64.urlsafe_b64encode(
                json.dumps(
                    ["job-errors-v1", self.at.isoformat(), self.id], separators=(",", ":")
                ).encode()
            )
            .decode()
            .rstrip("=")
        )

    @classmethod
    def decode(cls, value: str | None) -> ErrorCursor | None:
        if value is None:
            return None
        try:
            if not 1 <= len(value) <= 256:
                raise ValueError
            payload: object = json.loads(
                base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
            )
            if not isinstance(payload, list):
                raise ValueError
            parts = cast(list[object], payload)
            if (
                len(parts) != 3
                or parts[0] != "job-errors-v1"
                or not isinstance(parts[1], str)
                or not valid_id(parts[2])
            ):
                raise ValueError
            at = datetime.fromisoformat(parts[1])
            if at.utcoffset() is None:
                raise ValueError
            return cls(at, cast(int, parts[2]))
        except ValueError, TypeError, binascii.Error, UnicodeError, OverflowError:
            raise InvalidJobCursor from None


def item_cursor(value: str | None, request_id: int) -> int:
    if value is None:
        return 0
    try:
        prefix, identifier, number = value.split(":")
        if (
            prefix != "job-retry-v1"
            or identifier != str(request_id)
            or str(int(number)) != number
            or not valid_id(int(number))
        ):
            raise ValueError
        return int(number)
    except ValueError, AttributeError:
        raise InvalidJobCursor from None
