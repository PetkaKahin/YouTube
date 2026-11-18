"""Watch saves use ordinary viewing and own playlists, independent of Studio ownership."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError

from tests import test_watch_progress_http as support
from tests.test_library_http import clones
from tests.test_watch_progress_http import check, login, sql
from user.public import GetAccount, SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture(scope="module")
def media(backend_runtime):
    yield from support.media.__wrapped__(backend_runtime)


@pytest.fixture(autouse=True)
def reset(media, client):
    support.reset.__wrapped__(media, client)
    sql(client, "DELETE FROM channel_playlist_entries")
    sql(client, "DELETE FROM channel_playlists")
    sql(client, "DELETE FROM video_likes")


@pytest.fixture
def viewer(client, csrf, media):
    login(client, csrf, media.people[1])
    return media.people[1]


def create(client, csrf, video=None, visibility="private", title="Saved playlist"):
    body = {"title": title, "description": "", "visibility": visibility}
    if video is not None:
        body["video_id"] = video
    response = client.post("/v1/playlists", json=body, headers=csrf)
    check(response, 201)
    return response.json()


def save(client, csrf, playlist, video, selected=True):
    return client.put(
        f"/v1/playlists/{playlist}/videos/{video}", json={"selected": selected}, headers=csrf
    )


def listing(client, video, **query):
    return client.get(f"/v1/videos/{video}/saved-playlists", params=query)


def selection(client, video):
    response = listing(client, video)
    check(response)
    return response.json()


def test_auth_csrf_body_boundaries_and_openapi(client, csrf, media, viewer):
    video = media.videos[0]
    playlist = create(client, csrf)
    assert selection(client, video) == {
        "items": [{**playlist, "selected": False}],
        "next_cursor": None,
        "saved": False,
    }
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    for query in ({}, {"cursor": "bad"}, {"owner_id": "1"}):
        check(listing(client, video, **query), 401)
    check(save(client, csrf, playlist["id"], video), 401)
    check(
        client.post(
            "/v1/playlists",
            json={"title": "No", "visibility": "public", "video_id": video},
            headers=csrf,
        ),
        401,
    )
    login(client, csrf, viewer)
    check(
        client.put(f"/v1/playlists/{playlist['id']}/videos/{video}", json={"selected": True}), 403
    )
    check(
        client.post(
            "/v1/playlists", json={"title": "No", "visibility": "public", "video_id": video}
        ),
        403,
    )
    for body in (
        {},
        {"selected": "true"},
        {"selected": 1},
        {"selected": None},
        {"selected": True, "owner_id": viewer.id},
        {"selected": True, "role": "admin"},
    ):
        check(
            client.put(f"/v1/playlists/{playlist['id']}/videos/{video}", json=body, headers=csrf),
            422,
        )
    for identity in ("0", "-1", str(2**63), "1;DROP TABLE videos"):
        check(
            client.put(
                f"/v1/playlists/{playlist['id']}/videos/{identity}",
                json={"selected": True},
                headers=csrf,
            ),
            422,
        )
        check(
            client.post(
                "/v1/playlists",
                json={"title": "No", "visibility": "public", "video_id": identity},
                headers=csrf,
            ),
            422,
        )
    schema = client.get("/openapi.json").json()
    for path, method in (
        ("/v1/videos/{video_id}/saved-playlists", "get"),
        ("/v1/playlists/{playlist_id}/videos/{video_id}", "put"),
        ("/v1/playlists", "post"),
    ):
        assert (
            schema["paths"][path][method]["security"] == [{"SessionCookie": []}]
            if method == "get"
            else schema["paths"][path][method]["security"]
            == [{"SessionCookie": [], "CSRFToken": []}]
        )
    definitions = schema["components"]["schemas"]
    assert definitions["SavedSelectionBody"]["additionalProperties"] is False
    assert "saved" in definitions["SavedPlaylistListResponse"]["required"]
    assert "video_id" in definitions["PlaylistBody"]["properties"]
    assert set(definitions["PlaylistResponse"]["properties"]) == set(playlist)
    assert sql(client, "SELECT count(*) FROM channel_playlist_entries") == 0


@pytest.mark.parametrize("visibility", ["public", "unlisted", "private"])
def test_foreign_public_create_save_content_and_channel_paths(
    client, csrf, media, viewer, visibility
):
    first, second = media.videos
    assert selection(client, first) == {"items": [], "next_cursor": None, "saved": False}
    before = sql(
        client,
        "SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM video_publications p WHERE id=ANY(:ids)",
        {"ids": media.videos},
    )
    playlist = create(client, csrf, first, visibility, "<script>Saved; SELECT</script>")
    assert selection(client, first) == {
        "items": [{**playlist, "selected": True}],
        "next_cursor": None,
        "saved": True,
    }
    assert type(playlist["id"]) is int
    response = save(client, csrf, playlist["id"], second)
    check(response)
    assert response.json() == {
        "playlist_id": playlist["id"],
        "video_id": second,
        "selected": True,
        "saved": True,
    }
    path = f"/v1/playlists/{playlist['id']}"
    value = client.get(path)
    check(value)
    expected_channels = [
        client.get(f"/v1/channels/{person.tag}").json()["id"] for person in media.people
    ]
    assert [card["id"] for card in value.json()["items"]] == [first, second]
    assert [card["channel"]["id"] for card in value.json()["items"]] == expected_channels
    assert value.json()["channel"]["id"] == expected_channels[1]
    assert all(card["watch_progress"] is None for card in value.json()["items"])
    assert playlist["id"] in [row["id"] for row in client.get("/v1/playlists/mine").json()["items"]]
    channel_list = client.get(f"/v1/channels/{viewer.tag}/playlists")
    check(channel_list)
    assert [row["id"] for row in channel_list.json()["items"]] == (
        [playlist["id"]] if visibility == "public" else []
    )
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    guest = client.get(path)
    check(guest, 404 if visibility == "private" else 200)
    if visibility != "private":
        assert [card["channel"]["id"] for card in guest.json()["items"]] == expected_channels
    assert (
        sql(
            client,
            "SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM video_publications p "
            "WHERE id=ANY(:ids)",
            {"ids": media.videos},
        )
        == before
    )
    assert sql(client, "SELECT count(*) FROM video_watch_progress") == 0
    assert sql(client, "SELECT count(*) FROM video_likes") == 0


def test_single_pairs_idempotence_dates_and_reinsert_order(client, csrf, media, viewer):
    first, second = media.videos
    one, two = create(client, csrf, visibility="public"), create(client, csrf)
    check(save(client, csrf, one["id"], first))
    check(save(client, csrf, one["id"], second))
    check(save(client, csrf, two["id"], first))
    before = sql(client, "SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM channel_playlists p")
    entries = sql(
        client, "SELECT jsonb_agg(to_jsonb(e) ORDER BY id) FROM channel_playlist_entries e"
    )
    check(save(client, csrf, one["id"], first))
    check(save(client, csrf, two["id"], second, False))
    assert (
        sql(client, "SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM channel_playlists p") == before
    )
    assert (
        sql(client, "SELECT jsonb_agg(to_jsonb(e) ORDER BY id) FROM channel_playlist_entries e")
        == entries
    )
    removed = save(client, csrf, one["id"], first, False)
    check(removed)
    assert removed.json() == {
        "playlist_id": one["id"],
        "video_id": first,
        "selected": False,
        "saved": True,
    }
    check(save(client, csrf, one["id"], first))
    content = client.get(f"/v1/playlists/{one['id']}")
    check(content)
    assert [row["id"] for row in content.json()["items"]] == [second, first]
    check(save(client, csrf, one["id"], first, False))
    last = save(client, csrf, two["id"], first, False)
    check(last)
    assert last.json()["saved"] is False
    assert not selection(client, first)["saved"]


def test_pages_exact_bigints_cursor_scopes_and_saved_beyond_first_page(client, csrf, media, viewer):
    date = datetime(2026, 1, 1, tzinfo=UTC)
    channel = client.get(f"/v1/channels/{viewer.tag}").json()["id"]

    async def populate():
        async with client.app.state.resources.sessions.begin() as session:
            ids = list(
                await session.scalars(
                    text(
                        "INSERT INTO channel_playlists "
                        "(id,channel_id,title,description,visibility,created_at,updated_at) "
                        "SELECT :offset+g,:channel,'Playlist','',"
                        "(ARRAY['public','unlisted','private'])[1+g%3],:date,:date "
                        "FROM generate_series(1,21) g RETURNING id"
                    ),
                    {"offset": 2**53 + 501, "channel": channel, "date": date},
                )
            )
            return sorted(ids, reverse=True)

    expected = client.portal.call(populate)
    video, other_video = media.videos
    check(save(client, csrf, expected[-1], video))
    queries = []
    engine = client.app.state.resources.engine.sync_engine

    def capture(connection, cursor, statement, parameters, context, many):
        if "channel_playlist_entries" in statement:
            queries.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        first = listing(client, video)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    check(first)
    assert len(queries) == 1
    value = first.json()
    assert len(value["items"]) == 20 and value["saved"] and value["next_cursor"]
    assert not any(row["selected"] for row in value["items"])
    token = value["next_cursor"]
    second = listing(client, video, cursor=token)
    check(second)
    assert second.json()["saved"] and second.json()["next_cursor"] is None
    assert second.json()["items"][0]["selected"]
    assert [row["id"] for row in value["items"] + second.json()["items"]] == expected
    assert all(type(row["id"]) is int and row["id"] > 2**53 for row in value["items"])
    for query in (
        {"cursor": token + "="},
        {"cursor": "' OR TRUE; --"},
        {"cursor": "x" * 10000},
        {"owner_id": viewer.id},
        {"cursor": ""},
    ):
        check(listing(client, video, **query), 422)
    check(
        client.get(
            f"/v1/videos/{video}/saved-playlists", params=[("cursor", token), ("cursor", token)]
        ),
        422,
    )
    check(listing(client, other_video, cursor=token), 422)
    check(client.get("/v1/playlists/mine", params={"cursor": token}), 422)
    check(client.get(f"/v1/videos/{other_video}/playlists", params={"cursor": token}), 422)
    login(client, csrf, media.people[0])
    check(listing(client, video, cursor=token), 422)
    check(save(client, csrf, expected[0], video), 404)
    assert selection(client, video)["items"] == []


def test_private_grant_and_revocation_preserve_saved_pairs(client, csrf, media, viewer):
    video = media.videos[0]
    playlist = create(client, csrf, visibility="public")
    check(save(client, csrf, playlist["id"], video))
    sql(client, "UPDATE video_publications SET visibility='private' WHERE id=:id", {"id": video})
    for path in (f"/v1/playlists/{playlist['id']}",):
        response = client.get(path)
        check(response)
        assert response.json()["items"] == []
    check(listing(client, video), 404)
    check(save(client, csrf, playlist["id"], video, False), 404)

    async def make_admin():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(viewer.email, "admin")

    client.portal.call(make_admin)
    check(listing(client, video), 404)
    check(save(client, csrf, playlist["id"], video), 404)
    login(client, csrf, media.people[0])
    check(
        client.put(
            f"/v1/videos/{video}/permissions",
            json={"email": viewer.email, "allowed": True},
            headers=csrf,
        )
    )
    login(client, csrf, viewer)
    assert selection(client, video)["saved"]
    check(save(client, csrf, playlist["id"], video))
    assert [row["id"] for row in client.get(f"/v1/playlists/{playlist['id']}").json()["items"]] == [
        video
    ]
    login(client, csrf, media.people[0])
    check(
        client.put(
            f"/v1/videos/{video}/permissions",
            json={"email": viewer.email, "allowed": False},
            headers=csrf,
        )
    )
    login(client, csrf, viewer)
    check(listing(client, video), 404)
    assert (
        sql(
            client,
            "SELECT count(*) FROM channel_playlist_entries WHERE playlist_id=:id",
            {"id": playlist["id"]},
        )
        == 1
    )
    sql(client, "UPDATE video_publications SET visibility='public' WHERE id=:id", {"id": video})
    assert selection(client, video)["saved"]


@pytest.mark.parametrize("state", ["hidden", "draft", "unready", "missing"])
def test_unavailable_video_rejects_all_save_paths_and_leaves_no_playlist(
    client, csrf, media, viewer, state
):
    video = media.videos[0]
    playlist = create(client, csrf)
    if state == "hidden":
        sql(client, "UPDATE video_publications SET moderation='hidden' WHERE id=:id", {"id": video})
    elif state == "draft":
        sql(client, "UPDATE video_publications SET publication='draft' WHERE id=:id", {"id": video})
    elif state == "unready":
        sql(
            client,
            "UPDATE video_media_plans SET status='pending' WHERE upload_id="
            "(SELECT id FROM video_uploads WHERE video_id=:id AND status='uploaded')",
            {"id": video},
        )
    else:
        video = 2**62
    check(listing(client, video), 404)
    check(save(client, csrf, playlist["id"], video), 404)
    response = client.post(
        "/v1/playlists",
        json={"title": "Forbidden creation", "visibility": "private", "video_id": video},
        headers=csrf,
    )
    check(response, 404)
    assert "items" not in response.json() and "saved" not in response.json()
    assert sql(client, "SELECT count(*) FROM channel_playlists") == 1
    assert sql(client, "SELECT count(*) FROM channel_playlist_entries") == 0


def test_studio_full_set_preserves_other_accounts_saves(client, csrf, media, viewer):
    video = media.videos[0]
    own = create(client, csrf, video)
    check(client.get(f"/v1/videos/{video}/playlists"), 404)
    check(
        client.put(
            f"/v1/videos/{video}/playlists", json={"playlist_ids": [own["id"]]}, headers=csrf
        ),
        404,
    )
    login(client, csrf, media.people[0])
    author = create(client, csrf)
    for ids in ([author["id"]], []):
        check(client.put(f"/v1/videos/{video}/playlists", json={"playlist_ids": ids}, headers=csrf))
        assert (
            sql(
                client,
                "SELECT count(*) FROM channel_playlist_entries WHERE playlist_id=:id",
                {"id": own["id"]},
            )
            == 1
        )
    login(client, csrf, viewer)
    assert selection(client, video)["saved"]


def test_concurrent_distinct_pairs_are_not_lost(client, csrf, media, viewer):
    first, second = media.videos
    one, two = create(client, csrf), create(client, csrf)
    requests = [
        (one["id"], first),
        (one["id"], second),
        (two["id"], first),
        (two["id"], second),
    ] * 2
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda pair: save(client, csrf, pair[0], pair[1]), requests))
    for response in responses:
        check(response)
    assert sql(client, "SELECT count(*) FROM channel_playlist_entries") == 4
    assert selection(client, first)["saved"] and selection(client, second)["saved"]


@pytest.mark.parametrize(
    "fail_at", ["INSERT INTO channel_playlist_entries", "UPDATE channel_playlists"]
)
def test_create_and_save_roll_back_on_insert_or_followup_failure(
    client, csrf, media, viewer, fail_at
):
    engine = client.app.state.resources.engine.sync_engine

    def fail(connection, cursor, statement, parameters, context, many):
        if statement.startswith(fail_at):
            raise SQLAlchemyError("private-save-error-marker")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        response = client.post(
            "/v1/playlists",
            json={"title": "Must roll back", "visibility": "public", "video_id": media.videos[0]},
            headers=csrf,
        )
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    check(response, 500)
    assert "private-save-error-marker" not in response.text
    assert sql(client, "SELECT count(*) FROM channel_playlists") == 0
    assert sql(client, "SELECT count(*) FROM channel_playlist_entries") == 0


def test_foreign_author_contents_filter_before_page_and_keep_cursor(client, csrf, media, viewer):
    first = clones(client, media, media.people[0].id, 12)
    second = clones(client, media, media.people[1].id, 11)
    blocked = clones(client, media, media.people[0].id, 43)
    playlist = create(client, csrf, visibility="public")
    sql(
        client,
        "INSERT INTO channel_playlist_entries (playlist_id,video_id) "
        "SELECT :playlist,id FROM video_publications WHERE id=ANY(:ids) ORDER BY id",
        {"playlist": playlist["id"], "ids": blocked},
    )
    sql(
        client,
        "UPDATE video_publications SET visibility='private' WHERE id=ANY(:ids)",
        {"ids": blocked},
    )
    expected = first + second
    for video in expected:
        check(save(client, csrf, playlist["id"], video))
    page, cursor = [], None
    for count in (20, 3):
        response = client.get(
            f"/v1/playlists/{playlist['id']}", params={"cursor": cursor} if cursor else {}
        )
        check(response)
        result = response.json()
        assert len(result["items"]) == count
        page.extend(result["items"])
        cursor = result["next_cursor"]
    assert cursor is None and [card["id"] for card in page] == expected
    for start, end, person in ((0, 12, media.people[0]), (12, 23, media.people[1])):
        assert all(card["channel"]["tag"] == person.tag for card in page[start:end])
    assert not set(blocked) & {card["id"] for card in page}
    assert (
        sql(
            client,
            "SELECT count(*) FROM channel_playlist_entries WHERE playlist_id=:id",
            {"id": playlist["id"]},
        )
        == 66
    )


def test_pair_delete_rolls_back_without_losing_other_saves(client, csrf, media, viewer):
    video = media.videos[0]
    one, two = create(client, csrf, video), create(client, csrf, video)
    entries = sql(
        client, "SELECT jsonb_agg(to_jsonb(e) ORDER BY id) FROM channel_playlist_entries e"
    )
    playlists = sql(client, "SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM channel_playlists p")
    engine = client.app.state.resources.engine.sync_engine

    def fail(connection, cursor, statement, parameters, context, many):
        if statement.startswith("UPDATE channel_playlists"):
            raise SQLAlchemyError("private-delete-error-marker")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        response = save(client, csrf, one["id"], video, False)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    check(response, 500)
    assert "private-delete-error-marker" not in response.text
    assert (
        sql(client, "SELECT jsonb_agg(to_jsonb(e) ORDER BY id) FROM channel_playlist_entries e")
        == entries
    )
    assert (
        sql(client, "SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM channel_playlists p")
        == playlists
    )
    value = selection(client, video)
    assert value["saved"] and {item["id"] for item in value["items"] if item["selected"]} == {
        one["id"],
        two["id"],
    }


def test_list_dependency_failure_is_not_partial_success(client, csrf, media, viewer):
    create(client, csrf, media.videos[0])
    with patch.object(
        GetAccount, "__call__", new=AsyncMock(side_effect=SQLAlchemyError("private-account-marker"))
    ):
        response = listing(client, media.videos[0])
    check(response, 500)
    assert "private-account-marker" not in response.text and "items" not in response.json()


@pytest.mark.parametrize("action", ["pair", "create"])
def test_permission_change_winning_lock_prevents_save_and_create(
    client, csrf, media, viewer, action
):
    video = media.videos[0]
    playlist = create(client, csrf)
    resources = client.app.state.resources

    async def lock():
        session = resources.sessions()
        await session.begin()
        await session.execute(
            text("UPDATE video_publications SET visibility='private' WHERE id=:id"), {"id": video}
        )
        return session

    session = client.portal.call(lock)
    waiting = Event()
    engine = resources.engine.sync_engine

    def capture(connection, cursor, statement, parameters, context, many):
        if "FOR SHARE" in statement and "video_publications" in statement:
            waiting.set()

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = (
                pool.submit(save, client, csrf, playlist["id"], video)
                if action == "pair"
                else pool.submit(
                    client.post,
                    "/v1/playlists",
                    json={"title": "Forbidden", "visibility": "public", "video_id": video},
                    headers=csrf,
                )
            )
            try:
                assert waiting.wait(10)
            finally:
                client.portal.call(session.commit)
            check(pending.result(timeout=10), 404)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        client.portal.call(session.close)
    assert sql(client, "SELECT count(*) FROM channel_playlists") == 1
    assert sql(client, "SELECT count(*) FROM channel_playlist_entries") == 0
