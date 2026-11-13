"""Shared technical test environments; each caller supplies its isolated database/spool."""

from contextlib import asynccontextmanager

import procrastinate
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.resources import database_url
from app.secrets import read_secret
from app.settings import Settings
from common.infrastructure.job_error_model import JobErrorRow
from common.infrastructure.job_errors import DurableJobErrors, ErrorSpool, PostgresJobErrorStore


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


@asynccontextmanager
async def history(path):
    engine = create_async_engine(
        database_url(Settings(), read_secret("POSTGRES_PASSWORD")), hide_parameters=True
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    spool = ErrorSpool(path)
    try:
        yield engine, sessions, spool, DurableJobErrors(spool, PostgresJobErrorStore(sessions))
    finally:
        await engine.dispose()


async def rows(sessions, event):
    async with sessions() as session:
        return list(
            await session.scalars(select(JobErrorRow).where(JobErrorRow.event_id == event.event_id))
        )
