"""Prepare FFmpeg-normalized WebVTT for a known HLS video timeline."""

import re
from dataclasses import dataclass
from fractions import Fraction
from html import escape, unescape

from video.infrastructure.hls import MediaTrack, Segment


class SubtitlePreparationError(ValueError):
    """A subtitle track cannot be prepared; other media remains independent."""


_TIMESTAMP = r"(?:(\d{2,}):)?([0-5]\d):([0-5]\d)\.(\d{3})"
_TIMING = re.compile(
    rf"(?P<start>{_TIMESTAMP})[ \t]+-->[ \t]+(?P<end>{_TIMESTAMP})(?:[ \t]+[^\n]*)?"
)
_BASIC_TAG = re.compile(r"(</?[biu]>)")
_ENTITY = re.compile(r"&(?:amp|lt|gt|nbsp|lrm|rlm);")


def _milliseconds(timestamp: str) -> int:
    match = re.fullmatch(_TIMESTAMP, timestamp)
    if match is None:
        raise SubtitlePreparationError("Invalid WebVTT timestamp.")
    hours, minutes, seconds, milliseconds = match.groups()
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(milliseconds)


def _format_time(milliseconds: int) -> str:
    seconds, fraction = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{fraction:03d}"


def _safe_text(text: str) -> str:
    result: list[str] = []
    stack: list[str] = []
    for part in _BASIC_TAG.split(text):
        if _BASIC_TAG.fullmatch(part) is None:
            # WebVTT's named references only. Decoding arbitrary HTML numeric
            # entities could inject line breaks and create additional cues.
            decoded = _ENTITY.sub(lambda match: unescape(match[0]), part)
            result.append(escape(decoded, quote=False))
        elif part.startswith("</"):
            tag = part[2]
            if tag in stack:
                while stack:
                    current = stack.pop()
                    result.append(f"</{current}>")
                    if current == tag:
                        break
        else:
            stack.append(part[1])
            result.append(part)
    result.extend(f"</{tag}>" for tag in reversed(stack))
    return "".join(result)


@dataclass(frozen=True, slots=True)
class _Cue:
    start_ms: int
    end_ms: int
    text: str


def _parse(payload: bytes) -> tuple[_Cue, ...]:
    try:
        text = payload.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError:
        raise SubtitlePreparationError("WebVTT must be UTF-8.") from None
    if any(ord(char) < 32 and char not in "\n\t" or ord(char) == 127 for char in text):
        raise SubtitlePreparationError("Control characters in WebVTT.")
    blocks = re.split(r"\n[ \t]*\n", text.strip("\n"))
    # Only normalized FFmpeg output: no imported map/CSS or guessed timeline.
    if not blocks or blocks[0].strip() != "WEBVTT":
        raise SubtitlePreparationError("Expected normalized WebVTT header.")
    cues: list[_Cue] = []
    for block in blocks[1:]:
        if not block.strip() or block == "NOTE" or block.startswith(("NOTE ", "NOTE\n")):
            continue
        # WebVTT line endings are CR/LF. Unicode paragraph separators in cue
        # text must not become structural newlines through str.splitlines().
        lines = block.split("\n")
        timing_index = 0 if "-->" in lines[0] else 1
        if len(lines) <= timing_index + 1:
            raise SubtitlePreparationError("Incomplete WebVTT cue.")
        match = _TIMING.fullmatch(lines[timing_index])
        if match is None:
            raise SubtitlePreparationError("Invalid WebVTT cue timing.")
        start, end = _milliseconds(match["start"]), _milliseconds(match["end"])
        if end <= start:
            raise SubtitlePreparationError("Nonpositive WebVTT cue interval.")
        cue_text = _safe_text("\n".join(lines[timing_index + 1 :]))
        if cue_text.strip():
            cues.append(_Cue(start, end, cue_text))
    return tuple(cues)


@dataclass(frozen=True, slots=True)
class WebVttSegment:
    name: str
    duration: Fraction
    data: bytes


@dataclass(frozen=True, slots=True)
class PreparedSubtitles:
    target_duration: int
    segments: tuple[WebVttSegment, ...]

    @property
    def playlist(self) -> str:
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"#EXT-X-TARGETDURATION:{self.target_duration}",
            "#EXT-X-MEDIA-SEQUENCE:0",
            "#EXT-X-PLAYLIST-TYPE:VOD",
        ]
        for segment in self.segments:
            lines.extend((f"#EXTINF:{float(segment.duration):.6f},", segment.name))
        return "\n".join((*lines, "#EXT-X-ENDLIST", ""))

    def as_track(self, uri: str) -> MediaTrack:
        return MediaTrack(
            uri,
            self.target_duration,
            tuple(Segment(len(segment.data), segment.duration) for segment in self.segments),
        )


def _fraction(value: object) -> Fraction:
    if not isinstance(value, Fraction):
        raise SubtitlePreparationError("Timeline values must be exact rational numbers.")
    return value


def prepare_webvtt(
    payload: bytes,
    *,
    segment_durations: tuple[Fraction, ...],
    source_origin: Fraction,
    presentation_origin: Fraction,
) -> PreparedSubtitles | None:
    """Return a package, or None when no cues fall within the video timeline.

    Origins are seconds. The caller supplies the extraction timestamp origin
    and the actual presentation start of the prepared video, not audio priming.
    No subprocess, filesystem, source upload or access policy is involved here.
    """
    _fraction(source_origin)
    _fraction(presentation_origin)
    if not segment_durations or any(_fraction(value) <= 0 for value in segment_durations):
        raise SubtitlePreparationError("Expected a nonempty video segment timeline.")
    duration = sum(segment_durations, Fraction())
    cues: list[_Cue] = []
    for cue in _parse(payload):
        start = max(Fraction(), Fraction(cue.start_ms, 1000) - source_origin)
        end = min(duration, Fraction(cue.end_ms, 1000) - source_origin)
        start_ms, end_ms = int(start * 1000 + Fraction(1, 2)), int(end * 1000 + Fraction(1, 2))
        if start_ms < end_ms:
            cues.append(_Cue(start_ms, end_ms, cue.text))
    if not cues:
        return None
    cues.sort(key=lambda cue: cue.start_ms)
    mpegts = round(presentation_origin * 90000) % (1 << 33)
    header = f"WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:{mpegts}\n\n"
    segments: list[WebVttSegment] = []
    left = Fraction()
    next_cue = 0
    active: list[_Cue] = []
    for index, segment_duration in enumerate(segment_durations):
        right = left + segment_duration
        active = [cue for cue in active if cue.end_ms > left * 1000]
        while next_cue < len(cues) and cues[next_cue].start_ms < right * 1000:
            cue = cues[next_cue]
            if cue.end_ms > left * 1000:
                active.append(cue)
            next_cue += 1
        content = [header]
        for cue in active:
            content.append(
                f"{_format_time(cue.start_ms)} --> {_format_time(cue.end_ms)}\n{cue.text}\n\n"
            )
        segments.append(
            WebVttSegment(f"segment_{index:06d}.vtt", segment_duration, "".join(content).encode())
        )
        left = right
    return PreparedSubtitles(max(1, int(max(segment_durations) + Fraction(1, 2))), tuple(segments))
