"""Own-table SQL, exact like aggregates and public-only cross-module adapters."""

from sqlalchemy import delete, false, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from comment.domain.comments import (
    SIZE,
    CommentAuthenticationRequired,
    CommentCursor,
    CommentRecord,
    CommentSort,
    DiscussionNotFound,
)
from comment.infrastructure.models import CommentLikeRow, CommentRow
from user.public import GetPublicNames
from video.public import AuthenticationRequired, RequireDiscussionVideo, VideoNotFound


class VideoDiscussionAccess:
    def __init__(self, require_video: RequireDiscussionVideo) -> None:
        self.require_video = require_video

    async def require(self, account_id: int | None, video_id: int) -> None:
        try:
            await self.require_video(account_id, video_id)
        except AuthenticationRequired:
            raise CommentAuthenticationRequired from None
        except VideoNotFound:
            raise DiscussionNotFound from None


class UserCommentAuthors:
    def __init__(self, names: GetPublicNames) -> None:
        self.get_names = names

    async def names(self, account_ids: tuple[int, ...]) -> dict[int, str]:
        return await self.get_names(account_ids)


class SqlComments:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, video_id: int, author_id: int, text: str) -> CommentRecord:
        row = CommentRow(video_id=video_id, author_id=author_id, text=text)
        self.session.add(row)
        await self.session.flush()
        return CommentRecord(row.id, row.video_id, row.author_id, row.text, row.created_at)

    async def page(
        self, video_id: int, account_id: int | None, sort: CommentSort, after: CommentCursor | None
    ) -> tuple[CommentRecord, ...]:
        # Aggregate the complete video discussion before applying the popular boundary/limit.
        counts = (
            select(
                CommentRow.id.label("id"),
                func.count(CommentLikeRow.account_id).label("like_count"),
                func.coalesce(
                    func.bool_or(
                        CommentLikeRow.account_id == account_id
                        if account_id is not None
                        else false()
                    ),
                    False,
                ).label("liked"),
            )
            .outerjoin(CommentLikeRow, CommentLikeRow.comment_id == CommentRow.id)
            .where(CommentRow.video_id == video_id)
            .group_by(CommentRow.id)
            .subquery()
        )
        statement = select(CommentRow, counts.c.like_count, counts.c.liked).join(
            counts, counts.c.id == CommentRow.id
        )
        if sort == CommentSort.POPULAR:
            if after is not None:
                statement = statement.where(
                    tuple_(counts.c.like_count, CommentRow.created_at, CommentRow.id)
                    < tuple_(after.like_count, after.created_at, after.id)
                )
            statement = statement.order_by(counts.c.like_count.desc())
        elif after is not None:
            statement = statement.where(
                tuple_(CommentRow.created_at, CommentRow.id) < tuple_(after.created_at, after.id)
            )
        rows = (
            await self.session.execute(
                statement.order_by(CommentRow.created_at.desc(), CommentRow.id.desc()).limit(
                    SIZE + 1
                )
            )
        ).all()
        return tuple(
            CommentRecord(
                row.id,
                row.video_id,
                row.author_id,
                row.text,
                row.created_at,
                bool(liked),
                int(count),
            )
            for row, count, liked in rows
        )

    async def get(
        self, video_id: int, comment_id: int, account_id: int | None, *, lock: bool = False
    ) -> CommentRecord | None:
        if lock:
            exists = await self.session.scalar(
                select(CommentRow.id)
                .where(CommentRow.id == comment_id, CommentRow.video_id == video_id)
                .with_for_update()
            )
            if exists is None:
                return None
        result = (
            await self.session.execute(
                select(
                    CommentRow,
                    func.count(CommentLikeRow.account_id),
                    func.coalesce(
                        func.bool_or(
                            CommentLikeRow.account_id == account_id
                            if account_id is not None
                            else false()
                        ),
                        False,
                    ),
                )
                .outerjoin(CommentLikeRow, CommentLikeRow.comment_id == CommentRow.id)
                .where(CommentRow.id == comment_id, CommentRow.video_id == video_id)
                .group_by(CommentRow.id)
            )
        ).one_or_none()
        if result is None:
            return None
        row, count, liked = result
        return CommentRecord(
            row.id, row.video_id, row.author_id, row.text, row.created_at, bool(liked), int(count)
        )

    async def set_like(self, comment_id: int, account_id: int, liked: bool) -> None:
        if liked:
            await self.session.execute(
                insert(CommentLikeRow)
                .values(comment_id=comment_id, account_id=account_id)
                .on_conflict_do_nothing(
                    index_elements=[CommentLikeRow.comment_id, CommentLikeRow.account_id]
                )
            )
        else:
            await self.session.execute(
                delete(CommentLikeRow).where(
                    CommentLikeRow.comment_id == comment_id, CommentLikeRow.account_id == account_id
                )
            )
