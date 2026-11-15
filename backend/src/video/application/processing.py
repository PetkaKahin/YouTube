"""Durable media admission and repeatable first-stage execution."""

from typing import Protocol
from uuid import UUID

from common.contracts.transactions import Transactions
from video.domain.processing import SourceClaim, SourceInspection, SourceInspectionError
from video.domain.publications import valid_identifier

MEDIA_QUEUE = "media"
INSPECTION_TASK = "video.inspect_source"


class SourceJobs(Protocol):
    async def admit(self, upload_id: int) -> None: ...
    async def repair(self) -> int: ...
    async def claim(self, upload_id: int, job_id: int, attempt: int) -> SourceClaim | None: ...
    async def finish(self, upload_id: int, token: UUID, result: SourceInspection) -> None: ...
    async def fail(self, upload_id: int, token: UUID, code: str, *, terminal: bool) -> None: ...


class SourceInspector(Protocol):
    async def inspect(self, source: SourceClaim) -> SourceInspection: ...


class RepairSourceProcessing:
    def __init__(self, jobs: SourceJobs, transactions: Transactions) -> None:
        self.jobs, self.transactions = jobs, transactions

    async def __call__(self) -> int:
        async with self.transactions.begin():
            return await self.jobs.repair()


class InspectSource:
    def __init__(
        self, jobs: SourceJobs, inspector: SourceInspector, transactions: Transactions
    ) -> None:
        self.jobs, self.inspector, self.transactions = jobs, inspector, transactions

    async def __call__(
        self, upload_id: int, job_id: int, *, attempt: int, last_attempt: bool
    ) -> None:
        if (
            not valid_identifier(upload_id)
            or not valid_identifier(job_id)
            or type(attempt) is not int
            or attempt < 0
        ):
            raise SourceInspectionError("invalid_source_identity")
        async with self.transactions.begin():
            claim = await self.jobs.claim(upload_id, job_id, attempt)
        if claim is None:
            return
        try:
            result = await self.inspector.inspect(claim)
        except SourceInspectionError as error:
            async with self.transactions.begin():
                await self.jobs.fail(
                    upload_id, claim.token, error.code, terminal=last_attempt or not error.retryable
                )
            raise
        async with self.transactions.begin():
            await self.jobs.finish(upload_id, claim.token, result)
