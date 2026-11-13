"""Own clients for one application lifespan; never commit requests implicitly."""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field

import boto3
from anyio import to_thread
from botocore.config import Config
from redis.asyncio import Redis
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.secrets import ApplicationSecrets
from app.settings import Settings
from common.contracts.storage import ObjectStorageClient


def create_cache(settings: Settings, password: str) -> Redis:
    return Redis(
        host=settings.valkey_host,
        port=settings.valkey_port,
        password=password,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


def database_url(settings: Settings, password: str) -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


@dataclass(repr=False)
class Resources:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    cache: Redis
    objects: ObjectStorageClient
    bucket: str = field(repr=False)

    async def check(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await self.cache.ping()  # pyright: ignore[reportUnknownMemberType] -- redis kwargs lack types
        await to_thread.run_sync(lambda: self.objects.head_bucket(Bucket=self.bucket))


@asynccontextmanager
async def open_resources(settings: Settings, cache: Redis) -> AsyncGenerator[Resources]:
    secrets = ApplicationSecrets.load()
    async with AsyncExitStack() as stack:
        engine = create_async_engine(
            database_url(settings, secrets.postgres_password),
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={"connect_timeout": 5},
        )
        stack.push_async_callback(engine.dispose)
        stack.push_async_callback(cache.aclose)
        objects = boto3.client(  # pyright: ignore[reportUnknownMemberType] -- only S3 stubs installed
            "s3",
            endpoint_url=settings.rustfs_endpoint,
            aws_access_key_id=secrets.rustfs_access_key,
            aws_secret_access_key=secrets.rustfs_secret_key,
            region_name="us-east-1",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=5,
                retries={"max_attempts": 0},
            ),
        )
        stack.callback(objects.close)
        resources = Resources(
            engine=engine,
            sessions=async_sessionmaker(engine, expire_on_commit=False),
            cache=cache,
            objects=objects,
            bucket=settings.rustfs_bucket,
        )
        try:
            await resources.check()
        except Exception:
            raise RuntimeError("Application dependencies are unavailable") from None
        yield resources
