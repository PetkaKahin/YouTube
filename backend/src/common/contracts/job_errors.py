"""Safe technical error events; arbitrary exception messages are never accepted."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4


class FailureReason(StrEnum):
    TASK_FAILED = "task_failed"
    WORKER_LOST = "worker_lost"
    CANCELLED = "cancelled"


EXCEPTION_TYPES = frozenset(
    {"Exception", "TimeoutError", "OSError", "ValueError", "RuntimeError", "CancelledError"}
)

# Only fixed server-side diagnostics may cross into the durable journal.
ERROR_CODES = frozenset(
    {
        "invalid_media",
        "invalid_media_plan",
        "encoding_failed",
        "invalid_encoded_media",
        "invalid_thumbnail",
        "thumbnail_failed",
        "image_pixel_limit",
        "animated_thumbnail",
        "unsupported_subtitle",
        "subtitle_failed",
        "invalid_subtitle_timeline",
        "inspection_failed",
        "no_video",
        "invalid_duration",
        "duration_limit",
        "source_unavailable",
        "source_size_changed",
        "source_read_incomplete",
        "local_io_unavailable",
        "ffprobe_unavailable",
        "workspace_limit",
        "disk_reserve",
        "progress_timeout",
        "attempt_timeout",
        "memory_limit",
        "invalid_workspace",
        "invalid_workspace_entry",
        "workspace_unavailable",
        "media_start_failed",
        "cpu_budget_unavailable",
        "invalid_media_limits",
        "media_exit_failed",
        "media_pipe_failed",
        "invalid_media_executable",
        "invalid_source_identity",
        "source_not_uploaded",
        "source_queue_unavailable",
    }
)


@dataclass(frozen=True)
class TraceLocation:
    file: str
    function: str
    line: int

    def __post_init__(self) -> None:
        if (
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py|code", self.file) is None
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.function) is None
            or type(self.line) is not int
            or self.line < 0
        ):
            raise ValueError("Invalid trace location")

    def payload(self) -> dict[str, str | int]:
        return {"file": self.file, "function": self.function, "line": self.line}


@dataclass(frozen=True)
class JobError:
    event_id: UUID
    job_id: int
    attempt: int
    stage: str
    occurred_at: datetime
    reason: FailureReason
    exception_type: str
    trace: tuple[TraceLocation, ...] = ()
    error_code: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.event_id) is not UUID
            or type(self.job_id) is not int
            or not 0 < self.job_id < 2**63
            or type(self.attempt) is not int
            or not 0 < self.attempt < 2**63
            or re.fullmatch(r"[a-z][a-z0-9_.]*", self.stage) is None
            or self.occurred_at.utcoffset() is None
            or type(self.reason) is not FailureReason
            or self.exception_type not in EXCEPTION_TYPES
            or type(self.trace) is not tuple
            or any(type(frame) is not TraceLocation for frame in self.trace)
            or (
                self.error_code is not None
                and (type(self.error_code) is not str or self.error_code not in ERROR_CODES)
            )
        ):
            raise ValueError("Invalid job error event")

    @classmethod
    def worker_lost(cls, job_id: int, attempt: int, *, event_id: UUID) -> JobError:
        # Recovery supplies a stable observation ID when replaying the same loss.
        return cls(
            event_id=event_id,
            job_id=job_id,
            attempt=attempt,
            stage="recovery",
            occurred_at=datetime.now(UTC),
            reason=FailureReason.WORKER_LOST,
            exception_type="Exception",
        )

    @classmethod
    def failure(
        cls,
        job_id: int,
        attempt: int,
        stage: str,
        *,
        exception_type: str,
        trace: tuple[TraceLocation, ...],
        cancelled: bool = False,
        error_code: str | None = None,
    ) -> JobError:
        return cls(
            event_id=uuid4(),
            job_id=job_id,
            attempt=attempt,
            stage=stage,
            occurred_at=datetime.now(UTC),
            reason=FailureReason.CANCELLED if cancelled else FailureReason.TASK_FAILED,
            exception_type=exception_type,
            trace=trace,
            error_code=error_code,
        )


class JobErrorStore(Protocol):
    """Commit one safe event in an independent transaction, idempotent by event_id."""

    async def save(self, event: JobError) -> None: ...


class ErrorStoreUnavailable(Exception):
    """The independent history transaction could not be confirmed."""
