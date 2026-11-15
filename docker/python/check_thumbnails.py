"""Private media qualification in a disposable database; run via media-worker stdin."""

import asyncio
import importlib.util
import os
import subprocess
import sys
from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen
from uuid import uuid4

from botocore.exceptions import ClientError
from psycopg import AsyncConnection, sql
from sqlalchemy import select, text
from channel.public import (
    Register,
    Registration,
    CreatePlaylist,
    GetPlaylist,
    PlaylistMetadata,
    SetVideoPlaylists,
    GetChannel,
    GetChannelPlaylists,
    GetPublicChannels,
    GetSubscription,
    SetSubscription,
)
from video.infrastructure.thumbnail_media import S3ThumbnailProcessor

from app.di import create_container
from app.media_worker import create_queue
from app.upload_worker import create_queue as create_cleanup_queue, enqueue_original_cleanup
from app.resources import Resources, create_cache, database_url
from app.secrets import read_secret
from app.settings import Settings
from common.contracts.storage import ObjectBucket
from common.infrastructure.job_errors import DurableJobErrors, ErrorSpool, PostgresJobErrorStore
from common.infrastructure.job_monitor import configure_safe_queue_logging, error_history_middleware
from user.public import GetAccount
from video.infrastructure.encoder import S3MediaEncoder
from video.infrastructure.readiness import SqlProcessingViews
from video.infrastructure.models import (
    EncodedTrackRow,
    EncodingAttemptRow,
    SubtitleLinkRow,
    ThumbnailPreparationRow,
    ThumbnailUploadRow,
    UploadRow,
)
from video.public import (
    MEDIA_QUEUE,
    CompleteThumbnailUpload,
    CompleteUpload,
    CreateDraft,
    GetThumbnailImage,
    GetThumbnails,
    GetVideoSettings,
    GetProcessing,
    CleanupOriginals,
    RefreshUpload,
    SetPrivateAccess,
    GetPrivateAccess,
    GetViewableVideo,
    GetFeed,
    GetVideoLike,
    SetVideoLike,
    GetChannelFeed,
    GetSearch,
    SearchQuery,
    VideoNotFound,
    PublishVideo,
    RepairThumbnails,
    SelectThumbnail,
    StartThumbnailUpload,
    StartUpload,
    VideoMetadata,
)


async def qualify(check=None, *, production=True):
    configure_safe_queue_logging()
    if production:
        assert importlib.util.find_spec("pytest") is None
    else:
        assert importlib.util.find_spec("pytest") is not None
    database = "qualification_thumbnails_" + uuid4().hex
    admin = (
        database_url(Settings(), read_secret("POSTGRES_PASSWORD"))
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )

    async def manage(create):
        async with await AsyncConnection.connect(admin, autocommit=True) as connection:
            command = "CREATE DATABASE {}" if create else "DROP DATABASE {} WITH (FORCE)"
            await connection.execute(sql.SQL(command).format(sql.Identifier(database)))

    await manage(True)
    container = resources = None
    try:
        os.environ["POSTGRES_DB"] = database
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        settings = Settings()
        container = create_container(settings, create_cache(settings, ""), require_cache=False)

        async def invoke(scenario, *args):
            async with container() as operation:
                return await (await operation.get(scenario))(*args)

        with (
            patch(
                "redis.asyncio.Redis.execute_command",
                side_effect=ConnectionError("synthetic cache outage"),
            )
            if check is None
            else nullcontext()
        ):
            resources = await container.get(Resources)
            with TemporaryDirectory() as work:
                directory = Path(work)

                def ffmpeg(*args):
                    subprocess.run(
                        ["ffmpeg", "-v", "error", "-nostdin", *map(str, args)],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=60,
                    )

                video = directory / "source.mp4"
                captions = directory / "captions.srt"
                captions.write_text("1\n00:00:00,100 --> 00:00:00,300\nПривет\n", encoding="utf-8")
                media_input = (
                    [
                        "-f",
                        "lavfi",
                        "-i",
                        "testsrc2=size=1280x720:rate=1:duration=1",
                        "-f",
                        "lavfi",
                        "-i",
                        "sine=sample_rate=48000:duration=1",
                    ]
                    if check
                    else ["-f", "lavfi", "-i", "testsrc2=size=96x64:rate=24:duration=0.5"]
                )
                ffmpeg(
                    *media_input,
                    "-i",
                    captions,
                    "-c:v",
                    "mpeg4",
                    "-c:a",
                    "aac",
                    "-c:s",
                    "mov_text",
                    "-output_ts_offset",
                    "5",
                    video,
                )
                for extension in ("png", "bmp"):
                    ffmpeg(
                        "-f",
                        "lavfi",
                        "-i",
                        "testsrc=size=32x48",
                        "-frames:v",
                        "1",
                        directory / ("image." + extension),
                    )
                journal = DurableJobErrors(
                    ErrorSpool(directory / "history"), PostgresJobErrorStore(resources.sessions)
                )

                async def process():
                    queue = create_queue(settings, container)
                    async with queue.open_async():
                        await queue.run_worker_async(
                            queues=[MEDIA_QUEUE],
                            concurrency=1,
                            wait=False,
                            listen_notify=False,
                            install_signal_handlers=False,
                            worker_middleware=[error_history_middleware(journal)],
                        )

                pairs = []
                for _ in range(2):
                    token = uuid4().hex
                    registration = await invoke(
                        Register,
                        Registration(
                            "Author", token + "@example.com", "t" + token[:28], "abcdef", "abcdef"
                        ),
                    )
                    owner = await invoke(GetAccount, registration.account_id)
                    draft = await invoke(
                        CreateDraft, owner.id, VideoMetadata("Private preview", "", "private")
                    )
                    plan = await invoke(StartUpload, owner.id, draft.id, video.stat().st_size)
                    async with resources.sessions() as session:
                        upload = await session.get(UploadRow, plan.id)
                        key, multipart = upload.object_key, upload.multipart_id
                    part = await asyncio.to_thread(
                        resources.objects.upload_part,
                        Bucket=resources.bucket,
                        Key=key,
                        UploadId=multipart,
                        PartNumber=1,
                        Body=video.read_bytes(),
                    )
                    assert part["ETag"]
                    await invoke(CompleteUpload, owner.id, draft.id, plan.id)
                    pairs.append((owner.id, draft.id))
                await process()
                for owner_id, video_id in pairs:
                    options = await invoke(GetThumbnails, owner_id, video_id)
                    assert (
                        len(options.candidates) == 3
                        and options.selected == "generated"
                        and options.slot == 0
                    )
                await invoke(SelectThumbnail, *pairs[0], 2)
                assert (await invoke(GetThumbnails, *pairs[1])).slot == 0

                image_ids = []
                for (owner_id, video_id), extension in zip(pairs, ("png", "bmp"), strict=True):
                    data = (directory / ("image." + extension)).read_bytes()
                    plan = await invoke(StartThumbnailUpload, owner_id, video_id, len(data))
                    image_ids.append(plan.id)
                    async with resources.sessions() as session:
                        upload = await session.get(ThumbnailUploadRow, plan.id)
                        key, multipart = upload.object_key, upload.multipart_id
                    await asyncio.to_thread(
                        resources.objects.upload_part,
                        Bucket=resources.bucket,
                        Key=key,
                        UploadId=multipart,
                        PartNumber=1,
                        Body=data,
                    )
                    await invoke(CompleteThumbnailUpload, owner_id, video_id, plan.id)
                await process()
                async with resources.sessions() as session:
                    uploads = [
                        await session.get(ThumbnailUploadRow, identity) for identity in image_ids
                    ]
                    assert uploads[0].encoded_id == uploads[1].encoded_id
                    outputs = (await session.scalars(select(EncodedTrackRow))).all()
                assert all(output.status == "ready" for output in outputs)
                async with resources.sessions() as session:
                    references = (await session.scalars(select(SubtitleLinkRow))).all()
                    links = [
                        {
                            (ref.video_encoded_id, ref.stream_index, ref.encoded_id)
                            for ref in references
                            if ref.upload_id == upload_id
                        }
                        for upload_id in sorted({ref.upload_id for ref in references})
                    ]
                    assert len(references) == (6 if check else 2)
                    assert (
                        len(links) == 2
                        and links[0] == links[1]
                        and len(links[0]) == (3 if check else 1)
                    )
                for output in outputs:
                    artifact = output.artifact
                    for item in artifact["files"]:
                        response = await asyncio.to_thread(
                            resources.objects.get_object,
                            Bucket=resources.bucket,
                            Key=artifact["prefix"] + item["name"],
                        )
                        try:
                            data = response["Body"].read()
                        finally:
                            response["Body"].close()
                        assert (
                            len(data) == item["size"] and sha256(data).hexdigest() == item["sha256"]
                        )
                        if output.spec["kind"] in {"video", "audio", "subtitle"}:
                            package = directory / "master" / "tracks" / str(output.id)
                            package.mkdir(parents=True, exist_ok=True)
                            (package / item["name"]).write_bytes(data)
                        if item["name"].endswith("webp"):
                            path = directory / (str(output.id) + "-" + item["name"])
                            path.write_bytes(data)
                            ffmpeg("-i", path, "-f", "null", "-")
                        elif item["name"].endswith(".vtt"):
                            assert (
                                "Привет" in data.decode()
                                and b"00:00:00.100 --> 00:00:00.300" in data
                            )
                            assert b"X-TIMESTAMP-MAP=" in data
                for pair in pairs:
                    assert (await invoke(GetThumbnails, *pair)).selected == "custom"
                    assert (await invoke(GetThumbnailImage, *pair))[8:12] == b"WEBP"
                    assert (await invoke(GetVideoSettings, *pair)).processing.value == "ready"
                    state = await invoke(GetProcessing, *pair)
                    assert state.processing.value == "ready" and state.available_qualities
                    async with resources.sessions() as session:
                        prepared = await SqlProcessingViews(session).prepared(pair[1], pair[0])
                    assert "TYPE=SUBTITLES" in prepared.master and "av01." in prepared.master
                    master = directory / "master" / "index.m3u8"
                    master.write_text(prepared.master)
                    ffmpeg(
                        "-protocol_whitelist",
                        "file",
                        "-allowed_extensions",
                        "ALL",
                        "-i",
                        master,
                        "-map",
                        "0:v:0",
                        "-f",
                        "null",
                        "-",
                    )
                await invoke(RepairThumbnails)
                await invoke(RepairThumbnails)
                subtitle = next(output for output in outputs if output.spec["kind"] == "subtitle")
                sample_key = subtitle.artifact["prefix"] + "segment_000000.vtt"
                try:
                    with urlopen(
                        "http://rustfs:9000/" + resources.bucket + "/" + sample_key, timeout=5
                    ):
                        raise AssertionError("Anonymous media access")
                except HTTPError as error:
                    assert error.code == 403
                cleanup_queue = create_cleanup_queue(settings, container)
                async with cleanup_queue.open_async():
                    await enqueue_original_cleanup(cleanup_queue)
                    await enqueue_original_cleanup(cleanup_queue)
                    await cleanup_queue.run_worker_async(
                        queues=["maintenance"],
                        concurrency=1,
                        wait=False,
                        listen_notify=False,
                        install_signal_handlers=False,
                    )
                assert await invoke(CleanupOriginals) == 0
                async with resources.sessions() as session:
                    originals = (await session.scalars(select(UploadRow))).all()
                assert len(originals) == 2 and all(row.original_deleted_at for row in originals)
                for row in originals:
                    try:
                        await asyncio.to_thread(
                            resources.objects.head_object,
                            Bucket=resources.bucket,
                            Key=row.object_key,
                        )
                    except ClientError as error:
                        assert error.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}
                    else:
                        raise AssertionError("Original survived completion")
                    owner_id = next(
                        owner_id for owner_id, video_id in pairs if video_id == row.video_id
                    )
                    for action in (CompleteUpload, RefreshUpload):
                        assert (
                            await invoke(action, owner_id, row.video_id, row.id)
                        ).status == "uploaded"
                    assert (
                        await invoke(GetProcessing, owner_id, row.video_id)
                    ).processing.value == "ready"
                for output in outputs:
                    for item in output.artifact["files"]:
                        assert (
                            await asyncio.to_thread(
                                resources.objects.head_object,
                                Bucket=resources.bucket,
                                Key=output.artifact["prefix"] + item["name"],
                            )
                        )["ContentLength"] == item["size"]
                for pair in pairs:
                    await invoke(PublishVideo, *pair)
                first, second = pairs
                recipient = await invoke(GetAccount, second[0])
                profiles = await invoke(GetPublicChannels, (first[0], second[0]))
                own_subscription = await invoke(GetSubscription, first[0], profiles[first[0]].tag)
                assert not own_subscription.subscribed and not own_subscription.can_subscribe
                assert (await invoke(SetSubscription, recipient.id, profiles[first[0]].tag, True)).subscribed
                assert (await invoke(GetViewableVideo, first[0], first[1])).qualities
                for actor in (None, recipient.id):
                    try:
                        await invoke(GetViewableVideo, actor, first[1])
                    except VideoNotFound:
                        pass
                    else:
                        raise AssertionError("Private metadata leaked")
                assert (await invoke(SetPrivateAccess, *first, recipient.email.upper(), True)).emails == (
                    recipient.email,
                )
                assert (await invoke(GetPrivateAccess, *first)).emails == (recipient.email,)
                before_like = await invoke(GetVideoSettings, *first)
                liked = await invoke(SetVideoLike, recipient.id, first[1], True)
                assert liked.liked and liked.like_count == 1 and liked.can_like
                assert await invoke(SetVideoLike, recipient.id, first[1], True) == liked
                assert await invoke(GetVideoSettings, *first) == before_like
                assert not (await invoke(SetSubscription, recipient.id, profiles[first[0]].tag, False)).subscribed
                assert (await invoke(GetViewableVideo, recipient.id, first[1])).id == first[1]
                assert (await invoke(SetPrivateAccess, *first, recipient.email, False)).emails == ()
                assert (await invoke(SetSubscription, recipient.id, profiles[first[0]].tag, True)).subscribed
                try:
                    await invoke(GetViewableVideo, recipient.id, first[1])
                except VideoNotFound:
                    pass
                else:
                    raise AssertionError("Revoked metadata access survived")
                assert (await invoke(GetVideoLike, first[0], first[1])).like_count == 1
                try:
                    await invoke(GetVideoLike, recipient.id, first[1])
                except VideoNotFound:
                    pass
                else:
                    raise AssertionError("Private like state survived revocation")
                await invoke(SetPrivateAccess, *first, recipient.email, True)
                assert (await invoke(SetVideoLike, recipient.id, first[1], False)).like_count == 0
                await invoke(SetPrivateAccess, *first, recipient.email, False)
                assert not (await invoke(SetSubscription, recipient.id, profiles[first[0]].tag, False)).subscribed
                if check is None:
                    addresses = [f"cursor{index:03d}@example.com" for index in range(21)]
                    for address in addresses:
                        await invoke(SetPrivateAccess, *first, address, True)
                    permission_page = await invoke(GetPrivateAccess, *first)
                    assert permission_page.emails == tuple(reversed(addresses))[:20]
                    assert permission_page.next_cursor
                    final_page = await invoke(GetPrivateAccess, *first, permission_page.next_cursor)
                    assert final_page.emails == (addresses[0],) and final_page.next_cursor is None
                    for address in addresses:
                        await invoke(SetPrivateAccess, *first, address, False)
                async with resources.sessions.begin() as session:
                    await session.execute(
                        text("UPDATE video_publications SET visibility='public' WHERE id=:id"),
                        {"id": first[1]},
                    )
                assert (await invoke(GetViewableVideo, None, first[1])).id == first[1]
                assert (await invoke(SetVideoLike, *first, True)).like_count == 1
                guest_like = await invoke(GetVideoLike, None, first[1])
                assert guest_like.like_count == 1 and not guest_like.liked and not guest_like.can_like
                assert (await invoke(GetVideoLike, *second)).like_count == 0
                assert (await invoke(SetVideoLike, *first, False)).like_count == 0
                assert [item.id for item in (await invoke(GetFeed)).items] == [first[1]]
                profiles = await invoke(GetPublicChannels, (first[0], second[0]))
                channel = await invoke(GetChannel, "@" + profiles[first[0]].tag.upper())
                assert channel.id == profiles[first[0]].id and channel.name == "Author"
                assert [item.id for item in (await invoke(GetChannelFeed, channel.tag)).items] == [first[1]]
                assert (await invoke(GetChannelFeed, profiles[second[0]].tag)).items == ()
                assert [
                    item.video.id
                    for item in (
                        await invoke(GetSearch, SearchQuery("PRIVATE PREVIEW", "video"))
                    ).items
                ] == [first[1]]
                async with resources.sessions.begin() as session:
                    await session.execute(
                        text("UPDATE video_publications SET moderation='hidden' WHERE id=:id"),
                        {"id": first[1]},
                    )
                try:
                    await invoke(GetViewableVideo, first[0], first[1])
                except VideoNotFound:
                    pass
                else:
                    raise AssertionError("Hidden video opened to its owner")
                assert (await invoke(GetFeed)).items == ()
                assert (await invoke(GetChannelFeed, channel.tag)).items == ()
                assert (
                    await invoke(GetSearch, SearchQuery("PRIVATE PREVIEW", "video"))
                ).items == ()
                if check is not None:
                    await check(container, resources, settings, pairs)
                else:
                    listing = await invoke(
                        CreatePlaylist, first[0], PlaylistMetadata("public list", "", "public")
                    )
                    await invoke(SetVideoPlaylists, *first, (listing.id,))
                    assert (await invoke(GetPlaylist, first[0], listing.id)).items == ()
                    playlist_query = SearchQuery(
                        "public list", "playlist", duration="long", features=("4k", "hdr")
                    )
                    playlist_search = await invoke(GetSearch, playlist_query)
                    assert [item.playlist.id for item in playlist_search.items] == [listing.id]
                    assert playlist_search.next_cursor is None
                    assert [item.id for item in (await invoke(GetChannelPlaylists, channel.tag)).items] == [listing.id]
                    async with resources.sessions.begin() as session:
                        await session.execute(
                            text(
                                "UPDATE video_publications SET moderation='visible',visibility='private' WHERE id=:id"
                            ),
                            {"id": first[1]},
                        )
                    assert [
                        card.id for card in (await invoke(GetPlaylist, first[0], listing.id)).items
                    ] == [first[1]]
                    assert (await invoke(GetPlaylist, None, listing.id)).items == ()
                    await invoke(SetPrivateAccess, *first, recipient.email, True)
                    assert [
                        card.id
                        for card in (await invoke(GetPlaylist, recipient.id, listing.id)).items
                    ] == [first[1]]
                    await invoke(SetPrivateAccess, *first, recipient.email, False)
                    assert (await invoke(GetPlaylist, recipient.id, listing.id)).items == ()
                    assert await invoke(GetSearch, playlist_query) == playlist_search
        print(
            ("Production" if production else "Dev")
            + " thumbnails passed: shared three-frame sets, independent choices, "
            "PNG/BMP pixel deduplication, verified WebP, shared timed WebVTT, private RustFS and cleanup, "
            "ready-state/master decode, durable original deletion and idempotent receipts, "
            "private metadata permissions/revocation; "
            + ("forced Valkey outage" if check is None else "live HTTP sessions")
            + (", no pytest." if production else ".")
        )
    finally:
        try:
            if resources is not None:
                async with resources.sessions() as session:
                    prefixes = set((await session.scalars(select(EncodingAttemptRow.prefix))).all())
                    artifacts = (
                        await session.scalars(
                            select(EncodedTrackRow.artifact).where(
                                EncodedTrackRow.status == "ready"
                            )
                        )
                    ).all()
                    prefixes.update(artifact["prefix"] for artifact in artifacts)
                    sources = (
                        await session.execute(select(UploadRow.object_key, UploadRow.multipart_id))
                    ).all()
                    image_sources = (
                        await session.execute(
                            select(ThumbnailUploadRow.object_key, ThumbnailUploadRow.multipart_id)
                        )
                    ).all()
                    raw_prefixes = (
                        await session.scalars(select(ThumbnailPreparationRow.prefix))
                    ).all()
                encoder = S3MediaEncoder(resources.objects, ObjectBucket(resources.bucket))
                for prefix in prefixes:
                    await encoder.discard(prefix)
                processor = S3ThumbnailProcessor(resources.objects, ObjectBucket(resources.bucket))
                for prefix in raw_prefixes:
                    await processor.discard(prefix)
                for key, multipart in [*sources, *image_sources]:
                    if multipart:
                        try:
                            await asyncio.to_thread(
                                resources.objects.abort_multipart_upload,
                                Bucket=resources.bucket,
                                Key=key,
                                UploadId=multipart,
                            )
                        except ClientError as error:
                            if error.response["Error"]["Code"] != "NoSuchUpload":
                                raise
                    await asyncio.to_thread(
                        resources.objects.delete_object, Bucket=resources.bucket, Key=key
                    )
        finally:
            try:
                if container is not None:
                    await container.close()
            finally:
                await manage(False)


if __name__ == "__main__":
    try:
        asyncio.run(qualify())
    except Exception:
        raise SystemExit("Production thumbnail qualification failed") from None
