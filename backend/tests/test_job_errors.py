"""Durable diagnostics: real PostgreSQL, process races and crash boundaries."""

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from job_support import history, rows
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from common.contracts.job_errors import FailureReason, JobError, TraceLocation
from common.infrastructure.job_error_model import JobErrorRow
from common.infrastructure.job_errors import (
    DurableJobErrors,
    ErrorSpool,
    PostgresJobErrorStore,
    decode,
    encode,
)
from common.infrastructure.job_monitor import safe_failure

pytestmark = pytest.mark.usefixtures("isolated_account_database")


def failure(*, job_id=1, attempt=1):
    return JobError.failure(job_id, attempt, "execute", exception_type="RuntimeError", trace=())


def test_record_commits_independently_of_failed_job_transaction(tmp_path):
    async def exercise():
        async with history(tmp_path) as (engine, sessions, spool, journal):
            event = failure(job_id=2**40)
            async with engine.connect() as connection:
                transaction = await connection.begin()
                with pytest.raises(DBAPIError):
                    await connection.execute(text("SELECT 1/0"))
                assert await journal.record(event)
                await transaction.rollback()
            saved = await rows(sessions, event)
            assert len(saved) == 1
            assert saved[0].job_id == event.job_id
            assert saved[0].occurred_at == event.occurred_at
            assert not spool.events()
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM pg_constraint "
                            "WHERE conrelid='background_job_errors'::regclass AND contype='f'"
                        )
                    )
                    == 0
                )  # Removing a queue job cannot cascade into diagnostic history.

    asyncio.run(exercise())


def test_disconnected_database_buffers_then_replays_after_restart(tmp_path):
    async def exercise():
        event = failure()
        async with history(tmp_path) as (engine, sessions, spool, _):
            async with engine.connect() as broken:
                pid = await broken.scalar(text("SELECT pg_backend_pid()"))
                await broken.commit()
                async with engine.begin() as administrator:
                    assert await administrator.scalar(
                        text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
                    )
                broken_store = PostgresJobErrorStore(async_sessionmaker(bind=broken))
                assert not await DurableJobErrors(spool, broken_store).record(event)
            assert spool.read(event.event_id) == event
            assert await rows(sessions, event) == []
        # New engine and spool instance, as after a process/container restart.
        async with history(tmp_path) as (_, sessions, spool, restarted):
            result = await restarted.replay()
            assert result.transferred == 1
            assert not result.database_unavailable
            assert len(await rows(sessions, event)) == 1
            assert not spool.events()

    asyncio.run(exercise())


def test_crash_after_commit_before_acknowledgement_does_not_duplicate(tmp_path, monkeypatch):
    async def exercise():
        async with history(tmp_path) as (_, sessions, spool, journal):
            event = failure()

            def fail_ack(_):
                raise OSError("artificial process interruption before unlink")

            with monkeypatch.context() as patch:
                patch.setattr(spool, "acknowledge", fail_ack)
                with pytest.raises(OSError):
                    await journal.record(event)
            assert spool.read(event.event_id) == event
            assert len(await rows(sessions, event)) == 1
            result = await DurableJobErrors(
                ErrorSpool(tmp_path), PostgresJobErrorStore(sessions)
            ).replay()
            assert result.transferred == 1
            assert len(await rows(sessions, event)) == 1
            assert not spool.events()

    asyncio.run(exercise())


def test_concurrent_records_and_replays_preserve_one_event(tmp_path):
    async def exercise():
        async with history(tmp_path) as (_, sessions, spool, journal):
            event = failure()
            spool.append(event)
            results = await asyncio.gather(
                journal.record(event), journal.record(event), journal.replay(), journal.replay()
            )
            assert results[0] and results[1]
            assert len(await rows(sessions, event)) == 1
            assert not spool.events()

    asyncio.run(exercise())


def test_concurrent_replayers_wait_for_real_postgresql_unique_lock(tmp_path):
    async def exercise():
        async with history(tmp_path) as (engine, sessions, spool, journal):
            event = failure()
            spool.append(event)
            async with sessions() as owner:
                await owner.begin()
                blocker = await owner.scalar(text("SELECT pg_backend_pid()"))
                await owner.execute(
                    insert(JobErrorRow).values(
                        event_id=event.event_id,
                        job_id=event.job_id,
                        attempt=event.attempt,
                        stage=event.stage,
                        occurred_at=event.occurred_at,
                        reason=event.reason.value,
                        exception_type=event.exception_type,
                        trace=[],
                    )
                )
                first = asyncio.create_task(journal.replay())
                second = asyncio.create_task(journal.replay())
                try:
                    async with asyncio.timeout(10):
                        async with engine.connect() as observer:
                            await observer.execution_options(isolation_level="AUTOCOMMIT")
                            while True:
                                waiting = await observer.scalar(
                                    text(
                                        "SELECT count(*) FROM pg_stat_activity "
                                        "WHERE :pid = ANY(pg_blocking_pids(pid))"
                                    ),
                                    {"pid": blocker},
                                )
                                if waiting == 2:
                                    break
                                await asyncio.sleep(0.01)
                    assert not first.done() and not second.done()
                    await owner.commit()
                    results = await asyncio.gather(first, second)
                    assert [result.transferred for result in results] == [1, 1]
                finally:
                    await owner.rollback()
                    for task in (first, second):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(first, second, return_exceptions=True)
            assert len(await rows(sessions, event)) == 1
            assert not spool.events()

    asyncio.run(exercise())


CHILD_SPOOL = """
import os, sys
from pathlib import Path
from common.infrastructure.job_errors import ErrorSpool, decode
spool = ErrorSpool(Path(sys.argv[1]))
spool.append(decode(sys.stdin.buffer.read()))
os._exit(0)
"""


def test_spool_survives_process_exit_and_parallel_writers(tmp_path):
    event = failure()
    children = [
        subprocess.Popen(  # noqa: S603 -- fixed child program and test-owned directory
            [sys.executable, "-c", CHILD_SPOOL, str(tmp_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(3)
    ]
    try:
        for child in children:
            _, stderr = child.communicate(encode(event), timeout=10)
            assert child.returncode == 0, stderr.decode()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
    assert ErrorSpool(tmp_path).read(event.event_id) == event
    assert (tmp_path / f"{event.event_id}.json").stat().st_mode & 0o777 == 0o600


def test_fsync_failure_never_publishes_partial_event(tmp_path, monkeypatch):
    spool = ErrorSpool(tmp_path)
    event = failure()

    def disk_failure(_):
        raise OSError("artificial disk full")

    monkeypatch.setattr(os, "fsync", disk_failure)
    with pytest.raises(OSError):
        spool.append(event)
    assert not spool.events()
    assert list(tmp_path.iterdir()) == []


def test_corruption_symlinks_and_mismatched_ids_are_retained_without_blocking(tmp_path):
    async def exercise():
        async with history(tmp_path / "spool") as (_, sessions, spool, journal):
            valid = failure()
            spool.append(valid)
            bad = tmp_path / "spool" / f"{uuid4()}.json"
            bad.write_text("{incomplete")
            mismatch = tmp_path / "spool" / f"{uuid4()}.json"
            mismatch.write_bytes(encode(failure()))
            outside = tmp_path / "untouched.json"
            outside.write_bytes(encode(failure()))
            link = tmp_path / "spool" / f"{uuid4()}.json"
            link.symlink_to(outside)
            (tmp_path / "spool" / "not-an-event.json").write_text("do not remove")
            result = await journal.replay()
            assert result.transferred == 1
            assert result.invalid == 3
            assert len(await rows(sessions, valid)) == 1
            assert bad.exists() and mismatch.exists() and link.is_symlink()
            assert outside.read_bytes() == encode(decode(outside.read_bytes()))

    asyncio.run(exercise())


def test_diagnostic_data_excludes_exception_text_chain_notes_and_locals():
    class HostileError(RuntimeError):
        def __str__(self):
            raise AssertionError("Exception text must not be inspected")

    secret = "synthetic-token-password-cookie-signed-url"  # noqa: S105 -- synthetic leak marker
    try:
        try:
            raise ValueError(secret)
        except ValueError as cause:
            error = HostileError(secret)
            error.add_note(secret)
            raise error from cause
    except HostileError as error:
        event = safe_failure(1, 1, "execute", error)
    serialized = encode(event)
    assert secret.encode() not in serialized
    assert b"HostileError" not in serialized
    assert b"RuntimeError" in serialized
    assert event.trace and decode(serialized) == event
    assert all(Path(frame.file).name == frame.file for frame in event.trace)


@pytest.mark.parametrize(
    "changes",
    [
        {"job_id": True},
        {"job_id": 0},
        {"job_id": 2**63},
        {"attempt": -1},
        {"stage": "x'); DROP TABLE user_accounts;--"},
        {"stage": "../secrets"},
        {"occurred_at": datetime.now()},
        {"exception_type": "synthetic-secret"},
    ],
)
def test_invalid_events_never_enter_spool(changes, tmp_path):
    with pytest.raises(ValueError):
        ErrorSpool(tmp_path).append(replace(failure(), **changes))
    assert not list(tmp_path.iterdir())


def test_forged_spool_fields_are_rejected():
    payload = json.loads(encode(failure()))
    payload["message"] = "synthetic-secret"
    with pytest.raises(ValueError):
        decode(json.dumps(payload).encode())
    with pytest.raises(ValueError):
        TraceLocation("../secret.py", "run", 1)


def test_worker_loss_is_observation_without_fabricated_traceback(tmp_path):
    async def exercise():
        async with history(tmp_path) as (_, sessions, _, journal):
            event = JobError.worker_lost(1, 1, event_id=uuid4())
            assert event.reason is FailureReason.WORKER_LOST
            assert event.trace == ()
            assert event.occurred_at.tzinfo is UTC
            assert await journal.record(event)
            assert (await rows(sessions, event))[0].trace == []

    asyncio.run(exercise())
