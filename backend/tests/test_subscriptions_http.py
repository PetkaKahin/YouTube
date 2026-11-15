"""Subscription state is private to its authenticated caller and grants no media rights."""

from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from tests import test_video_access_http as access
from tests.test_channels_http import query_hook
from tests.test_videos_http import login
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


def path(data):
    return "/v1/channels/" + data["tag"] + "/subscription"


def test_guest_self_and_schema(client, csrf, owner):
    own = path(owner[0])
    assert client.get(own).json() == {"subscribed": False, "can_subscribe": False}
    for desired in (True, False):
        response = client.put(own, headers=csrf, json={"subscribed": desired})
        assert response.status_code == 422 and response.json()["code"] == "self_subscription"
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert client.get(own).status_code == 401
    assert client.put(own, headers=csrf, json={"subscribed": True}).status_code == 401
    assert client.get("/v1/channels/" + owner[0]["tag"]).status_code == 200
    schema = client.get("/openapi.json").json()
    for method in ("get", "put"):
        expected = {"SessionCookie": []}
        if method == "put":
            expected["CSRFToken"] = []
        assert schema["paths"]["/v1/channels/{tag}/subscription"][method]["security"] == [expected]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"subscribed": None},
        {"subscribed": 0},
        {"subscribed": 1},
        {"subscribed": "true"},
        {"subscribed": []},
    ],
)
def test_strict_boolean_no_state_change(client, csrf, owner, account_payload, body):
    target, _ = register(client, csrf, account_payload)
    response = client.put(path(target), headers=csrf, json=body)
    assert response.status_code == 422
    assert client.get(path(target)).json() == {"subscribed": False, "can_subscribe": True}


@pytest.mark.parametrize("tag", ["xx", "missing-channel", "';DROP--", "<script>"])
def test_invalid_tag_is_generic_not_found(client, csrf, owner, tag):
    endpoint = "/v1/channels/" + tag + "/subscription"
    for method, body in (("GET", None), ("PUT", {"subscribed": True})):
        response = client.request(method, endpoint, headers=csrf, json=body)
        assert response.status_code == 404 and response.json()["detail"] == "Channel not found"
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_actor_and_target_spoofing_never_controls_other_pairs(
    client, csrf, owner, account_payload, role
):
    target, _ = register(client, csrf, account_payload)
    third, third_account = register(client, csrf, account_payload)

    async def promote():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(owner[0]["email"], role)

    client.portal.call(promote)
    assert client.put(path(owner[0]), headers=csrf, json={"subscribed": True}).status_code == 422
    assert client.put(path(target), json={"subscribed": True}).status_code == 403
    response = client.put(
        path(target),
        headers=csrf,
        json={
            "subscribed": True,
            "subscriber_id": third_account["account_id"],
            "owner_id": third_account["account_id"],
            "channel_id": third_account["channel_id"],
        },
    )
    assert response.status_code == 200 and response.json() == {
        "subscribed": True,
        "can_subscribe": True,
    }
    assert (
        response.headers["cache-control"] == "no-store"
        and "cookie" in response.headers["vary"].lower()
    )
    alias = "/v1/channels/@" + target["tag"].upper() + "/subscription"
    assert client.get(alias).json() == response.json()
    assert client.get(path(third)).json() == {"subscribed": False, "can_subscribe": True}
    assert login(client, csrf, third).status_code == 200
    assert client.get(path(target), params={"subscriber_id": owner[1]["account_id"]}).json() == {
        "subscribed": False,
        "can_subscribe": True,
    }
    assert client.put(
        path(target),
        headers=csrf,
        json={"subscribed": False, "subscriber_id": owner[1]["account_id"]},
    ).json() == {"subscribed": False, "can_subscribe": True}
    assert login(client, csrf, owner[0]).status_code == 200
    assert client.get(path(target)).json() == {"subscribed": True, "can_subscribe": True}
    for desired in (True, True, False, False):
        assert client.put(path(target), headers=csrf, json={"subscribed": desired}).json() == {
            "subscribed": desired,
            "can_subscribe": True,
        }


def test_failures_roll_back_and_current_state_is_fresh(client, csrf, owner, account_payload):
    target, _ = register(client, csrf, account_payload)
    endpoint = path(target)
    profile = "/v1/channels/" + target["tag"]
    before = client.get(profile).json()

    def failed_read(_, __, statement, *args):
        if "channel_subscriptions" in statement:
            raise SQLAlchemyError("private subscription detail")

    with query_hook(client, failed_read):
        response = client.get(endpoint)
    assert (
        response.status_code == 500
        and "subscribed" not in response.json()
        and "private subscription detail" not in response.text
    )
    engine = client.app.state.resources.engine.sync_engine

    def failed_write(_, __, statement, *args):
        if "channel_subscriptions" in statement and statement.lstrip().upper().startswith(
            ("INSERT", "DELETE")
        ):
            raise SQLAlchemyError("private write detail")

    for current in (False, True):
        assert client.put(endpoint, headers=csrf, json={"subscribed": current}).status_code == 200
        event.listen(engine, "after_cursor_execute", failed_write)
        try:
            response = client.put(endpoint, headers=csrf, json={"subscribed": not current})
        finally:
            event.remove(engine, "after_cursor_execute", failed_write)
        assert (
            response.status_code == 500
            and "subscribed" not in response.json()
            and "private write detail" not in response.text
        )
        assert client.get(endpoint).json() == {"subscribed": current, "can_subscribe": True}
    assert client.get(profile).json() == before
    assert client.put(endpoint, headers=csrf, json={"subscribed": False}).json() == {
        "subscribed": False,
        "can_subscribe": True,
    }


def test_subscription_and_private_invitation_are_independent(
    client, csrf, owner, published, account_payload
):
    base, _ = published
    endpoint = path(owner[0])
    viewer, _ = register(client, csrf, account_payload)
    assert login(client, csrf, viewer).status_code == 200
    assert client.put(endpoint, headers=csrf, json={"subscribed": True}).status_code == 200
    assert (
        client.get(base).status_code == 404
        and client.get(base + "/hls/master.m3u8").status_code == 404
    )
    assert client.get("/v1/channels/" + owner[0]["tag"] + "/videos").json() == {
        "items": [],
        "next_cursor": None,
    }
    assert login(client, csrf, owner[0]).status_code == 200
    assert (
        client.put(
            base + "/permissions", headers=csrf, json={"email": viewer["email"], "allowed": True}
        ).status_code
        == 200
    )
    assert login(client, csrf, viewer).status_code == 200
    assert (
        client.get(base).status_code == 200
        and client.get(base + "/hls/master.m3u8").status_code == 200
    )
    assert client.put(endpoint, headers=csrf, json={"subscribed": False}).status_code == 200
    assert client.get(base).status_code == 200
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert login(client, csrf, viewer).status_code == 200
    assert client.get(endpoint).json() == {"subscribed": False, "can_subscribe": True}
    assert client.put(endpoint, headers=csrf, json={"subscribed": True}).status_code == 200
    assert login(client, csrf, owner[0]).status_code == 200
    assert (
        client.put(
            base + "/permissions", headers=csrf, json={"email": viewer["email"], "allowed": False}
        ).status_code
        == 200
    )
    assert login(client, csrf, viewer).status_code == 200
    assert client.get(endpoint).json() == {"subscribed": True, "can_subscribe": True}
    assert (
        client.get(base).status_code == 404
        and client.get(base + "/hls/master.m3u8").status_code == 404
    )
