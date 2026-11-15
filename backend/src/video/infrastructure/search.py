"""Search public prepared videos, merge channels through their public contract."""

from datetime import datetime
from fractions import Fraction
from typing import cast

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from channel.public import FindChannels, FindPlaylists
from video.application.feed import FeedAuthors
from video.domain.feed import FeedAuthor, FeedCard
from video.domain.search import (
    SearchChannel,
    SearchItem,
    SearchPlaylist,
    SearchPosition,
    SearchQuery,
)
from video.infrastructure.delivery import selected_preview
from video.infrastructure.feed import FEED_SELECT, PUBLIC_READY_WHERE
from video.infrastructure.readiness import (
    PreparedProcessing,
    fraction,
    object_value,
    prepare,
    sequence,
)

# Dynamic SQL parts are selected only from these constants, never user text.
SEARCH_SQL = {
    sort: text(f"""
{FEED_SELECT} {PUBLIC_READY_WHERE}
 AND p.title ILIKE :pattern ESCAPE '!'
 AND (CAST(:since AS timestamptz) IS NULL OR (p.added_at>=:since AND p.added_at<=:until))
 AND (CAST(:before_at AS timestamptz) IS NULL OR
      (CAST(:before_id AS bigint) IS NULL AND p.{column}<=CAST(:before_at AS timestamptz)) OR
      (p.{column},p.id)<(CAST(:before_at AS timestamptz),CAST(:before_id AS bigint)))
 ORDER BY p.{column} DESC,p.id DESC LIMIT 21
""")  # noqa: S608 -- constant identifiers/fragments, parameters bound
    for sort, column in (("created", "added_at"), ("updated", "updated_at"))
}


def matches_features(prepared: PreparedProcessing, features: tuple[str, ...]) -> bool:
    return any(
        ("hd" not in features or quality.quality >= 720)
        and ("4k" not in features or quality.quality == 2160)
        and ("hdr" not in features or quality.range == "pq")
        and (
            "subtitles" not in features
            or (
                any(
                    sub.status == "ready"
                    and sub.quality == quality.quality
                    and sub.range == quality.range
                    for sub in prepared.status.subtitles
                )
                and prepared.master is not None
                and "TYPE=SUBTITLES" in prepared.master
            )
        )
        for quality in prepared.status.available_qualities
    )


class SqlSearchVideos:
    def __init__(self, session: AsyncSession, authors: FeedAuthors) -> None:
        self.session, self.authors = session, authors

    async def get(
        self, query: SearchQuery, after: SearchPosition | None, anchor: datetime
    ) -> tuple[SearchItem, ...]:
        pattern = "%" + query.text.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        before_at = after.date if after else None
        before_id = after.source_id(1) if after else None
        items: list[SearchItem] = []
        while True:
            rows = (
                (
                    await self.session.execute(
                        SEARCH_SQL[query.sort],
                        {
                            "pattern": pattern,
                            "since": query.start(anchor),
                            "until": anchor,
                            "before_at": before_at,
                            "before_id": before_id,
                        },
                    )
                )
                .mappings()
                .all()
            )
            valid: list[tuple[RowMapping, Fraction, bool]] = []
            for row in rows:
                try:
                    media = object_value(row["media"])
                    prepared = prepare(media)
                    if not prepared.status.available_qualities:
                        continue
                    config = object_value(object_value(media["plan"])["config"])
                    track = next(
                        object_value(value)
                        for value in sequence(object_value(row["inspection"])["tracks"])
                        if object_value(value)["stream_index"] == config["video_index"]
                    )
                    duration = fraction(track["presentation_duration"])
                    if not 0 < duration <= 3600 or not query.duration_matches(duration):
                        continue
                    if not matches_features(prepared, query.features):
                        continue
                    valid.append(
                        (
                            row,
                            duration,
                            selected_preview(media, row["cover"], row["cover_slot"]) is not None,
                        )
                    )
                except (
                    KeyError,
                    ValueError,
                    TypeError,
                    ZeroDivisionError,
                    OverflowError,
                    StopIteration,
                ):
                    continue
            authors = await self.authors.get(
                tuple(dict.fromkeys(cast(int, row["owner_id"]) for row, _, _ in valid))
            )
            for row, duration, preview in valid:
                author = authors.get(cast(int, row["owner_id"]))
                if author is not None:
                    position = SearchPosition(
                        cast(
                            datetime, row["added_at" if query.sort == "created" else "updated_at"]
                        ),
                        1,
                        cast(int, row["id"]),
                    )
                    items.append(
                        SearchItem(
                            position,
                            video=FeedCard(
                                position.id,
                                str(row["title"]),
                                cast(datetime, row["added_at"]),
                                float(duration),
                                author,
                                preview,
                            ),
                        )
                    )
                if len(items) == 21:
                    return tuple(items)
            if len(rows) < 21:
                return tuple(items)
            before_at, before_id = (
                rows[-1]["added_at" if query.sort == "created" else "updated_at"],
                rows[-1]["id"],
            )


class ChannelSearchResults:
    def __init__(self, channels: FindChannels) -> None:
        self.channels = channels

    async def get(
        self, query: SearchQuery, after: SearchPosition | None, anchor: datetime
    ) -> tuple[SearchItem, ...]:
        hits = await self.channels(
            query.text,
            after.date if after else None,
            after.source_id(0) if after else None,
            query.start(anchor),
            anchor if query.period else None,
        )
        return tuple(
            SearchItem(
                SearchPosition(hit.created_at, 0, hit.id),
                channel=SearchChannel(hit.id, hit.name, hit.tag, hit.created_at, hit.created_at),
            )
            for hit in hits
        )


class PlaylistSearchResults:
    def __init__(self, playlists: FindPlaylists) -> None:
        self.playlists = playlists

    async def get(
        self, query: SearchQuery, after: SearchPosition | None, anchor: datetime
    ) -> tuple[SearchItem, ...]:
        hits = await self.playlists(
            query.text,
            query.sort,
            after.date if after else None,
            after.source_id(2) if after else None,
            query.start(anchor),
            anchor if query.period else None,
        )
        return tuple(
            SearchItem(
                SearchPosition(
                    hit.playlist.created_at if query.sort == "created" else hit.playlist.updated_at,
                    2,
                    hit.playlist.id,
                ),
                playlist=SearchPlaylist(
                    hit.playlist.id,
                    hit.playlist.title,
                    hit.playlist.description,
                    hit.playlist.created_at,
                    hit.playlist.updated_at,
                    FeedAuthor(hit.channel.id, hit.channel.name, hit.channel.tag),
                ),
            )
            for hit in hits
        )
