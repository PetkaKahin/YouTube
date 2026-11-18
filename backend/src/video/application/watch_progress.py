"""Batch read positions only after callers have selected viewable cards."""

from dataclasses import replace
from typing import Protocol

from video.application.ports import VideoActors
from video.application.publications import require_actor
from video.domain.feed import FeedCard
from video.domain.watch_progress import WatchProgress


class WatchProgressStore(Protocol):
    async def lock(self, video_id: int) -> bool: ...
    async def duration(self, video_id: int) -> float | None: ...
    async def save(
        self, account_id: int, video_id: int, position: float, duration: float
    ) -> WatchProgress: ...
    async def many(
        self, account_id: int, video_ids: tuple[int, ...]
    ) -> dict[int, WatchProgress]: ...


class ReadWatchProgress:
    def __init__(self, actors: VideoActors, store: WatchProgressStore) -> None:
        self.actors, self.store = actors, store

    async def __call__(
        self, account_id: int | None, video_ids: tuple[int, ...]
    ) -> dict[int, WatchProgress]:
        if account_id is None:
            return {}
        actor = require_actor(account_id)
        await self.actors.require(actor)
        return await self.store.many(actor, tuple(dict.fromkeys(video_ids))) if video_ids else {}

    async def cards(
        self, account_id: int | None, cards: tuple[FeedCard, ...]
    ) -> tuple[FeedCard, ...]:
        values = await self(account_id, tuple(card.id for card in cards))
        return tuple(replace(card, watch_progress=values.get(card.id)) for card in cards)
