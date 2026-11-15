"""Actual signed image writes, worker processing, owner-only bytes and CSRF."""

import logging
import struct
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text

from tests.test_uploads_http import send

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture
def thumbnail(client, csrf, registered_account):
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
        "/v1/videos", json={"title": "thumbnail", "visibility": "private"}, headers=csrf
    ).json()
    path = f"/v1/videos/{draft['id']}/thumbnail"
    try:
        yield path, draft, data
    finally:

        async def keys():
            async with client.app.state.resources.sessions() as session:
                rows = (
                    await session.execute(
                        text(
                            "SELECT t.object_key,t.multipart_id,t.normalized_key,e.artifact "
                            "FROM video_thumbnail_uploads t LEFT JOIN video_encoded_tracks e "
                            "ON e.id=t.encoded_id WHERE t.video_id=:id"
                        ),
                        {"id": draft["id"]},
                    )
                ).all()
                prefixes = (
                    await session.scalars(
                        text(
                            "SELECT p.prefix FROM video_thumbnail_preparations p "
                            "JOIN video_thumbnail_uploads t ON t.id=p.upload_id "
                            "WHERE t.video_id=:id"
                        ),
                        {"id": draft["id"]},
                    )
                ).all()
                return rows, prefixes

        rows, prefixes = client.portal.call(keys)
        objects, bucket = client.app.state.resources.objects, client.app.state.resources.bucket
        for key, multipart, normalized, artifact in rows:
            if multipart:
                try:
                    objects.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=multipart)
                except ClientError as error:
                    if error.response["Error"]["Code"] != "NoSuchUpload":
                        raise
            objects.delete_object(Bucket=bucket, Key=key)
            if normalized:
                objects.delete_object(Bucket=bucket, Key=normalized)
            if artifact:
                for item in artifact["files"]:
                    objects.delete_object(Bucket=bucket, Key=artifact["prefix"] + item["name"])
        for prefix in prefixes:
            objects.delete_object(Bucket=bucket, Key=prefix + "pixels.rgba")


def still_bmp():
    pixels = b"\x00\x00\xff\x00" * 4
    return (
        struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54)
        + struct.pack("<IiiHHIIiiII", 40, 1, 4, 1, 24, 0, len(pixels), 0, 0, 0, 0)
        + pixels
    )


def test_signed_image_transfer_worker_and_private_bytes(
    client, csrf, thumbnail, source_queue, caplog
):
    path, draft, _ = thumbnail
    image = still_bmp()
    with caplog.at_level(logging.INFO):
        response = client.post(path + "/upload", json={"size": len(image)}, headers=csrf)
        assert response.status_code == 200
        plan = response.json()
        transfer_status = send(plan["parts"][0]["url"], image)
        assert transfer_status == 200
        assert client.post(f"{path}/upload/{plan['id']}/complete", headers=csrf).status_code == 200
        before = client.get(f"/v1/videos/{draft['id']}/settings").json()

        async def process():
            queue = source_queue(client.app.state.dishka_container)
            async with queue.open_async():
                await queue.run_worker_async(
                    queues=["media"],
                    concurrency=1,
                    wait=False,
                    listen_notify=False,
                    install_signal_handlers=False,
                )

        client.portal.call(process)
        options = client.get(path)
        assert options.status_code == 200 and options.json()["selected"] == "custom"
        response = client.get(path + "/image")
        assert response.status_code == 200
        assert response.content == b""
        assert response.headers["x-accel-redirect"].startswith("/_media_storage/")
        assert client.head(path + "/image").status_code == 200
        assert response.headers["content-type"] == "image/webp"
        assert (
            response.headers["cache-control"] == "no-store"
            and "cookie" in response.headers["vary"].lower()
        )
        assert client.get(f"/v1/videos/{draft['id']}/settings").json() == before
        read_status = send(plan["parts"][0]["url"], method="GET")
        assert read_status == 403
    signature = parse_qs(urlsplit(plan["parts"][0]["url"]).query)["X-Amz-Signature"][0]
    leaked = signature in caplog.text
    assert not leaked


def test_thumbnail_mutations_require_csrf_and_all_routes_require_owner(client, csrf, thumbnail):
    path, draft, data = thumbnail
    for suffix, body in (("", {"slot": 0}), ("/upload", {"size": 1})):
        method = client.put if not suffix else client.post
        assert method(path + suffix, json=body).status_code == 403
    assert (
        client.post(path + "/upload", json={"size": 10 * 1024 * 1024 + 1}, headers=csrf).status_code
        == 422
    )
    assert client.put(path, json={"slot": 3}, headers=csrf).status_code == 422
    token = uuid4().hex
    other = {**data, "email": token + "@example.com", "tag": "t" + token[:28]}
    assert client.post("/v1/auth/register", json=other, headers=csrf).status_code == 201
    assert (
        client.post(
            "/v1/auth/login",
            json={"email": other["email"], "password": other["password"]},
            headers=csrf,
        ).status_code
        == 200
    )
    for suffix in ("", "/image", "/generated/0"):
        response = client.get(path + suffix)
        assert response.status_code == 404 and response.headers["cache-control"] == "no-store"
    for suffix in ("/upload", "/upload/1/refresh", "/upload/1/complete"):
        assert client.post(path + suffix, json={"size": 1}, headers=csrf).status_code == 404
    assert client.put(path, json={"slot": 0}, headers=csrf).status_code == 404
    client.post("/v1/auth/logout", headers=csrf)
    for suffix in ("", "/image", "/generated/0"):
        assert client.get(path + suffix).status_code == 401
    assert client.get(f"/v1/videos/{draft['id']}/settings").status_code == 401
