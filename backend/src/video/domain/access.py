"""Trusted identities, viewing conditions and public metadata values."""

from dataclasses import dataclass, field
from datetime import datetime

from video.domain.delivery import MediaDelivery


@dataclass(frozen=True)
class Viewer:
    account_id: int | None = None
    email: str | None = field(default=None, repr=False)

    @property
    def authenticated(self) -> bool:
        return self.account_id is not None


@dataclass(frozen=True)
class ViewConditions:
    owner_id: int
    published: bool
    visible: bool
    playable: bool
    public: bool
    permitted: bool


@dataclass(frozen=True)
class ReadyQuality:
    quality: int
    range: str


@dataclass(frozen=True)
class VideoView:
    id: int
    title: str
    description: str
    visibility: str
    added_at: datetime | None
    updated_at: datetime
    qualities: tuple[ReadyQuality, ...]
    hd: bool
    uhd: bool
    hdr: bool
    uhd_hdr: bool


@dataclass(frozen=True)
class ViewingSnapshot:
    conditions: ViewConditions
    video: VideoView = field(repr=False)
    delivery: MediaDelivery | None = field(default=None, repr=False)
