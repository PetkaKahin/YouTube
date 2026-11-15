"""Shared subtitle profiles use trusted fingerprints and exact presentation clocks."""

from typing import cast

from video.domain.encoding import PlannedTrack, mapping, pair, primary, rational
from video.domain.processing import SourceInspectionError

BITMAP_SUBTITLES = frozenset({"dvd_subtitle", "dvb_subtitle", "hdmv_pgs_subtitle", "xsub"})


def subtitle_profile(
    track: dict[str, object], video: dict[str, object], artifact: dict[str, object]
) -> PlannedTrack:
    try:
        properties = mapping(track["properties"])
        origin = rational(artifact["presentation_origin"])
        segments = [pair(rational(value)) for value in cast(list[object], artifact["segments"])]
        if not segments or any(rational(value) <= 0 for value in segments):
            raise ValueError
        spec: dict[str, object] = {
            "profile": "webvtt-v1",
            "kind": "subtitle",
            "codec": properties.get("codec_name", "unknown"),
            "empty_source": track.get("packet_count") == 0,
            "offset": pair(rational(track["offset"]) - rational(video["presentation_offset"])),
            "earliest": pair(
                rational(track["presentation_offset"]) - rational(video["presentation_offset"])
            ),
            "segments": segments,
            "presentation_origin": pair(origin),
        }
        return PlannedTrack(cast(int, track["stream_index"]), str(track["fingerprint"]), spec, 0)
    except KeyError, ValueError, TypeError, ZeroDivisionError:
        raise SourceInspectionError("invalid_subtitle_timeline") from None


def subtitle_inventory(
    payload: dict[str, object],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    tracks = [mapping(item) for item in cast(list[object], payload["tracks"])]
    video = primary(tracks, "video")
    if video is None:
        raise SourceInspectionError("invalid_media_plan")
    return video, [
        track for track in tracks if mapping(track["properties"]).get("codec_type") == "subtitle"
    ]
