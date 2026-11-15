"""Transactional planning and fenced execution of one shared output."""

import asyncio
from typing import Protocol
from uuid import UUID

from common.contracts.transactions import Transactions
from video.domain.encoding import EncodingClaim, EncodingPlan, make_plan
from video.domain.processing import SourceInspectionError
from video.domain.publications import valid_identifier


class EncodingJobs(Protocol):
    async def source(
        self, upload_id: int, job_id: int, attempt: int
    ) -> dict[str, object] | None: ...
    async def save_plan(self, upload_id: int, plan: EncodingPlan) -> None: ...
    async def reject_plan(self, upload_id: int, code: str) -> None: ...
    async def claim(self, encoded_id: int, job_id: int, attempt: int) -> EncodingClaim | None: ...
    async def finish(
        self, encoded_id: int, token: UUID, artifact: dict[str, object] | None
    ) -> bool: ...
    async def fail(self, encoded_id: int, token: UUID, code: str, *, terminal: bool) -> None: ...
    async def repair(self) -> int: ...
    async def expired_attempt(self) -> tuple[UUID, str] | None: ...
    async def forget_attempt(self, token: UUID) -> None: ...


class MediaEncoder(Protocol):
    async def encode(self, claim: EncodingClaim) -> dict[str, object] | None: ...
    async def discard(self, prefix: str) -> None: ...


class PlanMedia:
    def __init__(self, jobs: EncodingJobs, transactions: Transactions) -> None:
        self.jobs, self.transactions = jobs, transactions

    async def __call__(self, upload_id: int, job_id: int, *, attempt: int) -> None:
        failure: SourceInspectionError | None = None
        async with self.transactions.begin():
            source = await self.jobs.source(upload_id, job_id, attempt)
            if source is None:
                return
            try:
                plan = make_plan(source)
            except SourceInspectionError as error:
                await self.jobs.reject_plan(upload_id, error.code)
                failure = error
            else:
                await self.jobs.save_plan(upload_id, plan)
        if failure:
            raise failure


class EncodeTrack:
    def __init__(
        self, jobs: EncodingJobs, encoder: MediaEncoder, transactions: Transactions
    ) -> None:
        self.jobs, self.encoder, self.transactions = jobs, encoder, transactions

    async def __call__(
        self, encoded_id: int, job_id: int, *, attempt: int, last_attempt: bool
    ) -> None:
        if (
            not valid_identifier(encoded_id)
            or not valid_identifier(job_id)
            or type(attempt) is not int
            or attempt < 0
        ):
            raise SourceInspectionError("invalid_source_identity")
        async with self.transactions.begin():
            claim = await self.jobs.claim(encoded_id, job_id, attempt)
        if claim is None:
            return
        try:
            artifact = await self.encoder.encode(claim)
        except SourceInspectionError as error:
            async with self.transactions.begin():
                await self.jobs.fail(
                    encoded_id,
                    claim.token,
                    error.code,
                    terminal=last_attempt or not error.retryable,
                )
            raise
        async with self.transactions.begin():
            accepted = await self.jobs.finish(encoded_id, claim.token, artifact)
        if not accepted:
            await self.encoder.discard(claim.prefix)


class RepairEncoding:
    def __init__(
        self, jobs: EncodingJobs, encoder: MediaEncoder, transactions: Transactions
    ) -> None:
        self.jobs, self.encoder, self.transactions = jobs, encoder, transactions

    async def __call__(self) -> int:
        async with self.transactions.begin():
            count = await self.jobs.repair()
        try:
            async with asyncio.timeout(5):
                async with self.transactions.begin():
                    obsolete = await self.jobs.expired_attempt()
                    if obsolete is not None:
                        token, prefix = obsolete
                        await self.encoder.discard(prefix)
                        await self.jobs.forget_attempt(token)
        except TimeoutError:
            pass  # Durable prefix remains for the next idempotent cleanup pass.
        return count
