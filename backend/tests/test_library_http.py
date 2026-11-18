"""R19 personal pages on real PostgreSQL and sessions, reusing prepared media."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError

from tests import test_watch_progress_http as support
from tests.test_watch_progress_http import check, login, put, sql
from user.public import GetPublicNames, SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")
DATE = datetime(2026, 1, 1, tzinfo=UTC)
PATHS = (
    "/v1/channels/subscriptions",
    "/v1/videos/subscriptions",
    "/v1/videos/liked",
    "/v1/videos/history",
)


@pytest.fixture(scope="module")
def media(backend_runtime):
    yield from support.media.__wrapped__(backend_runtime)


@pytest.fixture(autouse=True)
def reset(media, client):
    client.app.state.resources.bucket = media.bucket
    if not hasattr(media, "duration"):
        response = client.get("/v1/videos/feed")
        check(response)
        media.duration = response.json()["items"][0]["duration_seconds"]
    sql(client, "DELETE FROM video_likes")
    sql(client, "DELETE FROM video_watch_progress")
    sql(client, "DELETE FROM channel_subscriptions")
    sql(
        client,
        (
            "UPDATE video_publications SET "
            "publication='draft',visibility='public',moderation='visible'"
        ),
    )
    sql(client, "UPDATE video_media_plans SET status='planned'")


@pytest.fixture
def viewer(client, csrf, registered_account):
    data, account = registered_account
    person = SimpleNamespace(**data, id=account["account_id"], channel_id=account["channel_id"])
    login(client, csrf, person)
    return person


def clones(client, media, owner, count, date=DATE, title="Library video"):
    async def create():
        async with client.app.state.resources.sessions.begin() as session:
            ids = list(
                await session.scalars(
                    text(
                        "INSERT INTO video_publications "
                        "(owner_id,title,description,visibility,publication,upload,processing,moderation,added_at,created_at,updated_at)"
                        " SELECT "
                        ":owner,:title,'fixture','public','published','uploaded','ready','visible',:date,:date,:date"
                        " FROM generate_series(1,CAST(:count AS integer)) RETURNING id"
                    ),
                    {"owner": owner, "title": title, "date": date, "count": count},
                )
            )
            params = {"ids": ids, "base": media.videos[0]}
            for statement in (
                (
                    "INSERT INTO video_uploads "
                    "(video_id,object_key,size,status,original_cleanup_started_at,original_deleted_at)"
                    " SELECT p.id,'library-fixture/'||p.id,b.size,'uploaded',now(),now() "
                    "FROM video_publications p CROSS JOIN video_uploads b WHERE "
                    "p.id=ANY(:ids) AND b.video_id=:base AND b.status='uploaded'"
                ),
                (
                    "INSERT INTO video_source_processing "
                    "(upload_id,job_id,content_id,status,inspection,inspected_at) SELECT "
                    "u.id,900000+u.id*2,s.content_id,s.status,s.inspection,s.inspected_at"
                    " FROM video_uploads u CROSS JOIN video_source_processing s JOIN "
                    "video_uploads b ON b.id=s.upload_id WHERE u.video_id=ANY(:ids) AND "
                    "b.video_id=:base AND b.status='uploaded'"
                ),
                (
                    "INSERT INTO video_source_tracks (upload_id,stream_index,track_id) "
                    "SELECT u.id,s.stream_index,s.track_id FROM video_uploads u CROSS "
                    "JOIN video_source_tracks s JOIN video_uploads b ON b.id=s.upload_id "
                    "WHERE u.video_id=ANY(:ids) AND b.video_id=:base AND "
                    "b.status='uploaded'"
                ),
                (
                    "INSERT INTO video_media_plans (upload_id,job_id,status,config) "
                    "SELECT u.id,900001+u.id*2,m.status,m.config FROM video_uploads u "
                    "CROSS JOIN video_media_plans m JOIN video_uploads b ON "
                    "b.id=m.upload_id WHERE u.video_id=ANY(:ids) AND b.video_id=:base AND"
                    " b.status='uploaded'"
                ),
                (
                    "INSERT INTO video_plan_tracks (upload_id,encoded_id) SELECT "
                    "u.id,r.encoded_id FROM video_uploads u CROSS JOIN video_plan_tracks "
                    "r JOIN video_uploads b ON b.id=r.upload_id WHERE "
                    "u.video_id=ANY(:ids) AND b.video_id=:base AND b.status='uploaded'"
                ),
            ):
                await session.execute(text(statement), params)
            return ids

    return client.portal.call(create)


def pairs(client, actor, ids, duration, date=DATE, *, liked=True):
    params = {"actor": actor, "ids": ids, "date": date, "duration": duration}
    if liked:
        sql(
            client,
            (
                "INSERT INTO video_likes (account_id,video_id,created_at) SELECT "
                ":actor,id,:date FROM video_publications WHERE id=ANY(:ids)"
            ),
            params,
        )
    sql(
        client,
        (
            "INSERT INTO video_watch_progress "
            "(account_id,video_id,position_seconds,duration_seconds,updated_at) "
            "SELECT :actor,id,1.5,:duration,:date FROM video_publications WHERE "
            "id=ANY(:ids)"
        ),
        params,
    )


def follow(client, csrf, tag, value=True):
    response = client.put(
        f"/v1/channels/{tag}/subscription", json={"subscribed": value}, headers=csrf
    )
    check(response)


def like(client, csrf, video, value=True):
    response = client.put(f"/v1/videos/{video}/like", json={"liked": value}, headers=csrf)
    check(response)
    return response.json()


def test_empty_auth_query_shape_and_openapi(client, csrf, viewer):
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    for path in PATHS:
        check(client.get(path), 401)
        check(client.get(path, params={"cursor": "bad", "role": "admin"}), 401)
    login(client, csrf, viewer)
    for path in PATHS:
        response = client.get(path)
        check(response)
        assert response.json() == {"items": [], "next_cursor": None}
        for query in (
            {"cursor": ""},
            {"cursor": "' OR TRUE; --"},
            {"cursor": "x" * 10000},
            {"account_id": "1"},
            {"role": "admin"},
            [("cursor", "a"), ("cursor", "b")],
        ):
            response = client.get(path, params=query)
            check(response, 422)
            assert response.json()["code"] == "invalid_cursor" and "items" not in response.json()
    schema = client.get("/openapi.json").json()
    for path in PATHS:
        operation = schema["paths"][path]["get"]
        assert operation["security"] == [{"SessionCookie": []}]
        assert [item["name"] for item in operation["parameters"]] == ["cursor"]
        expected = (
            "SubscribedChannelsResponse" if path.startswith("/v1/channels") else "FeedResponse"
        )
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/" + expected
        }


def test_subscribed_channel_pages_ties_batch_names_and_unfollow(client, csrf, viewer):
    token = uuid4().hex

    async def seed_channels():
        async with client.app.state.resources.sessions.begin() as session:
            owners = list(
                await session.scalars(
                    text(
                        "INSERT INTO user_accounts (name,email,password_hash) SELECT 'Library"
                        " author',CAST(g AS text)||:domain,b.password_hash FROM "
                        "generate_series(1,41) AS g CROSS JOIN user_accounts b WHERE "
                        "b.id=:actor RETURNING id"
                    ),
                    {"domain": token + "@example.com", "actor": viewer.id},
                )
            )
            channels = list(
                await session.scalars(
                    text(
                        "INSERT INTO channel_profiles (id,owner_id,tag,created_at) SELECT "
                        ":offset+id,id,'lib_'||id,:date FROM user_accounts WHERE "
                        "id=ANY(:owners) RETURNING id"
                    ),
                    {"offset": 2**53, "date": DATE, "owners": owners},
                )
            )
            await session.execute(
                text(
                    "INSERT INTO channel_subscriptions (subscriber_id,channel_id) SELECT "
                    ":actor,id FROM channel_profiles WHERE id=ANY(:ids)"
                ),
                {"actor": viewer.id, "ids": channels},
            )
            return sorted(channels, reverse=True)

    expected = client.portal.call(seed_channels)
    pages, cursor = [], None
    first_cursor = None
    for _ in range(3):
        statements = []

        def capture(
            connection, driver_cursor, statement, parameters, context, many, statements=statements
        ):
            if "FROM user_accounts" in statement:
                statements.append(statement)

        engine = client.app.state.resources.engine.sync_engine
        event.listen(engine, "before_cursor_execute", capture)
        try:
            response = client.get(PATHS[0], params={"cursor": cursor} if cursor else {})
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        check(response)
        assert len(statements) <= 2
        page = response.json()
        pages.append(page["items"])
        cursor = page["next_cursor"]
        first_cursor = first_cursor or cursor
    assert [len(page) for page in pages] == [20, 20, 1] and cursor is None
    assert [row["id"] for page in pages for row in page] == expected
    assert all(
        type(row["id"]) is int and row["id"] > 2**53 and row["created_at"] == row["updated_at"]
        for page in pages
        for row in page
    )
    assert set(pages[0][0]) == {"id", "name", "tag", "created_at", "updated_at"}
    tag = pages[0][0]["tag"]
    follow(client, csrf, tag, False)
    assert [row["id"] for row in client.get(PATHS[0]).json()["items"]] == expected[1:21]
    follow(client, csrf, tag)
    follow(client, csrf, tag)
    assert client.get(PATHS[0]).json()["items"] == pages[0]
    for path in PATHS[1:]:
        check(client.get(path, params={"cursor": first_cursor}), 422)


@pytest.mark.parametrize("kind", ["subscriptions", "liked", "history"])
def test_video_pages_filter_before_cursor_and_isolate(client, csrf, viewer, media, kind):
    valid = clones(client, media, media.people[0].id, 41)
    blocked = clones(
        client, media, media.people[1].id, 43, DATE + timedelta(days=1), "Forbidden library title"
    )
    pairs(client, viewer.id, valid, media.duration)
    pairs(client, viewer.id, blocked, media.duration, DATE + timedelta(days=1))
    if kind == "subscriptions":
        follow(client, csrf, media.people[0].tag)
    else:
        sql(
            client,
            "UPDATE video_publications SET visibility='private' WHERE id=ANY(:ids)",
            {"ids": blocked},
        )
    path = "/v1/videos/" + kind
    pages, cursor, first_cursor = [], None, None
    for _ in range(3):
        statements = []

        def capture(
            connection, driver_cursor, statement, parameters, context, many, statements=statements
        ):
            if "FROM video_watch_progress" in statement:
                statements.append(statement)

        engine = client.app.state.resources.engine.sync_engine
        event.listen(engine, "before_cursor_execute", capture)
        try:
            response = client.get(path, params={"cursor": cursor} if cursor else {})
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        check(response)
        assert "Forbidden library title" not in response.text
        assert len(statements) == 1
        page = response.json()
        pages.append(page["items"])
        cursor = page["next_cursor"]
        first_cursor = first_cursor or cursor
    assert [len(page) for page in pages] == [20, 20, 1] and cursor is None
    assert [row["id"] for page in pages for row in page] == sorted(valid, reverse=True)
    assert all(row["watch_progress"] is not None for page in pages for row in page)
    for other in ("subscriptions", "liked", "history"):
        if other != kind:
            response = client.get("/v1/videos/" + other, params={"cursor": first_cursor})
            check(response, 422)
            assert response.json()["code"] == "invalid_cursor"
    login(client, csrf, media.people[1])
    response = client.get(path)
    check(response)
    assert response.json() == {"items": [], "next_cursor": None}
    check(client.get(path, params={"cursor": first_cursor}), 422)
    login(client, csrf, viewer)
    assert (
        sql(client, "SELECT count(*) FROM video_likes WHERE account_id=:id", {"id": viewer.id})
        == 84
    )
    assert (
        sql(
            client,
            "SELECT count(*) FROM video_watch_progress WHERE account_id=:id",
            {"id": viewer.id},
        )
        == 84
    )
    if kind == "subscriptions":
        follow(client, csrf, media.people[0].tag, False)
        assert client.get(path).json() == {"items": [], "next_cursor": None}


def test_like_dates_and_repeated_progress_order_without_metadata_edits(client, csrf, viewer, media):
    first, second = clones(client, media, media.people[0].id, 2)
    before = sql(
        client,
        "SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM video_publications p WHERE id=ANY(:ids)",
        {"ids": [first, second]},
    )
    assert like(client, csrf, first)["like_count"] == 1
    timestamp = sql(
        client,
        "SELECT created_at FROM video_likes WHERE account_id=:actor AND video_id=:video",
        {"actor": viewer.id, "video": first},
    )
    like(client, csrf, second)
    assert [row["id"] for row in client.get(PATHS[2]).json()["items"]] == [second, first]
    like(client, csrf, first)
    assert (
        sql(
            client,
            "SELECT created_at FROM video_likes WHERE account_id=:actor AND video_id=:video",
            {"actor": viewer.id, "video": first},
        )
        == timestamp
    )
    assert [row["id"] for row in client.get(PATHS[2]).json()["items"]] == [second, first]
    like(client, csrf, first, False)
    assert [row["id"] for row in client.get(PATHS[2]).json()["items"]] == [second]
    like(client, csrf, first)
    assert [row["id"] for row in client.get(PATHS[2]).json()["items"]] == [first, second]
    check(put(client, csrf, first, 5))
    check(put(client, csrf, second, 2))
    assert [row["id"] for row in client.get(PATHS[3]).json()["items"]] == [second, first]
    check(put(client, csrf, first, 0))
    history = client.get(PATHS[3]).json()["items"]
    assert [row["id"] for row in history] == [first, second] and history[0]["watch_progress"][
        "position_seconds"
    ] == 0
    assert (
        sql(
            client,
            "SELECT count(*) FROM video_watch_progress WHERE account_id=:actor",
            {"actor": viewer.id},
        )
        == 2
    )
    positions = sql(
        client,
        (
            "SELECT jsonb_agg(to_jsonb(w) ORDER BY video_id) FROM "
            "video_watch_progress w WHERE account_id=:actor"
        ),
        {"actor": viewer.id},
    )
    likes = sql(
        client,
        (
            "SELECT jsonb_agg(to_jsonb(l) ORDER BY video_id) FROM video_likes l "
            "WHERE account_id=:actor"
        ),
        {"actor": viewer.id},
    )
    for path in PATHS:
        check(client.get(path))
    assert (
        sql(
            client,
            (
                "SELECT jsonb_agg(to_jsonb(w) ORDER BY video_id) FROM "
                "video_watch_progress w WHERE account_id=:actor"
            ),
            {"actor": viewer.id},
        )
        == positions
    )
    assert (
        sql(
            client,
            (
                "SELECT jsonb_agg(to_jsonb(l) ORDER BY video_id) FROM video_likes l "
                "WHERE account_id=:actor"
            ),
            {"actor": viewer.id},
        )
        == likes
    )
    assert (
        sql(
            client,
            (
                "SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM video_publications p "
                "WHERE id=ANY(:ids)"
            ),
            {"ids": [first, second]},
        )
        == before
    )


@pytest.mark.parametrize("kind", ["liked", "history"])
def test_private_grants_hidden_draft_unready_and_retained_records(
    client, csrf, viewer, media, kind
):
    first, second = clones(client, media, media.people[0].id, 2)
    pairs(client, viewer.id, [first, second], media.duration)
    path = "/v1/videos/" + kind
    sql(client, "UPDATE video_publications SET visibility='private' WHERE id=:id", {"id": first})

    async def admin():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(viewer.email, "admin")

    client.portal.call(admin)
    assert [row["id"] for row in client.get(path).json()["items"]] == [second]
    login(client, csrf, media.people[0])
    assert client.get(path).json() == {"items": [], "next_cursor": None}
    check(
        client.put(
            f"/v1/videos/{first}/permissions",
            json={"email": viewer.email, "allowed": True},
            headers=csrf,
        )
    )
    login(client, csrf, viewer)
    assert [row["id"] for row in client.get(path).json()["items"]] == [second, first]
    login(client, csrf, media.people[0])
    check(
        client.put(
            f"/v1/videos/{first}/permissions",
            json={"email": viewer.email, "allowed": False},
            headers=csrf,
        )
    )
    login(client, csrf, viewer)
    for statement in (
        "UPDATE video_publications SET moderation='hidden' WHERE id=:id",
        "UPDATE video_publications SET moderation='visible',publication='draft' WHERE id=:id",
    ):
        sql(client, statement, {"id": second})
        assert client.get(path).json() == {"items": [], "next_cursor": None}
    sql(
        client, "UPDATE video_publications SET publication='published' WHERE id=:id", {"id": second}
    )
    sql(
        client,
        (
            "UPDATE video_media_plans SET status='pending' WHERE "
            "upload_id=(SELECT id FROM video_uploads WHERE video_id=:id AND "
            "status='uploaded')"
        ),
        {"id": second},
    )
    assert client.get(path).json() == {"items": [], "next_cursor": None}
    assert (
        sql(
            client, "SELECT count(*) FROM video_likes WHERE account_id=:actor", {"actor": viewer.id}
        )
        == 2
    )
    assert (
        sql(
            client,
            "SELECT count(*) FROM video_watch_progress WHERE account_id=:actor",
            {"actor": viewer.id},
        )
        == 2
    )


@pytest.mark.parametrize("failure", ["candidates", "names", "progress", "membership", "channels"])
def test_dependency_errors_are_not_empty_or_partial_success(client, csrf, viewer, media, failure):
    identities = clones(client, media, media.people[0].id, 1)
    pairs(client, viewer.id, identities, media.duration)
    follow(client, csrf, media.people[0].tag)
    path = PATHS[1] if failure == "membership" else PATHS[0] if failure == "channels" else PATHS[2]
    engine = client.app.state.resources.engine.sync_engine
    patterns = {
        "candidates": "JOIN video_likes x",
        "progress": "FROM video_watch_progress",
        "membership": "channel_subscriptions",
        "channels": "channel_subscriptions",
    }

    def fail(connection, cursor, statement, parameters, context, many):
        if failure != "names" and patterns[failure] in statement:
            raise SQLAlchemyError("private-library-error-marker")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        if failure == "names":
            with patch.object(
                GetPublicNames,
                "__call__",
                new=AsyncMock(side_effect=SQLAlchemyError("private-library-error-marker")),
            ):
                response = client.get(path)
        else:
            response = client.get(path)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    check(response, 500)
    assert "items" not in response.json() and "private-library-error-marker" not in response.text
