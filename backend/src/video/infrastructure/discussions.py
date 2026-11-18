"""Shared publication locks allow discussions concurrently and serialize owner/moderation edits."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from video.infrastructure.models import PublicationRow


class SqlDiscussionPublications:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock(self, video_id: int) -> bool:
        return (
            await self.session.scalar(
                select(PublicationRow.id)
                .where(PublicationRow.id == video_id)
                .with_for_update(read=True)
            )
            is not None
        )
