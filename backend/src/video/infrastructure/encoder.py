"""CPU encoding of one trusted source stream into a private, verified HLS package."""

import asyncio
import contextlib
import json
import os
import re
import shutil
from fractions import Fraction
from hashlib import sha256
from math import ceil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from botocore.response import StreamingBody

from common.contracts.storage import ObjectBucket, ObjectStorageClient
from video.domain.encoding import EncodingClaim, mapping, rational
from video.domain.processing import SourceInspectionError, TemporarySourceError
from video.domain.uploads import StorageUnavailable
from video.infrastructure.hls import AacAudio, Av1Video, HlsMetadataError, MediaTrack, Segment
from video.infrastructure.media_process import check_workspace, run_media_process
from video.infrastructure.media_resources import CpuBudget, MediaLimits, MediaResourceError
from video.infrastructure.multipart import sdk


def configuration(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError
    return bytes.fromhex(
        "".join(
            line.split(": ", 1)[1].split("  ", 1)[0] for line in value.splitlines() if line.strip()
        )
    )


def package_files(directory: Path) -> tuple[MediaTrack, tuple[str, ...]]:
    playlist = (directory / "index.m3u8").read_text()
    if (
        "#EXT-X-ENDLIST" not in playlist
        or '#EXT-X-MAP:URI="init.mp4"' not in playlist
        or "#EXT-X-PLAYLIST-TYPE:VOD" not in playlist
    ):
        raise ValueError
    segments: list[Segment] = []
    names = ["init.mp4"]
    target = -1
    duration: Fraction | None = None
    for line in playlist.splitlines():
        if line.startswith("#EXT-X-TARGETDURATION:"):
            target = int(line.partition(":")[2])
        elif line.startswith("#EXTINF:"):
            if duration is not None:
                raise ValueError
            duration = Fraction(line.partition(":")[2].split(",", 1)[0])
        elif line and not line.startswith("#"):
            if (
                not re.fullmatch(r"segment_[0-9]{6}\.m4s", line)
                or line in names
                or duration is None
                or duration <= 0
            ):
                raise ValueError
            path = directory / line
            if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
                raise ValueError
            segments.append(Segment(path.stat().st_size, duration))
            names.append(line)
            duration = None
    if (
        duration is not None
        or not (directory / "init.mp4").is_file()
        or (directory / "init.mp4").stat().st_size <= 0
    ):
        raise ValueError
    names.append("index.m3u8")
    # FFmpeg 7.1 can round a subsecond VOD's target duration to zero.
    if target == 0 and segments:
        target = 1
        playlist = re.sub(r"(?m)^#EXT-X-TARGETDURATION:0$", "#EXT-X-TARGETDURATION:1", playlist)
        (directory / "index.m3u8").write_text(playlist)
    return MediaTrack("index.m3u8", target, tuple(segments)), tuple(names)


def file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


class S3MediaEncoder:
    def __init__(self, objects: ObjectStorageClient, bucket: ObjectBucket) -> None:
        self.objects, self.bucket = objects, bucket.name

    async def discard(self, prefix: str) -> None:
        if re.fullmatch(r"encoded/[1-9][0-9]*/[0-9a-f]{32}/", prefix) is None:
            raise SourceInspectionError("invalid_encoded_media")
        continuation: dict[str, str] = {}
        while True:
            page = await sdk(
                lambda continuation=continuation: self.objects.list_objects_v2(
                    Bucket=self.bucket, Prefix=prefix, **continuation
                )
            )
            for item in page.get("Contents", []):
                key = item["Key"]
                if not isinstance(key, str) or not key.startswith(prefix):
                    raise SourceInspectionError("invalid_encoded_media")
                await sdk(lambda key=key: self.objects.delete_object(Bucket=self.bucket, Key=key))
            if not page.get("IsTruncated"):
                return
            continuation = {"ContinuationToken": str(page["NextContinuationToken"])}

    async def encode(self, claim: EncodingClaim) -> dict[str, object] | None:
        limits = MediaLimits.from_environment()
        try:
            async with asyncio.timeout(limits.attempt_seconds):
                return await self._encode(claim, limits)
        except TimeoutError:
            raise SourceInspectionError("attempt_timeout") from None
        except StorageUnavailable:
            raise TemporarySourceError() from None
        except MediaResourceError as error:
            if error.code == "media_exit_failed":
                raise SourceInspectionError(
                    "subtitle_failed" if claim.spec.get("kind") == "subtitle" else "encoding_failed"
                ) from None
            if error.retryable:
                raise TemporarySourceError(error.code) from None
            raise SourceInspectionError(error.code) from None
        except OSError:
            raise TemporarySourceError("local_io_unavailable") from None
        except ValueError, KeyError, TypeError, ZeroDivisionError, HlsMetadataError:
            raise SourceInspectionError("invalid_encoded_media") from None

    async def download(
        self, claim: EncodingClaim, path: Path, root: Path, limits: MediaLimits
    ) -> None:
        response = await sdk(
            lambda: self.objects.get_object(Bucket=self.bucket, Key=claim.object_key)
        )
        body = cast(StreamingBody, response["Body"])
        try:
            if response["ContentLength"] != claim.size:
                raise SourceInspectionError("source_size_changed")
            actual = 0
            with path.open("xb") as output:
                while data := await sdk(lambda: body.read(1024 * 1024)):
                    actual += len(data)
                    if actual > claim.size:
                        raise SourceInspectionError("source_size_changed")
                    await asyncio.to_thread(output.write, data)
                    await asyncio.to_thread(check_workspace, root, limits)
            if actual != claim.size:
                raise TemporarySourceError("source_read_incomplete")
        finally:
            await sdk(body.close)

    async def _encode(self, claim: EncodingClaim, limits: MediaLimits) -> dict[str, object] | None:
        root = Path("/var/lib/youtube/media")
        await asyncio.to_thread(check_workspace, root, limits)
        with TemporaryDirectory(prefix=f"encode-{claim.encoded_id}-", dir=root) as work:
            directory = Path(work)
            source = directory / "source.bin"
            if claim.spec.get("kind") == "subtitle_clock":
                from video.infrastructure.subtitle_media import video_clock

                return await video_clock(claim, directory, limits, self.objects, self.bucket)
            if claim.spec.get("kind") == "subtitle":
                from video.domain.subtitles import BITMAP_SUBTITLES

                if claim.spec.get("codec") in BITMAP_SUBTITLES:
                    raise SourceInspectionError("unsupported_subtitle")
                if claim.spec.get("empty_source") is True:
                    return None
            await self.download(claim, source, root, limits)
            if claim.spec.get("kind") == "subtitle":
                from video.infrastructure.subtitle_media import encode_subtitles

                try:
                    return await encode_subtitles(
                        claim, directory, limits, self.objects, self.bucket
                    )
                except BaseException:
                    with contextlib.suppress(StorageUnavailable, SourceInspectionError):
                        await asyncio.shield(self.discard(claim.prefix))
                    raise
            if claim.spec.get("kind") in {"thumbnail_set", "thumbnail_image"}:
                from video.infrastructure.thumbnail_media import encode_thumbnails

                try:
                    return await encode_thumbnails(
                        claim, directory, limits, self.objects, self.bucket
                    )
                except BaseException:
                    with contextlib.suppress(StorageUnavailable, SourceInspectionError):
                        await asyncio.shield(self.discard(claim.prefix))
                    raise
            ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
            if ffmpeg is None or ffprobe is None:
                raise SourceInspectionError("encoding_failed")
            budget = CpuBudget.detect()
            environment = {
                **os.environ,
                "LP_NUM_THREADS": str(budget.pool_size),
                "VK_DRIVER_FILES": "/usr/share/vulkan/icd.d/lvp_icd.json",
            }
            spec = claim.spec
            kind = spec["kind"]
            if (
                kind not in {"video", "audio"}
                or type(claim.stream_index) is not int
                or claim.stream_index < 0
            ):
                raise ValueError
            duration = rational(spec["duration"])
            if not 0 < duration <= 3600:
                raise ValueError
            prefix = [
                "-hide_banner",
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-filter_threads",
                str(budget.pool_size),
            ]
            if kind == "video":
                prefix += ["-init_hw_device", "vulkan=cpu:0", "-filter_hw_device", "cpu"]
            with source.open("rb") as input_stream:
                args = [
                    *prefix,
                    "-threads",
                    str(budget.pool_size),
                    "-err_detect",
                    "explode",
                    "-protocol_whitelist",
                    "fd",
                    "-fd",
                    str(input_stream.fileno()),
                    "-i",
                    "fd:",
                    "-map",
                    f"0:{claim.stream_index}",
                    "-map_metadata",
                    "-1",
                    "-map_chapters",
                    "-1",
                ]
                if kind == "video":
                    width, height = spec["width"], spec["height"]
                    pix_fmt = spec["pix_fmt"]
                    encoder = spec["encoder"]
                    if (
                        type(width) is not int
                        or type(height) is not int
                        or min(width, height) < 1
                        or max(width, height) > 16384
                        or pix_fmt not in {"yuv420p", "yuv420p10le"}
                        or encoder not in {"libsvtav1", "libaom-av1"}
                    ):
                        raise ValueError
                    sar = rational(spec["sar"])
                    cap = spec["fps_cap"]
                    crf = spec["crf"]
                    if (
                        sar <= 0
                        or max(sar.numerator, sar.denominator) > 2**31 - 1
                        or cap not in {30, 60}
                        or crf not in {28, 30, 32, 34}
                    ):
                        raise ValueError
                    primaries, trc, matrix = (
                        ("bt2020", "smpte2084", "bt2020nc")
                        if spec["range"] == "pq"
                        else ("bt709", "bt709", "bt709")
                    )
                    filter_graph = (
                        f"setpts=PTS-STARTPTS,fps={rational(spec['fps'])},"
                        f"libplacebo=w={width}:h={height}:format={pix_fmt}:"
                        f"normalize_sar=false:color_primaries={primaries}:color_trc={trc}:"
                        f"colorspace={matrix}:range=tv:tonemapping=bt.2390:"
                        f"apply_dolbyvision=true,setsar=sar={sar}:max=2147483647"
                    )
                    args += [
                        "-an",
                        "-sn",
                        "-vf",
                        filter_graph,
                        "-c:v",
                        str(encoder),
                        "-pix_fmt",
                        str(pix_fmt),
                        "-crf",
                        str(crf),
                        "-b:v",
                        "0",
                        "-color_primaries",
                        primaries,
                        "-color_trc",
                        trc,
                        "-colorspace",
                        matrix,
                        "-color_range",
                        "tv",
                        "-fps_mode",
                        "cfr",
                        "-g",
                        str(spec["gop"]),
                        "-force_key_frames",
                        f"expr:gte(n,n_forced*{spec['gop']})",
                    ]
                    if encoder == "libsvtav1":
                        args += [
                            "-preset",
                            "6",
                            "-svtav1-params",
                            f"tune=0:lp={budget.svt_level}:irefresh-type=2",
                        ]
                    else:
                        args += ["-cpu-used", "4", "-threads", str(budget.pool_size)]
                else:
                    channels, bitrate = spec["channels"], spec["bitrate"]
                    if (
                        channels not in {1, 2}
                        or type(bitrate) is not int
                        or bitrate
                        not in {32000, 48000, 64000, 80000, 96000, 120000, 160000, 240000}
                    ):
                        raise ValueError
                    offset = rational(spec["offset"])
                    if abs(offset) > 3600:
                        raise ValueError
                    audio_filter = (
                        f"asetpts=PTS-STARTPTS+({offset})/TB,"
                        "aresample=48000:async=1:first_pts=0,apad,"
                        f"atrim=end_sample={ceil(duration * 48000)}"
                    )
                    args += [
                        "-vn",
                        "-sn",
                        "-af",
                        audio_filter,
                        "-c:a",
                        "aac",
                        "-profile:a",
                        "aac_low",
                        "-ar",
                        "48000",
                        "-ac",
                        str(channels),
                        "-b:a",
                        str(bitrate),
                    ]
                args += [
                    "-f",
                    "hls",
                    "-hls_time",
                    str(float(rational(spec["segment_duration"]))),
                    "-hls_segment_type",
                    "fmp4",
                    "-hls_playlist_type",
                    "vod",
                    "-hls_flags",
                    "independent_segments",
                    "-hls_fmp4_init_filename",
                    "init.mp4",
                    "-hls_segment_filename",
                    str(directory / "segment_%06d.m4s"),
                    "-progress",
                    "pipe:1",
                    "-nostats",
                    str(directory / "index.m3u8"),
                ]
                await run_media_process(
                    Path(ffmpeg),
                    args,
                    workspace=root,
                    limits=limits,
                    environment=environment,
                    pass_fds=(input_stream.fileno(),),
                )
            track, names = await asyncio.to_thread(package_files, directory)
            # These paths/URIs are generated by our muxer and checked above, never input URLs.
            probe_path = directory / "metadata.json"
            await run_media_process(
                Path(ffprobe),
                [
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file",
                    "-allowed_extensions",
                    "ALL",
                    "-i",
                    str(directory / "index.m3u8"),
                    "-show_entries",
                    "stream=codec_name,width,height,pix_fmt,sample_aspect_ratio,avg_frame_rate,r_frame_rate,color_primaries,color_transfer,color_space,color_range,extradata,channels,sample_rate,profile,nb_read_frames,start_pts,time_base",
                    "-count_frames",
                    "-show_data",
                    "-of",
                    "json",
                    "-o",
                    str(probe_path),
                ],
                workspace=root,
                limits=limits,
                environment=environment,
                reject_stderr=True,
            )
            payload = mapping(json.loads(await asyncio.to_thread(probe_path.read_bytes)))
            streams = cast(list[object], payload["streams"])
            if len(streams) != 1:
                raise ValueError
            stream = mapping(streams[0])
            extra = configuration(stream["extradata"])
            if kind == "video":
                if (
                    stream["codec_name"] != "av1"
                    or stream["width"] != spec["width"]
                    or stream["height"] != spec["height"]
                    or stream["pix_fmt"] != spec["pix_fmt"]
                ):
                    raise ValueError
                verified_fps = rational(spec["fps"])
                frame_count = int(str(stream["nb_read_frames"]))
                measured_duration = sum(
                    (segment.duration for segment in track.segments), Fraction(0)
                )
                if abs(measured_duration - duration) > 1 / verified_fps + Fraction(1, 1000):
                    raise ValueError
                if frame_count < 1 or abs(
                    Fraction(frame_count) / verified_fps - measured_duration
                ) > Fraction(1, 1000) + Fraction(len(track.segments), 1000000):
                    raise ValueError
                color_values = {"bt709": 1, "bt2020": 9, "bt2020nc": 9, "smpte2084": 16}
                video = Av1Video(
                    extra,
                    cast(int, stream["width"]),
                    cast(int, stream["height"]),
                    verified_fps,
                    color_values[str(stream["color_primaries"])],
                    color_values[str(stream["color_transfer"])],
                    color_values[str(stream["color_space"])],
                    stream["color_range"] == "pc",
                )
                if video.video_range != (
                    "PQ" if spec["range"] == "pq" else "SDR"
                ) or video.frame_rate > cast(int, spec["fps_cap"]):
                    raise ValueError
                codec = video.codec
                stream["verified_frame_rate"] = str(verified_fps)
            else:
                audio = AacAudio(track, extra)
                if (
                    stream["codec_name"] != "aac"
                    or audio.channels != spec["channels"]
                    or stream["sample_rate"] != "48000"
                    or stream["profile"] != "LC"
                ):
                    raise ValueError
                codec = "mp4a.40.2"
            await run_media_process(
                Path(ffmpeg),
                [
                    "-v",
                    "error",
                    "-nostdin",
                    "-xerror",
                    "-threads",
                    str(budget.pool_size),
                    "-protocol_whitelist",
                    "file",
                    "-allowed_extensions",
                    "ALL",
                    "-i",
                    str(directory / "index.m3u8"),
                    "-map",
                    "0:0",
                    "-f",
                    "null",
                    "-",
                    "-progress",
                    "pipe:1",
                    "-nostats",
                ],
                workspace=root,
                limits=limits,
                environment=environment,
                reject_stderr=True,
            )
            files: list[dict[str, object]] = []
            try:
                for name in names:
                    path = directory / name
                    size = path.stat().st_size
                    digest = await asyncio.to_thread(file_digest, path)
                    with path.open("rb") as data:
                        await sdk(
                            lambda name=name, data=data, size=size: self.objects.put_object(
                                Bucket=self.bucket,
                                Key=claim.prefix + name,
                                Body=data,
                                ContentLength=size,
                                ContentType="application/vnd.apple.mpegurl"
                                if name.endswith("m3u8")
                                else "audio/mp4"
                                if kind == "audio"
                                else "video/mp4",
                            )
                        )
                    files.append({"name": name, "size": size, "sha256": digest})
            except BaseException:
                with contextlib.suppress(StorageUnavailable, SourceInspectionError):
                    await asyncio.shield(self.discard(claim.prefix))
                raise
            peak, average = track.bitrates()
            return {
                "prefix": claim.prefix,
                "kind": kind,
                "codec": codec,
                "files": files,
                "configuration": extra.hex(),
                "metadata": {key: value for key, value in stream.items() if key != "extradata"},
                "segments": [
                    [segment.duration.numerator, segment.duration.denominator]
                    for segment in track.segments
                ],
                "target_duration": track.target_duration,
                "bandwidth": peak,
                "average_bandwidth": average,
                **(
                    {
                        "presentation_origin": [
                            (
                                Fraction(str(stream["start_pts"]))
                                * Fraction(str(stream["time_base"]))
                            ).numerator,
                            (
                                Fraction(str(stream["start_pts"]))
                                * Fraction(str(stream["time_base"]))
                            ).denominator,
                        ]
                    }
                    if kind == "video"
                    else {}
                ),
            }
