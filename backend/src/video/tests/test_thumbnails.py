"""Real CPU processing, shared objects, owner choices and hostile image inputs."""

import asyncio
import shutil
import struct
import subprocess
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest.mock import patch

import pytest

from video.domain.thumbnails import image_geometry, thumbnail_plan
from video.infrastructure.models import ThumbnailUploadRow
from video.infrastructure.thumbnail_media import webp
from video.public import (
    CompleteThumbnailUpload,
    CompleteUpload,
    GetThumbnailImage,
    GetThumbnails,
    GetVideoSettings,
    RepairThumbnails,
    SelectThumbnail,
    StartThumbnailUpload,
    VideoNotFound,
)
from video.tests.test_encoding import inventory
from video.tests.test_publications import account, invoke
from video.tests.test_source_processing import process, small_video, source_environment

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.fixture(scope="module")
def small_video_fixture(tmp_path_factory):
    return small_video.__wrapped__(tmp_path_factory)


@pytest.fixture(scope="module")
def image_samples(tmp_path_factory):
    directory = tmp_path_factory.mktemp("images")
    for suffix in ("png", "bmp", "tiff", "webp", "jpg", "avif"):
        subprocess.run(  # noqa: S603 -- fixed tool and generated fixtures
            [
                shutil.which("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=96x64",
                "-frames:v",
                "1",
                str(directory / ("still." + suffix)),
            ],
            check=True,
            capture_output=True,
            timeout=20,
        )
    subprocess.run(  # noqa: S603 -- fixed tool and generated fixtures
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=32x32:duration=0.5:rate=4",
            str(directory / "animated.gif"),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    png = (directory / "still.png").read_bytes()
    header = struct.pack("<HHH", 0, 1, 2)
    offset = 6 + 16 * 2
    entries = b"".join(
        struct.pack("<BBBBHHII", 96, 64, 0, 0, 1, 32, len(png), offset + i * len(png))
        for i in range(2)
    )
    (directory / "still.ico").write_bytes(header + entries + png * 2)
    return {p.suffix[1:]: p.read_bytes() for p in directory.iterdir()}


async def upload_image(container, resources, owner_id, video_id, data, *, before_complete=None):
    plan = await invoke(container, StartThumbnailUpload, owner_id, video_id, len(data))
    async with resources.sessions() as session:
        row = await session.get(ThumbnailUploadRow, plan.id)
        key, multipart = row.object_key, row.multipart_id
    # Same S3 operations as the signed browser single-part transfer, without an HTTP proxy here.
    part = await asyncio.to_thread(
        resources.objects.upload_part,
        Bucket=resources.bucket,
        Key=key,
        UploadId=multipart,
        PartNumber=1,
        Body=data,
    )
    if before_complete is not None:
        await before_complete()
    await invoke(container, CompleteThumbnailUpload, owner_id, video_id, plan.id)
    assert part["ETag"]
    return plan.id


def test_geometry_and_three_reproducible_random_positions():
    assert image_geometry(3840, 2160) == (1280, 720)
    assert image_geometry(100, 200) == (100, 200)
    assert image_geometry(720, 576, Fraction(16, 15)) == (768, 576)
    first = thumbnail_plan(inventory())
    assert first == thumbnail_plan(inventory())
    points = [Fraction(*pair) for pair in first.spec["positions"]]
    assert points[0] < points[1] < points[2]


def test_generated_options_selection_and_owner_isolation(
    backend_runtime, small_video_fixture, tmp_path, source_queue
):
    async def exercise():
        async with source_environment(backend_runtime, small_video_fixture) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            await process(container, resources, tmp_path, source_queue)
            options = await invoke(container, GetThumbnails, owner.id, draft.id)
            assert options.status == "ready" and len(options.candidates) == 3
            assert options.slot == 0 and options.selected == "generated"
            image = await invoke(container, GetThumbnailImage, owner.id, draft.id)
            assert image[:4] == b"RIFF" and image[8:12] == b"WEBP"
            after = await invoke(container, GetVideoSettings, owner.id, draft.id)
            assert after.updated_at == before.updated_at
            changed = await invoke(container, SelectThumbnail, owner.id, draft.id, 2)
            assert changed.slot == 2
            other = await account(container)
            for scenario in (GetThumbnails, GetThumbnailImage):
                with pytest.raises(VideoNotFound):
                    await invoke(container, scenario, other.id, draft.id)
            assert (
                await invoke(container, GetVideoSettings, owner.id, draft.id)
            ).processing.value == "ready"

    asyncio.run(exercise())


@pytest.mark.parametrize("extension", ["png", "bmp", "tiff", "webp", "jpg", "avif", "ico"])
def test_any_supported_still_image_becomes_private_webp(
    backend_runtime, image_samples, extension, tmp_path, source_queue
):
    async def exercise():
        from video.public import CreateDraft, VideoMetadata

        async with backend_runtime() as (container, resources):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("image", "", "private")
            )
            upload_id = await upload_image(
                container, resources, owner.id, draft.id, image_samples[extension]
            )
            await process(container, resources, tmp_path, source_queue)
            options = await invoke(container, GetThumbnails, owner.id, draft.id)
            assert options.selected == "custom" and options.custom_status == "ready", options
            image = await invoke(container, GetThumbnailImage, owner.id, draft.id)
            assert image[:4] == b"RIFF" and image[8:12] == b"WEBP"
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            await invoke(container, RepairThumbnails)
            # A repeated completion survives deletion of the temporary image source.
            await invoke(container, CompleteThumbnailUpload, owner.id, draft.id, upload_id)
            assert (
                await invoke(container, GetVideoSettings, owner.id, draft.id)
            ).updated_at == before.updated_at
            assert (await invoke(container, GetThumbnailImage, owner.id, draft.id)) == image

    asyncio.run(exercise())


def test_duplicate_pixels_are_encoded_once_and_animation_keeps_previous(
    backend_runtime, image_samples, small_video_fixture, tmp_path, source_queue
):
    async def exercise():
        async with source_environment(backend_runtime, small_video_fixture) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            await process(container, resources, tmp_path / "video", source_queue)
            first_id = await upload_image(
                container, resources, owner.id, draft.id, image_samples["png"]
            )
            await process(container, resources, tmp_path / "first", source_queue)
            selected = await invoke(container, GetThumbnailImage, owner.id, draft.id)
            second_id = await upload_image(
                container, resources, owner.id, draft.id, image_samples["png"]
            )
            with patch("video.infrastructure.thumbnail_media.webp", wraps=webp) as encode:
                await process(container, resources, tmp_path / "second", source_queue)
                assert encode.call_count == 0
            async with resources.sessions() as session:
                a, b = (
                    await session.get(ThumbnailUploadRow, first_id),
                    await session.get(ThumbnailUploadRow, second_id),
                )
                assert a.encoded_id == b.encoded_id
            await upload_image(container, resources, owner.id, draft.id, image_samples["gif"])
            await process(container, resources, tmp_path / "animated", source_queue)
            options = await invoke(container, GetThumbnails, owner.id, draft.id)
            assert options.custom_status == "failed" and options.error_code == "animated_thumbnail"
            assert await invoke(container, GetThumbnailImage, owner.id, draft.id) == selected
            await invoke(container, SelectThumbnail, owner.id, draft.id, 1)
            assert (
                await invoke(container, GetThumbnails, owner.id, draft.id)
            ).selected == "generated"

    asyncio.run(exercise())


def test_new_manual_choice_wins_over_late_custom_processing(
    backend_runtime, image_samples, small_video_fixture, tmp_path, source_queue
):
    async def exercise():
        async with source_environment(backend_runtime, small_video_fixture) as (
            container,
            resources,
            owner,
            draft,
            upload,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, upload.id)
            await process(container, resources, tmp_path / "video", source_queue)
            await upload_image(container, resources, owner.id, draft.id, image_samples["bmp"])
            await invoke(container, SelectThumbnail, owner.id, draft.id, 2)
            before = await invoke(container, GetVideoSettings, owner.id, draft.id)
            await process(container, resources, tmp_path / "image", source_queue)
            options = await invoke(container, GetThumbnails, owner.id, draft.id)
            assert (
                options.selected == "generated"
                and options.slot == 2
                and options.custom_status is None
            )
            assert (
                await invoke(container, GetVideoSettings, owner.id, draft.id)
            ).updated_at == before.updated_at

    asyncio.run(exercise())


@pytest.mark.parametrize("data", [b"not an image", b"#EXTM3U\nhttp://127.0.0.1:1/private\n"])
def test_invalid_or_external_media_does_not_create_a_thumbnail(
    backend_runtime, data, tmp_path, source_queue
):
    async def exercise():
        from video.public import CreateDraft, VideoMetadata

        async with backend_runtime() as (container, resources):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("bad", "", "private")
            )
            await upload_image(container, resources, owner.id, draft.id, data)
            await process(container, resources, tmp_path, source_queue)
            options = await invoke(container, GetThumbnails, owner.id, draft.id)
            assert options.custom_status == "failed" and options.current_url is None

    asyncio.run(exercise())


def test_svg_is_rasterized_without_loading_external_files_or_urls(
    backend_runtime, image_samples, tmp_path, source_queue
):
    from video.public import CreateDraft, VideoMetadata

    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(image_samples["png"])

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    local = tmp_path / "private.png"
    local.write_bytes(image_samples["png"])

    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("svg", "", "private")
            )
            svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
                '<rect width="32" height="32" fill="red"/>'
                f'<image href="file://{local}" width="32" height="32"/>'
                f'<image href="http://127.0.0.1:{server.server_port}/image" '
                'width="32" height="32"/>'
                "</svg>"
            ).encode()
            upload_id = await upload_image(container, resources, owner.id, draft.id, svg)
            await process(container, resources, tmp_path / "svg", source_queue)
            assert (
                await invoke(container, GetThumbnails, owner.id, draft.id)
            ).custom_status == "ready"
            async with resources.sessions() as session:
                row = await session.get(ThumbnailUploadRow, upload_id)
                normalized = await asyncio.to_thread(
                    resources.objects.get_object, Bucket=resources.bucket, Key=row.normalized_key
                )
            body = normalized["Body"]
            try:
                raw = body.read()
            finally:
                body.close()
            assert raw == bytes((255, 0, 0, 255)) * (32 * 32)
            assert hits == []

    try:
        asyncio.run(exercise())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
