"""Actual housekeeping task, persistence, SIGKILL recovery, and retry budget."""

import asyncio
import os
import subprocess
import sys

import pytest
from sqlalchemy import event, select

from app.settings import Settings
from app.upload_worker import QUEUE, create_queue, recover
from common.infrastructure.job_error_model import JobErrorRow
from common.infrastructure.job_errors import DurableJobErrors, ErrorSpool, PostgresJobErrorStore
from common.infrastructure.job_monitor import configure_safe_queue_logging, error_history_middleware

CHILD = """
import asyncio,sys
from app.upload_worker import create_queue
from app.settings import Settings
queue=create_queue(Settings(),None)
@queue.task(name='qualification.block',queue='maintenance')
async def block():
 await queue.connector.execute_query_async(
  query="INSERT INTO worker_qualification VALUES ('effect') ON CONFLICT DO NOTHING")
 await asyncio.sleep(3600)
async def run():
 async with queue.open_async():
  await queue.run_worker_async(queues=['maintenance'],concurrency=1,listen_notify=False)
asyncio.run(run())
"""


def test_registered_cleanup_task_uses_real_queue_and_agreed_retry_policy(backend_runtime, tmp_path):
    async def exercise():
        async with backend_runtime() as (container, resources):
            configure_safe_queue_logging()
            queue = create_queue(Settings(), container)
            task = queue.tasks["video.expire_uploads"]
            assert task.retry_strategy.max_attempts == 2
            assert task.retry_strategy.wait == 30 and task.retry_strategy.linear_wait == 90
            journal = DurableJobErrors(
                ErrorSpool(tmp_path), PostgresJobErrorStore(resources.sessions)
            )
            async with queue.open_async():
                job_id = await task.defer_async(timestamp=0)
                await queue.run_worker_async(
                    queues=[QUEUE],
                    wait=False,
                    listen_notify=False,
                    install_signal_handlers=False,
                    worker_middleware=[error_history_middleware(journal)],
                )
                assert (await queue.job_manager.get_job_status_async(job_id)).value == "succeeded"

    asyncio.run(exercise())


@pytest.mark.parametrize("attempts", [0, 2])
def test_killed_maintenance_worker_records_loss_and_recovers_or_finishes_failed(
    backend_runtime, tmp_path, attempts
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            queue = create_queue(Settings(), container)
            spool = ErrorSpool(tmp_path)
            journal = DurableJobErrors(spool, PostgresJobErrorStore(resources.sessions))
            async with queue.open_async(), asyncio.timeout(25):
                await queue.connector.execute_query_async(
                    query="CREATE TABLE worker_qualification (marker text PRIMARY KEY)"
                )

                @queue.task(name="qualification.block", queue=QUEUE)
                async def block():
                    await queue.connector.execute_query_async(
                        query="INSERT INTO worker_qualification VALUES ('effect') "
                        "ON CONFLICT DO NOTHING"
                    )

                job_id = await block.defer_async()
                await queue.connector.execute_query_async(
                    query="UPDATE procrastinate_jobs SET attempts=%(attempts)s WHERE id=%(id)s",
                    attempts=attempts,
                    id=job_id,
                )
                child = subprocess.Popen(  # noqa: S603 -- fixed worker code, isolated test database
                    [sys.executable, "-c", CHILD],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=dict(os.environ),
                )
                try:
                    while True:
                        row = await queue.connector.execute_query_one_async(
                            query="SELECT count(*) AS count FROM worker_qualification"
                        )
                        if row["count"] == 1:
                            break
                        assert child.poll() is None
                        await asyncio.sleep(0.01)
                    child.kill()
                    await asyncio.to_thread(child.wait, timeout=5)
                    await queue.connector.execute_query_async(
                        query="UPDATE procrastinate_workers "
                        "SET last_heartbeat=now()-interval '61 seconds'"
                    )
                    if attempts == 0:

                        def fail_retry(
                            connection, cursor, statement, parameters, context, executemany
                        ):
                            if statement.startswith("SELECT procrastinate_retry_job_v2"):
                                raise RuntimeError("artificial failure after history commit")

                        event.listen(
                            resources.engine.sync_engine, "before_cursor_execute", fail_retry
                        )
                        try:
                            with pytest.raises(RuntimeError):
                                await recover(queue, resources, journal, spool)
                        finally:
                            event.remove(
                                resources.engine.sync_engine, "before_cursor_execute", fail_retry
                            )
                        # Library pruning clears worker_id; the same lost attempt stays one event.
                        await queue.connector.execute_query_async(
                            query="DELETE FROM procrastinate_workers WHERE id=("
                            "SELECT worker_id FROM procrastinate_jobs WHERE id=%(id)s)",
                            id=job_id,
                        )
                    await asyncio.gather(
                        recover(queue, resources, journal, spool),
                        recover(queue, resources, journal, spool),
                    )
                    async with resources.sessions() as session:
                        history = list(
                            await session.scalars(
                                select(JobErrorRow).where(JobErrorRow.job_id == job_id)
                            )
                        )
                    assert len(history) == 1 and history[0].reason == "worker_lost"
                    assert history[0].attempt == attempts + 1 and history[0].trace == []
                    if attempts == 2:
                        assert (
                            await queue.job_manager.get_job_status_async(job_id)
                        ).value == "failed"
                    else:
                        assert (
                            await queue.job_manager.get_job_status_async(job_id)
                        ).value == "todo"
                        await queue.connector.execute_query_async(
                            query="UPDATE procrastinate_jobs SET scheduled_at=now() "
                            "WHERE id=%(id)s",
                            id=job_id,
                        )
                        await queue.run_worker_async(
                            queues=[QUEUE],
                            wait=False,
                            listen_notify=False,
                            install_signal_handlers=False,
                        )
                        assert (
                            await queue.job_manager.get_job_status_async(job_id)
                        ).value == "succeeded"
                    assert (
                        await queue.connector.execute_query_one_async(
                            query="SELECT count(*) AS count FROM worker_qualification"
                        )
                    )["count"] == 1
                finally:
                    if child.poll() is None:
                        child.kill()
                    await asyncio.to_thread(child.wait, timeout=5)
                    await queue.connector.execute_query_async(
                        query="DROP TABLE worker_qualification"
                    )

    asyncio.run(exercise())
