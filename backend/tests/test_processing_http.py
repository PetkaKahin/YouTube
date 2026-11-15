"""Owner processing API: real sessions, role boundaries and safe diagnostics."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.test_uploads_http import send
from tests.test_videos_http import create, login
from user.public import SetRole
from video.public import CleanupOriginals

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture
def owner(client, csrf, registered_account):
    data, account = registered_account
    assert login(client, csrf, data).status_code == 200
    return data, account


def test_processing_contract_is_owner_only_read_only_and_not_media_delivery(client, csrf, owner):
    data, _ = owner
    draft = create(client, csrf)
    path = f"/v1/videos/{draft['id']}/processing"
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {
        "upload": "pending",
        "processing": "pending",
        "error_code": None,
        "qualities": [],
        "available_qualities": [],
        "hd": False,
        "uhd": False,
        "hdr": False,
        "uhd_hdr": False,
        "thumbnail": {"status": "pending", "error_code": None},
        "subtitles": [],
    }
    assert response.headers["cache-control"] == "no-store"
    assert "cookie" in response.headers["vary"].lower()
    assert client.get(path).json() == response.json()
    assert client.get(path.removesuffix("/processing") + "/settings").json() == draft
    assert client.get(path.removesuffix("/processing") + "/master.m3u8").status_code == 404
    assert client.post(path).status_code == 403
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert client.get(path).status_code == 401
    assert login(client, csrf, data).status_code == 200
    cache = client.app.state.resources.cache
    assert client.portal.call(cache.delete, "sessions." + client.cookies["session"]) == 1
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_stranger_role_cannot_inspect_processing_and_cannot_detect_existence(
    client, csrf, owner, role
):
    data, _ = owner
    draft = create(client, csrf, title="private-marker")
    token = uuid4().hex
    other = {**data, "email": token + "@example.com", "tag": "t" + token[:28]}
    assert client.post("/v1/auth/register", json=other, headers=csrf).status_code == 201

    async def grant():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(other["email"], role)

    client.portal.call(grant)
    assert login(client, csrf, other).json()["role"] == role
    responses = [
        client.get(f"/v1/videos/{video_id}/processing") for video_id in [draft["id"], 2**62]
    ]
    assert all(response.status_code == 404 for response in responses)
    assert [
        {key: value for key, value in response.json().items() if key != "request_id"}
        for response in responses
    ][0] == {key: value for key, value in responses[1].json().items() if key != "request_id"}
    assert all("private-marker" not in response.text for response in responses)


@pytest.mark.parametrize("identifier", ["0", "-1", str(2**63), "true", "1 OR 1=1", "<script>"])
def test_invalid_ids_do_not_reach_or_echo_private_data(client, csrf, owner, identifier):
    draft = create(client, csrf)
    response = client.get(f"/v1/videos/{identifier}/processing")
    assert response.status_code == 422
    assert "input" not in response.json()
    assert "private" not in response.text
    assert client.get(f"/v1/videos/{draft['id']}/settings").json() == draft


def test_primary_failure_exposes_only_safe_code_without_changing_independent_states(
    client, csrf, owner
):
    draft = create(client, csrf)

    # Source failure before a media plan exists uses the persisted base state.
    async def failed():
        async with client.app.state.resources.sessions.begin() as session:
            await session.execute(
                text(
                    "UPDATE video_publications SET processing='failed', moderation='hidden' "
                    "WHERE id=:id"
                ),
                {"id": draft["id"]},
            )

    client.portal.call(failed)
    response = client.get(f"/v1/videos/{draft['id']}/processing")
    assert response.status_code == 200 and response.json()["processing"] == "failed"
    settings = client.get(f"/v1/videos/{draft['id']}/settings").json()
    assert settings["moderation"] == "hidden" and settings["publication"] == "draft"
    assert settings["updated_at"] == draft["updated_at"]


def test_ready_processing_response_after_real_upload_and_queue(client, csrf, owner, source_queue):
    draft = create(client, csrf)
    base = f"/v1/videos/{draft['id']}"
    image = b"P6\n1 1\n255\n\xff\x00\x00"
    plan = client.post(base + "/upload", json={"size": len(image)}, headers=csrf).json()
    try:
        assert send(plan["parts"][0]["url"], image) == 200
        assert client.post(f"{base}/upload/{plan['id']}/complete", headers=csrf).status_code == 200

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
        response = client.get(base + "/processing")
        assert response.status_code == 200
        result = response.json()
        assert result["processing"] == "ready" and result["upload"] == "uploaded"
        assert (
            result["qualities"]
            == result["available_qualities"]
            == [{"quality": 1, "range": "sdr", "status": "ready", "error_code": None}]
        )
        assert result["thumbnail"] == {"status": "ready", "error_code": None}
        assert result["subtitles"] == [] and result["error_code"] is None
        assert not any(
            key in response.text for key in ("encoded/", "artifact", "job_id", "sha256", "prefix")
        )
        settings = client.get(base + "/settings").json()
        assert settings["processing"] == "ready" and settings["publication"] == "draft"
        assert settings["visibility"] == "private" and settings["updated_at"] == draft["updated_at"]

        async def cleanup():
            async with client.app.state.dishka_container() as operation:
                await (await operation.get(CleanupOriginals))()

        client.portal.call(cleanup)
        assert client.get(base + "/processing").json() == result
        assert client.post(f"{base}/upload/{plan['id']}/complete", headers=csrf).status_code == 200
        refreshed = client.post(f"{base}/upload/{plan['id']}/refresh", headers=csrf)
        assert refreshed.status_code == 200 and refreshed.json()["status"] == "uploaded"
        assert client.get(base + "/settings").json() == settings
    finally:

        async def keys():
            async with client.app.state.resources.sessions() as session:
                source_key = await session.scalar(
                    text("SELECT object_key FROM video_uploads WHERE id=:id"), {"id": plan["id"]}
                )
                artifacts = (
                    await session.scalars(
                        text(
                            "SELECT artifact FROM video_encoded_tracks "
                            "WHERE source_upload_id=:id AND status='ready'"
                        ),
                        {"id": plan["id"]},
                    )
                ).all()
                return [source_key] + [
                    artifact["prefix"] + item["name"]
                    for artifact in artifacts
                    for item in artifact["files"]
                ]

        for key in client.portal.call(keys):
            client.app.state.resources.objects.delete_object(
                Bucket=client.app.state.resources.bucket, Key=key
            )
