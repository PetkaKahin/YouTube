"""Durable upload intent, authoritative completion, and repeatable expiry."""

from datetime import UTC, datetime, timedelta
from typing import Protocol

from common.contracts.transactions import Transactions
from video.application.ports import Publications, VideoActors
from video.application.publications import require_actor
from video.domain.publications import VideoNotFound, valid_identifier
from video.domain.uploads import (
    IDLE_TIMEOUT,
    URL_SECONDS,
    PartLink,
    StoredObject,
    StoredPart,
    Transfer,
    UploadError,
    UploadPlan,
    validate_parts,
    validate_size,
)


class Uploads(Protocol):
    async def active(self, video_id: int) -> Transfer | None: ...
    async def get(self, video_id: int, upload_id: int) -> Transfer | None: ...
    async def create(self, video_id: int, size: int) -> Transfer: ...
    async def activate(self, transfer: Transfer, multipart_id: str, now: datetime) -> Transfer: ...
    async def touch(self, transfer: Transfer, now: datetime) -> None: ...
    async def complete(self, transfer: Transfer, result: StoredObject) -> None: ...
    async def expire(self, transfer: Transfer) -> None: ...
    async def candidates(self, before: datetime) -> tuple[tuple[int, int], ...]: ...
    async def lock_video(self, video_id: int) -> bool: ...


class MultipartStorage(Protocol):
    async def initiate(self, transfer: Transfer) -> str: ...
    async def parts(self, transfer: Transfer) -> tuple[StoredPart, ...]: ...
    async def head(self, transfer: Transfer) -> StoredObject | None: ...
    async def sign(self, transfer: Transfer, number: int) -> str: ...
    async def complete(self, transfer: Transfer, parts: tuple[StoredPart, ...]) -> None: ...
    async def discard(self, transfer: Transfer) -> None: ...


class UploadAdmission(Protocol):
    async def admit(self, upload_id: int) -> None: ...


class UploadOperations:
    def __init__(
        self,
        actors: VideoActors,
        publications: Publications,
        uploads: Uploads,
        storage: MultipartStorage,
        transactions: Transactions,
        source_jobs: UploadAdmission,
    ) -> None:
        self.actors, self.publications, self.uploads = actors, publications, uploads
        self.storage, self.transactions = storage, transactions
        self.source_jobs = source_jobs

    def validate_size(self, size: int) -> None:
        validate_size(size)

    async def owner(self, account_id: int | None, video_id: int) -> int:
        actor = require_actor(account_id)
        await self.actors.require(actor)
        if (
            not valid_identifier(video_id)
            or await self.publications.owned(video_id, actor, lock=True) is None
        ):
            raise VideoNotFound
        return actor

    async def find(self, video_id: int, upload_id: int) -> Transfer:
        if not valid_identifier(upload_id):
            raise VideoNotFound
        transfer = await self.uploads.get(video_id, upload_id)
        if transfer is None:
            raise VideoNotFound
        if transfer.status == "expired":
            raise UploadError("upload_expired")
        return transfer

    async def recover_completed(self, transfer: Transfer) -> bool:
        if transfer.status == "uploaded" and transfer.original_released:
            return True  # Accepted receipt survives intentional removal of its temporary bytes.
        result = await self.storage.head(transfer)
        if result is None:
            if transfer.status == "uploaded":
                raise UploadError("source_unavailable")
            return False
        if result.size != transfer.size:
            raise UploadError("invalid_size")
        if transfer.status != "uploaded":
            await self.uploads.complete(transfer, result)
        await self.source_jobs.admit(transfer.id)
        return True

    async def plan(self, transfer: Transfer) -> UploadPlan:
        now = datetime.now(UTC)
        if await self.recover_completed(transfer):
            return UploadPlan(transfer.id, "uploaded", transfer.size, now, ())
        if transfer.multipart_id is None and transfer.activity_at <= now - IDLE_TIMEOUT:
            raise UploadError("upload_expired")
        if transfer.multipart_id is None:
            multipart_id = await self.storage.initiate(transfer)
            transfer = await self.uploads.activate(transfer, multipart_id, now)
        parts = await self.storage.parts(transfer)
        latest = max((p.modified_at for p in parts), default=transfer.activity_at)
        if max(transfer.activity_at, latest) <= now - IDLE_TIMEOUT:
            raise UploadError("upload_expired")
        present = {
            p.number
            for p in parts
            if 1 <= p.number <= transfer.part_count and p.size == transfer.part_size(p.number)
        }
        links = tuple(
            [
                PartLink(
                    number, transfer.part_size(number), await self.storage.sign(transfer, number)
                )
                for number in range(1, transfer.part_count + 1)
                if number not in present
            ]
        )
        await self.uploads.touch(transfer, now)
        return UploadPlan(
            transfer.id, "uploading", transfer.size, now + timedelta(seconds=URL_SECONDS), links
        )


class StartUpload:
    def __init__(self, operations: UploadOperations) -> None:
        self.ops = operations

    async def __call__(self, account_id: int | None, video_id: int, size: int) -> UploadPlan:
        # Persist an intent before external allocation; recover uncertain creates by object key.
        async with self.ops.transactions.begin():
            await self.ops.owner(account_id, video_id)
            self.ops.validate_size(size)
            transfer = await self.ops.uploads.active(video_id)
            if transfer is not None and transfer.size != size:
                raise UploadError("upload_size_conflict")
            if transfer is None:
                transfer = await self.ops.uploads.create(video_id, size)
        return await RefreshUpload(self.ops)(account_id, video_id, transfer.id)


class RefreshUpload:
    def __init__(self, operations: UploadOperations) -> None:
        self.ops = operations

    async def __call__(self, account_id: int | None, video_id: int, upload_id: int) -> UploadPlan:
        async with self.ops.transactions.begin():
            await self.ops.owner(account_id, video_id)
            return await self.ops.plan(await self.ops.find(video_id, upload_id))


class CompleteUpload:
    def __init__(self, operations: UploadOperations) -> None:
        self.ops = operations

    async def __call__(self, account_id: int | None, video_id: int, upload_id: int) -> UploadPlan:
        async with self.ops.transactions.begin():
            await self.ops.owner(account_id, video_id)
            transfer = await self.ops.find(video_id, upload_id)
            if not await self.ops.recover_completed(transfer):
                if transfer.multipart_id is None:
                    raise UploadError("incomplete_upload")
                parts = await self.ops.storage.parts(transfer)
                latest = max((p.modified_at for p in parts), default=transfer.activity_at)
                if max(transfer.activity_at, latest) <= datetime.now(UTC) - IDLE_TIMEOUT:
                    raise UploadError("upload_expired")
                validate_parts(transfer, parts)
                await self.ops.storage.complete(transfer, parts)
                if not await self.ops.recover_completed(transfer):
                    raise UploadError("source_unavailable")
            return UploadPlan(transfer.id, "uploaded", transfer.size, datetime.now(UTC), ())


class ExpireUploads:
    def __init__(self, operations: UploadOperations) -> None:
        self.ops = operations

    async def __call__(self) -> int:
        before = datetime.now(UTC) - IDLE_TIMEOUT
        async with self.ops.transactions.begin():
            candidates = await self.ops.uploads.candidates(before)
        expired = 0
        for video_id, upload_id in candidates:
            async with self.ops.transactions.begin():
                if not await self.ops.uploads.lock_video(video_id):
                    continue
                transfer = await self.ops.uploads.get(video_id, upload_id)
                if (
                    transfer is None
                    or transfer.status in {"uploaded", "expired"}
                    or transfer.activity_at > before
                ):
                    continue
                try:
                    if await self.ops.recover_completed(transfer):
                        continue
                except UploadError as error:
                    if error.code != "invalid_size":
                        raise
                try:
                    parts = await self.ops.storage.parts(transfer) if transfer.multipart_id else ()
                except UploadError as error:
                    if error.code != "upload_missing":
                        raise
                    parts = ()
                latest = max((p.modified_at for p in parts), default=transfer.activity_at)
                if latest > before:
                    await self.ops.uploads.touch(transfer, latest)
                    continue
                await self.ops.storage.discard(transfer)
                await self.ops.uploads.expire(transfer)
                expired += 1
        return expired
