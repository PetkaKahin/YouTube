"""Fill personal pages before cursor selection; ordinary viewing remains authoritative."""

from typing import Protocol

from common.contracts.transactions import Transactions
from video.application.playlist_cards import GetPlaylistVideoCards
from video.application.ports import VideoActors
from video.application.publications import require_actor
from video.application.watch_progress import ReadWatchProgress
from video.domain.feed import FeedCard, FeedPage
from video.domain.library import (
    SIZE,
    InvalidLibraryCursor,
    LibraryBoundary,
    LibraryCandidate,
    LibraryKind,
)


class LibraryCandidates(Protocol):
    async def scan(
        self,
        actor: int,
        kind: LibraryKind,
        after: LibraryBoundary | None,
        owner_ids: tuple[int, ...],
    ) -> tuple[LibraryCandidate, ...]: ...


class SubscribedOwners(Protocol):
    async def get(self, actor: int) -> tuple[int, ...]: ...


class GetLibraryVideos:
    def __init__(
        self,
        actors: VideoActors,
        candidates: LibraryCandidates,
        subscriptions: SubscribedOwners,
        cards: GetPlaylistVideoCards,
        progress: ReadWatchProgress,
        transactions: Transactions,
    ) -> None:
        self.actors, self.candidates, self.subscriptions = actors, candidates, subscriptions
        self.cards, self.progress, self.transactions = cards, progress, transactions

    async def page(
        self,
        actor: int,
        kind: LibraryKind,
        rows: list[tuple[LibraryCandidate, FeedCard]],
        more: bool,
    ) -> FeedPage:
        items = await self.progress.cards(actor, tuple(card for _, card in rows[:SIZE]))
        if kind is LibraryKind.HISTORY and any(card.watch_progress is None for card in items):
            raise RuntimeError("History progress is unavailable")
        return FeedPage(items, rows[SIZE - 1][0].boundary.encode(actor, kind) if more else None)

    async def __call__(
        self, account_id: int | None, kind: str, cursor: str | None = None
    ) -> FeedPage:
        actor = require_actor(account_id)
        async with self.transactions.begin():
            await self.actors.require(actor)
            try:
                source = LibraryKind(kind)
            except ValueError:
                raise InvalidLibraryCursor from None
            after = LibraryBoundary.decode(cursor, actor, source) if cursor is not None else None
            owners = (
                await self.subscriptions.get(actor) if source is LibraryKind.SUBSCRIPTIONS else ()
            )
            if source is LibraryKind.SUBSCRIPTIONS and not owners:
                return FeedPage((), None)
            selected: list[tuple[LibraryCandidate, FeedCard]] = []
            while True:
                rows = await self.candidates.scan(actor, source, after, owners)
                wanted = tuple(row.boundary.id for row in rows)
                cards = await self.cards(actor, wanted) if wanted else {}
                for row in rows:
                    if row.boundary.id in cards:
                        selected.append((row, cards[row.boundary.id]))
                    if len(selected) > SIZE:
                        return await self.page(actor, source, selected, True)
                if len(rows) < SIZE + 1:
                    return await self.page(actor, source, selected, False)
                if after == rows[-1].boundary:
                    raise RuntimeError("Library scan did not advance")
                after = rows[-1].boundary
