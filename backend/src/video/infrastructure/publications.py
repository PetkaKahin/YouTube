"""Owner-filtered PostgreSQL persistence; transaction boundaries stay in use cases."""

from dataclasses import replace
from typing import Any

from sqlalchemy import func, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from video.domain.own_videos import SIZE, OwnedVideoBoundary
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
from video.infrastructure.readiness import (
    MEDIA_JOINS,
    MEDIA_SNAPSHOT,
    SqlProcessingViews,
    object_value,
    prepare,
)


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

    async def touch(self, video_id: int, owner_id: int) -> None:
        await self.session.execute(
            update(PublicationRow)
            .where(PublicationRow.id == video_id, PublicationRow.owner_id == owner_id)
            .values(updated_at=func.clock_timestamp())
        )

    async def value(self, row: PublicationRow) -> VideoSettings:
        value = settings_value(row)
        processing = await SqlProcessingViews(self.session).owned(row.id, row.owner_id)
        return replace(value, processing=processing.processing) if processing is not None else value

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
        return await self.value(row) if row is not None else None

    async def list_owned(
        self, owner_id: int, publication: PublicationState, after: OwnedVideoBoundary | None
    ) -> tuple[VideoSettings, ...]:
        boundary = "AND (created_at,id)<(:created_at,:id)" if after is not None else ""
        parameters: dict[str, Any] = {
            "owner_id": owner_id,
            "publication": publication.value,
            "limit": SIZE + 1,
        }
        if after is not None:
            parameters.update(created_at=after.created_at, id=after.id)
        statement = text(
            f"SELECT p.*, {MEDIA_SNAPSHOT} AS media FROM ("  # noqa: S608 -- fixed SQL fragments; values bound
            "SELECT * FROM video_publications WHERE owner_id=:owner_id "
            f"AND publication=:publication {boundary} "
            "ORDER BY created_at DESC,id DESC LIMIT :limit"
            f") p {MEDIA_JOINS} ORDER BY p.created_at DESC,p.id DESC"
        )
        rows = await self.session.execute(
            select(PublicationRow, literal_column("media", type_=JSONB))
            .from_statement(statement)
            .execution_options(populate_existing=True),
            parameters,
        )
        return tuple(
            replace(settings_value(row), processing=prepare(object_value(media)).status.processing)
            for row, media in rows
        )

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
        return await self.value(row)

    async def publish(self, video_id: int, owner_id: int) -> VideoSettings:
        row = (
            await self.session.scalars(
                update(PublicationRow)
                .where(PublicationRow.id == video_id, PublicationRow.owner_id == owner_id)
                .values(publication="published", updated_at=func.clock_timestamp())
                .returning(PublicationRow)
                .execution_options(populate_existing=True)
            )
        ).one()
        return await self.value(row)
