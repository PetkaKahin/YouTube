"""Owner-filtered PostgreSQL persistence; transaction boundaries stay in use cases."""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from video.domain.publications import (
    ModerationState,
    ProcessingState,
    PublicationState,
    UploadState,
    VideoMetadata,
    VideoSettings,
    Visibility,
)
from video.infrastructure.models import PublicationRow


def settings_value(row: PublicationRow) -> VideoSettings:
    return VideoSettings(
        id=row.id,
        owner_id=row.owner_id,
        title=row.title,
        description=row.description,
        visibility=Visibility(row.visibility),
        publication=PublicationState(row.publication),
        upload=UploadState(row.upload),
        processing=ProcessingState(row.processing),
        moderation=ModerationState(row.moderation),
        added_at=row.added_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlPublications:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, owner_id: int, metadata: VideoMetadata) -> VideoSettings:
        row = PublicationRow(
            owner_id=owner_id,
            title=metadata.title,
            description=metadata.description,
            visibility=metadata.visibility,
        )
        self.session.add(row)
        await self.session.flush()
        return settings_value(row)

    async def owned(
        self, video_id: int, owner_id: int, *, lock: bool = False
    ) -> VideoSettings | None:
        query = select(PublicationRow).where(
            PublicationRow.id == video_id, PublicationRow.owner_id == owner_id
        )
        if lock:
            query = query.with_for_update()
        row = await self.session.scalar(query.execution_options(populate_existing=True))
        return settings_value(row) if row is not None else None

    async def update_metadata(self, value: VideoSettings) -> VideoSettings:
        row = (
            await self.session.scalars(
                update(PublicationRow)
                .where(PublicationRow.id == value.id, PublicationRow.owner_id == value.owner_id)
                .values(
                    title=value.title,
                    description=value.description,
                    visibility=value.visibility.value,
                    updated_at=func.clock_timestamp(),
                )
                .returning(PublicationRow)
                .execution_options(populate_existing=True)
            )
        ).one()
        return settings_value(row)
