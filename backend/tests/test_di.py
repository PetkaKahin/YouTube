"""Verify Dishka against real HTTP lifecycles and database connections."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from dishka import FromDishka, Provider, Scope, make_async_container, provide
from dishka.exceptions import GraphMissingFactoryError
from dishka.integrations.fastapi import DishkaRoute, FastapiProvider, setup_dishka
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.di import create_container
from app.resources import Resources, create_cache
from app.secrets import read_secret
from app.settings import Settings
from common.contracts.authorization import Authorization
from common.contracts.storage import ObjectStorageClient


@dataclass
class Tracked:
    closed: bool = False


class TrackingProvider(Provider):
    def __init__(self):
        super().__init__()
        self.instances = []

    @provide(scope=Scope.REQUEST)
    async def tracked(self) -> AsyncGenerator[Tracked]:
        instance = Tracked()
        self.instances.append(instance)
        try:
            yield instance
        finally:
            instance.closed = True


@pytest.fixture
def di_application():
    tracking = TrackingProvider()
    container = make_async_container(tracking, FastapiProvider())
    router = APIRouter(route_class=DishkaRoute)

    @router.get("/v1/_test/di/{mode}")
    async def lifecycle(mode: str, request: Request, tracked: FromDishka[Tracked]):
        assert await request.state.dishka_container.get(Tracked) is tracked
        assert not tracked.closed
        if mode == "error":
            raise RuntimeError("sensitive-di-error")
        if mode == "stream":

            async def chunks():
                assert not tracked.closed
                yield b"first"
                await asyncio.sleep(0)
                assert not tracked.closed
                yield b"second"

            return StreamingResponse(chunks())
        return {"ok": True}

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            await container.close()

    app = FastAPI(lifespan=lifespan)
    setup_dishka(container, app)
    app.include_router(router)
    return app, tracking


@pytest.mark.parametrize("mode,status", [("ok", 200), ("error", 500), ("stream", 200)])
def test_request_scope_lives_through_response_and_closes_on_every_exit(
    di_application, mode, status
):
    app, tracking = di_application
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"/v1/_test/di/{mode}")
        assert response.status_code == status
        if mode == "stream":
            assert response.content == b"firstsecond"
        assert tracking.instances[-1].closed
        client.get("/v1/_test/di/ok")
        assert tracking.instances[-1] is not tracking.instances[-2]
        assert all(instance.closed for instance in tracking.instances)


def test_http_injection_shares_app_resources_and_rolls_back_request_session(application):
    router = APIRouter(route_class=DishkaRoute)
    sessions = []
    resources_seen = []

    @router.get("/v1/_test/database/{mode}")
    async def database(
        mode: str,
        request: Request,
        session: FromDishka[AsyncSession],
        resources: FromDishka[Resources],
        authorization: FromDishka[Authorization],
    ):
        assert await request.state.dishka_container.get(AsyncSession) is session
        assert authorization is request.app.state.authorization
        sessions.append(session)
        resources_seen.append(resources)
        await session.execute(text("SELECT pg_advisory_xact_lock(7902351)"))
        if mode == "error":
            raise RuntimeError("sensitive-session-error")
        return {"ok": True}

    application.include_router(router)
    with TestClient(application, raise_server_exceptions=False) as client:
        resources = application.state.resources
        for mode, status in (("ok", 200), ("error", 500), ("ok", 200)):
            assert client.get(f"/v1/_test/database/{mode}").status_code == status
            assert resources.engine.pool.checkedout() == 0
            assert not sessions[-1].in_transaction()
        assert len({id(session) for session in sessions}) == 3
        assert all(value is resources for value in resources_seen)

        async def check_lock_released():
            async with resources.sessions() as session:
                assert (
                    await session.execute(text("SELECT pg_try_advisory_xact_lock(7902351)"))
                ).scalar_one()

        client.portal.call(check_lock_released)
    assert application.state.resources is None
    assert resources.engine.pool.checkedout() == 0


def test_same_container_works_outside_http():
    async def exercise():
        settings = Settings(cookie_secure=False)
        cache = create_cache(settings, read_secret("VALKEY_PASSWORD"))
        container = create_container(settings, cache)
        try:
            resources = await container.get(Resources)
            assert await container.get(ObjectStorageClient) is resources.objects
            async with container() as operation:
                session = await operation.get(AsyncSession)
                assert await operation.get(AsyncSession) is session
                assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
            assert resources.engine.pool.checkedout() == 0
            async with container() as operation:
                assert await operation.get(AsyncSession) is not session
        finally:
            await container.close()
        assert resources.engine.pool.checkedout() == 0

    asyncio.run(exercise())


class Missing:
    pass


class Unresolvable:
    def __init__(self, missing: Missing):
        self.missing = missing


def test_missing_dependency_is_rejected_when_container_is_built():
    provider = Provider()
    provider.provide(Unresolvable, scope=Scope.REQUEST)
    with pytest.raises(GraphMissingFactoryError):
        make_async_container(provider)
