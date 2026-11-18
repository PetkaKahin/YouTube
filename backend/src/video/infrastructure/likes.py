"""Own-module pair writes and exact PostgreSQL totals, without counter copies."""

from sqlalchemy import and_, delete, func, select
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
        await self.set_reaction(video_id, account_id, "like", liked)

    async def set_dislike(self, video_id: int, account_id: int, disliked: bool) -> None:
        await self.set_reaction(video_id, account_id, "dislike", disliked)

    async def set_reaction(
        self, video_id: int, account_id: int, reaction: str, selected: bool
    ) -> None:
        if selected:
            await self.session.execute(
                insert(LikeRow)
                .values(video_id=video_id, account_id=account_id, reaction=reaction)
                .on_conflict_do_update(
                    index_elements=[LikeRow.video_id, LikeRow.account_id],
                    set_={"reaction": reaction, "created_at": func.clock_timestamp()},
                    where=LikeRow.reaction != reaction,
                )
            )
        else:
            await self.session.execute(
                delete(LikeRow).where(
                    LikeRow.video_id == video_id,
                    LikeRow.account_id == account_id,
                    LikeRow.reaction == reaction,
                )
            )

    async def totals(self, video_id: int, account_id: int | None) -> LikeTotals:
        row = (
            await self.session.execute(
                select(
                    func.count().filter(LikeRow.reaction == "like"),
                    func.coalesce(
                        func.bool_or(
                            and_(LikeRow.account_id == account_id, LikeRow.reaction == "like")
                        ),
                        False,
                    ),
                    func.coalesce(
                        func.bool_or(
                            and_(LikeRow.account_id == account_id, LikeRow.reaction == "dislike")
                        ),
                        False,
                    ),
                )
                .select_from(LikeRow)
                .where(LikeRow.video_id == video_id)
            )
        ).one()
        return LikeTotals(bool(row[1]), int(row[0]), bool(row[2]))
