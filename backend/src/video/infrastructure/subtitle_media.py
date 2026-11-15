"""Trusted FD extraction, verified fMP4 clock calibration, private WebVTT upload."""

import asyncio
import json
import sys
from decimal import Decimal, localcontext
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import cast

from botocore.response import StreamingBody

from common.contracts.storage import ObjectStorageClient
from video.domain.encoding import EncodingClaim, mapping, pair, rational
from video.domain.processing import SourceInspectionError
from video.domain.subtitles import BITMAP_SUBTITLES
from video.infrastructure.media_process import check_workspace, run_media_process
from video.infrastructure.media_resources import CpuBudget, MediaLimits
from video.infrastructure.multipart import sdk


def seconds(value: Fraction) -> str:
    with localcontext() as context:
        context.prec = 80
        return format(Decimal(value.numerator) / Decimal(value.denominator), "f")


async def packet_clock(source: Path, directory: Path, limits: MediaLimits, index: int) -> Fraction:
    import shutil

    executable = shutil.which("ffprobe")
    if executable is None:
        raise SourceInspectionError("ffprobe_unavailable")
    report = directory / "subtitle-clock.json"
    with source.open("rb") as stream:
        await run_media_process(
            Path(executable),
            [
                "-v",
                "error",
                "-protocol_whitelist",
                "fd",
                "-fd",
                str(stream.fileno()),
                "-i",
                "fd:",
                "-select_streams",
                str(index),
                "-read_intervals",
                "%+#1",
                "-show_packets",
                "-show_entries",
                "packet=pts,dts:stream=time_base",
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
    payload = mapping(json.loads(report.read_bytes()))
    packets = cast(list[object], payload["packets"])
    if len(packets) != 1:
        raise SourceInspectionError("invalid_subtitle_timeline")
    packet = mapping(packets[0])
    streams = cast(list[object], payload["streams"])
    return Fraction(str(packet.get("dts", packet.get("pts")))) * Fraction(
        str(mapping(streams[0])["time_base"])
    )


async def video_clock(
    claim: EncodingClaim,
    directory: Path,
    limits: MediaLimits,
    objects: ObjectStorageClient,
    bucket: str,
) -> dict[str, object]:
    import shutil

    artifact = mapping(claim.spec["video_artifact"])
    files = [mapping(item) for item in cast(list[object], artifact["files"])]
    # Only the independently decodable first segment and its init are required.
    prefix = str(artifact["prefix"])
    import re

    if re.fullmatch(r"encoded/[1-9][0-9]*/[0-9a-f]{32}/", prefix) is None:
        raise SourceInspectionError("invalid_subtitle_timeline")
    source = directory / "clock.mp4"
    with source.open("xb") as output:
        for name in ("init.mp4", "segment_000000.m4s"):
            metadata = next((item for item in files if item["name"] == name), None)
            if metadata is None:
                raise SourceInspectionError("invalid_subtitle_timeline")
            response = await sdk(
                lambda name=name: objects.get_object(Bucket=bucket, Key=prefix + name)
            )
            body = cast(StreamingBody, response["Body"])
            digest = sha256()
            actual = 0
            try:
                while block := await sdk(lambda body=body: body.read(1024 * 1024)):
                    actual += len(block)
                    if actual > cast(int, metadata["size"]):
                        raise SourceInspectionError("invalid_subtitle_timeline")
                    digest.update(block)
                    await asyncio.to_thread(output.write, block)
                    await asyncio.to_thread(check_workspace, directory.parent, limits)
            finally:
                await sdk(body.close)
            if actual != metadata["size"] or digest.hexdigest() != metadata["sha256"]:
                raise SourceInspectionError("invalid_subtitle_timeline")
    probe = shutil.which("ffprobe")
    if probe is None:
        raise SourceInspectionError("ffprobe_unavailable")
    report = directory / "video-clock.json"
    with source.open("rb") as stream:
        await run_media_process(
            Path(probe),
            [
                "-v",
                "error",
                "-protocol_whitelist",
                "fd",
                "-fd",
                str(stream.fileno()),
                "-i",
                "fd:",
                "-show_entries",
                "stream=codec_type,start_pts,time_base",
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
    payload = mapping(json.loads(report.read_bytes()))
    streams = cast(list[object], payload["streams"])
    video = mapping(streams[0])
    if len(streams) != 1 or video["codec_type"] != "video":
        raise SourceInspectionError("invalid_subtitle_timeline")
    origin = Fraction(str(video["start_pts"])) * Fraction(str(video["time_base"]))
    return {
        "kind": "subtitle_clock",
        "prefix": claim.prefix,
        "files": [],
        "presentation_origin": pair(origin),
    }


async def encode_subtitles(
    claim: EncodingClaim,
    directory: Path,
    limits: MediaLimits,
    objects: ObjectStorageClient,
    bucket: str,
) -> dict[str, object] | None:
    import shutil

    if claim.spec.get("codec") in BITMAP_SUBTITLES:
        raise SourceInspectionError("unsupported_subtitle")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SourceInspectionError("subtitle_failed")
    source = directory / "source.bin"
    first = await packet_clock(source, directory, limits, claim.stream_index)
    origin = first - rational(claim.spec["offset"])
    earliest = origin + rational(claim.spec["earliest"])
    shift = -min(Fraction(0), origin, earliest)
    converted = directory / "normalized.vtt"
    with source.open("rb") as stream:
        await run_media_process(
            Path(ffmpeg),
            [
                "-v",
                "error",
                "-nostdin",
                "-xerror",
                "-copyts",
                "-threads",
                str(CpuBudget.detect().pool_size),
                "-itsoffset",
                seconds(shift),
                *(["-txt_format", "text"] if claim.spec.get("codec") == "dvb_teletext" else []),
                "-protocol_whitelist",
                "fd",
                "-fd",
                str(stream.fileno()),
                "-i",
                "fd:",
                "-map",
                f"0:{claim.stream_index}",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-c:s",
                "webvtt",
                "-avoid_negative_ts",
                "disabled",
                "-f",
                "webvtt",
                "-progress",
                "pipe:1",
                "-nostats",
                str(converted),
            ],
            workspace=directory.parent,
            limits=limits,
            pass_fds=(stream.fileno(),),
            reject_stderr=True,
        )
    (directory / "subtitle-spec.json").write_text(
        json.dumps({**claim.spec, "source_origin": pair(origin + shift)})
    )
    await run_media_process(
        Path(sys.executable),
        ["-m", "video.infrastructure.subtitle_package", str(directory)],
        workspace=directory.parent,
        limits=limits,
        reject_stderr=True,
    )
    result = mapping(json.loads((directory / "subtitle-result.json").read_bytes()))
    if result["empty"] is True:
        return None
    count = int(str(result["count"]))
    if count != len(cast(list[object], claim.spec["segments"])):
        raise SourceInspectionError("subtitle_failed")
    names = ["index.m3u8", *(f"segment_{index:06d}.vtt" for index in range(count))]
    files: list[dict[str, object]] = []
    for name in names:
        path = directory / name
        digest = sha256()
        with path.open("rb") as body:
            while block := await asyncio.to_thread(body.read, 1024 * 1024):
                digest.update(block)
            body.seek(0)
            size = path.stat().st_size
            await sdk(
                lambda name=name, body=body, size=size: objects.put_object(
                    Bucket=bucket,
                    Key=claim.prefix + name,
                    Body=body,
                    ContentLength=size,
                    ContentType="text/vtt; charset=utf-8"
                    if name.endswith(".vtt")
                    else "application/vnd.apple.mpegurl",
                )
            )
        files.append({"name": name, "size": size, "sha256": digest.hexdigest()})
    return {
        "kind": "subtitle",
        "prefix": claim.prefix,
        "files": files,
        "segments": claim.spec["segments"],
        "presentation_origin": claim.spec["presentation_origin"],
        "target_duration": result["target_duration"],
    }
