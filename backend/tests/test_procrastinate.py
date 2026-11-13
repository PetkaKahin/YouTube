"""Qualify the chosen queue on real PostgreSQL; no production worker policy here."""

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import procrastinate
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.resources import database_url
from app.secrets import read_secret
from app.settings import Settings


def make_queue():
    settings = Settings()
    return procrastinate.App(
        connector=procrastinate.PsycopgConnector(
            kwargs={
                "host": settings.postgres_host,
                "port": settings.postgres_port,
                "dbname": settings.postgres_db,
                "user": settings.postgres_user,
                "password": read_secret("POSTGRES_PASSWORD"),
                "connect_timeout": 5,
            },
            min_size=1,
            max_size=4,
        )
    )


@pytest.fixture(scope="module", autouse=True)
def queue_schema(isolated_account_database):
    async def setup():
        queue = make_queue()
        async with queue.open_async():
            # The application migration now installs the queue schema.
            assert await queue.connector.execute_query_one_async(
                query="SELECT to_regclass('procrastinate_jobs') AS installed"
            ) == {"installed": "procrastinate_jobs"}
            await queue.connector.execute_query_async(
                query="CREATE TABLE queue_qualification (marker text PRIMARY KEY)"
            )

    asyncio.run(setup())


@asynccontextmanager
async def environment():
    queue = make_queue()
    async with queue.open_async():
        async with asyncio.timeout(20):
            yield queue, "q_" + uuid4().hex


async def drain(queue, name, *, concurrency=1):
    await queue.run_worker_async(
        queues=[name],
        concurrency=concurrency,
        wait=False,
        listen_notify=False,
        install_signal_handlers=False,
    )


async def mark_once(queue, marker):
    await queue.connector.execute_query_async(
        query="INSERT INTO queue_qualification (marker) VALUES (%(marker)s) ON CONFLICT DO NOTHING",
        marker=marker,
    )


@pytest.mark.parametrize("commit", [False, True])
def test_enqueue_shares_sqlalchemy_transaction_and_commit_visibility(commit):
    async def exercise():
        async with environment() as (queue, name):
            observed = []

            @queue.task(name=name, queue=name)
            async def process(marker):
                observed.append(marker)

            engine = create_async_engine(
                database_url(Settings(), read_secret("POSTGRES_PASSWORD")), hide_parameters=True
            )
            try:
                async with engine.connect() as connection:
                    transaction = await connection.begin()
                    await connection.execute(
                        text("INSERT INTO queue_qualification (marker) VALUES (:marker)"),
                        {"marker": name},
                    )
                    raw = await connection.get_raw_connection()
                    job_id = await process.configure(connection=raw.driver_connection).defer_async(
                        marker=name
                    )
                    await drain(queue, name)
                    assert not observed  # Other connections cannot see uncommitted work.
                    if commit:
                        await transaction.commit()
                    else:
                        await transaction.rollback()
                await drain(queue, name)
                async with engine.connect() as connection:
                    assert await connection.scalar(
                        text("SELECT count(*) FROM queue_qualification WHERE marker=:marker"),
                        {"marker": name},
                    ) == int(commit)
                    assert await connection.scalar(
                        text("SELECT count(*) FROM procrastinate_jobs WHERE id=:id"), {"id": job_id}
                    ) == int(commit)
                assert observed == ([name] if commit else [])
            finally:
                await engine.dispose()

    asyncio.run(exercise())


def test_concurrent_enqueue_lock_accepts_one_job():
    async def exercise():
        async with environment() as (queue, name):
            observed = []

            @queue.task(name=name, queue=name, queueing_lock=name)
            async def process():
                observed.append("done")

            results = await asyncio.gather(
                process.defer_async(), process.defer_async(), return_exceptions=True
            )
            assert sum(type(result) is int for result in results) == 1
            assert (
                sum(
                    isinstance(result, procrastinate.exceptions.AlreadyEnqueued)
                    for result in results
                )
                == 1
            )
            await drain(queue, name, concurrency=2)
            assert observed == ["done"]

    asyncio.run(exercise())


@pytest.mark.parametrize("retry_limit,total_calls", [(1, 2), (2, 3)])
def test_retry_limit_is_enforced_and_failed_job_stays_in_postgresql(retry_limit, total_calls):
    async def exercise():
        async with environment() as (queue, name):
            observed = []

            @queue.task(
                name=name, queue=name, retry=procrastinate.RetryStrategy(max_attempts=retry_limit)
            )
            async def process():
                observed.append("attempt")
                raise RuntimeError("artificial retryable failure")

            job_id = await process.defer_async()
            await drain(queue, name)
            # A worker may finish its non-waiting cycle before the immediately retried job.
            await drain(queue, name)
            await drain(queue, name)
            # In 3.11.0 the initial execution comes before these counted retries.
            assert observed == ["attempt"] * total_calls
            assert (
                await queue.job_manager.get_job_status_async(job_id)
                is procrastinate.jobs.Status.FAILED
            )

    asyncio.run(exercise())


def test_job_survives_connector_restart_and_payload_stays_data():
    async def exercise():
        name = "q_" + uuid4().hex
        payload = "x'); DROP TABLE queue_qualification; --"
        queue = make_queue()

        @queue.task(name=name, queue=name)
        async def process(marker):
            await mark_once(queue, marker)

        async with queue.open_async():
            job_id = await process.defer_async(marker=payload)
        restarted = make_queue()

        @restarted.task(name=name, queue=name)
        async def resumed(marker):
            await mark_once(restarted, marker)

        async with restarted.open_async(), asyncio.timeout(20):
            await drain(restarted, name)
            assert (
                await restarted.job_manager.get_job_status_async(job_id)
                is procrastinate.jobs.Status.SUCCEEDED
            )
            result = await restarted.connector.execute_query_one_async(
                query="SELECT count(*) AS count FROM queue_qualification WHERE marker=%(marker)s",
                marker=payload,
            )
            assert result["count"] == 1

    asyncio.run(exercise())


KILLED_WORKER = """
import asyncio
import sys
from test_procrastinate import make_queue, mark_once
queue = make_queue()
name, marker = sys.argv[1:]
@queue.task(name=name, queue=name)
async def process(marker):
    await mark_once(queue, marker)
    await asyncio.sleep(3600)
async def main():
    async with queue.open_async():
        await queue.run_worker_async(queues=[name], concurrency=1, listen_notify=False)
asyncio.run(main())
"""


def test_killed_worker_can_be_recovered_without_duplicate_committed_effect():
    async def exercise():
        async with environment() as (queue, name):

            @queue.task(name=name, queue=name)
            async def process(marker):
                await mark_once(queue, marker)

            job_id = await process.defer_async(marker=name)
            child = subprocess.Popen(  # noqa: S603 -- fixed worker code and synthetic identifiers
                [sys.executable, "-c", KILLED_WORKER, name, name],
                env={**os.environ, "PYTHONPATH": str(Path(__file__).parent)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                while True:
                    result = await queue.connector.execute_query_one_async(
                        query="SELECT count(*) AS count FROM queue_qualification "
                        "WHERE marker=%(marker)s",
                        marker=name,
                    )
                    if result["count"] == 1:
                        break
                    if child.poll() is not None:
                        raise RuntimeError("Qualification worker exited before processing")
                    await asyncio.sleep(0.01)
                assert (
                    await queue.job_manager.get_job_status_async(job_id)
                    is procrastinate.jobs.Status.DOING
                )
                child.kill()
                await asyncio.to_thread(child.wait, timeout=5)
                stalled = list(
                    await queue.job_manager.get_stalled_jobs(queue=name, seconds_since_heartbeat=0)
                )
                assert [job.id for job in stalled] == [job_id]
                await queue.job_manager.retry_job(stalled[0])
                await drain(queue, name)
                assert (
                    await queue.job_manager.get_job_status_async(job_id)
                    is procrastinate.jobs.Status.SUCCEEDED
                )
                result = await queue.connector.execute_query_one_async(
                    query="SELECT count(*) AS count FROM queue_qualification "
                    "WHERE marker=%(marker)s",
                    marker=name,
                )
                assert result["count"] == 1
            finally:
                if child.poll() is None:
                    child.kill()
                await asyncio.to_thread(child.wait, timeout=5)
                if child.stderr is not None:
                    child.stderr.close()

    asyncio.run(exercise())
