"""One authorised snapshot for a batch of playlist entries."""

from datetime import datetime
from typing import cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from video.domain.access import ViewConditions, Viewer
from video.domain.feed import FeedCandidate, FeedPosition
from video.infrastructure.access import VIEW_SNAPSHOT
from video.infrastructure.delivery import selected_preview
from video.infrastructure.models import PublicationRow
from video.infrastructure.readiness import fraction, object_value, prepare, sequence

CARD_SQL = text(
    VIEW_SNAPSHOT.text.replace("SELECT p.id,", "SELECT s.inspection,p.id,").replace(
        "p.id=:video_id", "p.id=ANY(:video_ids)"
    )
)


class SqlPlaylistCardViews:
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

    async def candidates(
        self, ids: tuple[int, ...], viewer: Viewer
    ) -> tuple[tuple[FeedCandidate, ViewConditions], ...]:
        rows = (
            (
                await self.session.execute(
                    CARD_SQL,
                    {
                        "video_ids": list(ids),
                        "account_id": viewer.account_id,
                        "email": viewer.email,
                    },
                )
            )
            .mappings()
            .all()
        )
        result: list[tuple[FeedCandidate, ViewConditions]] = []
        for row in rows:
            try:
                media = object_value(row["media"])
                playable = bool(prepare(media).status.available_qualities)
                if not playable or row["added_at"] is None:
                    continue
                config = object_value(object_value(media["plan"])["config"])
                track = next(
                    object_value(item)
                    for item in sequence(object_value(row["inspection"])["tracks"])
                    if object_value(item)["stream_index"] == config["video_index"]
                )
                duration = fraction(track["presentation_duration"])
                if not 0 < duration <= 3600:
                    continue
                candidate = FeedCandidate(
                    FeedPosition(cast(datetime, row["added_at"]), cast(int, row["id"])),
                    cast(int, row["owner_id"]),
                    str(row["title"]),
                    float(duration),
                    selected_preview(media, row["cover"], row["cover_slot"]) is not None,
                )
                conditions = ViewConditions(
                    candidate.owner_id,
                    row["publication"] == "published",
                    row["moderation"] == "visible",
                    playable,
                    row["visibility"] == "public",
                    bool(row["permitted"]),
                )
                result.append((candidate, conditions))
            except KeyError, ValueError, TypeError, ZeroDivisionError, OverflowError, StopIteration:
                continue
        return tuple(result)
