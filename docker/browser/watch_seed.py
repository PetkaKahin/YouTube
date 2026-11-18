"""Downloaded test tracks and isolated data for frontend player checks."""

import asyncio
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from botocore.exceptions import ClientError
from sqlalchemy import select, text

from app.di import create_container
from app.media_worker import create_queue
from app.resources import Resources, create_cache
from app.secrets import read_secret
from app.settings import Settings
from channel.public import (
    CreatePlaylist,
    PlaylistMetadata,
    Register,
    Registration,
    SetVideoPlaylists,
)
from common.contracts.storage import ObjectBucket
from video.infrastructure.encoder import S3MediaEncoder
from video.infrastructure.models import (
    EncodedTrackRow,
    EncodingAttemptRow,
    ThumbnailPreparationRow,
    ThumbnailUploadRow,
    UploadRow,
)
from video.infrastructure.thumbnail_media import S3ThumbnailProcessor
from video.public import (
    MEDIA_QUEUE,
    CompleteUpload,
    CreateDraft,
    GetViewableVideo,
    PublishVideo,
    SetPrivateAccess,
    StartUpload,
    VideoMetadata,
)


def container():
    settings = Settings()
    return create_container(settings, create_cache(settings, read_secret("VALKEY_PASSWORD")))


async def seed(catalogue=False, links=False, quality=False):
    app = container()
    try:
        resources = await app.get(Resources)

        async def invoke(scenario, *args):
            async with app() as operation:
                return await (await operation.get(scenario))(*args)

        token = uuid4().hex[:16]
        people = {}
        for role in ("owner", "invited", "stranger"):
            email = f"{role}-{token}@example.com"
            account = await invoke(
                Register,
                Registration(
                    "Общий автор" if catalogue else f"Автор {role}",
                    email,
                    f"{role}_{token}",
                    "abcdef",
                    "abcdef",
                ),
            )
            people[role] = {"id": str(account.account_id), "email": email, "tag": f"{role}_{token}"}
        owner = int(people["owner"]["id"])
        async with resources.sessions.begin() as session:
            await session.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('video_publications','id'), "
                    "9007199254740993, false)"
                )
            )

        async def upload(path, title, visibility="public"):
            draft = await invoke(
                CreateDraft,
                owner,
                VideoMetadata(
                    title,
                    "Описание <img src=x onerror=window.frontendInject=1>\n"
                    "Вторая строка\nТретья строка\nЧетвёртая строка",
                    visibility,
                ),
            )
            plan = await invoke(StartUpload, owner, draft.id, path.stat().st_size)
            async with resources.sessions() as session:
                record = await session.get(UploadRow, plan.id)
                key, multipart = record.object_key, record.multipart_id
            await asyncio.to_thread(
                resources.objects.upload_part,
                Bucket=resources.bucket,
                Key=key,
                UploadId=multipart,
                PartNumber=1,
                Body=path.read_bytes(),
            )
            await invoke(CompleteUpload, owner, draft.id, plan.id)
            await invoke(PublishVideo, owner, draft.id)
            return draft.id

        def ffmpeg(*args):
            subprocess.run(  # noqa: S603 - Fixed fixture arguments and generated temporary paths.
                ["/usr/bin/ffmpeg", "-v", "error", "-nostdin", *map(str, args)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
            )

        with TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "source.mp4"
            ffmpeg(
                "-i",
                "/media/video.mp4",
                "-i",
                "/media/audio.mp4",
                "-i",
                "/media/captions.vtt",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-map",
                "2:s:0",
                "-t",
                "12",
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-c:s",
                "mov_text",
                source,
            )
            public = await upload(source, "Открытое видео <script>alert(1)</script>")
            private = await upload(source, "Приватное видео", "private")
            await invoke(SetPrivateAccess, owner, private, people["invited"]["email"], True)
            hidden = await upload(source, "Скрытый материал")
            silent_source = work / "silent.mp4"
            ffmpeg(
                "-i",
                "/media/portrait.mp4",
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "copy",
                silent_source,
            )
            silent = await upload(silent_source, "Вертикальное видео без звука")
            low = await upload(Path("/media/low.webm"), "Видео 240p") if quality else None
            copies = []
            if catalogue:
                for number in range(39):
                    copies.append(await upload(source, f"Публичное видео {number:02}"))
            queue = create_queue(Settings(), app)
            async with queue.open_async():
                await queue.run_worker_async(
                    queues=[MEDIA_QUEUE],
                    concurrency=1,
                    wait=False,
                    listen_notify=False,
                    install_signal_handlers=False,
                )
            for video_id in (public, private, hidden, silent):
                await invoke(GetViewableVideo, owner, video_id)
            if low is not None:
                await invoke(GetViewableVideo, owner, low)
            async with resources.sessions.begin() as session:
                await session.execute(
                    text("UPDATE video_publications SET moderation='hidden' WHERE id=:id"),
                    {"id": hidden},
                )
            pending = await upload(source, "Неготовое видео")
            draft = await invoke(CreateDraft, owner, VideoMetadata("Черновик", "", "public"))
            playlists = {}
            if catalogue or links:
                public_lists = []
                for number in range(41 if catalogue else 2):
                    item = await invoke(
                        CreatePlaylist,
                        owner,
                        PlaylistMetadata(
                            "Плейлист <img src=x onerror=window.frontendInject=1>"
                            if number == 0
                            else f"Плейлист {number:02}",
                            "Описание <script>alert(1)</script>\nВторая строка",
                            "public",
                        ),
                    )
                    public_lists.append(item.id)
                for visibility in ("unlisted", "private"):
                    item = await invoke(
                        CreatePlaylist,
                        owner,
                        PlaylistMetadata(f"{visibility} плейлист", "", visibility),
                    )
                    playlists[visibility] = str(item.id)
                playlists["public"] = str(public_lists[0])
                playlists["empty"] = str(public_lists[-1])
                if links:
                    for mode in ("unlisted", "private"):
                        item = await invoke(
                            CreatePlaylist, owner, PlaylistMetadata(f"Пустой {mode}", "", mode)
                        )
                        playlists[f"empty_{mode}"] = str(item.id)
                for video_id in [public, private, hidden, pending, draft.id, *copies, silent]:
                    ids = [public_lists[0]]
                    if video_id in (public, private):
                        ids += [int(playlists["unlisted"]), int(playlists["private"])]
                    await invoke(SetVideoPlaylists, owner, video_id, tuple(ids))
        return {
            "people": people,
            "playlists": playlists,
            "videos": {
                key: str(value)
                for key, value in {
                    "public": public,
                    "private": private,
                    "hidden": hidden,
                    "silent": silent,
                    "pending": pending,
                    "draft": draft.id,
                    **({"low": low} if low is not None else {}),
                }.items()
            },
        }
    finally:
        await app.close()


async def cleanup():
    app = container()
    try:
        resources = await app.get(Resources)
        async with resources.sessions() as session:
            prefixes = set((await session.scalars(select(EncodingAttemptRow.prefix))).all())
            artifacts = (
                await session.scalars(
                    select(EncodedTrackRow.artifact).where(EncodedTrackRow.status == "ready")
                )
            ).all()
            prefixes.update(artifact["prefix"] for artifact in artifacts)
            sources = (
                await session.execute(select(UploadRow.object_key, UploadRow.multipart_id))
            ).all()
            sources += (
                await session.execute(
                    select(ThumbnailUploadRow.object_key, ThumbnailUploadRow.multipart_id)
                )
            ).all()
            images = (await session.scalars(select(ThumbnailPreparationRow.prefix))).all()
        encoder = S3MediaEncoder(resources.objects, ObjectBucket(resources.bucket))
        for prefix in prefixes:
            await encoder.discard(prefix)
        processor = S3ThumbnailProcessor(resources.objects, ObjectBucket(resources.bucket))
        for prefix in images:
            await processor.discard(prefix)
        for key, multipart in sources:
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
        await app.close()
