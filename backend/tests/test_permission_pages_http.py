"""Permission pages are owner-only, bounded and contain no private cursor boundary."""

import base64
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from tests import test_video_access_http as support
from tests.test_channels_http import query_hook
from tests.test_videos_http import create, login
from user.public import SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture
def owner(client, csrf, registered_account):
    return support.owner.__wrapped__(client, csrf, registered_account)


@pytest.fixture(scope="module", autouse=True)
def clean_media(backend_runtime):
    yield from support.clean_media.__wrapped__(backend_runtime)


@pytest.fixture
def published(client, csrf, owner, source_queue):
    return support.published.__wrapped__(client, csrf, owner, source_queue)


def test_pages_mutation_response_scope_and_opaque_cursor(client, csrf, owner):
    drafts = [create(client, csrf) for _ in range(2)]
    paths = [f"/v1/videos/{draft['id']}/permissions" for draft in drafts]
    addresses = [f"permission{index:03d}@example.com" for index in range(41)]
    for index, email in enumerate(addresses):
        response = client.put(paths[0], headers=csrf, json={"email": email, "allowed": True})
        assert response.status_code == 200
        result = response.json()
        assert result["emails"] == list(reversed(addresses[: index + 1]))[:20]
        assert bool(result["next_cursor"]) == (index >= 20)
    first = client.get(paths[0]).json()
    second = client.get(paths[0], params={"cursor": first["next_cursor"]}).json()
    third = client.get(paths[0], params={"cursor": second["next_cursor"]}).json()
    assert [len(page["emails"]) for page in (first, second, third)] == [20, 20, 1]
    assert first["emails"] + second["emails"] + third["emails"] == list(reversed(addresses))
    assert third["next_cursor"] is None
    assert all(
        email.encode() not in base64.urlsafe_b64decode(first["next_cursor"] + "=")
        for email in addresses
    )
    assert client.get(paths[1], params={"cursor": first["next_cursor"]}).status_code == 422
    assert (
        client.get(
            "/v1/channels/" + owner[0]["tag"] + "/videos", params={"cursor": first["next_cursor"]}
        ).status_code
        == 422
    )
    for token in (
        "",
        first["next_cursor"] + "=",
        "x" * 10000,
        "';DROP TABLE video_private_grants;--",
        "../run/secrets",
        "http://rustfs:9000/",
    ):
        response = client.get(paths[0], params={"cursor": token})
        assert response.status_code == 422 and response.json()["code"] == "invalid_cursor"
        assert response.json()["errors"] == [] and response.headers["cache-control"] == "no-store"
        if len(token) > 15:
            assert token not in response.text
    assert client.get(paths[0], params=[("cursor", "a"), ("cursor", "b")]).status_code == 422
    assert client.get(paths[0]).json() == first
    settings = f"/v1/videos/{drafts[0]['id']}/settings"
    before = client.get(settings).json()
    assert (
        client.put(
            paths[0],
            headers=csrf,
            json={"email": " " + addresses[0].upper() + " ", "allowed": True},
        ).json()
        == first
    )
    assert client.get(settings).json() == before

    def fail(_, __, statement, *args):
        if "SELECT" in statement and "FROM video_private_grants" in statement:
            raise SQLAlchemyError("private permission page failure")

    with query_hook(client, fail):
        for method, data in (("GET", None), ("PUT", {"email": addresses[-1], "allowed": False})):
            response = client.request(method, paths[0], headers=csrf, json=data)
            assert response.status_code == 500 and response.json()["code"] == "internal_error"
            assert (
                "private permission page failure" not in response.text
                and "emails" not in response.json()
            )
            assert response.headers["cache-control"] == "no-store"
    assert client.get(paths[0]).json() == first and client.get(settings).json() == before
    schema = client.get("/openapi.json").json()
    assert (
        schema["components"]["schemas"]["PermissionsResponse"]["properties"]["emails"]["maxItems"]
        == 20
    )
    assert "next_cursor" in schema["components"]["schemas"]["PermissionsResponse"]["required"]


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_owner_authorization_before_cursor_and_staff_cannot_bypass(
    client, csrf, owner, account_payload, role
):
    draft = create(client, csrf)
    path = f"/v1/videos/{draft['id']}/permissions"
    body = {"email": "private-address@example.com", "allowed": True}
    assert client.put(path, headers=csrf, json=body).status_code == 200
    token = uuid4().hex
    other = {**account_payload, "email": token + "@example.com", "tag": "t" + token[:28]}
    assert client.post("/v1/auth/register", headers=csrf, json=other).status_code == 201

    async def promote():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(other["email"], role)

    client.portal.call(promote)
    assert login(client, csrf, other).status_code == 200
    for params in ({}, {"cursor": "malformed-private-cursor"}):
        response = client.get(path, params=params)
        assert response.status_code == 404 and body["email"] not in response.text
    assert client.put(path, headers=csrf, json=body).status_code == 404
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert client.get(path, params={"cursor": "bad"}).status_code == 401
    assert login(client, csrf, owner[0]).status_code == 200
    assert client.put(path, json=body).status_code == 403
    assert client.get(path).json() == {"emails": [body["email"]], "next_cursor": None}


def test_invited_viewer_outside_first_page_and_revocation(
    client, csrf, owner, published, account_payload
):
    base, _ = published
    path = base + "/permissions"
    token = uuid4().hex
    recipient = {**account_payload, "email": token + "@example.com", "tag": "t" + token[:28]}
    assert (
        client.put(
            path, headers=csrf, json={"email": recipient["email"], "allowed": True}
        ).status_code
        == 200
    )
    for index in range(40):
        assert (
            client.put(
                path,
                headers=csrf,
                json={"email": f"outside{index:03d}@example.com", "allowed": True},
            ).status_code
            == 200
        )
    first = client.get(path).json()
    assert len(first["emails"]) == 20 and recipient["email"] not in first["emails"]
    before = client.get(base + "/settings").json()
    assert client.post("/v1/auth/register", headers=csrf, json=recipient).status_code == 201
    assert login(client, csrf, recipient).status_code == 200
    assert (
        client.get(base).status_code == 200
        and client.get(base + "/hls/master.m3u8").status_code == 200
    )
    assert client.get(path).status_code == 404
    assert login(client, csrf, owner[0]).status_code == 200
    revoked = client.put(path, headers=csrf, json={"email": recipient["email"], "allowed": False})
    assert revoked.status_code == 200 and revoked.json() == first
    assert client.get(base + "/settings").json()["updated_at"] > before["updated_at"]
    assert login(client, csrf, recipient).status_code == 200
    assert (
        client.get(base).status_code == 404
        and client.get(base + "/hls/master.m3u8").status_code == 404
    )
