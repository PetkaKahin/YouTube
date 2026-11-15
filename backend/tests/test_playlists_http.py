"""Playlist ownership, CSRF, private metadata, ordered contents and safe errors."""

import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tests import test_video_access_http as support
from tests.test_videos_http import create, login
from user.public import SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture(scope="module", autouse=True)
def clean_media(backend_runtime):
    yield from support.clean_media.__wrapped__(backend_runtime)


@pytest.fixture
def owner(client, csrf, registered_account):
    return support.owner.__wrapped__(client, csrf, registered_account)


@pytest.fixture
def published(client, csrf, owner, source_queue):
    return support.published.__wrapped__(client, csrf, owner, source_queue)


def playlist(client, csrf, visibility="private", **extra):
    response = client.post(
        "/v1/playlists",
        headers=csrf,
        json={
            "title": "  <script>as text</script> ",
            "description": "x\r\ny",
            "visibility": visibility,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("visibility", ["public", "unlisted", "private"])
def test_creation_owner_list_and_guest_metadata_visibility(client, csrf, owner, visibility):
    value = playlist(client, csrf, visibility, channel_id=2**62, owner_id=2**62)
    assert value["title"] == "<script>as text</script>" and value["description"] == "x\ny"
    assert value["channel_id"] == owner[1]["channel_id"] and "owner_id" not in value
    listing = client.get("/v1/playlists/mine").json()
    assert [item["id"] for item in listing["items"]] == [value["id"]] and listing[
        "next_cursor"
    ] is None
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert client.get("/v1/playlists/mine").status_code == 401
    response = client.get(f"/v1/playlists/{value['id']}")
    assert response.status_code == (404 if visibility == "private" else 200)
    if visibility != "private":
        assert response.json() == {"playlist": value, "items": [], "next_cursor": None}
    else:
        assert value["title"] not in response.text
    assert (
        response.headers["cache-control"] == "no-store"
        and "cookie" in response.headers["vary"].lower()
    )


@pytest.mark.parametrize(
    "body",
    [
        {"title": "", "visibility": "public"},
        {"title": " \n ", "visibility": "public"},
        {"title": "a" * 101, "visibility": "public"},
        {"title": "x", "description": "a" * 5001, "visibility": "public"},
        {"title": "x\x00", "visibility": "public"},
        {"title": "x", "description": "\ud800", "visibility": "public"},
        {"title": True, "visibility": "public"},
        {"title": "x", "visibility": "other"},
        {"title": "x"},
    ],
)
def test_invalid_metadata_never_creates_a_playlist(client, csrf, owner, body):
    before = client.get("/v1/playlists/mine").json()
    response = client.post(
        "/v1/playlists",
        headers={**csrf, "Content-Type": "application/json"},
        content=json.dumps(body).encode(),
    )
    assert response.status_code == 422
    assert client.get("/v1/playlists/mine").json() == before


def test_csrf_guest_forged_owner_and_selection_scope(client, csrf, owner):
    draft = create(client, csrf)
    path = f"/v1/videos/{draft['id']}/playlists"
    assert (
        client.post("/v1/playlists", json={"title": "x", "visibility": "public"}).status_code == 403
    )
    value = playlist(client, csrf)
    assert client.put(path, json={"playlist_ids": [value["id"]]}).status_code == 403
    assert client.put(
        path, headers=csrf, json={"playlist_ids": [value["id"], value["id"]], "owner_id": 2**62}
    ).json() == {"playlist_ids": [value["id"]]}
    settings = client.get(f"/v1/videos/{draft['id']}/settings").json()
    assert client.put(path, headers=csrf, json={"playlist_ids": [value["id"]]}).status_code == 200
    assert client.get(f"/v1/videos/{draft['id']}/settings").json() == settings
    assert client.get(path).json()["items"][0]["selected"]
    for ids in ([True], [-1], ["1"], [2**63]):
        assert client.put(path, headers=csrf, json={"playlist_ids": ids}).status_code == 422
    assert client.put(path, headers=csrf, json={"playlist_ids": []}).json() == {"playlist_ids": []}


def test_private_video_invitation_is_independent_of_public_playlist(
    client, csrf, owner, published, account_payload
):
    base, draft = published
    value = playlist(client, csrf, "public")
    assert (
        client.put(
            base + "/playlists", headers=csrf, json={"playlist_ids": [value["id"]]}
        ).status_code
        == 200
    )
    assert [item["id"] for item in client.get(f"/v1/playlists/{value['id']}").json()["items"]] == [
        draft["id"]
    ]
    token = uuid4().hex
    recipient = {**account_payload, "email": token + "@example.com", "tag": "t" + token[:28]}
    assert (
        client.put(
            base + "/permissions", headers=csrf, json={"email": recipient["email"], "allowed": True}
        ).status_code
        == 200
    )
    assert client.post("/v1/auth/register", headers=csrf, json=recipient).status_code == 201
    assert login(client, csrf, recipient).status_code == 200
    assert [item["id"] for item in client.get(f"/v1/playlists/{value['id']}").json()["items"]] == [
        draft["id"]
    ]
    assert login(client, csrf, owner[0]).status_code == 200
    assert (
        client.put(
            base + "/permissions",
            headers=csrf,
            json={"email": recipient["email"], "allowed": False},
        ).status_code
        == 200
    )
    assert login(client, csrf, recipient).status_code == 200
    assert client.get(f"/v1/playlists/{value['id']}").json()["items"] == []
    assert client.get(base + "/hls/master.m3u8").status_code == 404
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert client.get(f"/v1/playlists/{value['id']}").json()["items"] == []


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_foreign_playlist_and_video_actions_do_not_gain_role_bypass(
    client, csrf, owner, account_payload, role
):
    value = playlist(client, csrf, "private")
    draft = create(client, csrf)
    token = uuid4().hex
    other = {**account_payload, "email": token + "@example.com", "tag": "t" + token[:28]}
    assert client.post("/v1/auth/register", headers=csrf, json=other).status_code == 201

    async def promote():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(other["email"], role)

    client.portal.call(promote)
    assert login(client, csrf, other).status_code == 200
    for identity in (value["id"], 2**62):
        response = client.get(f"/v1/playlists/{identity}")
        assert response.status_code == 404 and value["title"] not in response.text
    assert client.get(f"/v1/videos/{draft['id']}/playlists").status_code == 404
    assert (
        client.put(
            f"/v1/videos/{draft['id']}/playlists", headers=csrf, json={"playlist_ids": []}
        ).status_code
        == 404
    )


def test_cursor_pages_no_scope_replay_and_safe_database_failure(client, csrf, owner, caplog):
    values = [playlist(client, csrf) for _ in range(21)]
    first = client.get("/v1/playlists/mine").json()
    assert len(first["items"]) == 20 and first["next_cursor"]
    second = client.get("/v1/playlists/mine", params={"cursor": first["next_cursor"]}).json()
    assert [item["id"] for item in second["items"]] == [values[0]["id"]] and second[
        "next_cursor"
    ] is None
    draft = create(client, csrf)
    assert (
        client.get(
            f"/v1/videos/{draft['id']}/playlists", params={"cursor": first["next_cursor"]}
        ).status_code
        == 422
    )
    for token in ("x" * 10000, "../run/secrets", "'; DROP TABLE channel_playlists;--"):
        response = client.get("/v1/playlists/mine", params={"cursor": token})
        assert response.status_code == 422 and token not in response.text
    original = AsyncSession.execute

    async def fail(session, statement, *args, **kwargs):
        if "channel_playlists" in str(statement):
            raise SQLAlchemyError("private database marker")
        return await original(session, statement, *args, **kwargs)

    with patch.object(AsyncSession, "execute", fail):
        response = client.get("/v1/playlists/mine")
    assert response.status_code == 500 and "private database marker" not in response.text
    assert client.get("/v1/playlists/mine").json() == first
