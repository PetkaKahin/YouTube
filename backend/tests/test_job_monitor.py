"""Actual Procrastinate executions preserve every failed attempt and safe logs."""

import asyncio
import logging
from uuid import uuid4

import procrastinate
import pytest
from job_support import history, make_queue, rows
from procrastinate.job_context import JobContext
from procrastinate.jobs import Job
from procrastinate.worker import Worker
from sqlalchemy import select, text

from common.contracts.job_errors import ErrorStoreUnavailable
from common.infrastructure.job_error_model import JobErrorRow
from common.infrastructure.job_errors import DurableJobErrors
from common.infrastructure.job_monitor import (
    configure_safe_queue_logging,
    error_history_middleware,
)


@pytest.fixture(scope="module", autouse=True)
def queue_schema(isolated_account_database):
    async def setup():
        queue = make_queue()
        async with queue.open_async():
            assert await queue.connector.execute_query_one_async(
                query="SELECT to_regclass('procrastinate_jobs') AS installed"
            ) == {"installed": "procrastinate_jobs"}

    asyncio.run(setup())


@pytest.fixture(autouse=True)
def safe_queue_logs(monkeypatch, caplog):
    namespace = logging.getLogger("procrastinate")
    # Restore the library namespace after each test, preserving other suites' setup.
    monkeypatch.setattr(namespace, "handlers", [])
    monkeypatch.setattr(namespace, "propagate", True)
    configure_safe_queue_logging()
    caplog.set_level(logging.DEBUG)


async def drain(queue, name, journal):
    await queue.run_worker_async(
        queues=[name],
        concurrency=1,
        wait=False,
        listen_notify=False,
        install_signal_handlers=False,
        worker_middleware=[error_history_middleware(journal)],
    )


@pytest.mark.parametrize("synchronous", [False, True])
@pytest.mark.parametrize("eventual_success", [False, True])
def test_all_failed_attempts_survive_success_or_final_failure_and_queue_cleanup(
    tmp_path, caplog, synchronous, eventual_success
):
    async def exercise():
        async with history(tmp_path) as (_, sessions, spool, journal):
            queue = make_queue()
            name = "q_" + uuid4().hex
            calls = []
            synthetic_secret = "synthetic-password-cookie-token-signed-url"  # noqa: S105 -- test marker

            def run(payload):
                calls.append(payload)
                if not eventual_success or len(calls) < 3:
                    raise RuntimeError(payload)
                return payload  # Queue's normal success log includes return values too.

            async def async_run(payload):
                return run(payload)

            process = queue.task(
                name=name, queue=name, retry=procrastinate.RetryStrategy(max_attempts=2)
            )(run if synchronous else async_run)
            async with queue.open_async(), asyncio.timeout(20):
                job_id = await process.defer_async(payload=synthetic_secret)
                for _ in range(3):
                    await drain(queue, name, journal)
                assert len(calls) == 3
                expected = (
                    procrastinate.jobs.Status.SUCCEEDED
                    if eventual_success
                    else procrastinate.jobs.Status.FAILED
                )
                assert await queue.job_manager.get_job_status_async(job_id) is expected
                async with sessions() as session:
                    errors = list(
                        await session.scalars(
                            select(JobErrorRow)
                            .where(JobErrorRow.job_id == job_id)
                            .order_by(JobErrorRow.attempt)
                        )
                    )
                assert [error.attempt for error in errors] == (
                    [1, 2] if eventual_success else [1, 2, 3]
                )
                assert all(error.reason == "task_failed" for error in errors)
                assert all(error.trace for error in errors)
                assert not spool.events()
                assert synthetic_secret not in caplog.text
                assert all(
                    synthetic_secret not in repr(record.__dict__) for record in caplog.records
                )
                await queue.connector.execute_query_async(
                    query="DELETE FROM procrastinate_jobs WHERE id=%(id)s", id=job_id
                )
                async with sessions() as session:
                    assert len(
                        list(
                            await session.scalars(
                                select(JobErrorRow).where(JobErrorRow.job_id == job_id)
                            )
                        )
                    ) == len(errors)

    asyncio.run(exercise())


def test_buffered_error_does_not_turn_failed_task_into_success(tmp_path):
    class UnavailableStore:
        async def save(self, event):
            raise ErrorStoreUnavailable("artificial outage")

    async def exercise():
        async with history(tmp_path) as (_, sessions, spool, restored):
            journal = DurableJobErrors(spool, UnavailableStore())
            queue = make_queue()
            name = "q_" + uuid4().hex

            @queue.task(name=name, queue=name)
            async def process():
                raise ValueError("synthetic sensitive failure")

            async with queue.open_async(), asyncio.timeout(20):
                job_id = await process.defer_async()
                await drain(queue, name, journal)
                assert await queue.job_manager.get_job_status_async(job_id) is (
                    procrastinate.jobs.Status.FAILED
                )
            assert len(spool.events()) == 1
            event = spool.read(spool.events()[0])
            assert event.job_id == job_id
            assert await rows(sessions, event) == []
            assert (await restored.replay()).transferred == 1
            assert len(await rows(sessions, event)) == 1

    asyncio.run(exercise())


def test_cancelled_attempt_is_recorded_before_queue_marks_aborted(tmp_path):
    async def exercise():
        async with history(tmp_path) as (_, sessions, _, journal):
            queue = make_queue()
            name = "q_" + uuid4().hex

            @queue.task(name=name, queue=name)
            async def process():
                raise asyncio.CancelledError("synthetic cancellation token")

            async with queue.open_async(), asyncio.timeout(20):
                job_id = await process.defer_async()
                await drain(queue, name, journal)
                assert await queue.job_manager.get_job_status_async(job_id) is (
                    procrastinate.jobs.Status.ABORTED
                )
            async with sessions() as session:
                event = await session.scalar(
                    select(JobErrorRow).where(JobErrorRow.job_id == job_id)
                )
            assert event.reason == "cancelled"

    asyncio.run(exercise())


def test_cancellation_while_saving_does_not_interrupt_history_commit(tmp_path):
    async def exercise():
        async with history(tmp_path) as (engine, sessions, spool, journal):
            queue = make_queue()
            context = JobContext(
                app=queue,
                job=Job(id=2**41, queue="test", lock=None, queueing_lock=None, task_name="test"),
                start_timestamp=0,
                abort_reason=lambda: None,
            )
            worker = Worker(queue, install_signal_handlers=False)

            async def fail():
                raise RuntimeError("synthetic interrupted failure")

            async with engine.connect() as owner:
                blocker = await owner.scalar(text("SELECT pg_backend_pid()"))
                await owner.execute(
                    text("LOCK TABLE background_job_errors IN ACCESS EXCLUSIVE MODE")
                )
                running = asyncio.create_task(
                    error_history_middleware(journal)(fail, context, worker)
                )
                try:
                    async with asyncio.timeout(10):
                        async with engine.connect() as observer:
                            await observer.execution_options(isolation_level="AUTOCOMMIT")
                            while not await observer.scalar(
                                text(
                                    "SELECT count(*) FROM pg_stat_activity "
                                    "WHERE :pid = ANY(pg_blocking_pids(pid))"
                                ),
                                {"pid": blocker},
                            ):
                                await asyncio.sleep(0.01)
                    event = spool.read(spool.events()[0])
                    running.cancel()
                    await asyncio.sleep(0)
                    assert not running.done()
                    await owner.commit()
                    with pytest.raises(asyncio.CancelledError):
                        await running
                finally:
                    await owner.rollback()
                    if not running.done():
                        running.cancel()
                    await asyncio.gather(running, return_exceptions=True)
            assert len(await rows(sessions, event)) == 1
            assert not spool.events()

    asyncio.run(exercise())


def test_disk_failure_stops_worker_without_exposing_exception(tmp_path, monkeypatch, caplog):
    async def exercise():
        async with history(tmp_path) as (_, _, spool, journal):
            queue = make_queue()
            name = "q_" + uuid4().hex
            calls = []

            @queue.task(name=name, queue=name)
            async def process():
                calls.append(True)
                raise RuntimeError("synthetic task secret")

            def disk_full(event):
                raise OSError("synthetic filesystem secret")

            monkeypatch.setattr(spool, "append", disk_full)
            async with queue.open_async(), asyncio.timeout(20):
                first = await process.defer_async()
                second = await process.defer_async()
                await drain(queue, name, journal)
                assert calls == [True]
                assert await queue.job_manager.get_job_status_async(first) is (
                    procrastinate.jobs.Status.FAILED
                )
                assert await queue.job_manager.get_job_status_async(second) is (
                    procrastinate.jobs.Status.TODO
                )
            assert "synthetic task secret" not in caplog.text
            assert "synthetic filesystem secret" not in caplog.text

    asyncio.run(exercise())


def test_queue_log_filter_discards_raw_extras_exceptions_and_results(caplog):
    secret = "synthetic-raw-credential"  # noqa: S105 -- synthetic leak marker
    try:
        raise RuntimeError(secret)
    except RuntimeError:
        logging.getLogger("procrastinate.worker.test").error(
            "payload=%s", secret, exc_info=True, extra={"job": {"secret": secret}}
        )
    assert "Background queue event" in caplog.text
    assert secret not in caplog.text
    assert all(secret not in repr(record.__dict__) for record in caplog.records)
