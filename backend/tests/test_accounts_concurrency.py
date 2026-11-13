"""Controlled HTTP/database races with real PostgreSQL locks and Valkey sessions."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from channel.public import Register, Registration
from user.public import SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")


class StatementGate:
    """Pause one real statement after execution, while its transaction remains open."""

    def __init__(self, prefix):
        self.prefix = prefix
        self.entered = Event()
        self.release = asyncio.Event()
        self.pid = None

    def after_execute(self, connection, cursor, statement, parameters, context, executemany):
        if self.pid is not None or not statement.startswith(self.prefix):
            return
        adapted = connection.connection.dbapi_connection
        self.pid = adapted.driver_connection.info.backend_pid
        self.entered.set()
        adapted.run_async(lambda _: asyncio.wait_for(self.release.wait(), timeout=15))


@contextmanager
def hold_statement(client, prefix):
    gate = StatementGate(prefix)
    engine = client.app.state.resources.engine.sync_engine
    event.listen(engine, "after_cursor_execute", gate.after_execute)
    try:
        yield gate
    finally:
        client.portal.call(gate.release.set)
        event.remove(engine, "after_cursor_execute", gate.after_execute)


async def wait_for_database_waiter(engine, holder_pid):
    """Observe the conflict in PostgreSQL itself, rather than relying on sleeps."""
    async with asyncio.timeout(10), engine.connect() as connection:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        while True:
            waiter = await connection.scalar(
                text(
                    "SELECT pid FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND :holder = ANY(pg_blocking_pids(pid))"
                ),
                {"holder": holder_pid},
            )
            if waiter is not None:
                assert waiter != holder_pid
                return waiter
            await asyncio.sleep(0.01)


def login(client, csrf, data):
    return client.post(
        "/v1/auth/login", json={"email": data["email"], "password": data["password"]}, headers=csrf
    )


async def change_role(application, email, role):
    async with application.state.dishka_container() as operation:
        return await (await operation.get(SetRole))(email, role)


@pytest.mark.parametrize(
    "duplicate,table", [("email", "user_accounts"), ("tag", "channel_profiles")]
)
def test_unique_conflict_waits_for_real_database_transaction(
    client, csrf, account_payload, duplicate, table
):
    left = account_payload
    right = {**left, "email": uuid4().hex + "@example.com", "tag": "t" + uuid4().hex[:28]}
    right[duplicate] = left[duplicate].upper()
    with hold_statement(client, "INSERT INTO " + table) as gate, ThreadPoolExecutor(2) as pool:
        first = pool.submit(client.post, "/v1/auth/register", json=left, headers=csrf)
        try:
            assert gate.entered.wait(10)
            second = pool.submit(client.post, "/v1/auth/register", json=right, headers=csrf)
            client.portal.call(
                wait_for_database_waiter, client.app.state.resources.engine, gate.pid
            )
        finally:
            client.portal.call(gate.release.set)
        assert first.result(timeout=10).status_code == 201
        rejected = second.result(timeout=10)
        assert rejected.status_code == 409
        assert rejected.json()["errors"] == [
            {"location": ["body", duplicate], "code": "already_taken"}
        ]
    if duplicate == "tag":
        # The account inserted by the waiting transaction must have rolled back too.
        right["tag"] = "t" + uuid4().hex[:28]
        assert client.post("/v1/auth/register", json=right, headers=csrf).status_code == 201


@pytest.mark.parametrize("first_role,last_role", [("admin", "user"), ("moderator", "admin")])
def test_role_updates_serialize_without_exposing_uncommitted_role(
    client, csrf, registered_account, first_role, last_role
):
    data, _ = registered_account
    assert login(client, csrf, data).status_code == 200
    with hold_statement(client, "UPDATE user_accounts") as gate, ThreadPoolExecutor(2) as pool:
        first = pool.submit(client.portal.call, change_role, client.app, data["email"], first_role)
        try:
            assert gate.entered.wait(10)
            # A separate read sees the last committed role, not the staged grant.
            assert client.get("/v1/auth/me").json()["role"] == "user"
            second = pool.submit(
                client.portal.call, change_role, client.app, data["email"], last_role
            )
            client.portal.call(
                wait_for_database_waiter, client.app.state.resources.engine, gate.pid
            )
        finally:
            client.portal.call(gate.release.set)
        assert first.result(timeout=10).role.value == first_role
        assert second.result(timeout=10).role.value == last_role
    assert client.get("/v1/auth/me").json()["role"] == last_role


@pytest.mark.parametrize(
    "duplicate,table", [("email", "user_accounts"), ("tag", "channel_profiles")]
)
def test_waiting_registration_can_win_after_holder_disconnect(
    client, csrf, account_payload, duplicate, table
):
    left = account_payload
    right = {**left, "email": uuid4().hex + "@example.com", "tag": "t" + uuid4().hex[:28]}
    right[duplicate] = left[duplicate].upper()

    async def disconnect(pid):
        async with client.app.state.resources.engine.connect() as connection:
            assert await connection.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})

    with hold_statement(client, "INSERT INTO " + table) as gate, ThreadPoolExecutor(2) as pool:
        first = pool.submit(client.post, "/v1/auth/register", json=left, headers=csrf)
        try:
            assert gate.entered.wait(10)
            second = pool.submit(client.post, "/v1/auth/register", json=right, headers=csrf)
            client.portal.call(
                wait_for_database_waiter, client.app.state.resources.engine, gate.pid
            )
            client.portal.call(disconnect, gate.pid)
        finally:
            client.portal.call(gate.release.set)
        assert first.result(timeout=10).status_code == 500
        winner = second.result(timeout=10)
        assert winner.status_code == 201
    authenticated = login(client, csrf, right)
    assert authenticated.status_code == 200
    assert authenticated.json()["id"] == winner.json()["account_id"]
    client.post("/v1/auth/logout", headers=csrf)
    if duplicate == "tag":
        assert login(client, csrf, left).status_code == 401
        left["tag"] = "t" + uuid4().hex[:28]
        assert client.post("/v1/auth/register", json=left, headers=csrf).status_code == 201


@pytest.mark.parametrize("pending", ["login", "me"])
@pytest.mark.parametrize("terminal", ["logout", "login"])
def test_pending_account_request_cannot_restore_old_session(
    client, csrf, registered_account, pending, terminal
):
    data, account = registered_account
    assert login(client, csrf, data).status_code == 200
    old_id = client.cookies["session"]
    with hold_statement(client, "SELECT user_accounts.") as gate, ThreadPoolExecutor(1) as pool:
        held = (
            pool.submit(login, client, csrf, data)
            if pending == "login"
            else pool.submit(client.get, "/v1/auth/me")
        )
        try:
            assert gate.entered.wait(10)
            winner = (
                client.post("/v1/auth/logout", headers=csrf)
                if terminal == "logout"
                else login(client, csrf, data)
            )
            assert winner.status_code == (204 if terminal == "logout" else 200)
        finally:
            client.portal.call(gate.release.set)
        late = held.result(timeout=10)
    assert late.status_code == (409 if pending == "login" else 200)
    assert not any(value.startswith("session=") for value in late.headers.get_list("set-cookie"))
    assert client.portal.call(client.app.state.resources.cache.exists, "sessions." + old_id) == 0
    current = client.get("/v1/auth/me")
    assert current.status_code == (401 if terminal == "logout" else 200)
    if terminal == "login":
        assert current.json()["id"] == account["account_id"]


def test_cancelled_registration_rolls_back_both_inserts_and_releases_connection(
    client, csrf, account_payload
):
    closed = Event()

    async def create():
        try:
            async with client.app.state.dishka_container() as operation:
                await (await operation.get(Register))(Registration(**account_payload))
        finally:
            closed.set()

    with hold_statement(client, "INSERT INTO channel_profiles") as gate:
        task = client.portal.start_task_soon(create)
        try:
            assert gate.entered.wait(10)
            task.cancel()
            assert closed.wait(10)
            assert task.cancelled()
        finally:
            task.cancel()
    assert client.app.state.resources.engine.pool.checkedout() == 0
    assert login(client, csrf, account_payload).status_code == 401
    assert client.post("/v1/auth/register", json=account_payload, headers=csrf).status_code == 201


def test_database_disconnect_during_registration_is_safe_and_retryable(
    client, csrf, account_payload, caplog
):
    async def disconnect(pid):
        async with client.app.state.resources.engine.connect() as connection:
            assert await connection.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})

    with (
        hold_statement(client, "INSERT INTO channel_profiles") as gate,
        ThreadPoolExecutor(1) as pool,
    ):
        pending = pool.submit(client.post, "/v1/auth/register", json=account_payload, headers=csrf)
        try:
            assert gate.entered.wait(10)
            client.portal.call(disconnect, gate.pid)
        finally:
            client.portal.call(gate.release.set)
        failed = pending.result(timeout=10)
    assert failed.status_code == 500 and failed.json()["code"] == "internal_error"
    for value in (account_payload["password"], account_payload["email"]):
        assert value not in failed.text and value not in caplog.text
    assert "session" not in client.cookies
    assert login(client, csrf, account_payload).status_code == 401
    assert client.post("/v1/auth/register", json=account_payload, headers=csrf).status_code == 201
