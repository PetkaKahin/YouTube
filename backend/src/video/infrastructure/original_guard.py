"""Short admission lock shared with durable original-deletion decisions."""

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from video.domain.processing import SourceInspectionError
from video.infrastructure.models import UploadRow

NAMESPACE = 73145  # Two-int advisory namespace, separate from profile/subtitle locks.


async def require_original(session: AsyncSession, upload_id: int) -> UploadRow:
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(:namespace,:key)"),
        {"namespace": NAMESPACE, "key": upload_id % 2**31},
    )
    row = await session.scalar(
        select(UploadRow).where(UploadRow.id == upload_id).execution_options(populate_existing=True)
    )
    if row is None or row.status != "uploaded" or row.original_cleanup_started_at is not None:
        raise SourceInspectionError("source_not_uploaded")
    return row
