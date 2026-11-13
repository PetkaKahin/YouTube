"""Real service and library compatibility evidence for the technical foundation."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from time import sleep
from uuid import uuid4

import casbin
import pytest
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from sqlalchemy import text

from app.main import SESSION_LIFETIME, create_app
from app.settings import Settings
from common.infrastructure.authorization import MODEL, CasbinAuthorization, create_authorization


def session_key(client):
    return "sessions." + client.cookies["session"]


def cache_call(client, name, *args):
    assert client.portal is not None
    return client.portal.call(getattr(client.app.state.resources.cache, name), *args)


def test_sessions_roll_cookie_and_server_ttl(client, csrf):
    response = client.post("/v1/_test/session/start", headers=csrf)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    lifetime = int(SESSION_LIFETIME.total_seconds())
    assert f"max-age={lifetime}" in cookie
    assert "httponly" in cookie and "samesite=lax" in cookie
    key = session_key(client)
    try:
        assert lifetime - 3 <= cache_call(client, "ttl", key) <= lifetime
        cache_call(client, "expire", key, 10)
        response = client.get("/v1/_test/session")
        assert response.json()["user_id"] == "test-user"
        assert f"max-age={lifetime}" in response.headers["set-cookie"].lower()
        assert lifetime - 3 <= cache_call(client, "ttl", key) <= lifetime
    finally:
        cache_call(client, "delete", key)


def test_rotation_deletes_previous_id_and_logout_is_idempotent(client, csrf):
    client.post("/v1/_test/session/start", headers=csrf)
    old_key = session_key(client)
    client.post("/v1/_test/session/rotate", headers=csrf)
    new_key = session_key(client)
    try:
        assert old_key != new_key
        assert cache_call(client, "exists", old_key) == 0
        assert client.get("/v1/_test/session").json()["user_id"] == "test-user"
        response = client.post("/v1/_test/session/end", headers=csrf)
        assert response.json()["user_id"] is None
        assert "expires=thu, 01 jan 1970" in response.headers["set-cookie"].lower()
        assert cache_call(client, "exists", new_key) == 0
        assert client.post("/v1/_test/session/end", headers=csrf).status_code == 200
    finally:
        cache_call(client, "delete", old_key, new_key)


@pytest.mark.parametrize("mode", ["expire", "lost"])
def test_expired_or_lost_session_is_not_restored(client, csrf, mode):
    client.post("/v1/_test/session/start", headers=csrf)
    key = session_key(client)
    if mode == "expire":
        cache_call(client, "expire", key, 1)
        sleep(1.2)
    else:
        cache_call(client, "delete", key)
    response = client.get("/v1/_test/session")
    assert response.json()["user_id"] is None
    assert cache_call(client, "exists", key) == 0
    assert "session=" not in response.headers.get("set-cookie", "")


def test_parallel_reads_keep_session_and_rolling_ttl(client, csrf):
    client.post("/v1/_test/session/start", headers=csrf)
    key = session_key(client)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.get("/v1/_test/session"), range(2)))
        assert all(response.json()["user_id"] == "test-user" for response in responses)
        assert cache_call(client, "ttl", key) > int(SESSION_LIFETIME.total_seconds()) - 5
    finally:
        cache_call(client, "delete", key)


def test_production_csrf_cookie_is_secure():
    application = create_app(Settings(cookie_secure=True))
    with TestClient(application, base_url="https://testserver", root_path="/api") as client:
        response = client.get("/docs")
        cookie = response.headers["set-cookie"].lower()
        assert "secure" in cookie and "samesite=lax" in cookie
        assert "httponly" not in cookie  # Vue must read the CSRF cookie.
    session_layer = next(
        item
        for item in application.user_middleware
        if item.cls.__name__ == "GuardedSessionMiddleware"
    )
    assert session_layer.kwargs["cookie_https_only"] is True


def test_database_transaction_rollback_and_resource_shutdown(application):
    with TestClient(application, root_path="/api") as client:
        resources = application.state.resources

        async def exercise():
            async with resources.sessions() as session:
                await session.execute(text("CREATE TEMP TABLE foundation_probe (value integer)"))
                await session.commit()
                await session.execute(text("INSERT INTO foundation_probe VALUES (42)"))
                await session.rollback()
                result = await session.execute(text("SELECT count(*) FROM foundation_probe"))
                assert result.scalar_one() == 0
                await session.execute(text("DROP TABLE foundation_probe"))
                await session.commit()

        client.portal.call(exercise)
    assert application.state.resources is None
    assert resources.engine.pool.checkedout() == 0


def test_startup_failure_is_sanitized_and_clears_state():
    application = create_app(Settings(rustfs_bucket="foundation-missing-bucket"))
    with pytest.raises(RuntimeError, match="dependencies are unavailable") as error:
        with TestClient(application):
            pass
    assert application.state.resources is None
    assert error.value.__suppress_context__


def test_readiness_failure_is_503_without_driver_details(client, monkeypatch):
    async def fail(_self):
        raise RuntimeError("sensitive-driver-details")

    monkeypatch.setattr(type(client.app.state.resources), "check", fail)
    response = client.get("/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["code"] == "dependencies_unavailable"
    assert "sensitive-driver-details" not in response.text


def test_rustfs_object_roundtrip(client):
    resources = client.app.state.resources
    key = "_foundation-tests/" + uuid4().hex
    try:
        resources.objects.upload_fileobj(BytesIO(b"foundation-probe"), resources.bucket, key)
        response = resources.objects.get_object(Bucket=resources.bucket, Key=key)
        with response["Body"] as body:
            assert body.read() == b"foundation-probe"
        assert (
            resources.objects.head_object(Bucket=resources.bucket, Key=key)["ContentLength"] == 16
        )
    finally:
        resources.objects.delete_object(Bucket=resources.bucket, Key=key)


def test_argon2_and_email_libraries():
    from email_validator import validate_email

    passwords = PasswordHash.recommended()
    hashed = passwords.hash("пароль with spaces")
    assert hashed.startswith("$argon2id$")
    assert passwords.verify("пароль with spaces", hashed)
    assert not passwords.verify("other-password", hashed)
    assert (
        validate_email("user@example.com", check_deliverability=False).normalized
        == "user@example.com"
    )


def test_casbin_denies_unknown_rules_and_applies_revocation_immediately():
    assert not create_authorization().allows("admin", "private-video", "watch")
    model = casbin.Model()
    model.load_model_from_text(MODEL)
    engine = casbin.Enforcer(model)
    adapter = CasbinAuthorization(engine)
    engine.add_policy("moderator", "reports", "read")
    engine.add_grouping_policy("server-user", "moderator")
    assert adapter.allows("server-user", "reports", "read")
    assert not adapter.allows("other-user", "reports", "read")
    assert not adapter.allows("server-user", "private-video", "watch")
    engine.remove_grouping_policy("server-user", "moderator")
    assert not adapter.allows("server-user", "reports", "read")
