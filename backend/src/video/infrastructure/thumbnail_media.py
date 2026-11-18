"""Bounded CPU image decoding and WebP rendering; untrusted inputs read only by FD."""

import asyncio
import json
import os
import re
import shutil
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from botocore.response import StreamingBody

from common.contracts.storage import ObjectBucket, ObjectStorageClient
from video.domain.delivery import MEDIA_OBJECT_KEY
from video.domain.encoding import EncodingClaim, mapping, rational
from video.domain.orientation import display_geometry
from video.domain.processing import SourceInspectionError, TemporarySourceError
from video.domain.thumbnails import (
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    PreparedThumbnail,
    ThumbnailClaim,
    image_geometry,
)
from video.domain.uploads import StorageUnavailable
from video.infrastructure.media_process import check_workspace, run_media_process
from video.infrastructure.media_resources import CpuBudget, MediaLimits, MediaResourceError
from video.infrastructure.multipart import sdk
from video.infrastructure.orientation import input_orientation, orientation_filter


def digest_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def black_frame(path: Path) -> bool:
    data = path.read_bytes()
    return not any(
        data[channel]
        for pixel in range(0, len(data), 4)
        for channel in (pixel, pixel + 1, pixel + 2)
    )


def executable(name: str) -> Path:
    found = shutil.which(name)
    if found is None:
        raise SourceInspectionError("thumbnail_failed")
    return Path(found)


def environment() -> dict[str, str]:
    return {
        **os.environ,
        "LP_NUM_THREADS": str(CpuBudget.detect().pool_size),
        "VK_DRIVER_FILES": "/usr/share/vulkan/icd.d/lvp_icd.json",
    }


async def probe(
    source: Path, directory: Path, limits: MediaLimits, *, image_limit: bool = False
) -> list[dict[str, object]]:
    report = directory / "probe.json"
    with source.open("rb") as stream:
        await run_media_process(
            executable("ffprobe"),
            [
                "-v",
                "error",
                *(["-max_pixels", str(MAX_IMAGE_PIXELS)] if image_limit else []),
                "-protocol_whitelist",
                "fd",
                "-fd",
                str(stream.fileno()),
                "-i",
                "fd:",
                "-show_entries",
                "stream=index,codec_type,width,height,sample_aspect_ratio,color_transfer,start_time:"
                "stream_side_data=rotation,displaymatrix:stream_disposition=default,attached_pic",
                "-of",
                "json",
                "-o",
                str(report),
            ],
            workspace=directory.parent,
            limits=limits,
            pass_fds=(stream.fileno(),),
            reject_stderr=True,
        )
    if report.stat().st_size > MAX_IMAGE_BYTES:
        raise SourceInspectionError("invalid_thumbnail")
    payload = mapping(json.loads(await asyncio.to_thread(report.read_bytes)))
    return [mapping(item) for item in cast(list[object], payload["streams"])]


def image_filter(width: int, height: int, hdr: bool) -> str:
    if hdr:
        return (
            f"libplacebo=w={width}:h={height}:format=yuv420p10le:normalize_sar=true:"
            "color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv:"
            "tonemapping=bt.2390:apply_dolbyvision=true,setsar=1,format=rgba"
        )
    return f"scale={width}:{height}:flags=lanczos,setsar=1,format=rgba"


async def pixels(
    source: Path,
    output: Path,
    directory: Path,
    limits: MediaLimits,
    *,
    index: int,
    width: int,
    height: int,
    hdr: bool,
    frames: int,
    position: Fraction | None = None,
    strict: bool = False,
    orientation: object = None,
) -> None:
    budget = str(CpuBudget.detect().pool_size)
    args = ["-v", "error", "-nostdin", "-xerror", "-filter_threads", budget, "-threads", budget]
    if hdr:
        args += ["-init_hw_device", "vulkan=cpu:0", "-filter_hw_device", "cpu"]
    if position is not None:
        args += ["-seek_timestamp", "1", "-ss", str(float(position))]
    with source.open("rb") as stream:
        args += [
            *(["-err_detect", "explode"] if strict else ["-max_pixels", str(MAX_IMAGE_PIXELS)]),
            *input_orientation(orientation, index),
            "-protocol_whitelist",
            "fd",
            "-fd",
            str(stream.fileno()),
            "-i",
            "fd:",
            "-map",
            f"0:{index}",
            "-an",
            "-sn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-vf",
            orientation_filter(orientation) + image_filter(width, height, hdr),
            "-frames:v",
            str(frames),
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            "rgba",
            "-f",
            "rawvideo",
            "-progress",
            "pipe:1",
            "-nostats",
            str(output),
        ]
        await run_media_process(
            executable("ffmpeg"),
            args,
            workspace=directory.parent,
            limits=limits,
            environment=environment(),
            pass_fds=(stream.fileno(),),
            reject_stderr=True,
        )


async def webp(
    raw: Path, output: Path, directory: Path, limits: MediaLimits, width: int, height: int
) -> None:
    with raw.open("rb") as stream:
        await run_media_process(
            executable("ffmpeg"),
            [
                "-v",
                "error",
                "-nostdin",
                "-xerror",
                "-filter_threads",
                str(CpuBudget.detect().pool_size),
                "-protocol_whitelist",
                "fd",
                "-f",
                "rawvideo",
                "-pixel_format",
                "rgba",
                "-video_size",
                f"{width}x{height}",
                "-fd",
                str(stream.fileno()),
                "-i",
                "fd:",
                "-map_metadata",
                "-1",
                "-frames:v",
                "1",
                "-c:v",
                "libwebp",
                "-quality",
                "80",
                "-f",
                "webp",
                "-progress",
                "pipe:1",
                "-nostats",
                str(output),
            ],
            workspace=directory.parent,
            limits=limits,
            pass_fds=(stream.fileno(),),
            reject_stderr=True,
        )
    streams = await probe(output, directory, limits)
    if len(streams) != 1 or streams[0].get("width") != width or streams[0].get("height") != height:
        raise SourceInspectionError("invalid_thumbnail")
    decoded = directory / (output.stem + ".decoded")
    await pixels(
        output, decoded, directory, limits, index=0, width=width, height=height, hdr=False, frames=2
    )
    if decoded.stat().st_size != width * height * 4:
        raise SourceInspectionError("invalid_thumbnail")
    await asyncio.to_thread(decoded.unlink)


async def encode_thumbnails(
    claim: EncodingClaim,
    directory: Path,
    limits: MediaLimits,
    objects: ObjectStorageClient,
    bucket: str,
) -> dict[str, object]:
    source = directory / "source.bin"
    width, height = claim.spec.get("width"), claim.spec.get("height")
    if (
        type(width) is not int
        or type(height) is not int
        or not 1 <= width <= 1280
        or not 1 <= height <= 720
    ):
        raise SourceInspectionError("invalid_thumbnail")
    names: list[str] = []
    known: dict[str, str] = {}
    if claim.spec["kind"] == "thumbnail_image":
        if source.stat().st_size != width * height * 4:
            raise SourceInspectionError("invalid_thumbnail")
        await webp(source, directory / "image.webp", directory, limits, width, height)
        names = ["image.webp"]
    else:
        streams = await probe(source, directory, limits)
        selected = next(item for item in streams if item["index"] == claim.stream_index)
        start = Fraction(str(selected.get("start_time", "0")))
        positions = cast(list[object], claim.spec["positions"])
        if len(positions) != 3:
            raise SourceInspectionError("invalid_thumbnail")
        for slot, value in enumerate(positions):
            raw = directory / f"frame_{slot}.rgba"
            await pixels(
                source,
                raw,
                directory,
                limits,
                index=claim.stream_index,
                width=width,
                height=height,
                hdr=claim.spec.get("input_hdr") is True,
                frames=1,
                position=start + rational(value),
                strict=True,
                orientation=claim.spec.get("orientation"),
            )
            if raw.stat().st_size == 0:
                raw.unlink()
                await pixels(
                    source,
                    raw,
                    directory,
                    limits,
                    index=claim.stream_index,
                    width=width,
                    height=height,
                    hdr=claim.spec.get("input_hdr") is True,
                    frames=1,
                    position=start,
                    strict=True,
                    orientation=claim.spec.get("orientation"),
                )
            if raw.stat().st_size != width * height * 4:
                raise SourceInspectionError("invalid_thumbnail")
            if await asyncio.to_thread(black_frame, raw):
                alternate = directory / f"alternate_{slot}.rgba"
                duration = rational(claim.spec["duration"])
                await pixels(
                    source,
                    alternate,
                    directory,
                    limits,
                    index=claim.stream_index,
                    width=width,
                    height=height,
                    hdr=claim.spec.get("input_hdr") is True,
                    frames=1,
                    position=start + duration * Fraction(2 * slot + 1, 6),
                    strict=True,
                    orientation=claim.spec.get("orientation"),
                )
                if alternate.stat().st_size == width * height * 4 and not await asyncio.to_thread(
                    black_frame, alternate
                ):
                    await asyncio.to_thread(alternate.replace, raw)
            digest = await asyncio.to_thread(digest_file, raw)
            name = known.get(digest)
            if name is None:
                name = f"candidate_{slot}.webp"
                await webp(raw, directory / name, directory, limits, width, height)
                known[digest] = name
            names.append(name)
    files: list[dict[str, object]] = []
    for name in dict.fromkeys(names):
        path = directory / name
        size = path.stat().st_size
        with path.open("rb") as body:
            await sdk(
                lambda name=name, size=size, body=body: objects.put_object(
                    Bucket=bucket,
                    Key=claim.prefix + name,
                    Body=body,
                    ContentLength=size,
                    ContentType="image/webp",
                )
            )
        files.append(
            {"name": name, "size": size, "sha256": await asyncio.to_thread(digest_file, path)}
        )
    return {
        "prefix": claim.prefix,
        "kind": claim.spec["kind"],
        "width": width,
        "height": height,
        "files": files,
        "candidates": names,
        "content_type": "image/webp",
    }


class S3ThumbnailProcessor:
    def __init__(self, objects: ObjectStorageClient, bucket: ObjectBucket) -> None:
        self.objects, self.bucket = objects, bucket.name

    async def discard(self, key: str) -> None:
        if re.fullmatch(r"thumbnail-incoming/[0-9a-f]{32}", key):
            await sdk(lambda: self.objects.delete_object(Bucket=self.bucket, Key=key))
            return
        if re.fullmatch(r"thumbnail-pixels/[1-9][0-9]*/[0-9a-f]{32}/", key) is None:
            raise SourceInspectionError("invalid_thumbnail")
        # The only object written into a preparation prefix is a server-named raw frame.
        await sdk(lambda: self.objects.delete_object(Bucket=self.bucket, Key=key + "pixels.rgba"))

    async def read(self, key: str) -> bytes:
        if not key.endswith(".webp") or re.fullmatch(MEDIA_OBJECT_KEY, key) is None:
            raise SourceInspectionError("invalid_thumbnail")
        response = await sdk(lambda: self.objects.get_object(Bucket=self.bucket, Key=key))
        body = cast(StreamingBody, response["Body"])
        try:
            if not 0 < response["ContentLength"] <= MAX_IMAGE_BYTES:
                raise SourceInspectionError("invalid_thumbnail")
            data = await sdk(lambda: body.read(MAX_IMAGE_BYTES + 1))
            if len(data) != response["ContentLength"]:
                raise StorageUnavailable
            return data
        finally:
            await sdk(body.close)

    async def prepare(self, claim: ThumbnailClaim) -> PreparedThumbnail:
        limits = MediaLimits.from_environment()
        try:
            async with asyncio.timeout(limits.attempt_seconds):
                return await self._prepare(claim, limits)
        except TimeoutError:
            raise SourceInspectionError("attempt_timeout") from None
        except StorageUnavailable, OSError:
            raise TemporarySourceError() from None
        except MediaResourceError as error:
            if error.code == "media_exit_failed":
                raise SourceInspectionError("invalid_thumbnail") from None
            if error.retryable:
                raise TemporarySourceError(error.code) from None
            raise SourceInspectionError(
                "invalid_thumbnail" if error.code == "media_exit_failed" else error.code
            ) from None
        except ValueError, KeyError, TypeError, StopIteration, ZeroDivisionError, OverflowError:
            raise SourceInspectionError("invalid_thumbnail") from None

    async def _prepare(self, claim: ThumbnailClaim, limits: MediaLimits) -> PreparedThumbnail:
        # The general encoder dispatches image outputs back to this module.
        from video.infrastructure.encoder import S3MediaEncoder

        root = Path("/var/lib/youtube/media")
        await asyncio.to_thread(check_workspace, root, limits)
        with TemporaryDirectory(prefix="thumbnail-", dir=root) as work:
            directory = Path(work)
            source = directory / "source.bin"
            await S3MediaEncoder(self.objects, ObjectBucket(self.bucket)).download(
                EncodingClaim(
                    claim.upload_id, claim.token, claim.object_key, claim.size, 0, {}, claim.prefix
                ),
                source,
                root,
                limits,
            )
            streams = await probe(source, directory, limits, image_limit=True)
            if not streams or any(stream.get("codec_type") != "video" for stream in streams):
                raise SourceInspectionError("invalid_thumbnail")
            stream = min(
                streams,
                key=lambda stream: (
                    mapping(stream.get("disposition", {})).get("attached_pic") == 1,
                    mapping(stream.get("disposition", {})).get("default") != 1,
                    int(str(stream["index"])),
                ),
            )
            width, height = stream["width"], stream["height"]
            if (
                type(width) is not int
                or type(height) is not int
                or min(width, height) < 1
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise SourceInspectionError("image_pixel_limit")
            sar_value = str(stream.get("sample_aspect_ratio", "1:1"))
            sar = (
                Fraction(1)
                if sar_value in {"N/A", "0:1", ""}
                else Fraction(sar_value.replace(":", "/"))
            )
            if sar <= 0:
                sar = Fraction(1)
            side = cast(list[dict[str, object]], stream.get("side_data_list", []))
            fields = {key: value for item in side for key, value in item.items()}
            geometry = display_geometry(
                width,
                height,
                sar,
                Fraction(str(fields.get("rotation", "0"))),
                fields.get("displaymatrix"),
            )
            width, height = image_geometry(geometry.width, geometry.height, geometry.sar)
            raw = directory / "pixels.rgba"
            await pixels(
                source,
                raw,
                directory,
                limits,
                index=int(str(stream["index"])),
                width=width,
                height=height,
                hdr=stream.get("color_transfer") in {"smpte2084", "arib-std-b67"},
                frames=2,
                orientation=geometry.orientation.spec if geometry.orientation is not None else None,
            )
            if raw.stat().st_size != width * height * 4:
                raise SourceInspectionError("animated_thumbnail")
            fingerprint = sha256(
                f"thumbnail-pixels-v1:{width}:{height}:{digest_file(raw)}".encode()
            ).hexdigest()
            key = claim.prefix + "pixels.rgba"
            with raw.open("rb") as body:
                await sdk(
                    lambda: self.objects.put_object(
                        Bucket=self.bucket,
                        Key=key,
                        Body=body,
                        ContentLength=raw.stat().st_size,
                        ContentType="application/octet-stream",
                    )
                )
            return PreparedThumbnail(fingerprint, key, raw.stat().st_size, width, height)
