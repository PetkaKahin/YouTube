"""Public search hits contain no account identifiers or private fields."""

from dataclasses import dataclass
from datetime import datetime

from channel.domain.playlists import Playlist
from channel.domain.profiles import PublicChannel


@dataclass(frozen=True)
class ChannelSearchHit:
    id: int
    name: str
    tag: str
    created_at: datetime


@dataclass(frozen=True)
class PlaylistSearchHit:
    playlist: Playlist
    channel: PublicChannel
