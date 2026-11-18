"""Like counters and caller state never bypass ordinary video viewing rights."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from tests import test_feed_http as feed
from tests import test_video_access_http as access
from tests.test_channels_http import query_hook
from tests.test_videos_http import create, login
from user.public import SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture(scope="module", autouse=True)
def clean_media(backend_runtime):
    yield from access.clean_media.__wrapped__(backend_runtime)


@pytest.fixture
def owner(client, csrf, registered_account):
    return access.owner.__wrapped__(client, csrf, registered_account)


@pytest.fixture
def published(client, csrf, owner, source_queue):
    return access.published.__wrapped__(client, csrf, owner, source_queue)


def register(client, csrf, payload):
    token = uuid4().hex
    data = {**payload, "email": token + "@example.com", "tag": "t" + token[:28]}
    response = client.post("/v1/auth/register", headers=csrf, json=data)
    assert response.status_code == 201
    return data, response.json()


def test_guest_owner_idempotence_counter_and_forged_fields(
    client, csrf, owner, published, account_payload
):
    base, _ = published
    assert feed.expose(client, csrf, base).status_code == 200
    endpoint = base + "/like"
    other, account = register(client, csrf, account_payload)
    draft = create(client, csrf)
    before = client.get(base + "/settings").json()
    for desired, total in ((False, 0), (True, 1), (True, 1), (False, 0), (False, 0), (True, 1)):
        response = client.put(
            endpoint,
            headers=csrf,
            json={
                "liked": desired,
                "user_id": account["account_id"],
                "account_id": account["account_id"],
                "video_id": draft["id"],
                "like_count": -1000,
            },
        )
        assert response.status_code == 200 and response.json() == {
            "liked": desired,
            "like_count": total,
            "can_like": True,
            "disliked": False,
            "can_dislike": True,
        }
        assert (
            client.get(endpoint, params={"account_id": account["account_id"]}).json()
            == response.json()
        )
        assert (
            response.headers["cache-control"] == "no-store"
            and "cookie" in response.headers["vary"].lower()
        )
    assert client.get(base + "/settings").json() == before
    assert login(client, csrf, other).status_code == 200
    assert client.get(endpoint).json() == {
        "liked": False,
        "like_count": 1,
        "can_like": True,
        "disliked": False,
        "can_dislike": True,
    }
    assert client.put(
        endpoint, headers=csrf, json={"liked": False, "user_id": owner[1]["account_id"]}
    ).json() == {
        "liked": False,
        "like_count": 1,
        "can_like": True,
        "disliked": False,
        "can_dislike": True,
    }
    assert client.put(endpoint, json={"liked": True}).status_code == 403
    assert client.put(endpoint, headers=csrf, json={"liked": True}).json() == {
        "liked": True,
        "like_count": 2,
        "can_like": True,
        "disliked": False,
        "can_dislike": True,
    }
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert client.get(endpoint).json() == {
        "liked": False,
        "like_count": 2,
        "can_like": False,
        "disliked": False,
        "can_dislike": False,
    }
    assert client.put(endpoint, headers=csrf, json={"liked": True}).status_code == 401
    schema = client.get("/openapi.json").json()
    route = schema["paths"]["/v1/videos/{video_id}/like"]
    assert route["get"]["security"] == [{}, {"SessionCookie": []}]
    assert route["put"]["security"] == [{"SessionCookie": [], "CSRFToken": []}]
    assert (
        schema["components"]["schemas"]["LikeResponse"]["properties"]["like_count"]["minimum"] == 0
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"liked": None},
        {"liked": 0},
        {"liked": 1},
        {"liked": "true"},
        {"liked": []},
        {"liked": "'; DROP TABLE video_likes;--"},
    ],
)
def test_invalid_target_never_writes(client, csrf, owner, published, body):
    base, _ = published
    response = client.put(base + "/like", headers=csrf, json=body)
    assert response.status_code == 422
    assert client.get(base + "/like").json() == {
        "liked": False,
        "like_count": 0,
        "can_like": True,
        "disliked": False,
        "can_dislike": True,
    }


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_private_counters_and_mutations_require_access_for_every_role(
    client, csrf, owner, published, account_payload, role
):
    base, _ = published
    endpoint = base + "/like"
    assert client.put(endpoint, headers=csrf, json={"liked": True}).status_code == 200
    viewer, _ = register(client, csrf, account_payload)

    async def promote():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(viewer["email"], role)

    client.portal.call(promote)
    assert login(client, csrf, viewer).status_code == 200
    for method, data in (("GET", None), ("PUT", {"liked": True}), ("PUT", {"liked": False})):
        response = client.request(method, endpoint, headers=csrf, json=data)
        assert (
            response.status_code == 404
            and "like_count" not in response.json()
            and "liked" not in response.json()
        )
    assert login(client, csrf, owner[0]).status_code == 200
    assert (
        client.put(
            base + "/permissions", headers=csrf, json={"email": viewer["email"], "allowed": True}
        ).status_code
        == 200
    )
    assert login(client, csrf, viewer).status_code == 200
    assert client.put(endpoint, headers=csrf, json={"liked": True}).json() == {
        "liked": True,
        "like_count": 2,
        "can_like": True,
        "disliked": False,
        "can_dislike": True,
    }
    assert login(client, csrf, owner[0]).status_code == 200
    assert (
        client.put(
            base + "/permissions", headers=csrf, json={"email": viewer["email"], "allowed": False}
        ).status_code
        == 200
    )
    assert client.get(endpoint).json() == {
        "liked": True,
        "like_count": 2,
        "can_like": True,
        "disliked": False,
        "can_dislike": True,
    }
    assert login(client, csrf, viewer).status_code == 200
    assert (
        client.get(endpoint).status_code == 404
        and client.put(endpoint, headers=csrf, json={"liked": False}).status_code == 404
    )
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert client.get(endpoint).status_code == 404


def test_hidden_draft_and_unknown_video_do_not_expose_counts(client, csrf, owner, published):
    base, draft = published
    unready = create(client, csrf)
    for target in (f"/v1/videos/{unready['id']}/like", "/v1/videos/4611686018427387904/like"):
        assert client.get(target).status_code == 404
        assert client.put(target, headers=csrf, json={"liked": True}).status_code == 404
    assert client.put(base + "/like", headers=csrf, json={"liked": True}).status_code == 200

    async def hide():
        async with client.app.state.resources.sessions.begin() as session:
            await session.execute(
                text("UPDATE video_publications SET moderation='hidden' WHERE id=:id"),
                {"id": draft["id"]},
            )

    client.portal.call(hide)
    for method, data in (("GET", None), ("PUT", {"liked": False})):
        response = client.request(method, base + "/like", headers=csrf, json=data)
        assert response.status_code == 404 and "like_count" not in response.json()


def test_failed_aggregate_rolls_back_pair_and_preserves_metadata(client, csrf, owner, published):
    base, _ = published
    endpoint = base + "/like"
    before = client.get(base + "/settings").json()

    def fail(_, __, statement, *args):
        if statement.lstrip().upper().startswith("SELECT") and "FROM video_likes" in statement:
            raise SQLAlchemyError("private counter detail")

    for current in (False, True):
        expected = {
            "liked": current,
            "like_count": int(current),
            "can_like": True,
            "disliked": False,
            "can_dislike": True,
        }
        assert client.put(endpoint, headers=csrf, json={"liked": current}).json() == expected
        with query_hook(client, fail):
            for method, data in (("GET", None), ("PUT", {"liked": not current})):
                response = client.request(method, endpoint, headers=csrf, json=data)
                assert response.status_code == 500 and response.json()["code"] == "internal_error"
                assert (
                    "like_count" not in response.json()
                    and "private counter detail" not in response.text
                )
                assert response.headers["cache-control"] == "no-store"
        assert client.get(endpoint).json() == expected
        assert client.get(base + "/settings").json() == before
