"""One media slot; lightweight recovery belongs to upload-maintenance."""

import asyncio
from pathlib import Path

import procrastinate
from dishka import AsyncContainer
from procrastinate.job_context import JobContext
from sqlalchemy.exc import SQLAlchemyError

from app.di import create_container
from app.resources import Resources, create_cache
from app.secrets import read_secret
from app.settings import Settings
from common.infrastructure.job_errors import DurableJobErrors, ErrorSpool, PostgresJobErrorStore
from common.infrastructure.job_manager import GenerationJobManager
from common.infrastructure.job_monitor import configure_safe_queue_logging, error_history_middleware
from video.public import (
    ENCODE_TASK,
    INSPECTION_TASK,
    MEDIA_QUEUE,
    PLAN_TASK,
    PREPARE_THUMBNAIL_TASK,
    EncodeTrack,
    InspectSource,
    PlanMedia,
    PrepareThumbnail,
    TemporarySourceError,
)


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

    queue.job_manager = GenerationJobManager(queue.connector)

    @queue.task(
        name=INSPECTION_TASK,
        queue=MEDIA_QUEUE,
        pass_context=True,
        retry=procrastinate.RetryStrategy(
            max_attempts=2,
            wait=30,
            linear_wait=90,
            retry_exceptions=(TemporarySourceError, SQLAlchemyError, OSError),
        ),
    )
    async def inspect(context: JobContext, upload_id: int) -> None:
        if context.job.id is None:
            raise RuntimeError("Persisted job identity required")
        async with container() as operation:
            await (await operation.get(InspectSource))(
                upload_id,
                context.job.id,
                attempt=context.job.attempts,
                last_attempt=context.job.attempts >= 2,
            )

    retry = procrastinate.RetryStrategy(
        max_attempts=2,
        wait=30,
        linear_wait=90,
        retry_exceptions=(TemporarySourceError, SQLAlchemyError, OSError),
    )

    @queue.task(name=PLAN_TASK, queue=MEDIA_QUEUE, pass_context=True, retry=retry)
    async def plan(context: JobContext, upload_id: int) -> None:
        if context.job.id is None:
            raise RuntimeError("Persisted job identity required")
        async with container() as operation:
            await (await operation.get(PlanMedia))(
                upload_id, context.job.id, attempt=context.job.attempts
            )

    @queue.task(name=ENCODE_TASK, queue=MEDIA_QUEUE, pass_context=True, retry=retry)
    async def encode(context: JobContext, encoded_id: int) -> None:
        if context.job.id is None:
            raise RuntimeError("Persisted job identity required")
        async with container() as operation:
            await (await operation.get(EncodeTrack))(
                encoded_id,
                context.job.id,
                attempt=context.job.attempts,
                last_attempt=context.job.attempts >= 2,
            )

    @queue.task(name=PREPARE_THUMBNAIL_TASK, queue=MEDIA_QUEUE, pass_context=True, retry=retry)
    async def prepare_thumbnail(context: JobContext, upload_id: int) -> None:
        if context.job.id is None:
            raise RuntimeError("Persisted job identity required")
        async with container() as operation:
            await (await operation.get(PrepareThumbnail))(
                upload_id,
                context.job.id,
                attempt=context.job.attempts,
                last_attempt=context.job.attempts >= 2,
            )

    return queue


async def main() -> None:
    configure_safe_queue_logging()
    settings = Settings()
    container = create_container(settings, create_cache(settings, ""), require_cache=False)
    try:
        resources = await container.get(Resources)
        journal = DurableJobErrors(
            ErrorSpool(Path(settings.job_error_spool_path)),
            PostgresJobErrorStore(resources.sessions),
        )
        queue = create_queue(settings, container)
        async with queue.open_async():
            await queue.run_worker_async(
                queues=[MEDIA_QUEUE],
                concurrency=1,
                update_heartbeat_interval=10,
                stalled_worker_timeout=60,
                worker_middleware=[error_history_middleware(journal)],
            )
    finally:
        await container.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        raise SystemExit("Media worker is unavailable") from None
