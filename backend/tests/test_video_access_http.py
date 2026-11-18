"""Real sessions, private email commands and non-leaking ordinary viewer responses."""

import asyncio
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from channel.public import GetPublicChannels
from tests.test_uploads_http import send
from tests.test_videos_http import create, login
from user.public import SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture(scope="module", autouse=True)
def clean_media(backend_runtime):
    yield

    async def cleanup():
        async with backend_runtime() as (_, resources):
            async with resources.sessions() as session:
                sources = (
                    await session.scalars(text("SELECT object_key FROM video_uploads"))
                ).all()
                artifacts = (
                    await session.scalars(
                        text("SELECT artifact FROM video_encoded_tracks WHERE status='ready'")
                    )
                ).all()
            keys = list(sources) + [
                artifact["prefix"] + item["name"]
                for artifact in artifacts
                for item in artifact["files"]
            ]
            for key in keys:
                await asyncio.to_thread(
                    resources.objects.delete_object, Bucket=resources.bucket, Key=key
                )

    asyncio.run(cleanup())


@pytest.fixture
def owner(client, csrf, registered_account):
    data, account = registered_account
    assert login(client, csrf, data).status_code == 200
    return data, account


@pytest.fixture
def published(client, csrf, owner, source_queue):
    draft = create(client, csrf, title="private-marker", description="<script>as text</script>")
    base = f"/v1/videos/{draft['id']}"
    image = b"P6\n1 1\n255\n\xff\x00\x00"
    transfer = client.post(base + "/upload", json={"size": len(image)}, headers=csrf).json()
    assert send(transfer["parts"][0]["url"], image) == 200
    assert client.post(f"{base}/upload/{transfer['id']}/complete", headers=csrf).status_code == 200

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
    assert client.post(base + "/publish", headers=csrf).status_code == 200
    return base, draft


def test_email_before_registration_same_response_and_real_session_viewing(
    client, csrf, owner, published, caplog
):
    data, author = owner
    base, draft = published
    permissions = base + "/permissions"
    email = uuid4().hex + "@example.com"
    body = {"email": " " + email.upper() + " ", "allowed": True, "owner_id": 2**62}
    response = client.put(permissions, json=body, headers=csrf)
    assert response.status_code == 200 and response.json() == {
        "emails": [email],
        "next_cursor": None,
    }
    assert client.get(permissions).json() == response.json()
    changed = client.get(base + "/settings").json()
    token = uuid4().hex
    recipient = {**data, "email": email, "tag": "t" + token[:28]}
    assert client.post("/v1/auth/register", json=recipient, headers=csrf).status_code == 201
    repeated = client.put(permissions, json=body, headers=csrf)
    assert repeated.status_code == 200 and repeated.json() == response.json()
    assert client.get(base + "/settings").json() == changed
    assert login(client, csrf, recipient).status_code == 200
    view = client.get(base)
    assert view.status_code == 200
    assert (
        view.json()["title"] == "private-marker"
        and view.json()["description"] == draft["description"]
    )
    assert view.json()["qualities"] == [{"quality": 1, "range": "sdr"}]
    assert view.json()["channel"] == {
        "id": author["channel_id"],
        "name": data["name"],
        "tag": author["tag"],
    }
    assert not any(
        key in view.text
        for key in (
            email,
            "emails",
            "owner_id",
            "encoded/",
            "artifact",
            "error_code",
            "job_id",
            "fingerprint",
        )
    )
    assert client.get(permissions).status_code == 404
    assert client.get(base + "/master.m3u8").status_code == 404
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert client.get(base).status_code == 404
    assert login(client, csrf, data).status_code == 200
    assert client.put(
        permissions, json={"email": email, "allowed": False}, headers=csrf
    ).json() == {"emails": [], "next_cursor": None}
    assert login(client, csrf, recipient).status_code == 200
    denied = client.get(base)
    assert denied.status_code == 404 and "private-marker" not in denied.text
    assert "<script>" not in denied.text and email not in caplog.text
    for value in (response, view, denied):
        assert value.headers["cache-control"] == "no-store"
        assert "cookie" in value.headers["vary"].lower()


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_foreign_permissions_and_view_are_indistinguishable_from_missing(
    client, csrf, owner, published, role
):
    data, _ = owner
    base, draft = published
    token = uuid4().hex
    other = {**data, "email": token + "@example.com", "tag": "t" + token[:28]}
    assert client.post("/v1/auth/register", json=other, headers=csrf).status_code == 201

    async def grant():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(other["email"], role)

    client.portal.call(grant)
    assert login(client, csrf, other).json()["role"] == role
    for suffix in ("", "/permissions"):
        with patch.object(GetPublicChannels, "__call__") as channels:
            own = client.get(base + suffix)
            missing = client.get(f"/v1/videos/{2**62}" + suffix)
        channels.assert_not_awaited()
        assert own.status_code == missing.status_code == 404
        assert {k: v for k, v in own.json().items() if k != "request_id"} == {
            k: v for k, v in missing.json().items() if k != "request_id"
        }
        assert "private-marker" not in own.text
        assert "channel" not in own.json()
    assert (
        client.put(
            base + "/permissions", json={"email": other["email"], "allowed": True}, headers=csrf
        ).status_code
        == 404
    )
    assert login(client, csrf, data).status_code == 200
    assert client.get(base + "/permissions").json() == {"emails": [], "next_cursor": None}


@pytest.mark.parametrize(
    "body",
    [
        {"email": "bad", "allowed": True},
        {"email": 1, "allowed": True},
        {"email": "test@example.com", "allowed": "false"},
        {"email": "test@example.com", "allowed": 1},
        {"email": "test@example.com", "allowed": None},
        {"email": "test@example.com"},
        {"email": "a\x00@example.com", "allowed": True},
        {"email": "<script>@example.com", "allowed": True},
        {"email": "a@example.com\nBcc: other@example.com", "allowed": True},
    ],
)
def test_invalid_permission_inputs_never_change_settings(client, csrf, owner, body):
    draft = create(client, csrf)
    base = f"/v1/videos/{draft['id']}"
    result = client.put(base + "/permissions", json=body, headers=csrf)
    assert result.status_code == 422
    assert client.get(base + "/permissions").json() == {"emails": [], "next_cursor": None}
    assert client.get(base + "/settings").json() == draft


def test_csrf_and_lost_session_never_grant_private_access(client, csrf, owner, published):
    base, _ = published
    command = {"email": "test@example.com", "allowed": True}
    assert client.put(base + "/permissions", json=command).status_code == 403
    assert client.get(base + "/permissions").json() == {"emails": [], "next_cursor": None}
    cache = client.app.state.resources.cache
    assert client.portal.call(cache.delete, "sessions." + client.cookies["session"]) == 1
    assert client.get(base).status_code == 404
    assert client.get(base + "/permissions").status_code == 401
    assert client.put(base + "/permissions", json=command, headers=csrf).status_code == 401


def test_public_guest_then_private_hidden_and_missing_quality_fail_closed(
    client, csrf, owner, published
):
    data, author = owner
    base, draft = published
    expected_channel = {"id": author["channel_id"], "name": data["name"], "tag": author["tag"]}
    assert client.get(base).json()["channel"] == expected_channel

    async def change(values):
        allowed = {
            "visibility": {"public", "private"},
            "moderation": {"hidden", "visible"},
            "publication": {"draft", "published"},
        }
        async with client.app.state.resources.sessions.begin() as session:
            for key, value in values.items():
                assert value in allowed[key]
                statements = {
                    "visibility": text(
                        "UPDATE video_publications SET visibility=:value WHERE id=:id"
                    ),
                    "moderation": text(
                        "UPDATE video_publications SET moderation=:value WHERE id=:id"
                    ),
                    "publication": text(
                        "UPDATE video_publications SET publication=:value WHERE id=:id"
                    ),
                }
                await session.execute(statements[key], {"value": value, "id": draft["id"]})

    client.portal.call(change, {"visibility": "public"})
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    public = client.get(base)
    assert public.status_code == 200
    assert public.json()["channel"] == expected_channel
    profile = client.get(f"/v1/channels/{expected_channel['tag']}")
    assert profile.status_code == 200
    assert {key: profile.json()[key] for key in expected_channel} == expected_channel
    client.portal.call(change, {"visibility": "private"})
    assert client.get(base).status_code == 404
    assert login(client, csrf, data).status_code == 200
    for values in ({"moderation": "hidden"}, {"moderation": "visible", "publication": "draft"}):
        client.portal.call(change, values)
        with patch.object(GetPublicChannels, "__call__") as channels:
            denied = client.get(base)
        assert denied.status_code == 404 and "channel" not in denied.json()
        channels.assert_not_awaited()
        assert client.get(base + "/settings").status_code == 200


@pytest.mark.parametrize("unavailable", ["missing", "failure"])
def test_author_lookup_never_returns_partial_video(client, csrf, owner, published, unavailable):
    base, _ = published
    with patch.object(GetPublicChannels, "__call__") as channels:
        if unavailable == "missing":
            channels.return_value = {}
        else:
            channels.side_effect = SQLAlchemyError("synthetic author lookup marker")
        response = client.get(base)
    channels.assert_awaited_once()
    assert response.status_code == (404 if unavailable == "missing" else 500)
    assert not {"id", "description", "channel", "qualities", "hls_url"}.intersection(
        response.json()
    )
    assert "private-marker" not in response.text and "synthetic" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "cookie" in response.headers["vary"].lower()


def test_openapi_requires_public_author_identity_on_ordinary_view(client):
    schema = client.get("/openapi.json").json()
    response = schema["paths"]["/v1/videos/{video_id}"]["get"]["responses"]["200"]
    name = response["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
    details = schema["components"]["schemas"][name]
    assert "channel" in details["required"]
    channel_ref = details["properties"]["channel"]["$ref"].rsplit("/", 1)[-1]
    channel = schema["components"]["schemas"][channel_ref]
    assert set(channel["required"]) == set(channel["properties"]) == {"id", "name", "tag"}
    assert "channel" not in schema["components"]["schemas"]["VideoViewResponse"]["properties"]


def test_database_failure_cannot_return_cached_private_metadata(client, csrf, owner, published):
    base, _ = published
    assert client.get(base).status_code == 200
    original = AsyncSession.execute

    async def fail(session, statement, *args, **kwargs):
        if "SELECT" in str(statement) and "video_private_grants" in str(statement):
            raise SQLAlchemyError("synthetic private error marker")
        return await original(session, statement, *args, **kwargs)

    with patch.object(AsyncSession, "execute", fail):
        response = client.get(base)
    assert response.status_code in {500, 503}
    assert "private-marker" not in response.text and "synthetic" not in response.text
    assert client.get(base).status_code == 200


def test_publish_requires_upload_owner_session_and_csrf(client, csrf, owner):
    data, _ = owner
    draft = create(client, csrf)
    base = f"/v1/videos/{draft['id']}"
    assert client.post(base + "/publish").status_code == 403
    response = client.post(base + "/publish", headers=csrf)
    assert response.status_code == 409 and response.json()["code"] == "upload_not_completed"
    assert client.get(base + "/settings").json() == draft
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert client.post(base + "/publish", headers=csrf).status_code == 401
    assert login(client, csrf, data).status_code == 200


def test_playlists_head_internal_delivery_and_immediate_session_loss(
    client, csrf, owner, published
):
    base, _ = published
    view = client.get(base).json()
    assert view["hls_url"] == "/api" + base + "/hls/master.m3u8"
    master = client.get(base + "/hls/master.m3u8")
    assert master.status_code == 200 and master.text.startswith("#EXTM3U")
    assert "tracks/v1/index.m3u8" in master.text and "encoded/" not in master.text
    playlist = client.get(base + "/hls/tracks/v1/index.m3u8")
    assert playlist.status_code == 200 and "segment_000000.m4s" in playlist.text
    for suffix in ("/hls/master.m3u8", "/hls/tracks/v1/index.m3u8"):
        head = client.head(base + suffix)
        assert head.status_code == 200 and head.content == b""
        assert head.headers["content-length"] == client.get(base + suffix).headers["content-length"]
    for suffix in ("/hls/tracks/v1/init.mp4", "/hls/tracks/v1/segment_000000.m4s", "/preview.webp"):
        response = client.get(base + suffix)
        assert response.status_code == 200 and response.content == b""
        assert response.headers["x-accel-redirect"].startswith("/_media_storage/")
        assert response.headers["cache-control"] == "no-store"
    # TestClient sees internal hand-off headers. docker/check_delivery verifies
    # that the real browser sees only Nginx bytes, never these signatures.
    before = client.get(base + "/settings").json()
    assert client.post(base + "/publish", headers=csrf).json() == before
    cache = client.app.state.resources.cache
    assert client.portal.call(cache.delete, "sessions." + client.cookies["session"]) == 1
    for suffix in (
        "/hls/master.m3u8",
        "/hls/tracks/v1/index.m3u8",
        "/hls/tracks/v1/init.mp4",
        "/preview.webp",
    ):
        response = client.get(base + suffix)
        assert response.status_code == 404 and "x-accel-redirect" not in response.headers
