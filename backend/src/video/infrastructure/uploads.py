"""Persistent transfer attempts; publication locks serialize all transitions."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from video.domain.uploads import StoredObject, Transfer
from video.infrastructure.models import PublicationRow, UploadRow


def transfer_value(row: UploadRow) -> Transfer:
    return Transfer(
        row.id,
        row.video_id,
        row.object_key,
        row.multipart_id,
        row.size,
        row.status,
        row.activity_at,
    )


class SqlUploads:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def active(self, video_id: int) -> Transfer | None:
        row = await self.session.scalar(
            select(UploadRow)
            .where(UploadRow.video_id == video_id, UploadRow.status != "expired")
            .execution_options(populate_existing=True)
        )
        return transfer_value(row) if row else None

    async def get(self, video_id: int, upload_id: int) -> Transfer | None:
        row = await self.session.scalar(
            select(UploadRow)
            .where(UploadRow.video_id == video_id, UploadRow.id == upload_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return transfer_value(row) if row else None

    async def create(self, video_id: int, size: int) -> Transfer:
        row = UploadRow(video_id=video_id, size=size, object_key="incoming/" + uuid4().hex)
        self.session.add(row)
        await self.session.flush()
        await self.session.execute(
            update(PublicationRow).where(PublicationRow.id == video_id).values(upload="uploading")
        )
        return transfer_value(row)

    async def activate(self, transfer: Transfer, multipart_id: str, now: datetime) -> Transfer:
        row = (
            await self.session.scalars(
                update(UploadRow)
                .where(UploadRow.id == transfer.id)
                .values(
                    multipart_id=multipart_id,
                    status="uploading",
                    activity_at=now,
                )
                .returning(UploadRow)
                .execution_options(populate_existing=True)
            )
        ).one()
        return transfer_value(row)

    async def touch(self, transfer: Transfer, now: datetime) -> None:
        await self.session.execute(
            update(UploadRow).where(UploadRow.id == transfer.id).values(activity_at=now)
        )

    async def complete(self, transfer: Transfer, result: StoredObject) -> None:
        await self.session.execute(
            update(UploadRow).where(UploadRow.id == transfer.id).values(status="uploaded")
        )
        await self.session.execute(
            update(PublicationRow)
            .where(PublicationRow.id == transfer.video_id, PublicationRow.added_at.is_(None))
            .values(upload="uploaded", added_at=result.modified_at)
        )

    async def expire(self, transfer: Transfer) -> None:
        await self.session.execute(
            update(UploadRow).where(UploadRow.id == transfer.id).values(status="expired")
        )
        await self.session.execute(
            update(PublicationRow)
            .where(PublicationRow.id == transfer.video_id, PublicationRow.added_at.is_(None))
            .values(upload="failed")
        )

    async def candidates(self, before: datetime) -> tuple[tuple[int, int], ...]:
        result = await self.session.execute(
            select(UploadRow.video_id, UploadRow.id)
            .where(UploadRow.status.in_(("creating", "uploading")), UploadRow.activity_at <= before)
            .order_by(UploadRow.activity_at, UploadRow.id)
            .limit(100)
        )
        return tuple((video_id, upload_id) for video_id, upload_id in result)

    async def lock_video(self, video_id: int) -> bool:
        return (
            await self.session.scalar(
                select(PublicationRow.id)
                .where(PublicationRow.id == video_id)
                .with_for_update(skip_locked=True)
            )
            is not None
        )
