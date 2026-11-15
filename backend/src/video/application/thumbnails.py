"""Owner choices, independent image transfers and durable image preparation."""

import asyncio
from typing import Protocol
from uuid import UUID

from common.contracts.transactions import Transactions
from video.application.ports import Publications, VideoActors
from video.application.uploads import (
    CompleteUpload,
    ExpireUploads,
    MultipartStorage,
    RefreshUpload,
    UploadOperations,
    Uploads,
)
from video.domain.delivery import StoredMedia
from video.domain.processing import SourceInspectionError
from video.domain.publications import VideoNotFound, valid_identifier
from video.domain.thumbnails import (
    MAX_IMAGE_BYTES,
    PreparedThumbnail,
    ThumbnailClaim,
    ThumbnailError,
    ThumbnailOptions,
)
from video.domain.uploads import Transfer, UploadPlan


class ThumbnailUploads(Uploads, Protocol):
    pass


class ThumbnailJobs(Protocol):
    async def new_intent(self, upload_id: int) -> None: ...
    async def confirm_selection(self, upload_id: int) -> None: ...
    async def admit(self, upload_id: int) -> None: ...
    async def claim(self, upload_id: int, job_id: int, attempt: int) -> ThumbnailClaim | None: ...
    async def finish(self, upload_id: int, token: UUID, prepared: PreparedThumbnail) -> bool: ...
    async def fail(self, upload_id: int, token: UUID, code: str, *, terminal: bool) -> None: ...
    async def options(self, video_id: int) -> ThumbnailOptions: ...
    async def select(self, video_id: int, slot: int) -> None: ...
    async def image_key(self, video_id: int, slot: int | None) -> str: ...
    async def image_media(self, video_id: int, slot: int | None) -> StoredMedia: ...
    async def repair(self) -> int: ...
    async def expired_preparation(self) -> tuple[UUID, str] | None: ...
    async def forget_preparation(self, token: UUID) -> None: ...
    async def completed_source(self) -> tuple[int, tuple[str, ...]] | None: ...
    async def forget_source(self, upload_id: int) -> None: ...


class ThumbnailProcessor(Protocol):
    async def prepare(self, claim: ThumbnailClaim) -> PreparedThumbnail: ...
    async def discard(self, key: str) -> None: ...
    async def read(self, key: str) -> bytes: ...


class ThumbnailUploadOperations(UploadOperations):
    def __init__(
        self,
        actors: VideoActors,
        publications: Publications,
        uploads: ThumbnailUploads,
        storage: MultipartStorage,
        transactions: Transactions,
        jobs: ThumbnailJobs,
    ) -> None:
        super().__init__(actors, publications, uploads, storage, transactions, jobs)
        self.jobs = jobs

    def validate_size(self, size: int) -> None:
        if type(size) is not int or not 1 <= size <= MAX_IMAGE_BYTES:
            raise ThumbnailError("image_size_limit")

    async def recover_completed(self, transfer: Transfer) -> bool:
        if transfer.status in {"uploaded", "preparing", "prepared", "failed"}:
            return True  # The authoritative receipt survives original/intermediate cleanup.
        return await super().recover_completed(transfer)


class StartThumbnailUpload:
    def __init__(self, operations: ThumbnailUploadOperations) -> None:
        self.ops = operations

    async def __call__(self, account_id: int | None, video_id: int, size: int) -> UploadPlan:
        async with self.ops.transactions.begin():
            await self.ops.owner(account_id, video_id)
            self.ops.validate_size(size)
            transfer = await self.ops.uploads.create(video_id, size)
            await self.ops.jobs.new_intent(transfer.id)
        return await RefreshUpload(self.ops)(account_id, video_id, transfer.id)


class RefreshThumbnailUpload:
    def __init__(self, operations: ThumbnailUploadOperations) -> None:
        self.ops = operations

    async def __call__(self, account_id: int | None, video_id: int, upload_id: int) -> UploadPlan:
        return await RefreshUpload(self.ops)(account_id, video_id, upload_id)


class CompleteThumbnailUpload:
    def __init__(self, operations: ThumbnailUploadOperations) -> None:
        self.ops = operations

    async def __call__(self, account_id: int | None, video_id: int, upload_id: int) -> UploadPlan:
        result = await CompleteUpload(self.ops)(account_id, video_id, upload_id)
        async with self.ops.transactions.begin():
            await self.ops.owner(account_id, video_id)
            await self.ops.find(video_id, upload_id)
            await self.ops.jobs.confirm_selection(upload_id)
        return result


class ThumbnailActions:
    def __init__(
        self,
        operations: ThumbnailUploadOperations,
        jobs: ThumbnailJobs,
        processor: ThumbnailProcessor,
    ) -> None:
        self.ops, self.jobs, self.processor = operations, jobs, processor


class GetThumbnails:
    def __init__(self, actions: ThumbnailActions) -> None:
        self.actions = actions

    async def __call__(self, account_id: int | None, video_id: int) -> ThumbnailOptions:
        async with self.actions.ops.transactions.begin():
            await self.actions.ops.owner(account_id, video_id)
            return await self.actions.jobs.options(video_id)


class SelectThumbnail:
    def __init__(self, actions: ThumbnailActions) -> None:
        self.actions = actions

    async def __call__(self, account_id: int | None, video_id: int, slot: int) -> ThumbnailOptions:
        async with self.actions.ops.transactions.begin():
            await self.actions.ops.owner(account_id, video_id)
            if type(slot) is not int or slot not in range(3):
                raise ThumbnailError("invalid_thumbnail_slot")
            await self.actions.jobs.select(video_id, slot)
            return await self.actions.jobs.options(video_id)


class GetThumbnailImage:
    def __init__(self, actions: ThumbnailActions) -> None:
        self.actions = actions

    async def __call__(
        self, account_id: int | None, video_id: int, *, slot: int | None = None
    ) -> bytes:
        async with self.actions.ops.transactions.begin():
            await self.actions.ops.owner(account_id, video_id)
            if slot is not None and (type(slot) is not int or slot not in range(3)):
                raise VideoNotFound
            key = await self.actions.jobs.image_key(video_id, slot)
        return await self.actions.processor.read(key)


class PrepareThumbnail:
    def __init__(
        self, jobs: ThumbnailJobs, processor: ThumbnailProcessor, transactions: Transactions
    ) -> None:
        self.jobs, self.processor, self.transactions = jobs, processor, transactions

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
            prepared = await self.processor.prepare(claim)
        except SourceInspectionError as error:
            async with self.transactions.begin():
                await self.jobs.fail(
                    upload_id, claim.token, error.code, terminal=last_attempt or not error.retryable
                )
            raise
        async with self.transactions.begin():
            accepted = await self.jobs.finish(upload_id, claim.token, prepared)
        if not accepted:
            await self.processor.discard(claim.prefix)


class RepairThumbnails:
    def __init__(
        self,
        operations: ThumbnailUploadOperations,
        jobs: ThumbnailJobs,
        processor: ThumbnailProcessor,
    ) -> None:
        self.ops, self.jobs, self.processor = operations, jobs, processor

    async def __call__(self) -> int:
        count = await ExpireUploads(self.ops)()
        async with self.ops.transactions.begin():
            count += await self.jobs.repair()
        try:
            async with asyncio.timeout(5):
                async with self.ops.transactions.begin():
                    obsolete = await self.jobs.expired_preparation()
                    if obsolete:
                        token, prefix = obsolete
                        await self.processor.discard(prefix)
                        await self.jobs.forget_preparation(token)
                async with self.ops.transactions.begin():
                    source = await self.jobs.completed_source()
                    if source:
                        upload_id, keys = source
                        for key in keys:
                            await self.processor.discard(key)
                        await self.jobs.forget_source(upload_id)
        except TimeoutError:
            pass
        return count
