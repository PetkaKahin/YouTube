"""Extend starsessions without patching it or replacing its cookie implementation."""

from collections.abc import Callable
from datetime import timedelta
from functools import partial
from typing import cast

from starlette.exceptions import HTTPException
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starsessions import (
    SessionMiddleware,
    SessionStore,
    get_session_id,
    load_session,
    regenerate_session_id,
)

from common.contracts.sessions import ManagedSessionStore

STORE_SCOPE_KEY = "managed_session_store"


class GuardedSessionMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        store_factory: Callable[[], ManagedSessionStore],
        *,
        lifetime: timedelta,
        cookie_name: str = "session",
        cookie_https_only: bool = True,
    ) -> None:
        self.app = app
        self.store_factory = store_factory
        self.cookie_name = cookie_name.encode("ascii")
        self._middleware = partial(
            SessionMiddleware,
            lifetime=lifetime,
            rolling=True,
            cookie_name=cookie_name,
            cookie_path="/",
            cookie_same_site="lax",
            cookie_https_only=cookie_https_only,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        store = self.store_factory()
        scope[STORE_SCOPE_KEY] = store

        async def send_guarded(message: Message) -> None:
            if message["type"] == "http.response.start" and not store.cookie_allowed:
                message["headers"] = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if not (
                        name.lower() == b"set-cookie"
                        and value.partition(b"=")[0].strip() == self.cookie_name
                    )
                ]
            await send(message)

        async def dispatch(scope: Scope, receive: Receive, persist: Send) -> None:
            async def before_persist(message: Message) -> None:
                if message["type"] == "http.response.start" and message["status"] >= 400:
                    store.discard()
                await persist(message)

            await self.app(scope, receive, before_persist)

        middleware = self._middleware(app=dispatch, store=cast(SessionStore, store))
        await middleware(scope, receive, send_guarded)


def _store(connection: HTTPConnection) -> ManagedSessionStore:
    return cast(ManagedSessionStore, connection.scope[STORE_SCOPE_KEY])


async def rotate_session(connection: HTTPConnection) -> None:
    await load_session(connection)
    new_id = regenerate_session_id(connection)
    if not await _store(connection).rotate(new_id):
        raise HTTPException(status_code=409)


async def end_session(connection: HTTPConnection) -> None:
    await load_session(connection)
    session_id = get_session_id(connection)
    if session_id is not None:
        await _store(connection).remove(session_id)
    connection.session.clear()
