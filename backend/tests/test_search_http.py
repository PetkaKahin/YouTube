"""Search HTTP uses real accounts, private video, CSRF sessions and safe failures."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tests import test_feed_http as feed
from tests import test_video_access_http as access
from tests.test_videos_http import login

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture(scope="module", autouse=True)
def clean_media(backend_runtime):
    yield from access.clean_media.__wrapped__(backend_runtime)


@pytest.fixture(autouse=True)
def reset_feed(client):
    return feed.reset_feed.__wrapped__(client)


@pytest.fixture
def owner(client, csrf, registered_account):
    return feed.owner.__wrapped__(client, csrf, registered_account)


@pytest.fixture
def published(client, csrf, owner, source_queue):
    return feed.published.__wrapped__(client, csrf, owner, source_queue)


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": "   "},
        {"q": "x" * 101},
        {"q": "x\x00"},
        {"q": "x", "type": "collection"},
        {"q": "x", "sort": "relevance"},
        {"q": "x", "duration": "evil"},
        {"q": "x", "period": "yesterday"},
        {"q": "x", "feature": "unknown"},
        {"q": "x", "cursor": "';DROP TABLE user_accounts;--"},
    ],
)
def test_invalid_search_is_generic_and_not_reflected(client, params, caplog):
    response = client.get("/v1/search", params=params)
    assert response.status_code == 422 and response.json()["code"] == "invalid_search"
    assert response.json()["errors"] == [] and response.headers["cache-control"] == "no-store"
    logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if not record.name.startswith(("httpx", "httpcore"))
    )
    for value in params.values():
        if len(value) > 15:
            assert value not in response.text and value not in logs


@pytest.mark.parametrize("key", ["q", "type", "sort", "duration", "period", "cursor"])
def test_repeated_scalar_parameters_are_rejected(client, key):
    assert (
        client.get("/v1/search", params=[("q", "test"), (key, "a"), (key, "b")]).status_code == 422
    )


@pytest.mark.parametrize(
    "query",
    [
        "a",
        "x" * 100,
        "%;_\\!",
        "<script>alert(1)</script>",
        "' OR 1=1--",
        "../run/secrets",
        "http://rustfs:9000/",
    ],
)
def test_literal_data_does_not_execute_or_become_a_pattern(client, query):
    response = client.get("/v1/search", params={"q": query, "type": "video"})
    assert response.status_code == 200 and response.json() == {"items": [], "next_cursor": None}


def test_public_video_fields_private_invitation_and_fresh_hidden_rules(
    client, csrf, owner, published, account_payload
):
    base, draft = published
    assert client.get("/v1/search", params={"q": "MARKER", "type": "video"}).json()["items"] == []
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
    assert client.get("/v1/search", params={"q": "MARKER", "type": "video"}).json()["items"] == []
    assert login(client, csrf, owner[0]).status_code == 200
    assert feed.expose(client, csrf, base).status_code == 200
    response = client.get("/v1/search", params={"q": "MARKER", "type": "video"})
    assert response.status_code == 200
    result = response.json()
    assert [item["video"]["id"] for item in result["items"]] == [draft["id"]]
    assert result["items"][0]["type"] == "video"
    assert not any(
        value in response.text
        for value in (owner[0]["email"], "owner_id", "role", "artifact", "encoded/", "X-Amz")
    )
    for features in (("hd",), ("4k", "hdr"), ("subtitles",)):
        assert (
            client.get(
                "/v1/search",
                params=[
                    ("q", "marker"),
                    ("type", "video"),
                    *(("feature", item) for item in features),
                ],
            ).json()["items"]
            == []
        )
    before = client.get(base + "/settings").json()
    assert (
        client.put(
            base + "/settings",
            headers=csrf,
            json={
                "title": before["title"],
                "description": before["description"],
                "visibility": "private",
            },
        ).status_code
        == 200
    )
    assert client.get("/v1/search", params={"q": "marker", "type": "video"}).json()["items"] == []


def test_channel_cursor_query_binding_and_guest_valkey_outage(client, csrf, owner):
    for _ in range(23):
        token = uuid4().hex
        data = {
            **owner[0],
            "name": "Канал Поиска",
            "email": token + "@example.com",
            "tag": "find" + token[:20],
        }
        assert client.post("/v1/auth/register", headers=csrf, json=data).status_code == 201
    params = {"q": "КАНАЛ ПОИСКА", "type": "channel", "duration": "long", "feature": "4k"}
    first = client.get("/v1/search", params=params)
    assert first.status_code == 200
    first = first.json()
    assert len(first["items"]) == 20 and first["next_cursor"]
    assert all(
        item["type"] == "channel" and item["channel"]["created_at"] == item["channel"]["updated_at"]
        for item in first["items"]
    )
    second = client.get("/v1/search", params={**params, "cursor": first["next_cursor"]}).json()
    assert len(second["items"]) == 3 and second["next_cursor"] is None
    assert len({item["channel"]["id"] for item in first["items"] + second["items"]}) == 23
    assert (
        client.get(
            "/v1/search", params={**params, "q": "different", "cursor": first["next_cursor"]}
        ).status_code
        == 422
    )
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    with patch(
        "redis.asyncio.Redis.execute_command", side_effect=ConnectionError("synthetic outage")
    ):
        assert client.get("/v1/search", params=params).json()["items"] == first["items"]
        assert (
            client.get("/v1/search", params={**params, "cursor": first["next_cursor"]}).json()
            == second
        )


def test_failed_profile_read_returns_no_partial_or_cached_results(client, csrf, owner, published):
    base, _ = published
    assert feed.expose(client, csrf, base).status_code == 200
    params = {"q": "marker"}
    before = client.get("/v1/search", params=params).json()
    original = AsyncSession.execute

    async def fail(session, statement, *args, **kwargs):
        if "channel_profiles" in str(statement):
            raise SQLAlchemyError("private search marker")
        return await original(session, statement, *args, **kwargs)

    with patch.object(AsyncSession, "execute", fail):
        response = client.get("/v1/search", params=params)
    assert response.status_code == 500 and "private search marker" not in response.text
    assert "private-marker" not in response.text and response.headers["cache-control"] == "no-store"
    assert client.get("/v1/search", params=params).json() == before


def test_openapi_documents_features_and_query(client):
    operation = client.get("/openapi.json").json()["paths"]["/v1/search"]["get"]
    assert operation["security"] == [{}, {"SessionCookie": []}]
    assert {item["name"] for item in operation["parameters"]} == {
        "q",
        "type",
        "sort",
        "duration",
        "period",
        "cursor",
        "feature",
    }
