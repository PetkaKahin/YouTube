"""Owner-only Video HTTP API with real PostgreSQL, sessions, CSRF and injection probes."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from user.public import SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")


def login(client, csrf, data):
    return client.post(
        "/v1/auth/login", json={"email": data["email"], "password": data["password"]}, headers=csrf
    )


@pytest.fixture
def owner(client, csrf, registered_account):
    data, account = registered_account
    assert login(client, csrf, data).status_code == 200
    return data, account


def metadata(**changes):
    return {
        "title": "  Название видео  ",
        "description": " Текст\r\nс переносами\n ",
        "visibility": "private",
        **changes,
    }


def create(client, csrf, **changes):
    response = client.post("/v1/videos", json=metadata(**changes), headers=csrf)
    assert response.status_code == 201, response.text
    return response.json()


async def publication_count(client, owner_id):
    async with client.app.state.resources.engine.connect() as connection:
        return await connection.scalar(
            text("SELECT count(*) FROM video_publications WHERE owner_id=:owner"),
            {"owner": owner_id},
        )


@pytest.mark.parametrize("visibility", ["public", "private"])
def test_create_read_and_replace_settings_without_publishing(client, csrf, owner, visibility):
    _, account = owner
    value = create(
        client,
        csrf,
        visibility=visibility,
        owner_id=2**62,
        publication="published",
        processing="ready",
        added_at="forged",
    )
    assert value["owner_id"] == account["account_id"]
    assert value["title"] == "Название видео"
    assert value["description"] == metadata()["description"]
    assert value["visibility"] == visibility
    assert {key: value[key] for key in ("publication", "upload", "processing", "moderation")} == {
        "publication": "draft",
        "upload": "pending",
        "processing": "pending",
        "moderation": "visible",
    }
    assert value["added_at"] is None
    assert value["created_at"] == value["updated_at"]
    path = f"/v1/videos/{value['id']}/settings"
    read = client.get(path)
    assert read.status_code == 200 and read.json() == value
    changed = client.put(path, json=metadata(title="Другое", visibility="public"), headers=csrf)
    assert changed.status_code == 200
    assert changed.json()["updated_at"] > value["updated_at"]
    assert changed.json()["created_at"] == value["created_at"]
    assert changed.json()["added_at"] is None
    same = client.put(path, json=metadata(title=" \nДругое\t", visibility="public"), headers=csrf)
    assert same.json() == changed.json()
    for response in (read, changed, same):
        assert response.headers["cache-control"] == "no-store"
        assert "cookie" in response.headers["vary"].lower()
    assert client.get(f"/v1/videos/{value['id']}").status_code == 404


def test_description_is_optional_only_for_creation(client, csrf, owner):
    created = client.post("/v1/videos", json={"title": "v", "visibility": "private"}, headers=csrf)
    assert created.status_code == 201 and created.json()["description"] == ""
    path = f"/v1/videos/{created.json()['id']}/settings"
    response = client.put(path, json={"title": "changed", "visibility": "public"}, headers=csrf)
    assert response.status_code == 422
    assert client.get(path).json() == created.json()


def test_lost_valkey_session_does_not_remove_video_or_open_access(client, csrf, owner):
    data, _ = owner
    draft = create(client, csrf)
    path = f"/v1/videos/{draft['id']}/settings"
    cache = client.app.state.resources.cache
    key = "sessions." + client.cookies["session"]
    assert client.portal.call(cache.delete, key) == 1
    assert client.get(path).status_code == 401
    assert login(client, csrf, data).status_code == 200
    assert client.get(path).json() == draft


@pytest.mark.parametrize("field", ["title", "description"])
def test_surrogate_unicode_is_rejected_without_echo_or_server_error(client, csrf, owner, field):
    response = client.post(
        "/v1/videos",
        content=json.dumps(metadata(**{field: "escaped\ud800surrogate"})),
        headers={**csrf, "Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["errors"] == [{"location": ["body", field], "code": "string_unicode"}]
    assert "surrogate" not in response.text


@pytest.mark.parametrize("title,description", [("я", ""), ("🙂" * 100, "🙂" * 5000)])
def test_exact_unicode_metadata_boundaries(client, csrf, owner, title, description):
    value = create(client, csrf, title=" \t" + title + "\n ", description=description)
    assert value["title"] == title and value["description"] == description


@pytest.mark.parametrize(
    "changes",
    [
        {"title": ""},
        {"title": " \n\t"},
        {"title": "я" * 101},
        {"title": 123},
        {"title": True},
        {"title": None},
        {"description": None},
        {"description": ["text"]},
        {"description": "я" * 5001},
        {"visibility": "unlisted"},
        {"visibility": None},
        {"title": "test\x00"},
        {"description": "test\x00"},
    ],
)
def test_invalid_create_and_edit_leave_database_unchanged(client, csrf, owner, changes):
    _, account = owner
    original = create(client, csrf)
    path = f"/v1/videos/{original['id']}/settings"
    before = client.portal.call(publication_count, client, account["account_id"])
    for method, endpoint in ((client.post, "/v1/videos"), (client.put, path)):
        response = method(endpoint, json=metadata(**changes), headers=csrf)
        assert response.status_code == 422
        assert response.json()["errors"]
        assert client.get(path).json() == original
    assert client.portal.call(publication_count, client, account["account_id"]) == before


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_other_account_cannot_read_or_edit_even_with_staff_role(client, csrf, owner, role):
    data, _ = owner
    draft = create(
        client, csrf, title="private-title-marker", description="private-description-marker"
    )
    token = uuid4().hex
    stranger = {**data, "email": token + "@example.com", "tag": "t" + token[:28]}
    assert client.post("/v1/auth/register", json=stranger, headers=csrf).status_code == 201

    async def grant():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(stranger["email"], role)

    client.portal.call(grant)
    assert login(client, csrf, stranger).json()["role"] == role
    errors = []
    for video_id in (draft["id"], 2**62):
        path = f"/v1/videos/{video_id}/settings"
        for response in (client.get(path), client.put(path, json=metadata(), headers=csrf)):
            assert response.status_code == 404
            assert "private-title-marker" not in response.text
            assert "private-description-marker" not in response.text
            errors.append({k: v for k, v in response.json().items() if k != "request_id"})
    assert all(error == errors[0] for error in errors)
    assert login(client, csrf, data).status_code == 200
    assert client.get(f"/v1/videos/{draft['id']}/settings").json() == draft


def test_guest_forged_identity_csrf_and_logout_cannot_access_settings(client, csrf, owner):
    _, account = owner
    draft = create(client, csrf)
    path = f"/v1/videos/{draft['id']}/settings"
    no_token = client.put(path, json=metadata(title="forbidden"))
    assert no_token.status_code == 403
    bad_token = client.put(
        path, json=metadata(title="forbidden"), headers={"X-CSRFToken": "forged"}
    )
    assert bad_token.status_code == 403
    assert client.get(path).json() == draft
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    forged = {
        **csrf,
        "X-User-ID": str(account["account_id"]),
        "X-Role": "admin",
        "Authorization": "Bearer forged",
    }
    for response in (
        client.get(path, headers=forged),
        client.put(path, json=metadata(), headers=forged),
        client.post("/v1/videos", json=metadata(owner_id=account["account_id"]), headers=forged),
    ):
        assert response.status_code == 401
        assert draft["title"] not in response.text
    assert client.portal.call(publication_count, client, account["account_id"]) == 1


def test_sql_and_html_are_literal_data_and_never_enter_request_logs(client, csrf, owner, caplog):
    data, _ = owner
    payload = "x'); DROP TABLE video_publications;-- <script>alert(1)</script>"
    with caplog.at_level(logging.INFO, logger="app.requests"):
        draft = create(client, csrf, title=payload, description=payload)
        path = f"/v1/videos/{draft['id']}/settings"
        changed = client.put(
            path, json=metadata(title="new " + payload, description=payload), headers=csrf
        )
        assert changed.status_code == 200
        assert client.get(path).json() == changed.json()
    assert changed.json()["description"] == payload
    assert changed.headers["content-type"].startswith("application/json")
    assert payload not in caplog.text
    for sensitive in (
        client.cookies["session"],
        client.cookies["csrftoken"],
        data["password"],
        data["email"],
    ):
        assert sensitive not in caplog.text
        assert sensitive not in changed.text
    assert create(client, csrf)["id"] != draft["id"]


@pytest.mark.parametrize(
    "video_id", ["0", "-1", "9223372036854775808", "1;DROP TABLE video_publications", "true"]
)
def test_invalid_path_ids_do_not_reach_database(client, owner, video_id):
    statements = []

    def query(*args):
        statements.append(args[2])

    engine = client.app.state.resources.engine.sync_engine
    event.listen(engine, "before_cursor_execute", query)
    try:
        response = client.get(f"/v1/videos/{video_id}/settings")
    finally:
        event.remove(engine, "before_cursor_execute", query)
    assert response.status_code == 422
    assert not statements


@pytest.mark.parametrize("subject", ["1", True, 0, -1, 2**63, [1], {"id": 1}, 2**62])
def test_corrupted_or_absent_account_in_session_is_rejected(client, csrf, owner, subject):
    draft = create(client, csrf)
    cache = client.app.state.resources.cache
    key = "sessions." + client.cookies["session"]
    record = json.loads(client.portal.call(cache.get, key))
    record["user_id"] = subject
    client.portal.call(cache.set, key, json.dumps(record))
    path = f"/v1/videos/{draft['id']}/settings"
    for response in (
        client.get(path),
        client.put(path, json=metadata(), headers=csrf),
        client.post("/v1/videos", json=metadata(), headers=csrf),
    ):
        assert response.status_code == 401
        assert draft["title"] not in response.text


def test_concurrent_identical_http_edits_have_one_metadata_timestamp(client, csrf, owner):
    draft = create(client, csrf)
    path = f"/v1/videos/{draft['id']}/settings"
    with ThreadPoolExecutor(2) as pool:
        responses = list(
            pool.map(
                lambda _: client.put(path, json=metadata(title="Concurrent"), headers=csrf),
                range(2),
            )
        )
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert client.get(path).json() == responses[0].json()


def test_video_openapi_has_int64_and_session_plus_csrf(client):
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/v1/videos"]["post"]["security"] == [
        {"SessionCookie": [], "CSRFToken": []}
    ]
    assert schema["paths"]["/v1/videos/{video_id}/settings"]["put"]["security"] == [
        {"SessionCookie": [], "CSRFToken": []}
    ]
    fields = schema["components"]["schemas"]["VideoSettingsResponse"]["properties"]
    assert fields["id"]["format"] == fields["owner_id"]["format"] == "int64"
