"""Deterministic HTTP races against real Valkey, across independent middleware instances."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial
from http.cookies import SimpleCookie
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.exceptions import HTTPException
from starsessions import SessionAutoloadMiddleware

from app.resources import create_cache
from app.secrets import read_secret
from app.settings import Settings
from common.http.errors import http_error
from common.http.middleware import RequestLifecycleMiddleware
from common.http.sessions import GuardedSessionMiddleware, end_session, rotate_session
from common.infrastructure.session_store import GuardedRedisStore


@asynccontextmanager
async def environment(*, lifetime=60):
    prefix = "_session_tests." + uuid4().hex + "."
    cache = create_cache(Settings(), read_secret("VALKEY_PASSWORD"))
    try:
        yield cache, prefix, lifetime
    finally:
        keys = [key async for key in cache.scan_iter(match=prefix + "*")]
        if keys:
            await cache.delete(*keys)
        await cache.aclose()


def application(cache, prefix, lifetime, entered=None, release=None):
    app = FastAPI()
    app.add_exception_handler(HTTPException, http_error)

    @app.get("/{operation}")
    async def operate(operation: str, request: Request):
        if operation == "hold-created":
            await rotate_session(request)
            request.session["user_id"] = "test-user"
            entered.set()
            await release.wait()
        else:
            request.session.get("user_id")
            if operation.startswith("hold-"):
                entered.set()
                await release.wait()
                operation = operation.removeprefix("hold-")
            if operation == "start":
                await rotate_session(request)
                request.session["user_id"] = "test-user"
            elif operation == "rotate":
                await rotate_session(request)
            elif operation == "end":
                await end_session(request)
            elif operation == "write":
                request.session["probe"] = "updated"
            elif operation == "failed-login":
                await rotate_session(request)
                request.session["user_id"] = "must-not-be-saved"
                return JSONResponse({"failed": True}, status_code=400)
            elif operation == "stream":

                async def chunks():
                    yield b"first"
                    entered.set()
                    await release.wait()
                    yield b"second"

                return StreamingResponse(chunks())
        response = JSONResponse({"user_id": request.session.get("user_id")})
        response.set_cookie("other", "preserved")
        return response

    app.add_middleware(SessionAutoloadMiddleware)
    app.add_middleware(
        GuardedSessionMiddleware,
        store_factory=partial(GuardedRedisStore, cache, prefix=prefix, lifetime=lifetime),
        lifetime=timedelta(seconds=lifetime),
        cookie_https_only=False,
    )
    app.add_middleware(RequestLifecycleMiddleware)
    return app


async def request(app, operation, session_id=None):
    messages = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/" + operation,
        "raw_path": ("/" + operation).encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"cookie", f"session={session_id}".encode())] if session_id else [],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 50000),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    return messages


def session_cookie(messages):
    for name, value in messages[0].get("headers", []):
        if name.lower() == b"set-cookie":
            cookies = SimpleCookie()
            cookies.load(value.decode())
            if "session" in cookies:
                return cookies["session"]
    return None


def body(messages):
    return json.loads(b"".join(item.get("body", b"") for item in messages[1:]))


@pytest.mark.parametrize("terminal", ["end", "rotate"])
@pytest.mark.parametrize("held_operation", ["read", "write"])
def test_late_response_cannot_restore_session_or_cookie(terminal, held_operation):
    async def exercise():
        async with environment() as (cache, prefix, lifetime):
            entered, release = asyncio.Event(), asyncio.Event()
            slow = application(cache, prefix, lifetime, entered, release)
            other = application(cache, prefix, lifetime)
            old = session_cookie(await request(slow, "start")).value
            held = asyncio.create_task(request(slow, "hold-" + held_operation, old))
            await asyncio.wait_for(entered.wait(), timeout=5)
            try:
                winner = await request(other, terminal, old)
            finally:
                release.set()
            late = await held
            assert late[0]["status"] == 200
            assert session_cookie(late) is None
            assert any(value.startswith(b"other=") for _, value in late[0]["headers"])
            assert await cache.exists(prefix + old) == 0
            assert body(await request(other, "read", old))["user_id"] is None
            cookie = session_cookie(winner)
            if terminal == "rotate":
                assert cookie.value != old
                assert body(await request(other, "read", cookie.value))["user_id"] == "test-user"
            else:
                assert cookie["expires"] == "Thu, 01 Jan 1970 00:00:00 GMT"

    asyncio.run(exercise())


@pytest.mark.parametrize("held_operation,status", [("rotate", 409), ("end", 200)])
def test_only_first_rotation_can_retire_old_id(held_operation, status):
    async def exercise():
        async with environment() as (cache, prefix, lifetime):
            entered, release = asyncio.Event(), asyncio.Event()
            app = application(cache, prefix, lifetime, entered, release)
            other = application(cache, prefix, lifetime)
            old = session_cookie(await request(app, "start")).value
            held = asyncio.create_task(request(app, "hold-" + held_operation, old))
            await asyncio.wait_for(entered.wait(), timeout=5)
            try:
                winner = await request(other, "rotate", old)
            finally:
                release.set()
            late = await held
            assert late[0]["status"] == status
            assert session_cookie(late) is None
            fresh = session_cookie(winner).value
            assert body(await request(other, "read", fresh))["user_id"] == "test-user"
            assert [key async for key in cache.scan_iter(match=prefix + "*")] == [
                (prefix + fresh).encode()
            ]

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["expiry", "lost"])
@pytest.mark.parametrize("held_operation,status", [("write", 200), ("rotate", 409)])
def test_expired_or_lost_snapshot_cannot_be_saved_or_rotated(mode, held_operation, status):
    async def exercise():
        async with environment(lifetime=1) as (cache, prefix, lifetime):
            entered, release = asyncio.Event(), asyncio.Event()
            app = application(cache, prefix, lifetime, entered, release)
            old = session_cookie(await request(app, "start")).value
            held = asyncio.create_task(request(app, "hold-" + held_operation, old))
            await asyncio.wait_for(entered.wait(), timeout=5)
            if mode == "expiry":
                await asyncio.sleep(1.2)
            else:
                await cache.delete(prefix + old)
            release.set()
            late = await held
            assert late[0]["status"] == status
            assert session_cookie(late) is None
            assert not [key async for key in cache.scan_iter(match=prefix + "*")]

    asyncio.run(exercise())


def test_loss_between_new_id_reservation_and_response_does_not_recreate_session():
    async def exercise():
        async with environment() as (cache, prefix, lifetime):
            entered, release = asyncio.Event(), asyncio.Event()
            app = application(cache, prefix, lifetime, entered, release)
            held = asyncio.create_task(request(app, "hold-created"))
            await asyncio.wait_for(entered.wait(), timeout=5)
            keys = [key async for key in cache.scan_iter(match=prefix + "*")]
            assert len(keys) == 1
            await cache.delete(*keys)
            release.set()
            late = await held
            assert session_cookie(late) is None
            assert not [key async for key in cache.scan_iter(match=prefix + "*")]

    asyncio.run(exercise())


def test_untrusted_unknown_cookie_cannot_become_session_id():
    async def exercise():
        async with environment() as (cache, prefix, lifetime):
            app = application(cache, prefix, lifetime)
            reply = await request(app, "write", "attacker-controlled-id")
            assert session_cookie(reply) is None
            assert await cache.exists(prefix + "attacker-controlled-id") == 0
            reply = await request(app, "start", "attacker-controlled-id")
            assert session_cookie(reply).value != "attacker-controlled-id"
            assert body(reply)["user_id"] == "test-user"

    asyncio.run(exercise())


def test_failed_response_does_not_save_new_identity_or_cookie():
    async def exercise():
        async with environment() as (cache, prefix, lifetime):
            app = application(cache, prefix, lifetime)
            reply = await request(app, "failed-login")
            assert reply[0]["status"] == 400
            assert session_cookie(reply) is None
            for key in [key async for key in cache.scan_iter(match=prefix + "*")]:
                assert await cache.get(key) == b""  # Only an anonymous expiring reservation.

    asyncio.run(exercise())


def test_cancelled_request_does_not_persist_or_hold_session_lock():
    async def exercise():
        async with environment() as (cache, prefix, lifetime):
            entered, release = asyncio.Event(), asyncio.Event()
            app = application(cache, prefix, lifetime, entered, release)
            old = session_cookie(await request(app, "start")).value
            before = await cache.get(prefix + old)
            held = asyncio.create_task(request(app, "hold-write", old))
            await asyncio.wait_for(entered.wait(), timeout=5)
            held.cancel()
            with pytest.raises(asyncio.CancelledError):
                await held
            assert await cache.get(prefix + old) == before
            assert body(await request(app, "read", old))["user_id"] == "test-user"

    asyncio.run(exercise())


def test_streaming_body_does_not_delay_logout_or_save_after_headers():
    async def exercise():
        async with environment() as (cache, prefix, lifetime):
            entered, release = asyncio.Event(), asyncio.Event()
            app = application(cache, prefix, lifetime, entered, release)
            old = session_cookie(await request(app, "start")).value
            stream = asyncio.create_task(request(app, "stream", old))
            await asyncio.wait_for(entered.wait(), timeout=5)
            other = application(cache, prefix, lifetime)
            try:
                reply = await request(other, "end", old)
                assert reply[0]["status"] == 200
            finally:
                release.set()
            messages = await stream
            assert b"".join(item.get("body", b"") for item in messages[1:]) == b"firstsecond"
            assert await cache.exists(prefix + old) == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("method,operation", [("get", "read"), ("set", "write"), ("delete", "end")])
def test_store_failures_are_safe_and_do_not_issue_cookie(monkeypatch, method, operation):
    async def exercise():
        async with environment() as (cache, prefix, lifetime):
            app = application(cache, prefix, lifetime)
            old = session_cookie(await request(app, "start")).value

            async def fail(*args, **kwargs):
                raise RedisConnectionError("sensitive-driver-details")

            monkeypatch.setattr(cache, method, fail)
            try:
                reply = await request(app, operation, old)
                assert reply[0]["status"] == 500
                assert session_cookie(reply) is None
                assert "sensitive-driver-details" not in json.dumps(body(reply))
            finally:
                monkeypatch.undo()

    asyncio.run(exercise())
