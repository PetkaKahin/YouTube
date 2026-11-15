"""Real workers must not acknowledge a superseded attempt after recovery."""

import asyncio
from uuid import uuid4

import procrastinate
import pytest
from sqlalchemy import text

from app.media_worker import create_queue as media_queue
from app.settings import Settings
from app.upload_worker import create_queue as maintenance_queue


@pytest.mark.parametrize("factory", [media_queue, maintenance_queue])
@pytest.mark.parametrize("outcome", ["success", "retry", "failure"])
@pytest.mark.parametrize("recovered", [None, "todo", "doing"])
def test_worker_acknowledges_only_its_current_generation(
    backend_runtime, factory, outcome, recovered
):
    async def exercise():
        async with backend_runtime() as (container, resources):
            queue = factory(Settings(), container)
            started, release = asyncio.Event(), asyncio.Event()
            name = "gen_" + uuid4().hex[:12]

            @queue.task(
                name=name,
                queue=name,
                retry=procrastinate.RetryStrategy(
                    max_attempts=2, wait=3600, retry_exceptions=(OSError,)
                ),
            )
            async def task():
                started.set()
                await release.wait()
                if outcome == "retry":
                    raise OSError("synthetic temporary error")
                if outcome == "failure":
                    raise ValueError("synthetic permanent error")

            async def stored(job_id):
                async with resources.sessions() as session:
                    return (
                        await session.execute(
                            text(
                                "SELECT status::text,attempts,worker_id,scheduled_at "
                                "FROM procrastinate_jobs WHERE id=:id"
                            ),
                            {"id": job_id},
                        )
                    ).one_or_none()

            async with queue.open_async():
                job_id = await task.defer_async()
                worker = asyncio.create_task(
                    queue.run_worker_async(
                        queues=[name],
                        concurrency=1,
                        wait=False,
                        listen_notify=False,
                        install_signal_handlers=False,
                        # Even deletion of a successful old job must be fenced.
                        delete_jobs="always" if recovered else "never",
                    )
                )
                try:
                    await asyncio.wait_for(started.wait(), timeout=10)
                    if recovered:
                        async with resources.sessions.begin() as session:
                            # The same transition used by lost-worker recovery.
                            await session.execute(
                                text(
                                    "SELECT procrastinate_retry_job_v2(:id, "
                                    "now()+interval '1 hour', NULL, NULL, NULL)"
                                ),
                                {"id": job_id},
                            )
                            if recovered == "doing":
                                replacement = await session.scalar(
                                    text(
                                        "INSERT INTO procrastinate_workers(last_heartbeat) "
                                        "VALUES(now()) RETURNING id"
                                    )
                                )
                                await session.execute(
                                    text(
                                        "UPDATE procrastinate_jobs SET status='doing', "
                                        "worker_id=:worker WHERE id=:id"
                                    ),
                                    {"id": job_id, "worker": replacement},
                                )
                            else:
                                await session.execute(
                                    text(
                                        "UPDATE procrastinate_jobs SET worker_id=NULL WHERE id=:id"
                                    ),
                                    {"id": job_id},
                                )
                        before = await stored(job_id)
                    release.set()
                    await asyncio.wait_for(worker, timeout=10)
                    after = await stored(job_id)
                    if recovered:
                        assert after == before
                        assert after.status == recovered and after.attempts == 1
                    else:
                        expected = {"success": "succeeded", "retry": "todo", "failure": "failed"}
                        assert after.status == expected[outcome]
                        assert after.attempts == 1
                finally:
                    release.set()
                    if not worker.done():
                        worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(exercise())
