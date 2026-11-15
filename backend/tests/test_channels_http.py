"""Public channel reads, actual sessions, independent private access and safe failures."""

from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError

from tests import test_feed_http as feed
from tests import test_video_access_http as access
from tests.test_playlists_http import playlist
from tests.test_videos_http import login
from user.public import SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture(scope="module", autouse=True)
def clean_media(backend_runtime):
    yield from access.clean_media.__wrapped__(backend_runtime)


@pytest.fixture
def owner(client, csrf, registered_account):
    return feed.owner.__wrapped__(client, csrf, registered_account)


@pytest.fixture
def published(client, csrf, owner, source_queue):
    return access.published.__wrapped__(client, csrf, owner, source_queue)


def path(owner):
    return "/v1/channels/" + owner[0]["tag"]


@contextmanager
def query_hook(client, hook):
    engine = client.app.state.resources.engine.sync_engine
    event.listen(engine, "before_cursor_execute", hook)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", hook)


def test_empty_channel_guest_case_prefix_and_schema(client, csrf, owner):
    root = path(owner)
    expected = {
        "id": owner[1]["channel_id"],
        "name": owner[0]["name"],
        "tag": owner[0]["tag"],
        "created_at": owner[1]["created_at"],
        "updated_at": owner[1]["created_at"],
    }
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    for tag in (owner[0]["tag"], "@" + owner[0]["tag"].upper()):
        response = client.get("/v1/channels/" + tag)
        assert response.status_code == 200 and response.json() == expected
        assert (
            response.headers["cache-control"] == "no-store"
            and "cookie" in response.headers["vary"].lower()
        )
        assert not any(
            marker in response.text
            for marker in (owner[0]["email"], "owner_id", "password", "subscriber_count")
        )
    for suffix in ("/videos", "/playlists"):
        assert client.get(root + suffix).json() == {"items": [], "next_cursor": None}
    schema = client.get("/openapi.json").json()
    for suffix in ("", "/videos", "/playlists"):
        assert schema["paths"]["/v1/channels/{tag}" + suffix]["get"]["security"] == [
            {},
            {"SessionCookie": []},
        ]


@pytest.mark.parametrize(
    "tag", ["xx", "a" * 31, "@@tag", "<script>", "';DROP--", "missing-channel"]
)
@pytest.mark.parametrize("suffix", ["", "/videos", "/playlists"])
def test_invalid_and_absent_tags_are_generic_not_found(client, tag, suffix):
    response = client.get("/v1/channels/" + tag + suffix)
    assert response.status_code == 404 and response.json()["code"] == "not_found"
    assert (
        response.json()["detail"] == "Channel not found"
        and response.headers["cache-control"] == "no-store"
    )


@pytest.mark.parametrize("suffix", ["/videos", "/playlists"])
def test_cursor_damage_duplicates_and_injection_are_generic(client, owner, suffix):
    for cursor in (
        "",
        "x" * 10000,
        "';DROP TABLE channels;--",
        "../secrets",
        "http://rustfs:9000/",
    ):
        response = client.get(path(owner) + suffix, params={"cursor": cursor})
        assert response.status_code == 422 and response.json()["code"] == "invalid_cursor"
        assert response.headers["cache-control"] == "no-store"
        if len(cursor) > 15:
            assert cursor not in response.text
    assert (
        client.get(path(owner) + suffix, params=[("cursor", "a"), ("cursor", "b")]).status_code
        == 422
    )


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_private_owner_invitation_and_staff_never_expand_channel_lists(
    client, csrf, owner, published, account_payload, role
):
    base, draft = published
    root = path(owner)
    values = [
        playlist(client, csrf, visibility) for visibility in ("public", "private", "unlisted")
    ]
    assert (
        client.put(
            base + "/playlists", headers=csrf, json={"playlist_ids": [row["id"] for row in values]}
        ).status_code
        == 200
    )

    async def promote():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(owner[0]["email"], role)

    client.portal.call(promote)
    empty = {"items": [], "next_cursor": None}
    assert client.get(root + "/videos").json() == empty
    listing = client.get(root + "/playlists").json()
    assert [row["id"] for row in listing["items"]] == [values[0]["id"]]
    assert not any(
        key in listing["items"][0] for key in ("selected", "owner_id", "video_count", "preview_url")
    )
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
    assert client.get(base).status_code == 200
    assert (
        client.get(root + "/videos").json() == empty
        and client.get(root + "/playlists").json() == listing
    )
    assert login(client, csrf, owner[0]).status_code == 200
    assert feed.expose(client, csrf, base).status_code == 200
    visible = client.get(root + "/videos").json()
    assert [item["id"] for item in visible["items"]] == [draft["id"]]
    assert visible["items"][0]["channel"]["id"] == owner[1]["channel_id"]
    assert visible["items"][0]["preview_url"] == "/api" + base + "/preview.webp"
    before = client.get(base + "/settings").json()

    async def hide():
        async with client.app.state.resources.sessions.begin() as session:
            await session.execute(
                text("UPDATE video_publications SET moderation='hidden' WHERE id=:id"),
                {"id": draft["id"]},
            )

    client.portal.call(hide)
    assert client.get(root + "/videos").json() == empty and client.get(base).status_code == 404
    assert client.get(root + "/playlists").json() == listing
    assert client.get(base + "/settings").json()["updated_at"] == before["updated_at"]
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    with patch(
        "redis.asyncio.Redis.execute_command", side_effect=ConnectionError("synthetic outage")
    ):
        assert client.get(root).status_code == 200
        assert (
            client.get(root + "/videos").json() == empty
            and client.get(root + "/playlists").json() == listing
        )


def test_playlist_cursor_and_read_failure_never_leak_partial_state(client, csrf, owner):
    root = path(owner)
    rows = [playlist(client, csrf, "public", title="<script>as text</script>") for _ in range(21)]
    statements = []

    def deny_contents(_, __, statement, *args):
        statements.append(statement)
        assert "video_" not in statement and "channel_playlist_entries" not in statement

    with query_hook(client, deny_contents):
        first = client.get(root + "/playlists").json()
    assert any("channel_playlists" in statement for statement in statements)
    assert len(first["items"]) == 20 and first["next_cursor"]
    token = first["next_cursor"]
    second = client.get(root + "/playlists", params={"cursor": token}).json()
    assert [row["id"] for row in second["items"]] == [rows[0]["id"]] and second[
        "next_cursor"
    ] is None
    state = client.get("/v1/playlists/mine").json()
    assert client.get("/v1/playlists/mine", params={"cursor": token}).status_code == 422
    assert client.get(root + "/videos", params={"cursor": token}).status_code == 422

    def fail(_, __, statement, *args):
        if "channel_" in statement:
            raise SQLAlchemyError("private database detail")

    with query_hook(client, fail):
        for suffix in ("", "/videos", "/playlists"):
            response = client.get(root + suffix)
            assert response.status_code == 500 and response.json()["code"] == "internal_error"
            assert "private database detail" not in response.text and "items" not in response.json()
            assert response.headers["cache-control"] == "no-store"
    assert client.get("/v1/playlists/mine").json() == state
    assert client.get(root + "/playlists").json() == first


@pytest.mark.parametrize(
    "table,suffixes", [("user_accounts", ("", "/videos")), ("video_publications", ("/videos",))]
)
def test_name_and_video_read_failure_rolls_back_and_recovers(
    client, csrf, owner, published, table, suffixes
):
    base, _ = published
    assert feed.expose(client, csrf, base).status_code == 200
    root = path(owner)
    before = {suffix: client.get(root + suffix).json() for suffix in suffixes}
    settings = client.get(base + "/settings").json()

    def fail(_, __, statement, *args):
        if table in statement:
            raise SQLAlchemyError("private read detail")

    with query_hook(client, fail):
        for suffix in suffixes:
            response = client.get(root + suffix)
            assert response.status_code == 500 and response.json()["code"] == "internal_error"
            assert "private read detail" not in response.text and "items" not in response.json()
    assert {suffix: client.get(root + suffix).json() for suffix in suffixes} == before
    assert client.get(base + "/settings").json() == settings
