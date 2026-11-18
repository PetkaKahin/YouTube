"""Studio owner lists through real sessions and PostgreSQL, without media encoding."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError

from tests.test_videos_http import create, login
from user.public import SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")
PATH = "/v1/videos/mine"
DATE = datetime(2026, 1, 1, tzinfo=UTC)
TITLE = "<script>alert('title')</script>; SELECT * FROM private"


@pytest.fixture
def owner(client, csrf, registered_account):
    data, account = registered_account
    assert login(client, csrf, data).status_code == 200
    return data, account


def register_other(client, csrf, original):
    token = uuid4().hex
    data = {**original, "email": token + "@example.com", "tag": "t" + token[:24]}
    response = client.post("/v1/auth/register", json=data, headers=csrf)
    assert response.status_code == 201
    return data, response.json()


def seed_rows(client, actor, publication, count=41):
    async def insert():
        async with client.app.state.resources.sessions.begin() as session:
            rows = await session.execute(
                text(
                    "INSERT INTO video_publications "
                    "(id,owner_id,title,description,visibility,publication,upload,processing,"
                    "moderation,created_at,updated_at) "
                    "SELECT :base+g,:actor,:title,'private-description-marker',"
                    "(ARRAY['public','private'])[1+g%2],:publication,"
                    "(ARRAY['pending','uploading','uploaded','failed'])[1+g%4],"
                    "(ARRAY['pending','processing','ready','partial','failed'])[1+g%5],"
                    "(ARRAY['visible','hidden'])[1+g%2],"
                    "CAST(:date AS timestamptz)+(g%3)*INTERVAL '1 second',:date "
                    "FROM generate_series(1,CAST(:count AS integer)) AS g "
                    "RETURNING id,created_at"
                ),
                {
                    "base": 2**53 + actor * 1000 + (100 if publication == "published" else 0),
                    "actor": actor,
                    "title": TITLE,
                    "publication": publication,
                    "date": DATE,
                    "count": count,
                },
            )
            return [
                row.id
                for row in sorted(rows, key=lambda row: (row.created_at, row.id), reverse=True)
            ]

    return client.portal.call(insert)


def assert_private(response, status=200):
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "cookie" in response.headers["vary"].lower()


def test_empty_lists_default_and_openapi_contract(client, owner):
    for query in ({}, {"publication": "draft"}, {"publication": "published"}):
        response = client.get(PATH, params=query)
        assert_private(response)
        assert response.json() == {"items": [], "next_cursor": None}
    schema = client.get("/openapi.json").json()
    operation = schema["paths"][PATH]["get"]
    assert operation["security"] == [{"SessionCookie": []}]
    publication = next(row for row in operation["parameters"] if row["name"] == "publication")
    assert publication["schema"]["enum"] == ["published", "draft"]
    assert publication["schema"]["default"] == "published"
    definitions = schema["components"]["schemas"]
    page = definitions["VideoSettingsListResponse"]["properties"]
    assert page["items"]["items"] == {"$ref": "#/components/schemas/VideoSettingsResponse"}
    for field in ("id", "owner_id"):
        assert definitions["VideoSettingsResponse"]["properties"][field]["format"] == "int64"


@pytest.mark.parametrize("publication", ["published", "draft"])
def test_partition_pages_all_states_and_stable_order(client, csrf, owner, publication):
    data, account = owner
    actor = account["account_id"]
    expected = seed_rows(client, actor, publication)
    seed_rows(client, actor, "draft" if publication == "published" else "published")
    _, other = register_other(client, csrf, data)
    seed_rows(client, other["account_id"], publication)
    ids, pages, cursor = [], [], None
    first_cursor = None
    engine = client.app.state.resources.engine.sync_engine
    for page_number in range(3):
        statements = []

        def capture(
            connection, driver_cursor, statement, parameters, context, many, statements=statements
        ):
            if "FROM video_publications" in statement:
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            response = client.get(
                PATH, params={"publication": publication, **({"cursor": cursor} if cursor else {})}
            )
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert_private(response)
        assert len(statements) == 1
        page = response.json()
        pages.append(page["items"])
        ids.extend(row["id"] for row in page["items"])
        cursor = page["next_cursor"]
        assert all(
            row["owner_id"] == actor and row["publication"] == publication for row in page["items"]
        )
        assert all(type(row["id"]) is int and row["id"] > 2**53 for row in page["items"])
        if page_number == 0:
            first_cursor = cursor
            for row in page["items"][:3]:
                assert row == client.get(f"/v1/videos/{row['id']}/settings").json()

            # Changes to upload completion, processing and metadata do not change created_at.
            async def edit():
                async with client.app.state.resources.sessions.begin() as session:
                    await session.execute(
                        text(
                            "UPDATE video_publications SET updated_at=now(),added_at=now(),"
                            "processing='ready' WHERE id=:id"
                        ),
                        {"id": expected[-1]},
                    )

            client.portal.call(edit)
    assert [len(page) for page in pages] == [20, 20, 1]
    assert ids == expected and cursor is None
    rows = [row for page in pages for row in page]
    assert {row["upload"] for row in rows} == {"pending", "uploading", "uploaded", "failed"}
    assert {row["processing"] for row in rows} == {
        "pending",
        "processing",
        "ready",
        "partial",
        "failed",
    }
    assert {row["visibility"] for row in rows} == {"public", "private"}
    assert {row["moderation"] for row in rows} == {"visible", "hidden"}
    assert rows[0]["title"] == TITLE
    other_mode = "draft" if publication == "published" else "published"
    assert_private(
        client.get(PATH, params={"publication": other_mode, "cursor": first_cursor}), 422
    )


def test_authentication_precedes_query_validation_and_lost_session(client, csrf, owner):
    data, _ = owner
    malformed = (
        {"publication": "all"},
        {"publication": "' OR TRUE; --"},
        {"cursor": "' OR TRUE; --"},
        {"cursor": "x" * 10000},
        {"cursor": ""},
        {"owner_id": "1"},
        {"limit": "1000"},
        [("publication", "published"), ("publication", "draft")],
        [("cursor", "a"), ("cursor", "b")],
    )
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    for query in malformed:
        assert_private(client.get(PATH, params=query), 401)
    assert login(client, csrf, data).status_code == 200
    failures = []
    for query in malformed:
        response = client.get(PATH, params=query)
        assert_private(response, 422)
        failures.append(
            {key: value for key, value in response.json().items() if key != "request_id"}
        )
    assert all(row == failures[0] for row in failures)
    cache = client.app.state.resources.cache
    key = "sessions." + client.cookies["session"]
    record = json.loads(client.portal.call(cache.get, key))
    record["user_id"] = 2**62
    client.portal.call(cache.set, key, json.dumps(record))
    assert_private(client.get(PATH, params={"cursor": "bad"}), 401)
    client.portal.call(cache.delete, key)
    assert_private(client.get(PATH), 401)


def test_foreign_cursor_email_grant_and_staff_never_change_owner(client, csrf, owner):
    data, account = owner
    seed_rows(client, account["account_id"], "draft", 21)
    cursor = client.get(PATH, params={"publication": "draft"}).json()["next_cursor"]
    video = create(client, csrf, title="Private owner marker")
    other_data, _ = register_other(client, csrf, data)
    allowed = client.put(
        f"/v1/videos/{video['id']}/permissions",
        json={"email": other_data["email"], "allowed": True},
        headers=csrf,
    )
    assert allowed.status_code == 200
    assert login(client, csrf, other_data).status_code == 200
    own_video = create(client, csrf)
    for role in ("user", "moderator", "admin"):

        async def change_role(role=role):
            async with client.app.state.dishka_container() as operation:
                await (await operation.get(SetRole))(other_data["email"], role)

        client.portal.call(change_role)
        assert login(client, csrf, other_data).status_code == 200
        assert client.get(PATH).json() == {"items": [], "next_cursor": None}
        response = client.get(PATH, params={"publication": "draft"})
        assert_private(response)
        assert [row["id"] for row in response.json()["items"]] == [own_video["id"]]
        rejected = client.get(PATH, params={"publication": "draft", "cursor": cursor})
        assert_private(rejected, 422)
        assert "Private owner marker" not in rejected.text


def test_computed_processing_and_database_failure_are_not_partial(client, csrf, owner):
    video = create(client, csrf, title="secret-video-marker")

    async def failed_source():
        async with client.app.state.resources.sessions.begin() as session:
            await session.execute(
                text(
                    "UPDATE video_publications SET upload='uploaded',processing='processing' "
                    "WHERE id=:id"
                ),
                {"id": video["id"]},
            )
            upload = await session.scalar(
                text(
                    "INSERT INTO video_uploads (video_id,object_key,size,status) "
                    "VALUES (:id,:key,1,'uploaded') RETURNING id"
                ),
                {"id": video["id"], "key": uuid4().hex},
            )
            await session.execute(
                text(
                    "INSERT INTO video_source_processing (upload_id,job_id,status,error_code) "
                    "VALUES (:id,:id,'failed','invalid_source')"
                ),
                {"id": upload},
            )

    client.portal.call(failed_source)
    response = client.get(PATH, params={"publication": "draft"})
    assert_private(response)
    item = response.json()["items"][0]
    assert item["processing"] == "failed"
    assert item == client.get(f"/v1/videos/{video['id']}/settings").json()
    engine = client.app.state.resources.engine.sync_engine

    def fail(connection, cursor, statement, parameters, context, many):
        if "FROM video_publications" in statement:
            raise SQLAlchemyError("private-database-marker")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        response = client.get(PATH, params={"publication": "draft"})
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert_private(response, 500)
    assert not {"items", "next_cursor"} & response.json().keys()
    assert (
        "secret-video-marker" not in response.text
        and "private-database-marker" not in response.text
    )
