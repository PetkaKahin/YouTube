"""Procrastinate middleware: durable safe diagnostics before the queue sees failure."""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import PurePath
from typing import Any

from procrastinate.job_context import JobContext
from procrastinate.worker import Worker

from common.contracts.job_errors import EXCEPTION_TYPES, JobError, TraceLocation
from common.infrastructure.job_errors import DurableJobErrors


def safe_failure(job_id: int, attempt: int, stage: str, error: BaseException) -> JobError:
    # Inspect code locations only. Never format traceback/source lines or call str(error).
    trace: list[TraceLocation] = []
    current = error.__traceback__
    while current is not None:
        code = current.tb_frame.f_code
        filename = PurePath(code.co_filename).name
        function = code.co_name
        trace.append(
            TraceLocation(
                file=filename if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", filename) else "code",
                function=function if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function) else "code",
                line=current.tb_lineno,
            )
        )
        current = current.tb_next
    category = next(
        (base.__name__ for base in type(error).__mro__ if base.__name__ in EXCEPTION_TYPES),
        "Exception",
    )
    return JobError.failure(
        job_id,
        attempt,
        stage,
        exception_type=category,
        trace=tuple(trace),
        cancelled=isinstance(error, asyncio.CancelledError),
    )


class JobHistoryUnavailable(Exception):
    """Stop consuming work when not even the diagnostic buffer can accept it."""


def error_history_middleware(
    journal: DurableJobErrors,
) -> Callable[[Callable[[], Awaitable[Any]], JobContext, Worker], Awaitable[Any]]:
    async def monitor(
        call_next: Callable[[], Awaitable[Any]], context: JobContext, worker: Worker
    ) -> Any:
        try:
            return await call_next()
        except BaseException as error:
            if context.job.id is None:
                worker.stop()
                raise JobHistoryUnavailable("Job has no persisted identity") from None
            event = safe_failure(context.job.id, context.job.attempts + 1, "execute", error)
            recording = asyncio.create_task(journal.record(event))
            try:
                try:
                    await asyncio.shield(recording)
                except asyncio.CancelledError:
                    await recording
                    raise
            except Exception:
                worker.stop()
                raise JobHistoryUnavailable("Diagnostic buffer is unavailable") from None
            raise  # Preserve retry/abort decisions; a failed task must never become success.

    return monitor


class SafeQueueLogs(logging.Handler):
    """Procrastinate logs can contain task kwargs/results and original exceptions."""

    def emit(self, record: logging.LogRecord) -> None:
        # No raw message/args/extras/exception/stack crosses into application handlers.
        clean = logging.LogRecord(
            "background_jobs", record.levelno, __file__, 0, "Background queue event", (), None
        )
        # Also protect handlers attached after this sanitizer (e.g. test capture).
        record.__dict__.clear()
        record.__dict__.update(clean.__dict__)
        logging.getLogger("background_jobs").handle(record)


def configure_safe_queue_logging() -> None:
    """Call at worker startup, before processing; journal supplies detailed diagnostics."""
    queue_logger = logging.getLogger("procrastinate")
    queue_logger.handlers = [SafeQueueLogs()]
    queue_logger.propagate = False
