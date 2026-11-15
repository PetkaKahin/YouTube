"""Compose application resources and explicitly registered module providers."""

from collections.abc import AsyncGenerator

from dishka import (
    AsyncContainer,
    Provider,
    Scope,
    from_context,
    make_async_container,
    provide,  # pyright: ignore[reportUnknownVariableType] -- upstream overload uses untyped classmethod parameters
)
from pwdlib import PasswordHash
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.module_providers import module_providers
from app.resources import Resources, open_resources
from app.settings import Settings
from common.contracts.authorization import Authorization
from common.contracts.storage import ObjectBucket, ObjectStorageClient
from common.contracts.transactions import Transactions
from common.infrastructure.authorization import create_authorization
from common.infrastructure.transactions import SqlTransactions


class ApplicationProvider(Provider):
    def __init__(self, *, require_cache: bool = True) -> None:
        super().__init__()
        self.require_cache = require_cache

    transactions = provide(SqlTransactions, provides=Transactions, scope=Scope.REQUEST)
    configuration = from_context(provides=Settings, scope=Scope.APP)
    cache_client = from_context(provides=Redis, scope=Scope.APP)

    @provide(scope=Scope.APP)
    async def resources(self, settings: Settings, cache: Redis) -> AsyncGenerator[Resources]:
        async with open_resources(settings, cache, require_cache=self.require_cache) as resources:
            yield resources

    @provide(scope=Scope.APP)
    def engine(self, resources: Resources) -> AsyncEngine:
        return resources.engine

    @provide(scope=Scope.APP)
    def object_storage(self, resources: Resources) -> ObjectStorageClient:
        return resources.objects

    @provide(scope=Scope.APP)
    def object_bucket(self, resources: Resources) -> ObjectBucket:
        return ObjectBucket(resources.bucket)

    @provide(scope=Scope.APP)
    def authorization(self) -> Authorization:
        return create_authorization()

    @provide(scope=Scope.APP)
    def password_hash(self) -> PasswordHash:
        return PasswordHash.recommended()

    @provide(scope=Scope.REQUEST)
    async def database_session(self, resources: Resources) -> AsyncGenerator[AsyncSession]:
        # Close rolls back uncommitted work; use cases explicitly own commit/rollback.
        async with resources.sessions() as session:
            yield session


def create_container(
    settings: Settings, cache: Redis, *providers: Provider, require_cache: bool = True
) -> AsyncContainer:
    return make_async_container(
        ApplicationProvider(require_cache=require_cache),
        *module_providers(),
        *providers,
        context={Settings: settings, Redis: cache},
    )
