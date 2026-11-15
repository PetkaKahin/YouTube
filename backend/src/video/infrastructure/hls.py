"""Render HLS masters from prepared tracks, independently of FFmpeg's master writer."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from math import ceil


class HlsMetadataError(ValueError):
    """A prepared track lacks reliable metadata needed for publication."""


def _positive_integer(value: int) -> None:
    if type(value) is not int or value <= 0:
        raise HlsMetadataError("Expected a positive integer.")


def _relative_uri(uri: str) -> None:
    # Only server-generated paths within a media package; no URLs or escaping.
    if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", uri) or any(
        part in {".", ".."} for part in uri.split("/")
    ):
        raise HlsMetadataError("Expected a relative media path.")


def _positive_fraction(value: object) -> None:
    if not isinstance(value, Fraction) or value <= 0:
        raise HlsMetadataError("Expected a positive rational value.")


@dataclass(frozen=True, slots=True)
class Av1Video:
    configuration: bytes
    width: int
    height: int
    frame_rate: Fraction
    color_primaries: int
    transfer_characteristics: int
    matrix_coefficients: int
    full_range: bool

    def __post_init__(self) -> None:
        _positive_integer(self.width)
        _positive_integer(self.height)
        _positive_fraction(self.frame_rate)
        # CICP identifiers understood by the installed FFmpeg, excluding
        # reserved/unspecified values. RGB identity isn't valid for our 4:2:0.
        for field, allowed in (
            (self.color_primaries, {1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 22}),
            (self.transfer_characteristics, {1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16}),
            (self.matrix_coefficients, {1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17}),
        ):
            if type(field) is not int or field not in allowed:
                raise HlsMetadataError("Missing or invalid AV1 color metadata.")
        if type(self.full_range) is not bool:
            raise HlsMetadataError("Missing video range flag.")
        # av1C, not Sequence Header OBU bytes or an assumed resolution/level table.
        data = self.configuration
        if len(data) < 4 or data[0] != 0x81 or data[3] & 0xE0:
            raise HlsMetadataError("Invalid AV1CodecConfigurationRecord.")
        if not data[3] & 0x10 and data[3] & 0x0F:
            raise HlsMetadataError("Nonzero reserved AV1 configuration bits.")
        profile, level = data[1] >> 5, data[1] & 0x1F
        tier, twelve_bit = data[2] >> 7, bool(data[2] & 0x20)
        monochrome, subsampling = bool(data[2] & 0x10), (data[2] >> 2) & 3
        if (
            profile != 0
            or level > 23
            or (tier and level < 8)
            or twelve_bit
            or monochrome
            or subsampling != 3
            or data[2] & 3 == 3
        ):
            raise HlsMetadataError("Expected supported AV1 Main, 8/10-bit, 4:2:0 output.")

    @property
    def codec(self) -> str:
        data = self.configuration
        profile, level = data[1] >> 5, data[1] & 0x1F
        tier = "H" if data[2] & 0x80 else "M"
        depth = 10 if data[2] & 0x40 else 8
        position = data[2] & 3
        return (
            f"av01.{profile}.{level:02d}{tier}.{depth:02d}.0.11{position}."
            f"{self.color_primaries:02d}.{self.transfer_characteristics:02d}."
            f"{self.matrix_coefficients:02d}.{int(self.full_range)}"
        )

    @property
    def video_range(self) -> str:
        return "PQ" if self.transfer_characteristics == 16 else "SDR"


@dataclass(frozen=True, slots=True)
class Segment:
    size_bytes: int
    duration: Fraction

    def __post_init__(self) -> None:
        _positive_integer(self.size_bytes)
        _positive_fraction(self.duration)


@dataclass(frozen=True, slots=True)
class MediaTrack:
    uri: str
    target_duration: int
    segments: tuple[Segment, ...]

    def __post_init__(self) -> None:
        _relative_uri(self.uri)
        _positive_integer(self.target_duration)
        if not self.segments:
            raise HlsMetadataError("An empty track cannot be published.")
        if any(
            int(segment.duration + Fraction(1, 2)) > self.target_duration
            for segment in self.segments
        ):
            raise HlsMetadataError("Segment duration exceeds HLS TARGETDURATION.")

    def bitrates(self) -> tuple[int, int]:
        """Return peak and average bits/s, including the media container overhead."""
        total_bits = sum(segment.size_bytes * 8 for segment in self.segments)
        total_duration = sum((segment.duration for segment in self.segments), Fraction())
        average = Fraction(total_bits, 1) / total_duration
        peak = Fraction()
        lower, upper = Fraction(self.target_duration, 2), Fraction(3 * self.target_duration, 2)
        # RFC 8216 §4.1: contiguous windows of 0.5–1.5 target duration.
        # Generated six-second segments bound the inner loop to a few entries.
        for start in range(len(self.segments)):
            bits, duration = 0, Fraction()
            for index in range(start, len(self.segments)):
                segment = self.segments[index]
                bits += segment.size_bytes * 8
                duration += segment.duration
                if duration > upper:
                    break
                if duration >= lower:
                    peak = max(peak, Fraction(bits, 1) / duration)
        # A clip shorter than the minimum window still needs a bandwidth value.
        return ceil(peak or average), ceil(average)


@dataclass(frozen=True, slots=True)
class AacAudio:
    track: MediaTrack
    configuration: bytes

    def __post_init__(self) -> None:
        data = self.configuration
        if len(data) < 2:
            raise HlsMetadataError("Missing AAC AudioSpecificConfig.")
        object_type = data[0] >> 3
        frequency_index = ((data[0] & 7) << 1) | (data[1] >> 7)
        if object_type != 2 or frequency_index != 3 or self.channels not in {1, 2}:
            raise HlsMetadataError("Expected AAC-LC, 48 kHz, mono/stereo output.")

    @property
    def channels(self) -> int:
        return (self.configuration[1] >> 3) & 0x0F


@dataclass(frozen=True, slots=True)
class VideoRendition:
    track: MediaTrack
    video: Av1Video
    audio: AacAudio | None = None
    subtitles: tuple[SubtitleRendition, ...] = ()


@dataclass(frozen=True, slots=True)
class SubtitleRendition:
    track: MediaTrack
    name: str
    language: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.name.strip()
            or '"' in self.name
            or any(ord(char) < 32 or ord(char) == 127 for char in self.name)
        ):
            raise HlsMetadataError("Invalid subtitle display name.")
        if self.language is not None and not re.fullmatch(
            r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", self.language
        ):
            raise HlsMetadataError("Invalid subtitle language tag.")


def render_master(
    renditions: Sequence[VideoRendition], *, subtitles: Sequence[SubtitleRendition] = ()
) -> str:
    """Return a complete master or raise; storage readiness/access are caller duties."""
    if not renditions:
        raise HlsMetadataError("At least one prepared video rendition is required.")
    video_uris = [rendition.track.uri for rendition in renditions]
    if len(set(video_uris)) != len(video_uris):
        raise HlsMetadataError("Duplicate video playlist URI.")
    audio_tracks: dict[str, AacAudio] = {}
    for rendition in renditions:
        audio = rendition.audio
        if audio is None:
            continue
        uri = audio.track.uri
        if uri in video_uris or (uri in audio_tracks and audio_tracks[uri] != audio):
            raise HlsMetadataError("Conflicting playlist metadata for the same URI.")
        audio_tracks[uri] = audio

    if subtitles and any(rendition.subtitles for rendition in renditions):
        raise HlsMetadataError("Use either common or per-variant subtitles.")
    subtitle_groups: dict[tuple[SubtitleRendition, ...], str] = {}
    subtitle_tracks: dict[str, MediaTrack] = {}
    for rendition in renditions:
        members = tuple(subtitles) or rendition.subtitles
        uris = [member.track.uri for member in members]
        if len(set(uris)) != len(uris) or len({member.name for member in members}) != len(members):
            raise HlsMetadataError("Conflicting subtitle names or playlist URIs.")
        for member in members:
            uri = member.track.uri
            if uri in (*video_uris, *audio_tracks) or (
                uri in subtitle_tracks and subtitle_tracks[uri] != member.track
            ):
                raise HlsMetadataError("Conflicting subtitle playlist metadata.")
            subtitle_tracks[uri] = member.track
        if members and members not in subtitle_groups:
            subtitle_groups[members] = (
                "subtitles" if not subtitle_groups else f"subtitles-{len(subtitle_groups)}"
            )
    lines = ["#EXTM3U", "#EXT-X-VERSION:7"]
    memberships = {
        frozenset((member.name, member.language) for member in members)
        for members in subtitle_groups
    }
    if len(memberships) > 1:
        raise HlsMetadataError("Subtitle groups must contain the same logical members.")
    groups = {uri: f"audio-{index}" for index, uri in enumerate(audio_tracks)}
    for uri, audio in audio_tracks.items():
        group = groups[uri]
        lines.append(
            f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="{group}",NAME="Audio",'
            f'DEFAULT=YES,AUTOSELECT=YES,CHANNELS="{audio.channels}",URI="{uri}"'
        )
    for members, group in subtitle_groups.items():
        for subtitle in members:
            language = f',LANGUAGE="{subtitle.language}"' if subtitle.language is not None else ""
            lines.append(
                f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="{group}",NAME="{subtitle.name}",'
                f'DEFAULT=NO,AUTOSELECT=NO,URI="{subtitle.track.uri}"{language}'
            )
    for rendition in renditions:
        video, audio = rendition.video, rendition.audio
        peak, average = rendition.track.bitrates()
        members = tuple(subtitles) or rendition.subtitles
        rates = [member.track.bitrates() for member in members]
        peak += max((rate[0] for rate in rates), default=0)
        average += max((rate[1] for rate in rates), default=0)
        codec = video.codec
        audio_attribute = ""
        if audio is not None:
            audio_peak, audio_average = audio.track.bitrates()
            peak += audio_peak
            average += audio_average
            codec += ",mp4a.40.2"
            audio_attribute = f',AUDIO="{groups[audio.track.uri]}"'
        subtitle_attribute = f',SUBTITLES="{subtitle_groups[members]}"' if members else ""
        lines.extend(
            (
                f"#EXT-X-STREAM-INF:BANDWIDTH={peak},AVERAGE-BANDWIDTH={average},"
                f'CODECS="{codec}",RESOLUTION={video.width}x{video.height},'
                f"FRAME-RATE={float(video.frame_rate):.3f},VIDEO-RANGE={video.video_range}"
                f"{audio_attribute}{subtitle_attribute}",
                rendition.track.uri,
            )
        )
    return "\n".join(lines) + "\n"
