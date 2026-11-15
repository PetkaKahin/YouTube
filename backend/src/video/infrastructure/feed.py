"""Bounded SQL seeks and the same committed-media validation as ordinary viewing."""

from datetime import datetime
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from channel.public import ChannelNotFound, FindChannel, GetPublicChannels
from video.application.feed import FeedChannel
from video.domain.feed import FEED_SIZE, FeedAuthor, FeedBatch, FeedCandidate, FeedPosition
from video.infrastructure.access import PREVIEW_JOINS
from video.infrastructure.delivery import selected_preview
from video.infrastructure.readiness import (
    MEDIA_JOINS,
    MEDIA_SNAPSHOT,
    fraction,
    object_value,
    prepare,
    sequence,
)

SCAN_SIZE = FEED_SIZE + 1
FEED_SELECT = f"""
SELECT p.id,p.owner_id,p.title,p.added_at,p.updated_at,s.inspection,
       {MEDIA_SNAPSHOT} AS media,
       coalesce(to_jsonb(new_cover),to_jsonb(old_cover)) AS cover,c.slot AS cover_slot
FROM video_publications p {MEDIA_JOINS} {PREVIEW_JOINS}
"""  # noqa: S608 -- fixed module SQL fragments only
PUBLIC_READY_WHERE = """
WHERE p.visibility='public' AND p.publication='published' AND p.moderation='visible'
  AND p.added_at IS NOT NULL AND m.status='planned'
  AND EXISTS (
    SELECT 1 FROM video_plan_tracks r JOIN video_encoded_tracks e ON e.id=r.encoded_id
    WHERE r.upload_id=u.id AND e.status='ready' AND e.spec->>'kind'='video')
"""
FEED_SQL = text(f"""
{FEED_SELECT} {PUBLIC_READY_WHERE}
  AND (CAST(:owner_id AS bigint) IS NULL OR p.owner_id=:owner_id)
  AND (CAST(:before_at AS timestamptz) IS NULL
       OR (p.added_at,p.id)<(CAST(:before_at AS timestamptz),CAST(:before_id AS bigint)))
ORDER BY p.added_at DESC,p.id DESC LIMIT :scan_size
""")  # noqa: S608 -- fixed module fragments only; cursor values are bound


class SqlFeedCandidates:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def scan(self, after: FeedPosition | None, owner_id: int | None = None) -> FeedBatch:
        rows = (
            (
                await self.session.execute(
                    FEED_SQL,
                    {
                        "before_at": after.added_at if after else None,
                        "before_id": after.id if after else None,
                        "scan_size": SCAN_SIZE,
                        "owner_id": owner_id,
                    },
                )
            )
            .mappings()
            .all()
        )
        items: list[FeedCandidate] = []
        for row in rows:
            try:
                media = object_value(row["media"])
                if not prepare(media).status.available_qualities:
                    continue
                inspection = object_value(row["inspection"])
                config = object_value(object_value(media["plan"])["config"])
                track = next(
                    object_value(value)
                    for value in sequence(inspection["tracks"])
                    if object_value(value)["stream_index"] == config["video_index"]
                )
                duration = fraction(track["presentation_duration"])
                if not 0 < duration <= 3600:
                    continue
                items.append(
                    FeedCandidate(
                        FeedPosition(cast(datetime, row["added_at"]), cast(int, row["id"])),
                        cast(int, row["owner_id"]),
                        str(row["title"]),
                        float(duration),
                        selected_preview(media, row["cover"], row["cover_slot"]) is not None,
                    )
                )
            except KeyError, ValueError, TypeError, ZeroDivisionError, OverflowError, StopIteration:
                continue
        last = rows[-1] if rows else None
        return FeedBatch(
            tuple(items),
            FeedPosition(cast(datetime, last["added_at"]), cast(int, last["id"])) if last else None,
            len(rows) < SCAN_SIZE,
        )


class ChannelFeedAuthors:
    def __init__(self, channels: GetPublicChannels) -> None:
        self.channels = channels

    async def get(self, owner_ids: tuple[int, ...]) -> dict[int, FeedAuthor]:
        return {
            owner_id: FeedAuthor(value.id, value.name, value.tag)
            for owner_id, value in (await self.channels(owner_ids)).items()
        }


class ChannelFeedScope:
    def __init__(self, find: FindChannel) -> None:
        self.lookup = find

    async def find(self, tag: str) -> FeedChannel | None:
        try:
            channel = await self.lookup(tag)
        except ChannelNotFound:
            return None
        return FeedChannel(channel.id, channel.owner_id)
