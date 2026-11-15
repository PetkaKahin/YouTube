"""Owner-only processing diagnostics; a complete master stays inside infrastructure."""

from typing import Protocol

from common.contracts.transactions import Transactions
from video.application.ports import VideoActors
from video.application.publications import require_actor
from video.domain.publications import VideoNotFound, valid_identifier
from video.domain.readiness import ProcessingStatus


class ProcessingViews(Protocol):
    async def owned(self, video_id: int, owner_id: int) -> ProcessingStatus | None: ...


class GetProcessing:
    def __init__(
        self, actors: VideoActors, views: ProcessingViews, transactions: Transactions
    ) -> None:
        self.actors, self.views, self.transactions = actors, views, transactions

    async def __call__(self, account_id: int | None, video_id: int) -> ProcessingStatus:
        actor = require_actor(account_id)
        async with self.transactions.begin():
            await self.actors.require(actor)
            if not valid_identifier(video_id):
                raise VideoNotFound
            value = await self.views.owned(video_id, actor)
            if value is None:
                raise VideoNotFound
            return value
