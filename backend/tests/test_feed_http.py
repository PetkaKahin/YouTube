"""Home-feed contracts use real profiles, uploads, sessions, media and PostgreSQL."""

import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tests import test_video_access_http as support
from tests.test_uploads_http import send
from tests.test_videos_http import create, login
from user.public import SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture(scope="module", autouse=True)
def clean_media(backend_runtime):
    yield from support.clean_media.__wrapped__(backend_runtime)


@pytest.fixture(autouse=True)
def reset_feed(client):
    async def reset():
        async with client.app.state.resources.sessions.begin() as session:
            await session.execute(text("UPDATE video_publications SET visibility='private'"))

    client.portal.call(reset)


@pytest.fixture
def owner(client, csrf, registered_account):
    data, account = registered_account
    assert login(client, csrf, data).status_code == 200
    return data, account


@pytest.fixture
def published(client, csrf, owner, source_queue):
    return support.published.__wrapped__(client, csrf, owner, source_queue)


def expose(client, csrf, base):
    metadata = client.get(base + "/settings").json()
    return client.put(
        base + "/settings",
        headers=csrf,
        json={
            key: "public" if key == "visibility" else metadata[key]
            for key in ("title", "description", "visibility")
        },
    )


def test_invited_viewer_can_watch_private_video_but_cannot_discover_it_in_feed(
    client, csrf, owner, published, account_payload
):
    owner_data, _ = owner
    base, _ = published
    token = uuid4().hex
    recipient = {**account_payload, "email": token + "@example.com", "tag": "t" + token[:28]}
    assert (
        client.put(
            base + "/permissions", json={"email": recipient["email"], "allowed": True}, headers=csrf
        ).status_code
        == 200
    )
    assert client.post("/v1/auth/register", json=recipient, headers=csrf).status_code == 201
    assert login(client, csrf, recipient).status_code == 200
    assert client.get(base).status_code == 200
    assert client.get("/v1/videos/feed").json() == {"items": [], "next_cursor": None}
    assert login(client, csrf, owner_data).status_code == 200


def test_public_feed_shape_private_owner_and_staff_never_gain_discovery(
    client, csrf, owner, published, caplog
):
    data, account = owner
    base, draft = published
    path = "/v1/videos/feed"
    assert client.get(path).json() == {"items": [], "next_cursor": None}
    for role in ("moderator", "admin"):

        async def promote(chosen_role=role):
            async with client.app.state.dishka_container() as operation:
                await (await operation.get(SetRole))(data["email"], chosen_role)

        client.portal.call(promote)
        assert client.get(path).json()["items"] == []
    assert expose(client, csrf, base).status_code == 200
    response = client.get(path)
    assert response.status_code == 200
    card = response.json()["items"][0]
    assert set(card) == {
        "id",
        "title",
        "added_at",
        "duration_seconds",
        "channel",
        "preview_url",
        "watch_progress",
    }
    assert card["watch_progress"] is None
    assert card["id"] == draft["id"] and card["title"] == draft["title"]
    assert card["channel"]["name"] == data["name"] and card["channel"]["tag"] == data["tag"]
    assert set(card["channel"]) == {"id", "name", "tag"}
    assert card["preview_url"] == "/api" + base + "/preview.webp"
    assert not any(
        marker in response.text
        for marker in (
            data["email"],
            "owner_id",
            "password",
            "role",
            "encoded/",
            "X-Amz",
            "fingerprint",
            "artifact",
        )
    )
    assert (
        response.headers["cache-control"] == "no-store"
        and "cookie" in response.headers["vary"].lower()
    )
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert client.get(path).json() == response.json()
    assert data["email"] not in caplog.text


def test_cursor_response_roundtrip_with_real_uploaded_publications(
    client, csrf, owner, published, source_queue
):
    base, draft = published
    assert expose(client, csrf, base).status_code == 200
    image = b"P6\n1 1\n255\n\xff\x00\x00"
    for index in range(20):
        video = create(client, csrf, title=f"feed-{index}", visibility="public")
        uri = f"/v1/videos/{video['id']}"
        plan = client.post(uri + "/upload", json={"size": len(image)}, headers=csrf).json()
        assert send(plan["parts"][0]["url"], image) == 200
        assert client.post(f"{uri}/upload/{plan['id']}/complete", headers=csrf).status_code == 200
        assert client.post(uri + "/publish", headers=csrf).status_code == 200

    async def prepare():
        queue = source_queue(client.app.state.dishka_container)
        async with queue.open_async():
            await queue.run_worker_async(
                queues=["media"],
                concurrency=1,
                wait=False,
                listen_notify=False,
                install_signal_handlers=False,
            )

    client.portal.call(prepare)
    first = client.get("/v1/videos/feed").json()
    assert len(first["items"]) == 20 and first["next_cursor"]
    second = client.get("/v1/videos/feed", params={"cursor": first["next_cursor"]}).json()
    assert [item["id"] for item in second["items"]] == [draft["id"]]
    assert second["next_cursor"] is None
    assert client.get("/v1/videos/feed", params={"cursor": first["next_cursor"]}).json() == second


@pytest.mark.parametrize(
    "token",
    [
        "",
        "a",
        "x" * 23,
        "x" * 10000,
        "'; DROP TABLE video_publications;--",
        "<script>alert(1)</script>",
        "../../etc/passwd",
        "http://rustfs:9000/",
        "\x00",
        "AA==",
    ],
)
def test_invalid_cursor_is_generic_and_never_reflected(client, token, caplog):
    response = client.get("/v1/videos/feed", params={"cursor": token})
    assert response.status_code == 422 and response.json()["code"] == "invalid_cursor"
    server_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if not record.name.startswith(("httpx", "httpcore"))
    )
    if len(token) > 3:
        assert token not in response.text and token not in server_logs
    assert response.json()["errors"] == []
    assert response.headers["cache-control"] == "no-store"


def test_duplicate_cursor_and_unknown_inputs_do_not_change_sort_or_size(client):
    assert client.get("/v1/videos/feed?cursor=a&cursor=b").status_code == 422
    assert client.get(
        "/v1/videos/feed?limit=100000&owner_id=1&visibility=private&sort=evil"
    ).json() == {"items": [], "next_cursor": None}


def test_database_failure_has_no_partial_or_cached_cards_and_guest_needs_no_valkey(
    client, csrf, owner, published
):
    base, _ = published
    assert expose(client, csrf, base).status_code == 200
    expected = client.get("/v1/videos/feed").json()
    original = AsyncSession.execute

    async def fail(session, statement, *args, **kwargs):
        if "channel_profiles" in str(statement):
            raise SQLAlchemyError("private database marker")
        return await original(session, statement, *args, **kwargs)

    with patch.object(AsyncSession, "execute", fail):
        response = client.get("/v1/videos/feed")
    assert response.status_code == 500 and "private database marker" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "private-marker" not in response.text
    assert client.get("/v1/videos/feed").json() == expected
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    with patch(
        "redis.asyncio.Redis.execute_command", side_effect=ConnectionError("synthetic outage")
    ):
        assert client.get("/v1/videos/feed").json() == expected


def test_openapi_documents_the_feed_without_colliding_with_video_id(client):
    schema = client.get("/openapi.json").json()
    route = schema["paths"]["/v1/videos/feed"]["get"]
    assert route["security"] == [{}, {"SessionCookie": []}]
    assert [field["name"] for field in route["parameters"]] == ["cursor"]
    assert "next_cursor" in schema["components"]["schemas"]["FeedResponse"]["properties"]
    assert "password" not in json.dumps(schema["components"]["schemas"]["FeedCardResponse"])
