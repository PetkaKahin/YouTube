"""HTTP account contracts against Compose PostgreSQL and Valkey."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from time import sleep
from uuid import uuid4

import pytest

from user.public import Role, SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")


def registration(**changes):
    token = uuid4().hex
    return {
        "name": "  Иван Петров  ",
        "email": f"{token}@example.com",
        "tag": "@t" + token[:28],
        "password": " Пароль 123 ",
        "password_confirmation": " Пароль 123 ",
        **changes,
    }


def register(client, csrf, data=None):
    data = data or registration()
    response = client.post("/v1/auth/register", json=data, headers=csrf)
    assert response.status_code == 201, response.text
    return data, response.json()


def login(client, csrf, data):
    return client.post(
        "/v1/auth/login", json={"email": data["email"], "password": data["password"]}, headers=csrf
    )


def cache_call(client, method, *args):
    return client.portal.call(getattr(client.app.state.resources.cache, method), *args)


def test_registration_creates_ids_without_login_or_client_role(client, csrf):
    data, result = register(client, csrf, registration(role="admin", user_id=1))
    assert type(result["account_id"]) is int and result["account_id"] > 0
    assert type(result["channel_id"]) is int and result["channel_id"] > 0
    assert result["tag"] == data["tag"][1:].lower()
    assert "session" not in client.cookies
    assert client.get("/v1/auth/me").status_code == 401
    response = login(client, csrf, data)
    assert response.status_code == 200
    assert response.json()["name"] == "Иван Петров"
    assert response.json()["role"] == "user"
    assert not any(key in response.json() for key in ("password", "password_hash", "session_id"))
    client.post("/v1/auth/logout", headers=csrf)


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"name": "A"}, "name"),
        ({"name": "a" * 51}, "name"),
        ({"email": "bad"}, "email"),
        ({"tag": "ab"}, "tag"),
        ({"tag": "a" * 31}, "tag"),
        ({"tag": "канал"}, "tag"),
        ({"password": "12345", "password_confirmation": "12345"}, "password"),
        ({"password": "a" * 129, "password_confirmation": "a" * 129}, "password"),
        ({"password_confirmation": "different"}, "password_confirmation"),
    ],
)
def test_registration_errors_are_field_errors_without_values(client, csrf, changes, field):
    response = client.post("/v1/auth/register", json=registration(**changes), headers=csrf)
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["errors"][0]["location"] == ["body", field]
    assert "password_hash" not in response.text and "Пароль" not in response.text


@pytest.mark.parametrize(
    "name,tag,password", [("Ан", "abc", " " * 6), ("A" * 50, "x" * 30, "я" * 128)]
)
def test_registration_accepts_exact_boundaries(client, csrf, name, tag, password):
    # A unique tag with the exact boundary length.
    tag = uuid4().hex[: len(tag)]
    data, _ = register(
        client,
        csrf,
        registration(name=name, tag=tag, password=password, password_confirmation=password),
    )
    assert login(client, csrf, data).status_code == 200
    client.post("/v1/auth/logout", headers=csrf)


def test_duplicate_email_and_tag_are_case_insensitive_and_channel_error_rolls_back(client, csrf):
    data, result = register(client, csrf)
    response = client.post(
        "/v1/auth/register", json=registration(email=data["email"].upper()), headers=csrf
    )
    assert response.status_code == 409
    assert response.json()["errors"] == [{"location": ["body", "email"], "code": "already_taken"}]
    retry = registration(tag="@" + result["tag"].upper())
    response = client.post("/v1/auth/register", json=retry, headers=csrf)
    assert response.status_code == 409 and response.json()["errors"][0]["location"][-1] == "tag"
    # The rejected channel did not leave a persistent account behind.
    retry["tag"] = "t" + uuid4().hex[:28]
    register(client, csrf, retry)


@pytest.mark.parametrize("duplicate", ["email", "tag"])
def test_concurrent_registration_has_one_winner_and_no_partial_account(client, csrf, duplicate):
    left, right = registration(), registration()
    right[duplicate] = left[duplicate].upper()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda data: client.post("/v1/auth/register", json=data, headers=csrf),
                [left, right],
            )
        )
    assert sorted(response.status_code for response in responses) == [201, 409]
    if duplicate == "tag":
        loser = [left, right][
            next(i for i, response in enumerate(responses) if response.status_code == 409)
        ]
        loser["tag"] = "t" + uuid4().hex[:28]
        register(client, csrf, loser)


def test_login_rotates_existing_id_and_logout_revokes_it(client, csrf):
    data, _ = register(client, csrf)
    client.post("/v1/_test/session/start", headers=csrf)
    old_id = client.cookies["session"]
    assert login(client, csrf, data).status_code == 200
    new_id = client.cookies["session"]
    assert old_id != new_id
    assert cache_call(client, "exists", "sessions." + old_id) == 0
    stored = json.loads(cache_call(client, "get", "sessions." + new_id))
    assert stored["user_id"] == client.get("/v1/auth/me").json()["id"]
    assert "role" not in stored and "password" not in stored
    response = client.post("/v1/auth/logout", headers=csrf)
    assert response.status_code == 204 and not response.content
    assert cache_call(client, "exists", "sessions." + new_id) == 0
    client.cookies.set("session", new_id)
    assert client.get("/v1/auth/me").status_code == 401
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert login(client, csrf, data).status_code == 200
    client.post("/v1/auth/logout", headers=csrf)


def test_login_uses_normalized_email_exact_password_and_uniform_rejection(client, csrf):
    data, _ = register(client, csrf)
    assert (
        login(client, csrf, {**data, "email": "  " + data["email"].upper() + "  "}).status_code
        == 200
    )
    client.post("/v1/auth/logout", headers=csrf)
    wrong = login(client, csrf, {**data, "password": data["password"].strip()})
    unknown = login(client, csrf, registration())
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["code"] == unknown.json()["code"] == "invalid_credentials"
    assert "session" not in client.cookies


@pytest.mark.parametrize(
    "method,path", [("post", "/register"), ("post", "/login"), ("post", "/logout")]
)
def test_account_mutations_require_csrf(client, method, path):
    response = getattr(client, method)("/v1/auth" + path, json=registration())
    assert response.status_code == 403 and response.json()["code"] == "csrf_failed"


@pytest.mark.parametrize("mode", ["expire", "lost", "forged"])
def test_unusable_real_account_session_requires_new_login(client, csrf, mode):
    data, _ = register(client, csrf)
    assert login(client, csrf, data).status_code == 200
    key = "sessions." + client.cookies["session"]
    if mode == "expire":
        cache_call(client, "pexpire", key, 1)
        sleep(0.02)
    elif mode == "lost":
        cache_call(client, "delete", key)
    else:
        client.cookies.clear()
        client.cookies.set("session", "forged-id")
    assert client.get("/v1/auth/me").status_code == 401
    if mode != "forged":
        assert cache_call(client, "exists", key) == 0
    else:
        cache_call(client, "delete", key)


def test_role_changes_are_visible_in_existing_sessions_and_cli_uses_scenario(client, csrf):
    data, _ = register(client, csrf)
    assert login(client, csrf, data).status_code == 200

    async def change_role(role):
        async with client.app.state.dishka_container() as operation:
            scenario = await operation.get(SetRole)
            return await scenario(data["email"], role)

    for role in [Role.MODERATOR, Role.ADMIN, Role.USER]:
        client.portal.call(change_role, role.value)
        assert client.get("/v1/auth/me").json()["role"] == role.value
    result = subprocess.run(  # noqa: S603 -- generated test email; no shell
        [sys.executable, "-m", "app.cli", "role", "set", "--", data["email"], "moderator"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert client.get("/v1/auth/me").json()["role"] == "moderator"
    current = subprocess.run(  # noqa: S603 -- generated test email; no shell
        [sys.executable, "-m", "app.cli", "role", "get", "--", "  " + data["email"].upper() + "  "],
        capture_output=True,
        text=True,
        check=False,
    )
    assert current.returncode == 0 and current.stdout == "moderator\n"
    assert client.get("/v1/auth/me").json()["role"] == "moderator"
    missing = subprocess.run(
        [sys.executable, "-m", "app.cli", "role", "set", "--", "missing@example.com", "admin"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 1 and "Account not found" in missing.stderr
    missing_read = subprocess.run(
        [sys.executable, "-m", "app.cli", "role", "get", "--", "missing@example.com"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing_read.returncode == 1 and "Account not found" in missing_read.stderr
    invalid_role = subprocess.run(  # noqa: S603 -- generated test email; no shell
        [sys.executable, "-m", "app.cli", "role", "set", "--", data["email"], "forged-role"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid_role.returncode == 2
    assert client.get("/v1/auth/me").json()["role"] == "moderator"
    client.post("/v1/auth/logout", headers=csrf)


def test_registration_json_types_are_strict_and_secrets_do_not_leak(client, csrf, caplog):
    data = registration(password=123456, password_confirmation=123456)
    response = client.post("/v1/auth/register", json=data, headers=csrf)
    assert response.status_code == 422
    assert "123456" not in response.text and "123456" not in caplog.text


def test_accounts_openapi_documents_int64_and_cookie_auth(client):
    schema = client.get("/openapi.json").json()
    assert (
        schema["components"]["schemas"]["RegisteredBody"]["properties"]["account_id"]["format"]
        == "int64"
    )
    assert schema["paths"]["/v1/auth/me"]["get"]["security"] == [{"SessionCookie": []}]
