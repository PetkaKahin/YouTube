"""Run migrations with credentials loaded only from Compose Secrets."""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.resources import database_url
from app.secrets import read_secret
from app.settings import Settings
from common.infrastructure.database import Base

target_metadata = Base.metadata


def configure(connection=None):
    context.configure(
        connection=connection,
        url=database_url(Settings(), read_secret("POSTGRES_PASSWORD")),
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_online():
    engine = create_async_engine(database_url(Settings(), read_secret("POSTGRES_PASSWORD")))
    try:
        async with engine.connect() as connection:
            await connection.run_sync(configure)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    configure()
else:
    asyncio.run(run_online())
