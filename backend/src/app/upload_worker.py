"""Separate Procrastinate housekeeping worker; no FFmpeg work occupies this process."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import procrastinate
from dishka import AsyncContainer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.di import create_container
from app.resources import Resources, create_cache
from app.secrets import read_secret
from app.settings import Settings
from common.contracts.job_errors import JobError
from common.infrastructure.job_errors import DurableJobErrors, ErrorSpool, PostgresJobErrorStore
from common.infrastructure.job_monitor import configure_safe_queue_logging, error_history_middleware
from video.public import ExpireUploads, StorageUnavailable

QUEUE = "maintenance"


def create_queue(settings: Settings, container: AsyncContainer) -> procrastinate.App:
    queue = procrastinate.App(
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

    @queue.periodic(cron="* * * * *", queueing_lock="uploads.cleanup")
    @queue.task(
        name="video.expire_uploads",
        queue=QUEUE,
        retry=procrastinate.RetryStrategy(
            max_attempts=2,
            wait=30,
            linear_wait=90,
            retry_exceptions=(StorageUnavailable, SQLAlchemyError, OSError),
        ),
    )
    async def cleanup(timestamp: int) -> None:
        async with container() as operation:
            await (await operation.get(ExpireUploads))()

    return queue


async def recover(
    queue: procrastinate.App,
    resources: Resources,
    journal: DurableJobErrors,
    spool: ErrorSpool,
) -> None:
    for job in await queue.job_manager.get_stalled_jobs(queue=QUEUE, seconds_since_heartbeat=60):
        async with resources.sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT id, attempts, worker_id FROM procrastinate_jobs "
                            "WHERE id=:id AND status='doing' FOR UPDATE SKIP LOCKED"
                        ),
                        {"id": job.id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["worker_id"] != job.worker_id:
                continue
            event_id = uuid5(NAMESPACE_URL, f"youtube:lost:{row['id']}:{row['attempts']}")
            try:
                event = await asyncio.to_thread(spool.read, event_id)
            except FileNotFoundError:
                event = JobError.worker_lost(row["id"], row["attempts"] + 1, event_id=event_id)
            await journal.record(event)
            if row["attempts"] < 2:
                await session.execute(
                    text("SELECT procrastinate_retry_job_v2(:id, :at, NULL, NULL, NULL)"),
                    {
                        "id": job.id,
                        "at": datetime.now(UTC)
                        + timedelta(seconds=30 if row["attempts"] == 0 else 120),
                    },
                )
            else:
                await session.execute(
                    text("SELECT procrastinate_finish_job_v1(:id, 'failed', false)"), {"id": job.id}
                )


async def main() -> None:
    configure_safe_queue_logging()
    settings = Settings()
    container = create_container(settings, create_cache(settings, read_secret("VALKEY_PASSWORD")))
    try:
        resources = await container.get(Resources)
        spool = ErrorSpool(Path(settings.job_error_spool_path))
        journal = DurableJobErrors(spool, PostgresJobErrorStore(resources.sessions))
        queue = create_queue(settings, container)
        async with queue.open_async():

            async def maintenance() -> None:
                while True:
                    try:
                        await journal.replay()
                        await recover(queue, resources, journal, spool)
                    except Exception:
                        logging.getLogger("background_jobs").error("Queue recovery is unavailable")
                    await asyncio.sleep(15)

            recovery = asyncio.create_task(maintenance())
            try:
                await queue.run_worker_async(
                    queues=[QUEUE],
                    concurrency=1,
                    update_heartbeat_interval=10,
                    stalled_worker_timeout=60,
                    worker_middleware=[error_history_middleware(journal)],
                )
            finally:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)
    finally:
        await container.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        raise SystemExit("Upload maintenance is unavailable") from None
