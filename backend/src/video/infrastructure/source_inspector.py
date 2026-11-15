"""Download a trusted private object, inspect a local stream without network protocols."""

import asyncio
import os
import shutil
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from botocore.response import StreamingBody

from common.contracts.storage import ObjectBucket, ObjectStorageClient
from video.domain.processing import (
    SourceClaim,
    SourceInspection,
    SourceInspectionError,
    TemporarySourceError,
)
from video.domain.uploads import StorageUnavailable
from video.infrastructure.media_process import check_workspace, run_media_process
from video.infrastructure.media_resources import CpuBudget, MediaLimits, MediaResourceError
from video.infrastructure.multipart import sdk
from video.infrastructure.track_inspector import PROBE_ENTRIES, inspect_report


class S3SourceInspector:
    def __init__(self, objects: ObjectStorageClient, bucket: ObjectBucket) -> None:
        self.objects, self.bucket = objects, bucket.name

    async def inspect(self, source: SourceClaim) -> SourceInspection:
        limits = MediaLimits.from_environment()
        try:
            async with asyncio.timeout(limits.attempt_seconds):
                return await self._inspect(source, limits)
        except TimeoutError:
            raise SourceInspectionError("attempt_timeout") from None
        except StorageUnavailable:
            raise TemporarySourceError() from None
        except MediaResourceError as error:
            if error.retryable:
                raise TemporarySourceError(error.code) from None
            raise SourceInspectionError(error.code) from None
        except OSError:
            raise TemporarySourceError("local_io_unavailable") from None

    async def _inspect(self, source: SourceClaim, limits: MediaLimits) -> SourceInspection:
        root = Path("/var/lib/youtube/media")
        await asyncio.to_thread(check_workspace, root, limits)
        with TemporaryDirectory(prefix=f"source-{source.upload_id}-", dir=root) as work:
            directory = Path(work)
            local = directory / "source.bin"
            response = await sdk(
                lambda: self.objects.get_object(Bucket=self.bucket, Key=source.object_key)
            )
            body = cast(StreamingBody, response["Body"])
            source_hash = sha256()
            try:
                if response["ContentLength"] != source.size:
                    raise SourceInspectionError("source_size_changed")
                actual = 0
                with local.open("xb") as output:
                    while data := await sdk(lambda: body.read(1024 * 1024)):
                        actual += len(data)
                        source_hash.update(data)
                        if actual > source.size:
                            raise SourceInspectionError("source_size_changed")
                        await asyncio.to_thread(output.write, data)
                        await asyncio.to_thread(check_workspace, root, limits)
                if actual != source.size:
                    raise TemporarySourceError("source_read_incomplete")
            finally:
                await sdk(body.close)
            probe = shutil.which("ffprobe")
            if probe is None:
                raise SourceInspectionError("ffprobe_unavailable")
            result = directory / "inspection.xml"
            try:
                # Only this already-open descriptor may be read by libavformat.
                # A malicious playlist cannot open file/http/tcp URLs or secrets.
                with local.open("rb") as input_stream:
                    await run_media_process(
                        Path(probe),
                        [
                            "-v",
                            "error",
                            "-threads",
                            str(CpuBudget.detect().pool_size),
                            "-protocol_whitelist",
                            "fd",
                            "-fd",
                            str(input_stream.fileno()),
                            "-show_streams",
                            "-show_packets",
                            "-show_format",
                            "-show_error",
                            "-show_data_hash",
                            "sha256",
                            "-err_detect",
                            "explode",
                            "-show_entries",
                            PROBE_ENTRIES,
                            "-of",
                            "xml",
                            "-o",
                            str(result),
                            "fd:",
                        ],
                        workspace=root,
                        limits=limits,
                        environment={
                            **os.environ,
                            "LP_NUM_THREADS": str(CpuBudget.detect().pool_size),
                        },
                        pass_fds=(input_stream.fileno(),),
                        reject_stderr=True,
                    )
            except MediaResourceError as error:
                if error.code == "media_exit_failed":
                    raise SourceInspectionError("invalid_media") from None
                raise
            return await inspect_report(result, source_hash.hexdigest())
