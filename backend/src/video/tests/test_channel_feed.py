"""Scoped channel cursors and readiness filtering reuse the real home-feed path."""

import asyncio
import copy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from channel.public import Register, Registration
from video.domain.feed import ChannelFeedCursor, FeedPosition, InvalidFeedCursor
from video.infrastructure.models import MediaPlanRow, PublicationRow, SourceProcessingRow
from video.public import GetChannelFeed, GetFeed, VideoNotFound
from video.tests import test_feed as feed
from video.tests.test_publications import invoke

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.fixture(scope="module")
def feed_seed(backend_runtime, tmp_path_factory, source_queue):
    return feed.feed_seed.__wrapped__(backend_runtime, tmp_path_factory, source_queue)


@pytest.fixture(autouse=True)
def reset_feed(backend_runtime, feed_seed):
    async def reset():
        async with backend_runtime() as (_, resources):
            async with resources.sessions.begin() as session:
                await session.execute(text("UPDATE video_publications SET visibility='private'"))

    asyncio.run(reset())


@pytest.mark.parametrize(
    "date",
    [
        feed.DATE,
        datetime(1, 1, 1, tzinfo=UTC),
        datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
    ],
)
def test_cursor_roundtrip_scope_bounds_and_other_list_formats(date):
    position = FeedPosition(date, 2**63 - 1)
    cursor = ChannelFeedCursor(2**63 - 1, position)
    token = cursor.encode()
    assert len(token) == 34 and ChannelFeedCursor.decode(token, cursor.channel_id) == cursor
    for invalid in ("", token + "=", token[:-1], "x" * 10000, position.encode()):
        with pytest.raises(InvalidFeedCursor):
            ChannelFeedCursor.decode(invalid, cursor.channel_id)
    with pytest.raises(InvalidFeedCursor):
        ChannelFeedCursor.decode(token, 1)
    with pytest.raises(InvalidFeedCursor):
        FeedPosition.decode(token)


@pytest.mark.parametrize("invalid_media", ["duration", "aac"])
def test_pages_same_dates_invalid_media_and_foreign_owner_do_not_shorten_page(
    backend_runtime, feed_seed, invalid_media
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = feed_seed[0]
            token = uuid4().hex
            other = await invoke(
                container,
                Register,
                Registration("Автор", token + "@example.com", "t" + token[:28], "abcdef", "abcdef"),
            )
            valid = await feed.clone(resources, feed_seed, count=41)
            rejected = await feed.clone(
                resources, feed_seed, count=43, when=feed.DATE + timedelta(days=1)
            )
            await feed.clone(
                resources,
                feed_seed,
                count=21,
                owner_id=other.account_id,
                when=feed.DATE + timedelta(days=2),
            )
            async with resources.sessions.begin() as session:
                for _, upload_id in rejected:
                    if invalid_media == "duration":
                        source = await session.get(SourceProcessingRow, upload_id)
                        data = copy.deepcopy(source.inspection)
                        data["tracks"][0]["presentation_duration"] = [0, 1]
                        source.inspection = data
                    else:
                        plan = await session.get(MediaPlanRow, upload_id)
                        config = copy.deepcopy(plan.config)
                        config["variants"][0]["audio"] = 2**62
                        plan.config = config
            pages, cursor = [], None
            for _ in range(3):
                page = await invoke(container, GetChannelFeed, "@" + owner.tag.upper(), cursor)
                pages.append(page)
                cursor = page.next_cursor
            assert [len(page.items) for page in pages] == [20, 20, 1] and cursor is None
            assert [item.id for page in pages for item in page.items] == sorted(
                (identity for identity, _ in valid), reverse=True
            )
            assert all(item.channel.id == owner.channel_id for page in pages for item in page.items)
            for tag, token in (
                (other.tag, pages[0].next_cursor),
                (owner.tag, (await invoke(container, GetFeed)).next_cursor),
            ):
                with pytest.raises(InvalidFeedCursor):
                    await invoke(container, GetChannelFeed, tag, token)
            with pytest.raises(InvalidFeedCursor):
                await invoke(container, GetFeed, pages[0].next_cursor)
            async with resources.sessions.begin() as session:
                (await session.get(PublicationRow, valid[0][0])).visibility = "private"
            last = await invoke(container, GetChannelFeed, owner.tag, pages[1].next_cursor)
            assert last.items == () and last.next_cursor is None
            with pytest.raises(VideoNotFound):
                await invoke(container, GetChannelFeed, "missing-channel")

    asyncio.run(exercise())
