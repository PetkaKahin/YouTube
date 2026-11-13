"""Account attack cases through real HTTP, PostgreSQL and the CLI's public scenario."""

import json
import logging
from uuid import uuid4

import pytest
from sqlalchemy import event

from user.public import AccountError, AccountNotFound, GetRole, Role, SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")


def login(client, csrf, data, **changes):
    return client.post(
        "/v1/auth/login",
        json={"email": data["email"], "password": data["password"], **changes},
        headers=csrf,
    )


@pytest.mark.parametrize("payload", ["x'/**/OR/**/1=1--", "x'/**/OR/**/'a'='a'--"])
def test_sql_email_is_bound_as_data_and_never_selects_or_updates_victim(
    client, csrf, registered_account, payload
):
    victim, victim_ids = registered_account
    email = payload + uuid4().hex[:6] + "@example.com"
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if isinstance(parameters, dict) and email.casefold() in parameters.values():
            statements.append(statement)

    engine = client.app.state.resources.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:

        async def read():
            async with client.app.state.dishka_container() as operation:
                return await (await operation.get(GetRole))(email)

        with pytest.raises(AccountNotFound):
            client.portal.call(read)
        # These syntactically valid email addresses reach the repository, not just validation.
        failed = login(client, csrf, victim, email=email)
        assert failed.status_code == 401 and "session" not in client.cookies
        attacker = {**victim, "email": email, "tag": "t" + uuid4().hex[:28]}
        response = client.post("/v1/auth/register", json=attacker, headers=csrf)
        assert response.status_code == 201

        async def change():
            async with client.app.state.dishka_container() as operation:
                return await (await operation.get(SetRole))(email, "moderator")

        account = client.portal.call(change)
        assert client.portal.call(read) is Role.MODERATOR
        assert account.id == response.json()["account_id"] != victim_ids["account_id"]
        assert login(client, csrf, attacker).json()["role"] == "moderator"
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(statements) >= 3
    assert all(email.casefold() not in statement.casefold() for statement in statements)
    role_queries = [
        statement for statement in statements if statement.startswith("SELECT user_accounts.role")
    ]
    assert role_queries and all("password_hash" not in statement for statement in role_queries)
    profile = login(client, csrf, victim)
    assert profile.status_code == 200
    assert profile.json()["id"] == victim_ids["account_id"] and profile.json()["role"] == "user"


@pytest.mark.parametrize(
    "password",
    ["' OR '1'='1", "'; DROP TABLE user_accounts; --", "' UNION SELECT password_hash --"],
)
def test_sql_password_cannot_bypass_login_or_damage_accounts(
    client, csrf, registered_account, password
):
    data, result = registered_account
    rejected = login(client, csrf, data, password=password)
    assert rejected.status_code == 401 and "session" not in client.cookies
    assert client.get("/v1/auth/me").status_code == 401
    assert login(client, csrf, data).json()["id"] == result["account_id"]


@pytest.mark.parametrize(
    "field,payload",
    [
        ("name", "<script>alert(1)</script>"),
        ("name", "{{7*7}}"),
        ("tag", "../../etc/passwd"),
        ("tag", "/etc/passwd"),
        ("tag", "a;whoami"),
        ("tag", "<img src=x onerror=alert(1)>"),
    ],
)
def test_script_command_and_path_inputs_cannot_create_account(
    client, csrf, account_payload, field, payload
):
    data = {**account_payload, field: payload}
    rejected = client.post("/v1/auth/register", json=data, headers=csrf)
    assert rejected.status_code == 422
    assert payload not in rejected.text and "session" not in client.cookies
    # Rejection must leave neither a reusable account nor a reserved channel identity.
    assert login(client, csrf, account_payload).status_code == 401
    assert client.post("/v1/auth/register", json=account_payload, headers=csrf).status_code == 201


def test_untrusted_identity_role_and_password_hash_fields_do_not_change_subject(
    client, csrf, registered_account
):
    data, result = registered_account
    response = login(
        client,
        csrf,
        data,
        user_id=1,
        owner_id=1,
        role="admin",
        password_hash="forged",  # noqa: S106 -- intentionally forged client input
    )
    assert response.status_code == 200
    assert response.json()["id"] == result["account_id"] and response.json()["role"] == "user"
    profile = client.get(
        "/v1/auth/me",
        params={"user_id": 1, "role": "admin"},
        headers={"X-User-ID": "1", "X-Role": "admin", "Authorization": "Bearer forged"},
    )
    assert profile.json()["id"] == result["account_id"] and profile.json()["role"] == "user"
    assert client.post("/v1/auth/set-role", json={"role": "admin"}, headers=csrf).status_code == 404
    assert client.get("/v1/auth/me").json()["role"] == "user"


def test_unsigned_double_submit_token_cannot_create_account_or_session(client, account_payload):
    client.cookies.clear()
    client.cookies.set("csrftoken", "unsigned-attacker-token")
    rejected = client.post(
        "/v1/auth/register",
        json=account_payload,
        headers={"X-CSRFToken": "unsigned-attacker-token"},
    )
    assert rejected.status_code == 403 and "session" not in client.cookies
    client.cookies.clear()
    client.get("/docs")
    csrf = {"X-CSRFToken": client.cookies["csrftoken"]}
    assert login(client, csrf, account_payload).status_code == 401
    assert client.post("/v1/auth/register", json=account_payload, headers=csrf).status_code == 201


def test_bad_role_cannot_inject_sql_through_operator_scenario(client, csrf, registered_account):
    data, _ = registered_account

    async def change():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(
                data["email"], "admin'; DROP TABLE user_accounts; --"
            )

    with pytest.raises(AccountError):
        client.portal.call(change)
    assert login(client, csrf, data).json()["role"] == "user"


def test_credentials_and_tokens_are_absent_from_responses_and_logs(
    client, csrf, registered_account, caplog
):
    data, result = registered_account
    with caplog.at_level(logging.INFO, logger="app.requests"):
        logged_in = login(client, csrf, data)
        session_id = client.cookies["session"]
        csrf_value = client.cookies["csrftoken"]
        duplicate = client.post("/v1/auth/register", json=data, headers=csrf)
        profile = client.get("/v1/auth/me")
    assert logged_in.status_code == profile.status_code == 200 and duplicate.status_code == 409
    assert "request_id" in caplog.text
    for response in (logged_in, duplicate, profile):
        for forbidden in (data["password"], session_id, csrf_value, "$argon2", "password_hash"):
            assert forbidden not in response.text
    for secret in (data["password"], session_id, csrf_value, data["email"]):
        assert secret not in caplog.text
    assert profile.json()["id"] == result["account_id"]


@pytest.mark.parametrize("subject", ["1", True, 0, -1, 2**63, [1], {"id": 1}])
def test_corrupted_session_subject_is_rejected_before_database_query(
    client, csrf, registered_account, subject
):
    data, _ = registered_account
    assert login(client, csrf, data).status_code == 200
    cache = client.app.state.resources.cache
    key = "sessions." + client.cookies["session"]
    record = json.loads(client.portal.call(cache.get, key))
    record["user_id"] = subject
    client.portal.call(cache.set, key, json.dumps(record))
    statements = []

    def record_query(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = client.app.state.resources.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record_query)
    try:
        response = client.get("/v1/auth/me")
    finally:
        event.remove(engine, "before_cursor_execute", record_query)
    assert response.status_code == 401
    assert not statements
    assert not any(
        value.startswith("session=") for value in response.headers.get_list("set-cookie")
    )
