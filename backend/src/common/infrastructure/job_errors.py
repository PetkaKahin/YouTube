"""Write-ahead diagnostic spool and independently committed PostgreSQL history."""

import asyncio
import json
import os
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common.contracts.job_errors import (
    ErrorStoreUnavailable,
    FailureReason,
    JobError,
    JobErrorStore,
    TraceLocation,
)
from common.infrastructure.job_error_model import JobErrorRow


class PostgresJobErrorStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def save(self, event: JobError) -> None:
        try:
            await self._save(event)
        except SQLAlchemyError, OSError:
            raise ErrorStoreUnavailable("History transaction was not confirmed") from None

    async def _save(self, event: JobError) -> None:
        # Never reuse the failed job's transaction: it may already be aborted.
        async with self._sessions.begin() as session:
            await session.execute(
                insert(JobErrorRow)
                .values(
                    event_id=event.event_id,
                    job_id=event.job_id,
                    attempt=event.attempt,
                    stage=event.stage,
                    occurred_at=event.occurred_at,
                    reason=event.reason.value,
                    exception_type=event.exception_type,
                    trace=[frame.payload() for frame in event.trace],
                    error_code=event.error_code,
                )
                .on_conflict_do_nothing(index_elements=[JobErrorRow.event_id])
            )


def encode(event: JobError) -> bytes:
    return json.dumps(
        {
            "version": 2,
            "event_id": str(event.event_id),
            "job_id": event.job_id,
            "attempt": event.attempt,
            "stage": event.stage,
            "occurred_at": event.occurred_at.isoformat(),
            "reason": event.reason.value,
            "exception_type": event.exception_type,
            "trace": [frame.payload() for frame in event.trace],
            "error_code": event.error_code,
        },
        ensure_ascii=True,
        sort_keys=True,
    ).encode("ascii")


def decode(data: bytes) -> JobError:
    value: object = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Invalid journal record")
    payload = cast(dict[str, object], value)
    if payload.get("version") == 1:
        if "error_code" in payload:
            raise ValueError("Invalid legacy journal record")
        payload = {**payload, "error_code": None}
    if (
        set(payload)
        != {
            "version",
            "event_id",
            "job_id",
            "attempt",
            "stage",
            "occurred_at",
            "reason",
            "exception_type",
            "trace",
            "error_code",
        }
        or type(payload["version"]) is not int
        or payload["version"] not in {1, 2}
    ):
        raise ValueError("Invalid journal record")
    for field in ("event_id", "stage", "occurred_at", "reason", "exception_type"):
        if not isinstance(payload[field], str):
            raise ValueError("Invalid journal record")
    frames = payload["trace"]
    if not isinstance(frames, list):
        raise ValueError("Invalid journal record")
    trace: list[TraceLocation] = []
    for item in cast(list[object], frames):
        if not isinstance(item, dict):
            raise ValueError("Invalid journal record")
        frame = cast(Mapping[str, object], item)
        if set(frame) != {"file", "function", "line"}:
            raise ValueError("Invalid journal record")
        if not isinstance(frame["file"], str) or not isinstance(frame["function"], str):
            raise ValueError("Invalid journal record")
        if type(frame["line"]) is not int:
            raise ValueError("Invalid journal record")
        trace.append(TraceLocation(frame["file"], frame["function"], frame["line"]))
    return JobError(
        event_id=UUID(cast(str, payload["event_id"])),
        job_id=cast(int, payload["job_id"]),
        attempt=cast(int, payload["attempt"]),
        stage=cast(str, payload["stage"]),
        occurred_at=datetime.fromisoformat(cast(str, payload["occurred_at"])),
        reason=FailureReason(payload["reason"]),
        exception_type=cast(str, payload["exception_type"]),
        trace=tuple(trace),
        error_code=cast(str | None, payload["error_code"]),
    )


class ErrorSpool:
    """Trusted private directory on a persistent volume; only safe events enter it."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        # Mount root is provisioned by Docker with the container user's ownership.
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise ValueError("Invalid journal directory")

    def _sync_directory(self) -> None:
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def append(self, event: JobError) -> None:
        # mkstemp uses O_EXCL and 0600; a private directory is never web-served.
        fd, temporary = tempfile.mkstemp(prefix=".event-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encode(event))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Publish a complete inode without replacing another writer's event.
                os.link(temporary, self.directory / f"{event.event_id}.json")
            except FileExistsError:
                # A retry with the same ID must not overwrite different diagnostic data.
                if self.read(event.event_id) != event:
                    raise ValueError("Conflicting journal event") from None
            self._sync_directory()
        finally:
            os.unlink(temporary)

    def read(self, event_id: UUID) -> JobError:
        descriptor = os.open(
            self.directory / f"{event_id}.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Invalid journal file")
            event = decode(stream.read())
        if event.event_id != event_id:
            raise ValueError("Mismatched journal event")
        return event

    def events(self) -> list[UUID]:
        result: list[UUID] = []
        for path in self.directory.glob("*.json"):
            try:
                event_id = UUID(path.stem)
            except ValueError:
                continue
            if path.name == f"{event_id}.json":
                result.append(event_id)
        return result

    def acknowledge(self, event_id: UUID) -> None:
        (self.directory / f"{event_id}.json").unlink(missing_ok=True)
        self._sync_directory()


@dataclass(frozen=True)
class ReplayResult:
    transferred: int = 0
    invalid: int = 0
    database_unavailable: bool = False


class DurableJobErrors:
    def __init__(self, spool: ErrorSpool, store: JobErrorStore) -> None:
        self._spool = spool
        self._store = store

    async def record(self, event: JobError) -> bool:
        """Return True for a DB commit, False for durable spool-only acceptance."""
        await asyncio.to_thread(self._spool.append, event)
        try:
            await self._store.save(event)
        except ErrorStoreUnavailable:
            # Do not log connection exceptions: DSNs/SQL params may contain secrets.
            return False
        await asyncio.to_thread(self._spool.acknowledge, event.event_id)
        return True

    async def replay(self) -> ReplayResult:
        transferred = invalid = 0
        for event_id in await asyncio.to_thread(self._spool.events):
            try:
                event = await asyncio.to_thread(self._spool.read, event_id)
            except FileNotFoundError:
                continue  # A concurrent replayer already acknowledged this event.
            except ValueError, TypeError, OSError, RecursionError:
                invalid += 1
                continue  # Retain the invalid file and keep draining healthy events.
            try:
                await self._store.save(event)
            except ErrorStoreUnavailable:
                return ReplayResult(transferred, invalid, database_unavailable=True)
            await asyncio.to_thread(self._spool.acknowledge, event_id)
            transferred += 1
        return ReplayResult(transferred, invalid)
