"""Playback completeness is independent of publication and auxiliary work."""

from dataclasses import dataclass

from video.domain.publications import ProcessingState


@dataclass(frozen=True)
class QualityStatus:
    quality: int
    range: str
    status: str
    error_code: str | None = None


@dataclass(frozen=True)
class AuxiliaryStatus:
    status: str
    error_code: str | None = None


@dataclass(frozen=True)
class SubtitleStatus:
    stream_index: int
    quality: int
    range: str
    status: str
    error_code: str | None = None


@dataclass(frozen=True)
class ProcessingStatus:
    upload: str
    processing: ProcessingState
    error_code: str | None = None
    qualities: tuple[QualityStatus, ...] = ()
    thumbnail: AuxiliaryStatus = AuxiliaryStatus("pending")
    subtitles: tuple[SubtitleStatus, ...] = ()

    @property
    def available_qualities(self) -> tuple[QualityStatus, ...]:
        return tuple(item for item in self.qualities if item.status == "ready")

    @property
    def hd(self) -> bool:
        return any(item.quality >= 720 for item in self.available_qualities)

    @property
    def uhd(self) -> bool:
        return any(item.quality == 2160 for item in self.available_qualities)

    @property
    def hdr(self) -> bool:
        return any(item.range == "pq" for item in self.available_qualities)

    @property
    def uhd_hdr(self) -> bool:
        return any(item.quality == 2160 and item.range == "pq" for item in self.available_qualities)


def overall(qualities: tuple[QualityStatus, ...], *, active: bool) -> ProcessingState:
    if active or any(item.status in {"pending", "processing"} for item in qualities):
        return ProcessingState.PROCESSING
    ready = sum(item.status == "ready" for item in qualities)
    if not ready:
        return ProcessingState.FAILED
    return ProcessingState.READY if ready == len(qualities) else ProcessingState.PARTIAL
