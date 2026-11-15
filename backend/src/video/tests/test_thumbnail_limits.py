"""Approved byte/pixel boundaries, tested with actual decoder and multipart storage."""

import asyncio
import shutil
import subprocess

import pytest

from video.domain.thumbnails import MAX_IMAGE_BYTES, ThumbnailError
from video.infrastructure.models import ThumbnailUploadRow
from video.public import CreateDraft, GetThumbnails, StartThumbnailUpload, VideoMetadata
from video.tests.test_publications import account, invoke
from video.tests.test_source_processing import process
from video.tests.test_thumbnails import upload_image

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


@pytest.mark.parametrize("width,accepted", [(8000, True), (8001, False)])
def test_forty_megapixel_boundary_is_enforced_before_webp(
    backend_runtime, tmp_path, source_queue, width, accepted
):
    path = tmp_path / "large.png"
    subprocess.run(  # noqa: S603 -- fixed tool, generated bounded fixture
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=red:size={width}x5000,format=rgb24",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=90,
    )
    assert path.stat().st_size < MAX_IMAGE_BYTES

    async def exercise():
        async with backend_runtime() as (container, resources):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("pixels", "", "private")
            )
            upload_id = await upload_image(
                container, resources, owner.id, draft.id, path.read_bytes()
            )
            await process(container, resources, tmp_path / "worker", source_queue)
            options = await invoke(container, GetThumbnails, owner.id, draft.id)
            assert options.custom_status == ("ready" if accepted else "failed")
            async with resources.sessions() as session:
                row = await session.get(ThumbnailUploadRow, upload_id)
                assert (row.normalized_key is not None) == accepted
                if accepted:
                    assert row.normalized_size == 1152 * 720 * 4

    asyncio.run(exercise())


def test_image_byte_limit_in_the_use_case_matches_http(backend_runtime):
    async def exercise():
        async with backend_runtime() as (container, _):
            owner = await account(container)
            draft = await invoke(
                container, CreateDraft, owner.id, VideoMetadata("bytes", "", "private")
            )
            for size in (0, MAX_IMAGE_BYTES + 1, True):
                with pytest.raises(ThumbnailError):
                    await invoke(container, StartThumbnailUpload, owner.id, draft.id, size)
            plan = await invoke(
                container, StartThumbnailUpload, owner.id, draft.id, MAX_IMAGE_BYTES
            )
            assert plan.size == MAX_IMAGE_BYTES and len(plan.parts) == 1

    asyncio.run(exercise())
