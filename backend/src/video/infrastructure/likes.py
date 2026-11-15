"""Own-module pair writes and exact PostgreSQL totals, without counter copies."""

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from video.domain.likes import LikeTotals
from video.infrastructure.models import LikeRow, PublicationRow


class SqlVideoLikes:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock(self, video_id: int) -> bool:
        return (
            await self.session.scalar(
                select(PublicationRow.id).where(PublicationRow.id == video_id).with_for_update()
            )
            is not None
        )

    async def set(self, video_id: int, account_id: int, liked: bool) -> None:
        if liked:
            await self.session.execute(
                insert(LikeRow)
                .values(video_id=video_id, account_id=account_id)
                .on_conflict_do_nothing(index_elements=[LikeRow.video_id, LikeRow.account_id])
            )
        else:
            await self.session.execute(
                delete(LikeRow).where(
                    LikeRow.video_id == video_id, LikeRow.account_id == account_id
                )
            )

    async def totals(self, video_id: int, account_id: int | None) -> LikeTotals:
        row = (
            await self.session.execute(
                select(
                    func.count(),
                    func.coalesce(func.bool_or(LikeRow.account_id == account_id), False),
                )
                .select_from(LikeRow)
                .where(LikeRow.video_id == video_id)
            )
        ).one()
        return LikeTotals(bool(row[1]), int(row[0]))
