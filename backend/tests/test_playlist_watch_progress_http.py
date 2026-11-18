"""Nonempty playlist cards retain per-viewer progress through the module boundary."""

import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from tests import test_watch_progress_http as support
from tests.test_watch_progress_http import check, login, put, sql

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture(scope="module")
def media(backend_runtime):
    yield from support.media.__wrapped__(backend_runtime)


@pytest.fixture(autouse=True)
def reset(media, client):
    support.reset.__wrapped__(media, client)


@pytest.fixture
def populated_playlist(client, csrf, media, request):
    owner, other = media.people
    # Both prepared videos belong to the playlist author in this fixture.
    sql(
        client,
        "UPDATE video_publications SET owner_id=:owner WHERE id=:id",
        {"owner": owner.id, "id": media.videos[1]},
    )
    try:
        login(client, csrf, owner)
        response = client.post(
            "/v1/playlists",
            json={"title": "Progress playlist", "visibility": getattr(request, "param", "public")},
            headers=csrf,
        )
        assert response.status_code == 201
        playlist = response.json()
        for video in media.videos:
            response = client.put(
                f"/v1/videos/{video}/playlists",
                json={"playlist_ids": [playlist["id"]]},
                headers=csrf,
            )
            check(response)
        listing = client.get("/v1/playlists/mine")
        check(listing)
        playlist = next(row for row in listing.json()["items"] if row["id"] == playlist["id"])
        playlist.pop("selected")
        yield playlist
    finally:
        sql(
            client,
            "UPDATE video_publications SET owner_id=:owner WHERE id=:id",
            {"owner": other.id, "id": media.videos[1]},
        )


def read(client, playlist, expected_queries):
    count = sql(client, "SELECT count(*) FROM video_watch_progress")
    statements = []
    engine = client.app.state.resources.engine.sync_engine

    def capture(connection, cursor, statement, parameters, context, many):
        if "video_watch_progress" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.get(f"/v1/playlists/{playlist['id']}")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    check(response)
    value = response.json()
    assert value["playlist"] == playlist
    assert value["next_cursor"] is None
    assert len(statements) == expected_queries
    assert sql(client, "SELECT count(*) FROM video_watch_progress") == count
    return value["items"]


@pytest.mark.parametrize("populated_playlist", ["public", "unlisted"], indirect=True)
def test_nonempty_playlist_guest_owner_and_other_progress(client, csrf, media, populated_playlist):
    playlist = populated_playlist
    first, second = media.videos
    rows = read(client, playlist, 1)
    assert [row["id"] for row in rows] == [first, second]
    assert all(row["watch_progress"] is None for row in rows)
    saved_owner = put(client, csrf, first, 5).json()
    rows = read(client, playlist, 1)
    assert rows[0]["watch_progress"] == saved_owner and rows[1]["watch_progress"] is None
    login(client, csrf, media.people[1])
    assert all(row["watch_progress"] is None for row in read(client, playlist, 1))
    saved_other = put(client, csrf, first, 2).json()
    rows = read(client, playlist, 1)
    assert rows[0]["watch_progress"] == saved_other
    assert rows[1]["watch_progress"] is None
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    rows = read(client, playlist, 0)
    assert [row["id"] for row in rows] == [first, second]
    assert all(row["watch_progress"] is None for row in rows)
    login(client, csrf, media.people[0])
    assert read(client, playlist, 1)[0]["watch_progress"] == saved_owner


@pytest.mark.parametrize("populated_playlist", ["private"], indirect=True)
def test_private_playlist_with_progress_remains_owner_only(client, csrf, media, populated_playlist):
    saved = put(client, csrf, media.videos[0], 3).json()
    assert read(client, populated_playlist, 1)[0]["watch_progress"] == saved
    login(client, csrf, media.people[1])
    for guest in (False, True):
        if guest:
            assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
        response = client.get(f"/v1/playlists/{populated_playlist['id']}")
        check(response, 404)
        assert "items" not in response.json() and "watch_progress" not in response.text


def test_playlist_filters_private_hidden_and_revoked_video(client, csrf, media, populated_playlist):
    owner, other = media.people
    first, second = media.videos
    saved_owner = put(client, csrf, first, 5).json()
    login(client, csrf, other)
    saved_other = put(client, csrf, first, 2).json()
    sql(client, "UPDATE video_publications SET visibility='private' WHERE id=:id", {"id": first})
    assert [row["id"] for row in read(client, populated_playlist, 1)] == [second]
    login(client, csrf, owner)
    assert read(client, populated_playlist, 1)[0]["watch_progress"] == saved_owner
    response = client.put(
        f"/v1/videos/{first}/permissions",
        json={"email": other.email, "allowed": True},
        headers=csrf,
    )
    check(response)
    login(client, csrf, other)
    assert read(client, populated_playlist, 1)[0]["watch_progress"] == saved_other
    login(client, csrf, owner)
    check(
        client.put(
            f"/v1/videos/{first}/permissions",
            json={"email": other.email, "allowed": False},
            headers=csrf,
        )
    )
    login(client, csrf, other)
    assert [row["id"] for row in read(client, populated_playlist, 1)] == [second]
    sql(client, "UPDATE video_publications SET moderation='hidden' WHERE id=:id", {"id": second})
    assert read(client, populated_playlist, 0) == []


def test_playlist_progress_database_failure_is_not_partial(client, media, populated_playlist):
    engine = client.app.state.resources.engine.sync_engine

    def fail(connection, cursor, statement, parameters, context, many):
        if "video_watch_progress" in statement:
            raise SQLAlchemyError("private-playlist-progress-marker")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        response = client.get(f"/v1/playlists/{populated_playlist['id']}")
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    check(response, 500)
    assert "items" not in response.json() and "playlist" not in response.json()
    assert "private-playlist-progress-marker" not in response.text


def test_progress_is_batched_once_after_multiple_filtered_entry_batches(
    client, csrf, media, populated_playlist
):
    drafts = sql(
        client,
        "WITH added AS (INSERT INTO video_publications (owner_id,title,visibility) "
        "SELECT :owner,'Draft','public' FROM generate_series(1,43) RETURNING id) "
        "SELECT jsonb_agg(id ORDER BY id) FROM added",
        {"owner": media.people[0].id},
    )
    first, second = media.videos
    entries = [first, *drafts[:21], second, *drafts[21:]]
    sql(
        client,
        "DELETE FROM channel_playlist_entries WHERE playlist_id=:id",
        {"id": populated_playlist["id"]},
    )
    sql(
        client,
        "INSERT INTO channel_playlist_entries (playlist_id,video_id) "
        "SELECT :playlist,video FROM unnest(CAST(:videos AS bigint[])) "
        "WITH ORDINALITY AS t(video,position) ORDER BY position",
        {"playlist": populated_playlist["id"], "videos": entries},
    )
    saved = put(client, csrf, first, 2).json()
    rows = read(client, populated_playlist, 1)
    assert [row["id"] for row in rows] == [first, second]
    assert rows[0]["watch_progress"] == saved and rows[1]["watch_progress"] is None
