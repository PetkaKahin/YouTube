"""Shared real-database fixtures for application and in-module business tests."""

import asyncio
import subprocess
import sys
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql

from app.di import create_container
from app.media_worker import create_queue as create_media_queue
from app.resources import Resources, create_cache, database_url
from app.secrets import read_secret
from app.settings import Settings
from app.upload_worker import create_queue as create_upload_queue
from app.upload_worker import enqueue_original_cleanup
from app.upload_worker import recover as recover_queue
from video.public import MEDIA_QUEUE


@pytest.fixture(scope="module")
def source_queue():
    return lambda container: create_media_queue(Settings(), container)


@pytest.fixture
def original_cleanup_queue():
    return lambda container: create_upload_queue(Settings(), container)


@pytest.fixture
def enqueue_originals():
    return enqueue_original_cleanup


@pytest.fixture
def recover_media():
    async def recover(queue, resources, journal, spool):
        await recover_queue(queue, resources, journal, spool, queue_name=MEDIA_QUEUE)

    return recover


@pytest.fixture(scope="module")
def isolated_account_database():
    """Opt-in disposable database, with every application migration applied."""
    database = "accounts_test_" + uuid4().hex
    admin_url = database_url(Settings(), read_secret("POSTGRES_PASSWORD")).set(
        drivername="postgresql"
    )

    async def manage(create):
        async with await AsyncConnection.connect(
            admin_url.render_as_string(hide_password=False), autocommit=True
        ) as connection:
            command = "CREATE DATABASE {}" if create else "DROP DATABASE {} WITH (FORCE)"
            await connection.execute(sql.SQL(command).format(sql.Identifier(database)))

    asyncio.run(manage(True))
    try:
        with pytest.MonkeyPatch.context() as configuration:
            configuration.setenv("POSTGRES_DB", database)
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
            yield
    finally:
        asyncio.run(manage(False))


@pytest.fixture(scope="module")
def backend_runtime(isolated_account_database):
    @asynccontextmanager
    async def runtime():
        settings = Settings(cookie_secure=False)
        cache = create_cache(settings, read_secret("VALKEY_PASSWORD"))
        container = create_container(settings, cache)
        try:
            yield container, await container.get(Resources)
        finally:
            await container.close()

    return runtime
