"""Keep shared test media alive until all references in the disposable module DB expire."""

import asyncio

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import select

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


@pytest.fixture(scope="module")
def video_media_cleanup(backend_runtime):
    yield

    async def clean():
        async with backend_runtime() as (_, resources):
            async with resources.sessions() as session:
                prefixes = set((await session.scalars(select(EncodingAttemptRow.prefix))).all())
                artifacts = (
                    await session.scalars(
                        select(EncodedTrackRow.artifact).where(EncodedTrackRow.status == "ready")
                    )
                ).all()
                prefixes.update(artifact["prefix"] for artifact in artifacts)
                uploads = (
                    await session.execute(select(UploadRow.object_key, UploadRow.multipart_id))
                ).all()
                image_uploads = (
                    await session.execute(
                        select(ThumbnailUploadRow.object_key, ThumbnailUploadRow.multipart_id)
                    )
                ).all()
                image_prefixes = (
                    await session.scalars(select(ThumbnailPreparationRow.prefix))
                ).all()
            encoder = S3MediaEncoder(resources.objects, ObjectBucket(resources.bucket))
            for prefix in prefixes:
                await encoder.discard(prefix)
            processor = S3ThumbnailProcessor(resources.objects, ObjectBucket(resources.bucket))
            for prefix in image_prefixes:
                await processor.discard(prefix)
            for key, multipart in [*uploads, *image_uploads]:
                if multipart is not None:
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

    asyncio.run(clean())
