"""Channel identity is public; account ownership stays inside module adapters."""

from dataclasses import dataclass
from datetime import datetime

from channel.domain.playlists import Playlist


class ChannelNotFound(Exception):
    pass


@dataclass(frozen=True)
class ChannelIdentity:
    id: int
    owner_id: int
    tag: str
    created_at: datetime


@dataclass(frozen=True)
class ChannelProfile:
    id: int
    name: str
    tag: str
    created_at: datetime


@dataclass(frozen=True)
class ChannelPlaylistPage:
    items: tuple[Playlist, ...]
    next_cursor: str | None
