"""Seek pagination against real committed media, profiles and PostgreSQL snapshots."""

import asyncio
import base64
import copy
import struct
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.exc import SQLAlchemyError

from channel.public import Register, Registration
from user.public import GetPublicNames
from video.domain.feed import FeedPosition, InvalidFeedCursor
from video.infrastructure.models import (
    EncodedTrackRow,
    MediaPlanRow,
    PlanTrackRow,
    PublicationRow,
    SourceProcessingRow,
    UploadRow,
)
from video.public import (
    CompleteUpload,
    CreateDraft,
    GetFeed,
    PublishVideo,
    StartUpload,
    VideoMetadata,
)
from video.tests import test_source_processing as support
from video.tests.test_publications import invoke
from video.tests.test_source_processing import process

pytestmark = pytest.mark.usefixtures("video_media_cleanup")
DATE = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)


@pytest.mark.parametrize(
    "date",
    [
        DATE,
        DATE.replace(microsecond=1),
        datetime(1, 1, 1, tzinfo=UTC),
        datetime(9999, 12, 31, tzinfo=UTC),
    ],
)
@pytest.mark.parametrize("identifier", [1, 2**63 - 1])
def test_cursor_roundtrip_is_exact_at_microsecond_and_bigint_boundaries(date, identifier):
    value = FeedPosition(date, identifier)
    token = value.encode()
    assert len(token) == 23 and "=" not in token
    assert FeedPosition.decode(token) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        1,
        "",
        "a" * 22,
        "a" * 24,
        "x" * 10000,
        "'; DROP TABLE video_publications;--",
        "<script>alert(1)</script>",
        "../etc/passwd",
        "\x00",
        "a\ud800",
    ],
)
def test_malformed_cursor_is_not_interpreted(value):
    with pytest.raises(InvalidFeedCursor):
        FeedPosition.decode(value)


@pytest.mark.parametrize(
    "version,micros,identifier",
    [(0, 0, 1), (2, 0, 1), (1, 0, 0), (1, 0, 2**63), (1, 2**63 - 1, 1), (1, -(2**63), 1)],
)
def test_unknown_version_overflow_and_invalid_id_are_rejected(version, micros, identifier):
    token = (
        base64.urlsafe_b64encode(struct.pack(">BqQ", version, micros, identifier))
        .decode()
        .rstrip("=")
    )
    with pytest.raises(InvalidFeedCursor):
        FeedPosition.decode(token)


def test_noncanonical_padding_bits_are_rejected():
    token = FeedPosition(DATE, 1).encode()
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    alias = token[:-1] + alphabet[alphabet.index(token[-1]) + 1]
    with pytest.raises(InvalidFeedCursor):
        FeedPosition.decode(alias)


@pytest.fixture(scope="module")
def feed_seed(backend_runtime, tmp_path_factory, source_queue):
    data = support.small_video.__wrapped__(tmp_path_factory)
    directory = tmp_path_factory.mktemp("feed")

    async def create():
        async with backend_runtime() as (container, resources):
            token = uuid4().hex
            author = await invoke(
                container,
                Register,
                Registration("Автор", token + "@example.com", "t" + token[:28], "abcdef", "abcdef"),
            )
            draft = await invoke(
                container, CreateDraft, author.account_id, VideoMetadata("base", "", "private")
            )
            upload = await invoke(container, StartUpload, author.account_id, draft.id, len(data))
            async with resources.sessions() as session:
                row = await session.get(UploadRow, upload.id)
                key, multipart = row.object_key, row.multipart_id
            await asyncio.to_thread(
                resources.objects.upload_part,
                Bucket=resources.bucket,
                Key=key,
                UploadId=multipart,
                PartNumber=1,
                Body=data,
            )
            await invoke(container, CompleteUpload, author.account_id, draft.id, upload.id)
            await process(container, resources, directory, source_queue)
            await invoke(container, PublishVideo, author.account_id, draft.id)
            return author, draft.id, upload.id

    return asyncio.run(create())


@pytest.fixture(autouse=True)
def reset_feed(backend_runtime, feed_seed):
    async def reset():
        async with backend_runtime() as (_, resources):
            async with resources.sessions.begin() as session:
                await session.execute(text("UPDATE video_publications SET visibility='private'"))

    asyncio.run(reset())


async def clone(resources, seed, *, count=1, when=DATE, **changes):
    """Set up publication links to one genuinely encoded package, without re-encoding."""
    author, _, source_id = seed
    async with resources.sessions.begin() as session:
        original = await session.get(UploadRow, source_id)
        inspected = await session.get(SourceProcessingRow, source_id)
        plan = await session.get(MediaPlanRow, source_id)
        tracks = (
            await session.scalars(
                select(PlanTrackRow.encoded_id).where(PlanTrackRow.upload_id == source_id)
            )
        ).all()
        result = []
        for _ in range(count):
            publication = PublicationRow(
                **{
                    "owner_id": author.account_id,
                    "title": "<script>as text</script>",
                    "visibility": "public",
                    "publication": "published",
                    "upload": "uploaded",
                    "added_at": when,
                    **changes,
                }
            )
            session.add(publication)
            await session.flush()
            upload = UploadRow(
                video_id=publication.id,
                object_key="tests/feed/" + uuid4().hex,
                size=original.size,
                status="uploaded",
            )
            session.add(upload)
            await session.flush()
            session.add(
                SourceProcessingRow(
                    upload_id=upload.id,
                    job_id=1000000 + upload.id,
                    status="inspected",
                    content_id=inspected.content_id,
                    inspection=copy.deepcopy(inspected.inspection),
                    inspected_at=inspected.inspected_at,
                )
            )
            await session.flush()
            session.add(
                MediaPlanRow(
                    upload_id=upload.id,
                    job_id=2000000 + upload.id,
                    status="planned",
                    config=copy.deepcopy(plan.config),
                )
            )
            await session.flush()
            session.add_all(
                PlanTrackRow(upload_id=upload.id, encoded_id=identity) for identity in tracks
            )
            result.append((publication.id, upload.id))
    return result


@pytest.mark.parametrize("count", [0, 1, 19, 20, 21, 40, 41])
def test_page_sizes_equal_dates_no_duplicates_or_false_continuation(
    backend_runtime, feed_seed, count
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            pairs = await clone(resources, feed_seed, count=count)
            expected = sorted((identity for identity, _ in pairs), reverse=True)
            found, cursor = [], None
            while True:
                page = await invoke(container, GetFeed, cursor)
                assert len(page.items) == min(20, len(expected) - len(found))
                found.extend(item.id for item in page.items)
                if page.next_cursor is None:
                    break
                assert FeedPosition.decode(page.next_cursor) == FeedPosition(
                    DATE, page.items[-1].id
                )
                cursor = page.next_cursor
            assert found == expected

    asyncio.run(exercise())


def test_fresh_visibility_deleted_boundary_and_newer_insert_do_not_shift_next_page(
    backend_runtime, feed_seed
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            pairs = await clone(resources, feed_seed, count=25)
            page = await invoke(container, GetFeed)
            assert len(page.items) == 20 and page.next_cursor
            remaining = sorted((identity for identity, _ in pairs), reverse=True)[20:]
            await clone(resources, feed_seed, when=DATE + timedelta(microseconds=1))
            async with resources.sessions.begin() as session:
                # The cursor must not depend on looking up its original row.
                boundary_upload = dict(pairs)[page.items[-1].id]
                for model in (PlanTrackRow, MediaPlanRow, SourceProcessingRow):
                    await session.execute(delete(model).where(model.upload_id == boundary_upload))
                await session.execute(delete(UploadRow).where(UploadRow.id == boundary_upload))
                await session.execute(
                    delete(PublicationRow).where(PublicationRow.id == page.items[-1].id)
                )
                (await session.get(PublicationRow, remaining[0])).visibility = "private"
                (await session.get(PublicationRow, remaining[1])).moderation = "hidden"
            next_page = await invoke(container, GetFeed, page.next_cursor)
            assert [item.id for item in next_page.items] == remaining[2:]
            assert next_page.next_cursor is None
            first = await invoke(container, GetFeed)
            assert first.items[0].added_at == DATE + timedelta(microseconds=1)

    asyncio.run(exercise())


def test_filters_before_page_boundary_and_ready_audio_dependency_validation(
    backend_runtime, feed_seed
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            good = await clone(resources, feed_seed, count=21)
            invalid = await clone(resources, feed_seed, count=45, when=DATE + timedelta(days=1))
            async with resources.sessions.begin() as session:
                for _, upload_id in invalid:
                    plan = await session.get(MediaPlanRow, upload_id)
                    config = copy.deepcopy(plan.config)
                    config["variants"][0]["audio"] = 2**62  # absent required AAC, never playable
                    plan.config = config
            page = await invoke(container, GetFeed)
            assert [item.id for item in page.items] == sorted(
                (identity for identity, _ in good), reverse=True
            )[:20]
            assert page.next_cursor
            next_page = await invoke(container, GetFeed, page.next_cursor)
            assert [item.id for item in next_page.items] == [
                good[0][0]
            ] and next_page.next_cursor is None

    asyncio.run(exercise())


def test_order_uses_added_date_not_publication_edit_or_secondary_quality(
    backend_runtime, feed_seed
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            older = (await clone(resources, feed_seed, when=DATE))[0]
            newer = (await clone(resources, feed_seed, when=DATE + timedelta(microseconds=1)))[0]
            async with resources.sessions.begin() as session:
                (await session.get(PublicationRow, older[0])).publication = "draft"
            assert [card.id for card in (await invoke(container, GetFeed)).items] == [newer[0]]
            await invoke(container, PublishVideo, feed_seed[0].account_id, older[0])
            async with resources.sessions.begin() as session:
                plan = await session.get(MediaPlanRow, older[1])
                config = copy.deepcopy(plan.config)
                config["variants"].append(
                    {"quality": 720, "range": "sdr", "video": 2**62, "audio": None}
                )
                plan.config = config
            assert [card.id for card in (await invoke(container, GetFeed)).items] == [
                newer[0],
                older[0],
            ]

    asyncio.run(exercise())


def test_batch_public_profiles_no_n_plus_one_no_private_fields_or_object_reads(
    backend_runtime, feed_seed
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            pairs = await clone(resources, feed_seed, count=21)
            channels = [feed_seed[0].channel_id]
            for identity, _ in pairs[1:]:
                token = uuid4().hex
                author = await invoke(
                    container,
                    Register,
                    Registration(
                        "Автор", token + "@example.com", "t" + token[:28], "abcdef", "abcdef"
                    ),
                )
                channels.append(author.channel_id)
                async with resources.sessions.begin() as session:
                    (await session.get(PublicationRow, identity)).owner_id = author.account_id
            statements = []

            def capture(_, __, statement, *args):
                statements.append(statement)

            event.listen(resources.engine.sync_engine, "before_cursor_execute", capture)
            try:
                with (
                    patch.object(
                        resources.objects, "get_object", side_effect=AssertionError("I/O")
                    ),
                    patch.object(
                        resources.objects, "head_object", side_effect=AssertionError("I/O")
                    ),
                ):
                    page = await invoke(container, GetFeed)
            finally:
                event.remove(resources.engine.sync_engine, "before_cursor_execute", capture)
            selects = [value for value in statements if value.lstrip().startswith("SELECT")]
            assert len(selects) == 3
            names = next(value for value in selects if "user_accounts" in value)
            assert all(field not in names for field in (".email", ".role", ".password_hash"))
            assert page.items[0].channel.name == "Автор"
            assert [item.channel.id for item in page.items] == list(reversed(channels))[:20]
            assert page.items[0].duration_seconds == 0.5 and page.items[0].preview_ready

    asyncio.run(exercise())


def test_failed_read_does_not_return_cached_page_or_change_publications(backend_runtime, feed_seed):
    async def exercise():
        async with backend_runtime() as (container, resources):
            identity, _ = (await clone(resources, feed_seed))[0]
            before = await invoke(container, GetFeed)
            with (
                patch.object(
                    GetPublicNames, "__call__", side_effect=SQLAlchemyError("private marker")
                ),
                pytest.raises(SQLAlchemyError),
            ):
                await invoke(container, GetFeed)
            assert await invoke(container, GetFeed) == before
            async with resources.sessions() as session:
                row = await session.get(PublicationRow, identity)
                assert row.visibility == "public" and row.added_at == DATE

    asyncio.run(exercise())


def test_missing_preview_does_not_hide_playable_video(backend_runtime, feed_seed):
    async def exercise():
        async with backend_runtime() as (container, resources):
            identity, upload_id = (await clone(resources, feed_seed))[0]
            async with resources.sessions.begin() as session:
                plan = await session.get(MediaPlanRow, upload_id)
                plan.config = {**plan.config, "thumbnail": None}
            page = await invoke(container, GetFeed)
            assert [item.id for item in page.items] == [identity] and not page.items[
                0
            ].preview_ready

    asyncio.run(exercise())


def test_corrupt_ready_package_is_not_a_feed_candidate(backend_runtime, feed_seed):
    async def exercise():
        async with backend_runtime() as (container, resources):
            _, upload_id = (await clone(resources, feed_seed))[0]
            async with resources.sessions.begin() as session:
                plan = await session.get(MediaPlanRow, upload_id)
                config = copy.deepcopy(plan.config)
                original = await session.get(EncodedTrackRow, config["variants"][0]["video"])
                broken = EncodedTrackRow(
                    track_id=original.track_id,
                    profile_key=uuid4().hex + uuid4().hex,
                    spec=copy.deepcopy(original.spec),
                    source_upload_id=upload_id,
                    stream_index=0,
                    job_id=3000000 + upload_id,
                    status="ready",
                    artifact=copy.deepcopy(original.artifact),
                )
                session.add(broken)
                await session.flush()
                artifact = copy.deepcopy(broken.artifact)
                artifact["prefix"] = f"encoded/{broken.id}/{uuid4().hex}/"
                artifact["files"] = artifact["files"][:-1]
                broken.artifact = artifact
                config["variants"][0]["video"] = broken.id
                plan.config = config
                session.add(PlanTrackRow(upload_id=upload_id, encoded_id=broken.id))
            assert (await invoke(container, GetFeed)).items == ()

    asyncio.run(exercise())


def test_duration_uses_selected_presentation_timeline_not_container_or_other_tracks(
    backend_runtime, feed_seed
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            await clone(resources, feed_seed)
            async with resources.sessions.begin() as session:
                rows = (await session.scalars(select(SourceProcessingRow))).all()
                for source in rows:
                    inspection = copy.deepcopy(source.inspection)
                    inspection["duration_num"] = 37
                    inspection["duration_den"] = 1
                    inspection["tracks"].append(
                        {"stream_index": 9999, "presentation_duration": [90, 1]}
                    )
                    source.inspection = inspection
            page = await invoke(container, GetFeed)
            assert len(page.items) == 1 and page.items[0].duration_seconds == 0.5

    asyncio.run(exercise())


def test_private_hidden_drafts_and_missing_dates_do_not_supply_cards_or_continuation(
    backend_runtime, feed_seed
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            identity, _ = (await clone(resources, feed_seed))[0]
            for values in (
                {"visibility": "private"},
                {"moderation": "hidden"},
                {"publication": "draft"},
                {"added_at": None},
            ):
                await clone(resources, feed_seed, count=21, **values)
            page = await invoke(container, GetFeed)
            assert [item.id for item in page.items] == [identity] and page.next_cursor is None

    asyncio.run(exercise())
