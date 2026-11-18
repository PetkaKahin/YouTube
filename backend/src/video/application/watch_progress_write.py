"""A progress write holds the same publication lock as other access-sensitive changes."""

from common.contracts.transactions import Transactions
from video.application.access import VideoAccess
from video.application.publications import require_actor
from video.application.watch_progress import WatchProgressStore
from video.domain.publications import VideoNotFound, valid_identifier
from video.domain.watch_progress import WatchProgress, position_seconds


class SetWatchProgress:
    def __init__(
        self, access: VideoAccess, store: WatchProgressStore, transactions: Transactions
    ) -> None:
        self.access, self.store, self.transactions = access, store, transactions

    async def __call__(
        self, account_id: int | None, video_id: int, position: object
    ) -> WatchProgress:
        actor = require_actor(account_id)
        value = position_seconds(position)
        async with self.transactions.begin():
            viewer = await self.access.viewer(actor)
            if not valid_identifier(video_id) or not await self.store.lock(video_id):
                raise VideoNotFound
            await self.access.snapshot(viewer, video_id)
            duration = await self.store.duration(video_id)
            if duration is None:
                raise VideoNotFound
            return await self.store.save(actor, video_id, min(value, duration), duration)
