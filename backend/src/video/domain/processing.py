"""First source-processing stage; inspection is never playable-video readiness."""

from dataclasses import dataclass, field
from fractions import Fraction
from uuid import UUID


class SourceInspectionError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code, self.retryable = code, retryable
        self.job_error_code = code
        super().__init__(code)


class TemporarySourceError(SourceInspectionError):
    def __init__(self, code: str = "source_unavailable") -> None:
        super().__init__(code, retryable=True)


@dataclass(frozen=True)
class SourceClaim:
    upload_id: int
    video_id: int
    object_key: str = field(repr=False)
    size: int
    token: UUID


@dataclass(frozen=True)
class InspectedTrack:
    stream_index: int
    fingerprint: str
    properties: dict[str, object]
    packet_count: int
    offset: Fraction
    language: str
    disposition: dict[str, str]
    presentation_offset: Fraction | None = None
    presentation_duration: Fraction | None = None

    def layout(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "offset": [self.offset.numerator, self.offset.denominator],
            "language": self.language,
            "disposition": self.disposition,
        }


@dataclass(frozen=True)
class SourceInspection:
    codec: str
    width: int
    height: int
    duration: Fraction
    fingerprint: str | None = None
    tracks: tuple[InspectedTrack, ...] = ()

    def manifest(self) -> dict[str, object]:
        return {"version": "compressed-v1", "tracks": [track.layout() for track in self.tracks]}

    def payload(self) -> dict[str, object]:
        return {
            "version": (
                3 if all(track.presentation_offset is not None for track in self.tracks) else 2
            )
            if self.fingerprint
            else 1,
            "codec": self.codec,
            "width": self.width,
            "height": self.height,
            "duration_num": self.duration.numerator,
            "duration_den": self.duration.denominator,
            **(
                {
                    "fingerprint": self.fingerprint,
                    "tracks": [
                        {
                            "stream_index": track.stream_index,
                            "packet_count": track.packet_count,
                            "properties": track.properties,
                            "presentation_offset": (
                                [
                                    track.presentation_offset.numerator,
                                    track.presentation_offset.denominator,
                                ]
                                if track.presentation_offset is not None
                                else None
                            ),
                            "presentation_duration": (
                                [
                                    track.presentation_duration.numerator,
                                    track.presentation_duration.denominator,
                                ]
                                if track.presentation_duration is not None
                                else None
                            ),
                            **track.layout(),
                        }
                        for track in self.tracks
                    ],
                }
                if self.fingerprint
                else {}
            ),
        }
