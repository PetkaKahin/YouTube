"""Report revisions, closed reason values and an oldest-first queue boundary."""

import base64
import binascii
import re
import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

SIZE = 20
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
TOKEN = struct.Struct(">BBqQ")


class ModerationAuthenticationRequired(Exception):
    pass


class ModerationForbidden(Exception):
    pass


class ReportNotFound(Exception):
    pass


class InvalidReport(Exception):
    pass


class InvalidReportCursor(Exception):
    pass


class ReportConflict(Exception):
    pass


class ReviewMediaUnavailable(Exception):
    pass


def valid_id(value: object) -> bool:
    return type(value) is int and 0 < value < 2**63


def actor_id(value: int | None) -> int:
    if value is None or not valid_id(value):
        raise ModerationAuthenticationRequired
    return value


class Reason(StrEnum):
    SPAM = "spam"
    HARASSMENT = "harassment"
    VIOLENCE = "violence"
    SEXUAL_CONTENT = "sexual_content"
    COPYRIGHT = "copyright"
    OTHER = "other"

    @classmethod
    def parse(cls, value: object) -> Reason:
        if not isinstance(value, str):
            raise InvalidReport
        try:
            return cls(value)
        except ValueError:
            raise InvalidReport from None


@dataclass(frozen=True)
class DecisionCommand:
    revision: int
    action: str
    reason: object = None

    def validated(self) -> DecisionCommand:
        if not valid_id(self.revision) or self.action not in ("dismiss", "hide"):
            raise InvalidReport
        if self.action == "hide":
            return DecisionCommand(self.revision, self.action, Reason.parse(self.reason))
        if self.reason is not None:
            raise InvalidReport
        return self


@dataclass(frozen=True)
class ReportDecision:
    revision: int
    actor_id: int
    action: str
    reason: Reason | None
    created_at: datetime


@dataclass(frozen=True)
class Report:
    id: int
    video_id: int
    reporter_id: int = field(repr=False)
    revision: int
    reason: Reason
    state: str
    submitted_at: datetime
    created_at: datetime
    decision: ReportDecision | None


@dataclass(frozen=True)
class ReportPage:
    items: tuple[Report, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class ReportCursor:
    submitted_at: datetime
    id: int

    def encode(self) -> str:
        delta = self.submitted_at.astimezone(UTC) - EPOCH
        micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
        return base64.urlsafe_b64encode(TOKEN.pack(1, 1, micros, self.id)).decode().rstrip("=")

    @classmethod
    def decode(cls, token: object) -> ReportCursor:
        if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{24}", token) is None:
            raise InvalidReportCursor
        try:
            version, kind, micros, identity = TOKEN.unpack(
                base64.b64decode(token, altchars=b"-_", validate=True)
            )
            if (version, kind) != (1, 1) or not valid_id(identity):
                raise InvalidReportCursor
            result = cls(EPOCH + timedelta(microseconds=micros), identity)
            if result.encode() != token:
                raise InvalidReportCursor
            return result
        except ValueError, OverflowError, struct.error, binascii.Error:
            raise InvalidReportCursor from None


@dataclass(frozen=True)
class ReportedQuality:
    quality: int
    range: str


@dataclass(frozen=True)
class ReportedVideo:
    id: int
    title: str
    description: str = field(repr=False)
    visibility: str
    added_at: datetime | None
    updated_at: datetime
    qualities: tuple[ReportedQuality, ...]
    hd: bool
    uhd: bool
    hdr: bool
    uhd_hdr: bool


@dataclass(frozen=True)
class ReviewPlaylist:
    text: str = field(repr=False)
    media_type: str


@dataclass(frozen=True)
class ReviewObject:
    redirect: str = field(repr=False)
    media_type: str
