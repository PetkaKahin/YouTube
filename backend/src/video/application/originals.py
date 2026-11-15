"""Commit a deletion intent before external I/O; replay uncertain deletions."""

import logging
from typing import Protocol

from common.contracts.transactions import Transactions
from video.domain.originals import OriginalDeletion
from video.domain.uploads import StorageUnavailable


class OriginalDeletions(Protocol):
    async def candidates(self) -> tuple[int, ...]: ...
    async def prepare(self, upload_id: int) -> OriginalDeletion | None: ...
    async def finish(self, upload_id: int) -> None: ...


class OriginalStorage(Protocol):
    async def delete(self, intent: OriginalDeletion) -> None: ...


class CleanupOriginals:
    def __init__(
        self, originals: OriginalDeletions, storage: OriginalStorage, transactions: Transactions
    ) -> None:
        self.originals, self.storage, self.transactions = originals, storage, transactions

    async def __call__(self) -> int:
        async with self.transactions.begin():
            candidates = await self.originals.candidates()
        deleted = 0
        unavailable = False
        for upload_id in candidates:
            async with self.transactions.begin():
                intent = await self.originals.prepare(upload_id)
            if intent is None:
                continue
            try:
                await self.storage.delete(intent)
            except StorageUnavailable:
                unavailable = True
                logging.getLogger("background_jobs").error(
                    "Original cleanup storage is unavailable"
                )
                continue
            async with self.transactions.begin():
                await self.originals.finish(upload_id)
            deleted += 1
        if unavailable:
            raise StorageUnavailable
        return deleted
