"""Shared real-database fixtures for application and in-module business tests."""

import asyncio
import subprocess
import sys
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql

from app.di import create_container
from app.resources import Resources, create_cache, database_url
from app.secrets import read_secret
from app.settings import Settings


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


@pytest.fixture
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
