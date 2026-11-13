"""Upload API, real storage gateway, CSRF, and hidden server-side transfer identifiers."""

import http.client
import logging
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture
def upload(client, csrf, registered_account):
    data, _ = registered_account
    assert (
        client.post(
            "/v1/auth/login",
            json={"email": data["email"], "password": data["password"]},
            headers=csrf,
        ).status_code
        == 200
    )
    draft = client.post(
        "/v1/videos", json={"title": "Upload", "visibility": "private"}, headers=csrf
    ).json()
    path = f"/v1/videos/{draft['id']}/upload"
    response = client.post(path, json={"size": 3}, headers=csrf)
    assert response.status_code == 200
    plan = response.json()
    try:
        yield path, plan, data
    finally:
        link = urlsplit(plan["parts"][0]["url"])
        key = link.path.removeprefix("/uploads/videos/")
        multipart_id = parse_qs(link.query)["uploadId"][0]
        objects = client.app.state.resources.objects
        try:
            objects.abort_multipart_upload(Bucket="videos", Key=key, UploadId=multipart_id)
        except ClientError:
            pass
        objects.delete_object(Bucket="videos", Key=key)


def send(link, data=b"abc", method="PUT"):
    connection = http.client.HTTPConnection("nginx", 8080, timeout=10)
    try:
        connection.request(method, link, body=data)
        response = connection.getresponse()
        response.read()
        return response.status
    except Exception:
        raise RuntimeError("Synthetic storage request failed") from None
    finally:
        connection.close()


def test_http_plan_and_actual_transfer_do_not_publish_or_echo_client_assertions(
    client, csrf, upload
):
    path, plan, _ = upload
    assert plan["part_size"] == 16777216 and plan["parallel_parts"] == 4
    assert plan["parts"][0]["url"].startswith("/uploads/videos/incoming/")
    assert (
        client.post(
            f"{path}/{plan['id']}/complete", json={"completed": True, "size": 3}, headers=csrf
        ).status_code
        == 409
    )
    assert send(plan["parts"][0]["url"]) == 200
    response = client.post(
        f"{path}/{plan['id']}/complete",
        json={"object_key": "forged", "hash": "forged"},
        headers=csrf,
    )
    assert response.status_code == 200 and response.json()["status"] == "uploaded"
    assert response.json()["parts"] == []
    assert response.headers["cache-control"] == "no-store"
    settings = client.get(path.removesuffix("/upload") + "/settings").json()
    assert settings["added_at"] is not None and settings["publication"] == "draft"
    assert settings["processing"] == "pending"
    repeated = client.post(f"{path}/{plan['id']}/complete", headers=csrf)
    assert repeated.status_code == 200
    assert client.get(path.removesuffix("/upload") + "/settings").json() == settings
    assert send(plan["parts"][0]["url"], method="GET") == 403


def test_controls_require_csrf_and_owner_session_and_never_log_signatures(
    client, csrf, upload, caplog
):
    path, plan, data = upload
    with caplog.at_level(logging.INFO, logger="app.requests"):
        response = client.post(f"{path}/{plan['id']}/refresh", headers=csrf)
        assert response.status_code == 200
    link = plan["parts"][0]["url"]
    signature = parse_qs(urlsplit(link).query)["X-Amz-Signature"][0]
    # Assertions deliberately expose only a boolean, never credential contents on failure.
    leaked = signature in caplog.text
    assert not leaked
    assert client.post(f"{path}/{plan['id']}/refresh").status_code == 403
    token = uuid4().hex
    stranger = {**data, "email": token + "@example.com", "tag": "t" + token[:28]}
    assert client.post("/v1/auth/register", json=stranger, headers=csrf).status_code == 201
    assert (
        client.post(
            "/v1/auth/login",
            json={"email": stranger["email"], "password": stranger["password"]},
            headers=csrf,
        ).status_code
        == 200
    )
    for endpoint, body in (
        (path, {"size": 3}),
        (f"{path}/{plan['id']}/refresh", {}),
        (f"{path}/{plan['id']}/complete", {}),
    ):
        response = client.post(endpoint, json=body, headers=csrf)
        assert response.status_code == 404 and "parts" not in response.json()
    client.post("/v1/auth/logout", headers=csrf)
    assert client.post(path, json={"size": 3}, headers=csrf).status_code == 401


@pytest.mark.parametrize("size", [0, -1, True, "3", None, 5_000_000_001])
def test_invalid_size_never_changes_active_transfer(client, csrf, upload, size):
    path, plan, _ = upload
    assert client.post(path, json={"size": size}, headers=csrf).status_code == 422
    assert client.post(path, json={"size": 3}, headers=csrf).json()["id"] == plan["id"]


def test_unknown_client_keys_do_not_change_destination_or_size(client, csrf, upload):
    path, plan, _ = upload
    response = client.post(
        path,
        json={
            "size": 3,
            "object_key": "../victim",
            "bucket": "victim",
            "upload_id": 2**62,
            "owner_id": 2**62,
        },
        headers=csrf,
    )
    assert response.status_code == 200
    assert response.json()["id"] == plan["id"]
    assert response.json()["parts"][0]["url"].split("?")[0] == plan["parts"][0]["url"].split("?")[0]
