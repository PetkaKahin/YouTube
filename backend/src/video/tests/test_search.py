"""Search filters, context-bound cursors and literal SQL matching on real media."""

import asyncio
import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from itertools import combinations
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from channel.public import Register, Registration
from video.domain.search import InvalidSearch, SearchCursor, SearchPosition, SearchQuery
from video.infrastructure.models import PublicationRow, SourceProcessingRow
from video.infrastructure.readiness import prepare
from video.infrastructure.search import matches_features
from video.public import GetSearch
from video.tests import test_feed as feed
from video.tests.test_publications import invoke
from video.tests.test_readiness import output, snapshot

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.fixture(scope="module")
def feed_seed(backend_runtime, tmp_path_factory, source_queue):
    return feed.feed_seed.__wrapped__(backend_runtime, tmp_path_factory, source_queue)


@pytest.fixture
def search_environment(backend_runtime, feed_seed):
    async def reset():
        async with backend_runtime() as (_, resources):
            async with resources.sessions.begin() as session:
                await session.execute(text("UPDATE video_publications SET visibility='private'"))

    asyncio.run(reset())
    return feed_seed


@pytest.mark.parametrize(
    "value,valid",
    [
        ("", False),
        (" \t ", False),
        ("a", True),
        ("Я" * 100, True),
        ("a" * 101, False),
        (" x ", True),
        ("x\x00", False),
        ("x\ud800", False),
        ("<script>", True),
        ("%;_\\!", True),
    ],
)
def test_text_boundaries_and_data_characters(value, valid):
    if valid:
        assert SearchQuery(value).normalized().text == value.strip()
    else:
        with pytest.raises(InvalidSearch):
            SearchQuery(value).normalized()


@pytest.mark.parametrize(
    "duration,seconds,expected",
    [
        (kind, seconds, expected)
        for kind, cases in {
            "short": [(Fraction(179999, 1000), True), (180, False)],
            "medium": [
                (Fraction(179999, 1000), False),
                (180, True),
                (1200, True),
                (Fraction(1200001, 1000), False),
            ],
            "long": [(1200, False), (Fraction(1200001, 1000), True)],
        }.items()
        for seconds, expected in cases
    ],
)
def test_exact_duration_boundaries(duration, seconds, expected):
    assert SearchQuery("x", duration=duration).duration_matches(Fraction(seconds)) == expected


@pytest.mark.parametrize(
    "period,anchor,expected",
    [
        ("today", "2026-03-31T21:00:00+00:00", "2026-03-31T21:00:00+00:00"),
        ("today", "2026-03-31T20:59:59+00:00", "2026-03-30T21:00:00+00:00"),
        ("week", "2026-11-15T21:01:00+00:00", "2026-11-15T21:00:00+00:00"),
        ("week", "2026-11-15T20:59:59+00:00", "2026-11-08T21:00:00+00:00"),
        ("month", "2026-03-31T21:00:00+00:00", "2026-03-31T21:00:00+00:00"),
        ("year", "2025-12-31T21:00:00+00:00", "2025-12-31T21:00:00+00:00"),
    ],
)
def test_moscow_calendar_boundaries(period, anchor, expected):
    assert SearchQuery("x", period=period).start(
        datetime.fromisoformat(anchor)
    ) == datetime.fromisoformat(expected)


def test_cursor_canonicality_query_binding_and_calendar_anchor():
    query = SearchQuery("Тест", features=("hdr", "hd")).normalized()
    value = SearchCursor(SearchPosition(feed.DATE, 1, 2**63 - 1), feed.DATE)
    token = value.encode(query)
    assert len(token) == 78 and SearchCursor.decode(token, query) == value
    for changed in (
        replace(query, text="other"),
        replace(query, kind="video"),
        replace(query, sort="updated"),
        replace(query, duration="short"),
        replace(query, period="week"),
        replace(query, features=()),
    ):
        with pytest.raises(InvalidSearch):
            SearchCursor.decode(token, changed)
    for malformed in ("", token + "=", token[:-1], "x" * 10000, "x" * 78):
        with pytest.raises(InvalidSearch):
            SearchCursor.decode(malformed, query)


@pytest.mark.parametrize(
    "boundary,source,expected",
    [
        (1, 1, 99),
        (1, 0, None),
        (1, 2, None),
        (0, 1, 0),
        (0, 0, 99),
        (0, 2, None),
        (2, 1, 0),
        (2, 0, 0),
        (2, 2, 99),
    ],
)
def test_cursor_seeks_each_source_in_global_type_order(boundary, source, expected):
    position = SearchPosition(feed.DATE, boundary, 99)
    query = SearchQuery("x")
    restored = SearchCursor.decode(SearchCursor(position, feed.DATE).encode(query), query)
    assert restored.position.source_id(source) == expected


@pytest.mark.parametrize(
    "anchor,period",
    [
        (datetime(1, 1, 1, tzinfo=UTC), "year"),
        (datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC), "today"),
    ],
)
def test_forged_calendar_overflow_is_invalid_cursor(anchor, period):
    query = SearchQuery("x", period=period)
    token = SearchCursor(SearchPosition(feed.DATE, 1, 1), anchor).encode(query)
    with pytest.raises(InvalidSearch):
        SearchCursor.decode(token, query)


@pytest.mark.parametrize("needle", ["тЕсТ", "%", "_", "!", "\\", "<script>", "';--"])
def test_literal_video_substring_case_and_injection(backend_runtime, search_environment, needle):
    async def exercise():
        async with backend_runtime() as (container, resources):
            identity, _ = (
                await feed.clone(resources, search_environment, title="Тест % _ ! \\ <script> ';--")
            )[0]
            await feed.clone(resources, search_environment, title="ordinary")
            result = await invoke(container, GetSearch, SearchQuery(needle, "video"))
            assert [item.video.id for item in result.items] == [identity]
            assert result.next_cursor is None

    asyncio.run(exercise())


def test_mixed_equal_date_pages_do_not_lose_channels_or_duplicate_videos(
    backend_runtime, search_environment
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            authors = []
            for _ in range(22):
                token = uuid4().hex
                authors.append(
                    await invoke(
                        container,
                        Register,
                        Registration(
                            "Смесь", token + "@example.com", "t" + token[:28], "abcdef", "abcdef"
                        ),
                    )
                )
            # Use an existing channel date to exercise the global type boundary.
            date = authors[-1].created_at
            videos = await feed.clone(
                resources, search_environment, count=21, when=date, title="Смесь"
            )
            expected_videos = sorted((identity for identity, _ in videos), reverse=True)
            cursor = None
            values = []
            pages = []
            while True:
                page = await invoke(container, GetSearch, SearchQuery("смесь"), cursor)
                pages.append(page)
                values.extend((item.position.kind, item.position.id) for item in page.items)
                cursor = page.next_cursor
                if cursor is None:
                    break
            assert values == [(1, identity) for identity in expected_videos] + [
                (0, author.channel_id) for author in reversed(authors)
            ]
            assert [len(page.items) for page in pages] == [20, 20, 3]

    asyncio.run(exercise())


def test_filtering_before_cursor_and_fresh_rights(backend_runtime, search_environment):
    async def exercise():
        async with backend_runtime() as (container, resources):
            valid = await feed.clone(resources, search_environment, count=21, title="FILTER")
            rejected = await feed.clone(
                resources,
                search_environment,
                count=43,
                when=feed.DATE + timedelta(days=1),
                title="FILTER",
            )
            async with resources.sessions.begin() as session:
                for _, upload_id in rejected:
                    source = await session.get(SourceProcessingRow, upload_id)
                    inspection = copy.deepcopy(source.inspection)
                    inspection["tracks"][0]["presentation_duration"] = [200, 1]
                    source.inspection = inspection
            query = SearchQuery("filter", "video", duration="short")
            page = await invoke(container, GetSearch, query)
            assert [item.video.id for item in page.items] == sorted(
                (identity for identity, _ in valid), reverse=True
            )[:20]
            assert page.next_cursor
            async with resources.sessions.begin() as session:
                (await session.get(PublicationRow, valid[0][0])).visibility = "private"
            assert (await invoke(container, GetSearch, query, page.next_cursor)).items == ()

    asyncio.run(exercise())


def test_updated_sort_and_period_filter_use_distinct_dates(backend_runtime, search_environment):
    async def exercise():
        async with backend_runtime() as (container, resources):
            now = datetime.now(UTC)
            old = (
                await feed.clone(
                    resources, search_environment, title="ordered", when=now - timedelta(days=500)
                )
            )[0][0]
            new = (
                await feed.clone(
                    resources, search_environment, title="ordered", when=now - timedelta(seconds=1)
                )
            )[0][0]
            async with resources.sessions.begin() as session:
                (await session.get(PublicationRow, old)).updated_at = now
                (await session.get(PublicationRow, new)).updated_at = now - timedelta(seconds=1)
            assert [
                item.video.id
                for item in (
                    await invoke(container, GetSearch, SearchQuery("ordered", "video"))
                ).items
            ] == [new, old]
            assert [
                item.video.id
                for item in (
                    await invoke(
                        container, GetSearch, SearchQuery("ordered", "video", sort="updated")
                    )
                ).items
            ] == [old, new]
            assert [
                item.video.id
                for item in (
                    await invoke(
                        container,
                        GetSearch,
                        SearchQuery("ordered", "video", sort="updated", period="year"),
                    )
                ).items
            ] == [new]

    asyncio.run(exercise())


def test_channel_names_tags_and_inapplicable_video_filters(backend_runtime, search_environment):
    async def exercise():
        async with backend_runtime() as (container, _):
            token = uuid4().hex[:20]
            author = await invoke(
                container,
                Register,
                Registration(
                    "Иван Поиск", token + "@example.com", "find" + token, "abcdef", "abcdef"
                ),
            )
            for query in (
                SearchQuery("ИВАН ПОИСК", "channel"),
                SearchQuery(
                    "@FIND" + token.upper(),
                    "channel",
                    duration="long",
                    features=("4k", "hdr", "subtitles"),
                ),
            ):
                page = await invoke(container, GetSearch, query)
                assert [item.channel.id for item in page.items] == [author.channel_id]
            assert (
                await invoke(
                    container, GetSearch, SearchQuery(author.tag + "@example.com", "channel")
                )
            ).items == ()

    asyncio.run(exercise())


FEATURES = [
    tuple(values)
    for size in range(5)
    for values in combinations(("hd", "4k", "hdr", "subtitles"), size)
]


@pytest.mark.parametrize("features", FEATURES)
def test_features_require_one_ready_variant_and_advertised_subtitles(features):
    data = snapshot()
    # Ready 4K SDR plus ready 720 HDR. Detection of each separately is insufficient.
    for variant, encoded, quality in zip(
        data["plan"]["config"]["variants"], data["outputs"][:2], (2160, 720), strict=True
    ):
        variant["quality"] = encoded["spec"]["quality"] = quality
    data["outputs"].extend([output(5, "subtitle"), output(6, "subtitle")])
    data["subtitles"] = [
        {
            "stream_index": 7,
            "video_encoded_id": video_id,
            "encoded_id": sub_id,
            "status": "queued",
            "language": "en",
        }
        for video_id, sub_id in ((1, 5), (2, 6))
    ]
    assert matches_features(prepare(data), features) == (
        not ("4k" in features and "hdr" in features)
    )
    # A missing AAC closes every variant and every combination.
    data["outputs"][2]["status"] = "pending"
    data["outputs"][2]["artifact"] = None
    assert not matches_features(prepare(data), features)


def test_unadvertised_incomplete_subtitle_groups_do_not_supply_search_feature():
    data = snapshot()
    data["outputs"].append(output(5, "subtitle"))
    data["subtitles"] = [
        {
            "stream_index": 7,
            "video_encoded_id": 1,
            "encoded_id": 5,
            "status": "queued",
            "language": "en",
        }
    ]
    prepared = prepare(data)
    assert prepared.status.subtitles[0].status == "ready"
    assert "TYPE=SUBTITLES" not in prepared.master
    assert not matches_features(prepared, ("subtitles",))


@pytest.mark.parametrize("period", ["today", "week", "month", "year"])
def test_period_sql_includes_start_and_anchor_and_excludes_neighbors(
    backend_runtime, search_environment, period
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            anchor = datetime(2026, 4, 15, 10, tzinfo=UTC)
            query = SearchQuery("Период", "video", period=period)
            start = query.start(anchor)
            before = (
                await feed.clone(
                    resources,
                    search_environment,
                    title="Период",
                    when=start - timedelta(microseconds=1),
                )
            )[0][0]
            first = (await feed.clone(resources, search_environment, title="Период", when=start))[
                0
            ][0]
            last = (await feed.clone(resources, search_environment, title="Период", when=anchor))[
                0
            ][0]
            future = (
                await feed.clone(
                    resources,
                    search_environment,
                    title="Период",
                    when=anchor + timedelta(microseconds=1),
                )
            )[0][0]
            cursor = SearchCursor(
                SearchPosition(datetime(9999, 1, 1, tzinfo=UTC), 1, 2**63 - 1), anchor
            ).encode(query)
            page = await invoke(container, GetSearch, query, cursor)
            assert [item.video.id for item in page.items] == [last, first]
            assert before not in [item.position.id for item in page.items] and future not in [
                item.position.id for item in page.items
            ]

    asyncio.run(exercise())


def test_period_cursor_keeps_first_calendar_window_across_midnight(
    backend_runtime, search_environment
):
    class Frozen(datetime):
        moment = datetime(2026, 3, 31, 20, 59, 59, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.moment.astimezone(tz) if tz else cls.moment.replace(tzinfo=None)

    async def exercise():
        async with backend_runtime() as (container, resources):
            pairs = await feed.clone(
                resources,
                search_environment,
                count=21,
                title="Полночь",
                when=Frozen.moment - timedelta(seconds=1),
            )
            query = SearchQuery("Полночь", "video", period="today")
            with patch("video.application.search.datetime", Frozen):
                first = await invoke(container, GetSearch, query)
                assert len(first.items) == 20 and first.next_cursor
                Frozen.moment += timedelta(seconds=2)
                second = await invoke(container, GetSearch, query, first.next_cursor)
                assert [item.video.id for item in second.items] == [pairs[0][0]]
                assert (await invoke(container, GetSearch, query)).items == ()

    asyncio.run(exercise())
