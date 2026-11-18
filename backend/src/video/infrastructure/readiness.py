"""One MVCC snapshot of a publication's shared outputs; no cached readiness."""

import re
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from common.contracts.job_errors import ERROR_CODES
from video.domain.publications import ProcessingState
from video.domain.readiness import (
    AuxiliaryStatus,
    ProcessingStatus,
    QualityStatus,
    SubtitleStatus,
    overall,
)
from video.infrastructure.hls import (
    AacAudio,
    Av1Video,
    MediaTrack,
    Segment,
    SubtitleRendition,
    VideoRendition,
    render_master,
)
from video.infrastructure.prepared_bundle import prepared_prefix


def object_value(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Invalid stored media descriptor")
    return cast(dict[str, object], value)


def sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("Invalid stored media list")
    return cast(list[object], value)


def number(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid stored media number")
    return value


def fraction(value: object) -> Fraction:
    pair = sequence(value)
    if len(pair) != 2 or any(type(item) is not int for item in pair):
        raise ValueError("Invalid stored media duration")
    return Fraction(cast(int, pair[0]), cast(int, pair[1]))


def error_code(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and value in ERROR_CODES else fallback


def valid_artifact_prefix(output: dict[str, object]) -> bool:
    artifact = object_value(output["artifact"])
    prefix = str(artifact["prefix"])
    if re.fullmatch(rf"encoded/{number(output['id'])}/[0-9a-f]{{32}}/", prefix):
        return True
    profile = output.get("profile_key")
    return isinstance(profile, str) and prefix == prepared_prefix(profile, artifact)


def media_track(output: dict[str, object], kind: str) -> MediaTrack:
    artifact = object_value(output["artifact"])
    encoded_id = number(output["id"])
    if artifact["kind"] != kind or not valid_artifact_prefix(output):
        raise ValueError("Invalid stored media identity")
    durations = [fraction(item) for item in sequence(artifact["segments"])]
    extension = "vtt" if kind == "subtitle" else "m4s"
    names = [f"segment_{index:06d}.{extension}" for index in range(len(durations))]
    expected = {*names, "index.m3u8"}
    if kind != "subtitle":
        expected.add("init.mp4")
    sizes: dict[str, int] = {}
    for item in sequence(artifact["files"]):
        entry = object_value(item)
        name = str(entry["name"])
        if (
            name not in expected
            or name in sizes
            or re.fullmatch(r"[0-9a-f]{64}", str(entry["sha256"])) is None
        ):
            raise ValueError("Invalid stored package manifest")
        sizes[name] = number(entry["size"])
    if set(sizes) != expected:
        raise ValueError("Incomplete stored package manifest")
    return MediaTrack(
        f"tracks/{encoded_id}/index.m3u8",
        number(artifact["target_duration"]),
        tuple(
            Segment(sizes[name], duration) for name, duration in zip(names, durations, strict=True)
        ),
    )


def video_rendition(video: dict[str, object], audio: dict[str, object] | None) -> VideoRendition:
    artifact = object_value(video["artifact"])
    metadata, spec = object_value(artifact["metadata"]), object_value(video["spec"])
    colors = {"bt709": 1, "bt2020": 9, "bt2020nc": 9, "smpte2084": 16}
    descriptor = Av1Video(
        bytes.fromhex(str(artifact["configuration"])),
        number(metadata["width"]),
        number(metadata["height"]),
        Fraction(str(metadata["verified_frame_rate"])),
        colors[str(metadata["color_primaries"])],
        colors[str(metadata["color_transfer"])],
        colors[str(metadata["color_space"])],
        metadata["color_range"] == "pc",
    )
    if (
        spec["kind"] != "video"
        or metadata["codec_name"] != "av1"
        or descriptor.width != spec["width"]
        or descriptor.height != spec["height"]
        or descriptor.frame_rate != fraction(spec["fps"])
        or descriptor.video_range != ("PQ" if spec["range"] == "pq" else "SDR")
        or metadata["color_range"] not in {"pc", "tv"}
    ):
        raise ValueError("Inconsistent stored video descriptor")
    aac = None
    if audio is not None:
        aac_artifact = object_value(audio["artifact"])
        aac = AacAudio(
            media_track(audio, "audio"), bytes.fromhex(str(aac_artifact["configuration"]))
        )
        if object_value(audio["spec"])["kind"] != "audio":
            raise ValueError("Invalid stored audio descriptor")
    return VideoRendition(media_track(video, "video"), descriptor, aac)


@dataclass(frozen=True)
class PreparedProcessing:
    status: ProcessingStatus
    master: str | None = field(default=None, repr=False)


def prepare(snapshot: dict[str, object]) -> PreparedProcessing:
    upload = str(snapshot["upload"])
    plan = object_value(snapshot["plan"]) if snapshot["plan"] is not None else {}
    source = object_value(snapshot["source"]) if snapshot["source"] is not None else {}
    if plan.get("status") != "planned":
        failed = source.get("status") == "failed" or plan.get("status") == "failed"
        failure = plan.get("error_code") or source.get("error_code")
        return PreparedProcessing(
            ProcessingStatus(
                upload,
                ProcessingState.FAILED if failed else ProcessingState(str(snapshot["processing"])),
                error_code(failure, "inspection_failed") if failed else None,
            )
        )
    config = object_value(plan["config"])
    outputs = {
        number(item["id"]): item
        for raw in sequence(snapshot["outputs"] or [])
        if (item := object_value(raw))
    }
    links = [object_value(item) for item in sequence(snapshot["subtitles"] or [])]
    qualities: list[QualityStatus] = []
    subtitle_statuses: list[SubtitleStatus] = []
    renditions: list[VideoRendition] = []
    active = False
    for raw in sequence(config["variants"]):
        variant = object_value(raw)
        quality, color = number(variant["quality"]), str(variant["range"])
        video_id = number(variant["video"])
        video = outputs.get(video_id)
        audio = outputs.get(number(variant["audio"])) if variant["audio"] is not None else None
        dependencies = [video] + ([audio] if variant["audio"] is not None else [])
        active |= any(
            item is not None and item["status"] in {"pending", "running"} for item in dependencies
        )
        failed_outputs = [
            item for item in dependencies if item is None or item["status"] in {"failed", "empty"}
        ]
        rendition: VideoRendition | None = None
        code: str | None = None
        if failed_outputs:
            state = "failed"
            failed_output = failed_outputs[0]
            code = error_code(
                failed_output.get("error_code") if failed_output else None, "encoding_failed"
            )
        elif any(item is not None and item["status"] == "running" for item in dependencies):
            state = "processing"
        elif any(item is not None and item["status"] != "ready" for item in dependencies):
            state = "pending"
        else:
            try:
                rendition = video_rendition(cast(dict[str, object], video), audio)
                if (
                    color not in {"sdr", "pq"}
                    or object_value(cast(dict[str, object], video)["spec"])["quality"] != quality
                    or object_value(cast(dict[str, object], video)["spec"])["range"] != color
                ):
                    raise ValueError("Invalid stored quality")
                state = "ready"
            except KeyError, ValueError, TypeError, ZeroDivisionError, OverflowError:
                state, code = "failed", "invalid_encoded_media"
                rendition = None
        members: list[SubtitleRendition] = []
        for link in sorted(links, key=lambda item: cast(int, item["stream_index"])):
            if link["video_encoded_id"] != video_id:
                continue
            output = outputs.get(cast(int, link.get("encoded_id")))
            sub_state, sub_code = str(link["status"]), None
            if sub_state == "failed":
                sub_code = error_code(link.get("error_code"), "subtitle_failed")
            elif output is not None:
                sub_state = str(output["status"])
                if sub_state == "failed":
                    sub_code = error_code(output.get("error_code"), "subtitle_failed")
                elif sub_state == "ready" and state == "ready":
                    try:
                        sub_artifact = object_value(output["artifact"])
                        video_artifact = object_value(cast(dict[str, object], video)["artifact"])
                        if any(
                            sub_artifact[key] != video_artifact[key]
                            for key in ("segments", "presentation_origin")
                        ):
                            raise ValueError("Incompatible subtitle timeline")
                        language: str | None = str(link["language"])
                        if (
                            re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", language) is None
                            or language == "und"
                        ):
                            language = None
                        members.append(
                            SubtitleRendition(
                                media_track(output, "subtitle"),
                                f"Track {link['stream_index']}",
                                language,
                            )
                        )
                    except KeyError, ValueError, TypeError, ZeroDivisionError, OverflowError:
                        sub_state, sub_code = "failed", "invalid_subtitle_timeline"
            subtitle_statuses.append(
                SubtitleStatus(cast(int, link["stream_index"]), quality, color, sub_state, sub_code)
            )
        qualities.append(QualityStatus(quality, color, state, code))
        if rendition is not None:
            renditions.append(
                VideoRendition(rendition.track, rendition.video, rendition.audio, tuple(members))
            )
    thumbnail = outputs.get(cast(int, config.get("thumbnail")))
    thumbnail_status = AuxiliaryStatus(
        str(thumbnail["status"]) if thumbnail else "pending",
        error_code(thumbnail.get("error_code"), "thumbnail_failed")
        if thumbnail and thumbnail["status"] == "failed"
        else None,
    )
    result = tuple(qualities)
    # RFC 8216 4.3.4.1.1: all groups of a TYPE have the same logical members.
    # A cue track is advertised after its compatible packages cover the ready
    # variants. This never delays the video itself or invents an empty package.
    if renditions:
        common = {(member.name, member.language) for member in renditions[0].subtitles}
        for item in renditions[1:]:
            common.intersection_update((member.name, member.language) for member in item.subtitles)
        renditions = [
            replace(
                item,
                subtitles=tuple(
                    member for member in item.subtitles if (member.name, member.language) in common
                ),
            )
            for item in renditions
        ]
    return PreparedProcessing(
        ProcessingStatus(
            upload,
            overall(result, active=active),
            qualities=result,
            thumbnail=thumbnail_status,
            subtitles=tuple(subtitle_statuses),
        ),
        render_master(renditions) if renditions else None,
    )


# Fixed identifiers only. The correlated aggregates and state rows are evaluated
# by ONE SELECT, so concurrent shared completions cannot mix database snapshots.
MEDIA_SNAPSHOT = """jsonb_build_object(
 'upload', p.upload, 'processing', p.processing,
 'source', jsonb_build_object('status',s.status,'error_code',s.error_code),
 'plan', jsonb_build_object('status',m.status,'config',m.config,'error_code',m.error_code),
 'outputs', (SELECT jsonb_agg(jsonb_build_object(
   'id',e.id,'profile_key',e.profile_key,'status',e.status,'spec',e.spec,'artifact',e.artifact,'error_code',e.error_code))
 FROM video_encoded_tracks e WHERE
   EXISTS (SELECT 1 FROM video_plan_tracks r WHERE r.upload_id=u.id AND r.encoded_id=e.id)
   OR e.id=(m.config->>'thumbnail')::bigint
   OR EXISTS (SELECT 1 FROM video_subtitle_links l WHERE l.upload_id=u.id AND l.encoded_id=e.id)),
 'subtitles', (SELECT jsonb_agg(to_jsonb(l)) FROM video_subtitle_links l WHERE l.upload_id=u.id)
)
"""
MEDIA_JOINS = """
LEFT JOIN video_uploads u ON u.video_id=p.id AND u.status<>'expired'
LEFT JOIN video_source_processing s ON s.upload_id=u.id
LEFT JOIN video_media_plans m ON m.upload_id=u.id
"""
SNAPSHOT = text(
    f"SELECT {MEDIA_SNAPSHOT} FROM video_publications p {MEDIA_JOINS} "  # noqa: S608 -- constant fragments; bound values
    "WHERE p.id=:video_id AND p.owner_id=:owner_id"
)


class SqlProcessingViews:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def prepared(self, video_id: int, owner_id: int) -> PreparedProcessing | None:
        value = await self.session.scalar(SNAPSHOT, {"video_id": video_id, "owner_id": owner_id})
        return prepare(object_value(value)) if value is not None else None

    async def owned(self, video_id: int, owner_id: int) -> ProcessingStatus | None:
        value = await self.prepared(video_id, owner_id)
        return value.status if value is not None else None
