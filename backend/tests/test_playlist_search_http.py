"""Three-source search, discovery isolation and fresh pagination over real SQL."""

import base64
import struct
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tests import test_feed_http as feed
from tests import test_video_access_http as access
from tests.test_playlists_http import playlist
from tests.test_videos_http import create
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


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_discovery_same_for_owner_staff_and_guest_without_content_reads(client, csrf, owner, role):
    title = uuid4().hex
    values = [
        playlist(client, csrf, visibility, title=title)
        for visibility in ("public", "private", "unlisted")
    ]
    draft = create(client, csrf)
    assert (
        client.put(
            f"/v1/videos/{draft['id']}/playlists",
            headers=csrf,
            json={"playlist_ids": [value["id"] for value in values]},
        ).status_code
        == 200
    )
    # Membership is a real owner change, so compare the current metadata.
    current = client.get(f"/v1/playlists/{values[0]['id']}").json()["playlist"]

    async def promote():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(owner[0]["email"], role)

    client.portal.call(promote)
    original = AsyncSession.execute

    async def deny_contents(session, statement, *args, **kwargs):
        sql = str(statement)
        assert "video_" not in sql and "channel_playlist_entries" not in sql
        return await original(session, statement, *args, **kwargs)

    params = [
        ("q", title),
        ("type", "playlist"),
        ("duration", "long"),
        ("feature", "4k"),
        ("feature", "hdr"),
        ("feature", "subtitles"),
    ]
    with patch.object(AsyncSession, "execute", deny_contents):
        response = client.get("/v1/search", params=params)
    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None and len(body["items"]) == 1
    item = body["items"][0]
    assert item["type"] == "playlist" and item["video"] is None and item["channel"] is None
    assert item["playlist"] == {
        key: current[key] for key in ("id", "title", "description", "created_at", "updated_at")
    } | {
        "channel": {"id": owner[1]["channel_id"], "name": owner[0]["name"], "tag": owner[0]["tag"]}
    }
    assert (
        response.headers["cache-control"] == "no-store"
        and "cookie" in response.headers["vary"].lower()
    )
    assert not any(
        field in response.text
        for field in (
            owner[0]["email"],
            "owner_id",
            "playlist_ids",
            "preview_url",
            "video_count",
            "encoded/",
            "X-Amz",
        )
    )
    # The known unlisted URL still works; that fact never makes it discoverable.
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert client.get("/v1/playlists/" + str(values[2]["id"])).status_code == 200
    assert client.get("/v1/playlists/" + str(values[1]["id"])).status_code == 404
    assert client.get(f"/v1/videos/{draft['id']}").status_code == 404
    with patch(
        "redis.asyncio.Redis.execute_command", side_effect=ConnectionError("synthetic outage")
    ):
        assert client.get("/v1/search", params=params).json() == body
    # Omitted type includes the same public playlist and never its draft.
    assert client.get("/v1/search", params={"q": title}).json() == body


@pytest.mark.parametrize("sort", ["created", "updated"])
def test_mixed_equal_dates_all_source_boundaries_and_cursor_context(
    client, csrf, owner, published, sort
):
    base, draft = published
    assert feed.expose(client, csrf, base).status_code == 200
    title = "Поиск" + uuid4().hex.translate(str.maketrans("0123456789", "abcdefghij"))
    rows = [playlist(client, csrf, "public", title=title) for _ in range(41)]
    date = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)

    async def dates():
        async with client.app.state.resources.sessions.begin() as session:
            await session.execute(
                text("UPDATE user_accounts SET name=:name WHERE id=:id"),
                {"name": title, "id": owner[1]["account_id"]},
            )
            await session.execute(
                text("UPDATE channel_profiles SET created_at=:date WHERE id=:id"),
                {"date": date, "id": owner[1]["channel_id"]},
            )
            await session.execute(
                text(
                    "UPDATE channel_playlists SET created_at=:date,updated_at=:date "
                    "WHERE channel_id=:id"
                ),
                {"date": date, "id": owner[1]["channel_id"]},
            )
            await session.execute(
                text(
                    "UPDATE video_publications SET title=:name,added_at=:date,updated_at=:date "
                    "WHERE id=:id"
                ),
                {"date": date, "name": title, "id": draft["id"]},
            )

    client.portal.call(dates)
    params = {"q": title, "sort": sort}
    pages, cursor = [], None
    while True:
        response = client.get("/v1/search", params=params | ({"cursor": cursor} if cursor else {}))
        assert response.status_code == 200
        page = response.json()
        pages.append(page)
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert [len(page["items"]) for page in pages] == [20, 20, 3]
    expected = [("video", draft["id"]), ("channel", owner[1]["channel_id"])] + [
        ("playlist", row["id"]) for row in reversed(rows)
    ]
    actual = [(item["type"], item[item["type"]]["id"]) for page in pages for item in page["items"]]
    assert actual == expected
    token = pages[0]["next_cursor"]
    binary = struct.Struct(">BqBQq32s")
    version, stamp, kind, identity, anchor, digest = binary.unpack(
        base64.urlsafe_b64decode(token + "==")
    )
    assert version == 1 and kind == 2 and identity == rows[-18]["id"]
    # Legacy code 1/0 still means video/channel, independently of the new type rank.
    for code, boundary, offset in ((1, draft["id"], 1), (0, owner[1]["channel_id"], 2)):
        legacy = (
            base64.urlsafe_b64encode(binary.pack(version, stamp, code, boundary, anchor, digest))
            .decode()
            .rstrip("=")
        )
        result = client.get("/v1/search", params=params | {"cursor": legacy}).json()
        assert [(item["type"], item[item["type"]]["id"]) for item in result["items"]] == expected[
            offset : offset + 20
        ]
    for changed in (
        {"q": "other"},
        {"type": "playlist"},
        {"sort": "updated" if sort == "created" else "created"},
        {"period": "year"},
        {"duration": "short"},
        {"feature": "hd"},
    ):
        assert (
            client.get("/v1/search", params=params | changed | {"cursor": token}).status_code == 422
        )


def test_playlist_failure_discards_mixed_partial_results_and_preserves_state(
    client, csrf, owner, published
):
    base, _ = published
    assert feed.expose(client, csrf, base).status_code == 200
    playlist(client, csrf, "public", title="marker")
    params = {"q": "marker"}
    before = client.get("/v1/search", params=params).json()
    assert {item["type"] for item in before["items"]} == {"video", "playlist"}
    state = client.get("/v1/playlists/mine").json()
    original = AsyncSession.execute

    async def fail(session, statement, *args, **kwargs):
        if "channel_playlists" in str(statement):
            raise SQLAlchemyError("private playlist database detail")
        return await original(session, statement, *args, **kwargs)

    with patch.object(AsyncSession, "execute", fail):
        result = client.get("/v1/search", params=params)
    assert result.status_code == 500 and result.json()["code"] == "internal_error"
    assert "private playlist database detail" not in result.text and "items" not in result.json()
    assert result.headers["cache-control"] == "no-store"
    assert client.get("/v1/search", params=params).json() == before
    assert client.get("/v1/playlists/mine").json() == state
