"""Persistent pair-wise UPSERT and one position query per page."""

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from video.domain.watch_progress import WatchProgress
from video.infrastructure.models import PublicationRow, WatchProgressRow
from video.infrastructure.readiness import MEDIA_JOINS, fraction, object_value, sequence

DURATION = text(
    f"SELECT s.inspection,m.config FROM video_publications p {MEDIA_JOINS} WHERE p.id=:id"  # noqa: S608 -- constant join; ID bound
)


def value(row: WatchProgressRow) -> WatchProgress:
    return WatchProgress(row.position_seconds, row.duration_seconds, row.updated_at)


class SqlWatchProgress:
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

    async def duration(self, video_id: int) -> float | None:
        row = (await self.session.execute(DURATION, {"id": video_id})).mappings().one_or_none()
        if row is None:
            return None
        try:
            config = object_value(row["config"])
            inspection = object_value(row["inspection"])
            track = next(
                object_value(item)
                for item in sequence(inspection["tracks"])
                if object_value(item)["stream_index"] == config["video_index"]
            )
            duration = fraction(track["presentation_duration"])
            return float(duration) if 0 < duration <= 3600 else None
        except KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError, StopIteration:
            return None

    async def save(
        self, account_id: int, video_id: int, position: float, duration: float
    ) -> WatchProgress:
        statement = insert(WatchProgressRow).values(
            account_id=account_id,
            video_id=video_id,
            position_seconds=position,
            duration_seconds=duration,
        )
        row = (
            await self.session.scalars(
                statement.on_conflict_do_update(
                    index_elements=[WatchProgressRow.account_id, WatchProgressRow.video_id],
                    set_={
                        "position_seconds": statement.excluded.position_seconds,
                        "duration_seconds": statement.excluded.duration_seconds,
                        "updated_at": func.clock_timestamp(),
                    },
                )
                .returning(WatchProgressRow)
                .execution_options(populate_existing=True)
            )
        ).one()
        return value(row)

    async def many(self, account_id: int, video_ids: tuple[int, ...]) -> dict[int, WatchProgress]:
        if not video_ids:
            return {}
        rows = await self.session.scalars(
            select(WatchProgressRow).where(
                WatchProgressRow.account_id == account_id, WatchProgressRow.video_id.in_(video_ids)
            )
        )
        return {row.video_id: value(row) for row in rows}
