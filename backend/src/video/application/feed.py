"""Fill a public page before choosing its continuation boundary."""

from dataclasses import dataclass
from typing import Protocol

from common.contracts.transactions import Transactions
from video.application.watch_progress import ReadWatchProgress
from video.domain.feed import (
    FEED_SIZE,
    ChannelFeedCursor,
    FeedAuthor,
    FeedBatch,
    FeedCard,
    FeedPage,
    FeedPosition,
    FeedWindow,
)
from video.domain.publications import VideoNotFound


class FeedCandidates(Protocol):
    async def scan(self, after: FeedPosition | None, owner_id: int | None = None) -> FeedBatch: ...


class FeedAuthors(Protocol):
    async def get(self, owner_ids: tuple[int, ...]) -> dict[int, FeedAuthor]: ...


class ReadFeed:
    def __init__(self, candidates: FeedCandidates, authors: FeedAuthors) -> None:
        self.candidates, self.authors = candidates, authors

    async def __call__(
        self, position: FeedPosition | None, owner_id: int | None = None
    ) -> FeedWindow:
        cards: list[FeedCard] = []
        while True:
            batch = await self.candidates.scan(position, owner_id)
            authors = await self.authors.get(
                tuple(dict.fromkeys(item.owner_id for item in batch.items))
            )
            for item in batch.items:
                author = authors.get(item.owner_id)
                if author is not None:
                    cards.append(
                        FeedCard(
                            item.position.id,
                            item.title,
                            item.position.added_at,
                            item.duration_seconds,
                            author,
                            item.preview_ready,
                        )
                    )
                if len(cards) > FEED_SIZE:
                    last = cards[FEED_SIZE - 1]
                    return FeedWindow(
                        tuple(cards[:FEED_SIZE]), FeedPosition(last.added_at, last.id)
                    )
            if batch.exhausted:
                return FeedWindow(tuple(cards), None)
            if batch.after is None or batch.after == position:
                raise ValueError("Feed scan did not advance")
            position = batch.after


class GetFeed:
    def __init__(
        self, reader: ReadFeed, transactions: Transactions, progress: ReadWatchProgress
    ) -> None:
        self.reader, self.transactions, self.progress = reader, transactions, progress

    async def __call__(
        self, cursor: str | None = None, *, account_id: int | None = None
    ) -> FeedPage:
        position = FeedPosition.decode(cursor) if cursor is not None else None
        async with self.transactions.begin():
            window = await self.reader(position)
            return FeedPage(
                await self.progress.cards(account_id, window.items),
                window.after.encode() if window.after else None,
            )


@dataclass(frozen=True)
class FeedChannel:
    id: int
    owner_id: int


class FeedChannels(Protocol):
    async def find(self, tag: str) -> FeedChannel | None: ...


class GetChannelFeed:
    def __init__(
        self,
        channels: FeedChannels,
        reader: ReadFeed,
        transactions: Transactions,
        progress: ReadWatchProgress,
    ) -> None:
        self.channels, self.reader, self.transactions = channels, reader, transactions
        self.progress = progress

    async def __call__(
        self, tag: str, cursor: str | None = None, *, account_id: int | None = None
    ) -> FeedPage:
        async with self.transactions.begin():
            channel = await self.channels.find(tag)
            if channel is None:
                raise VideoNotFound
            after = (
                ChannelFeedCursor.decode(cursor, channel.id).position
                if cursor is not None
                else None
            )
            window = await self.reader(after, channel.owner_id)
            return FeedPage(
                await self.progress.cards(account_id, window.items),
                ChannelFeedCursor(channel.id, window.after).encode() if window.after else None,
            )
