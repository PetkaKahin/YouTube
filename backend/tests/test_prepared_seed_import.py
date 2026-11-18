"""Cold ready-media import, rollback, storage recovery and normal HTTP authorisation."""

import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError

from tests.test_videos_http import login
from video.public import (
    ImportPreparedVideo,
    RepairEncoding,
    RepairSourceProcessing,
    RepairThumbnails,
    VideoMetadata,
)

pytestmark = pytest.mark.usefixtures("isolated_account_database")
BUNDLE = Path("/app/seed-media/sun-in-canopy")


@pytest.fixture
def private_bucket(client):
    resources = client.app.state.resources
    objects = resources.objects
    previous = resources.bucket
    name = "prepared-seed-test-" + uuid4().hex
    objects.create_bucket(Bucket=name)
    resources.bucket = name
    try:
        yield resources
    finally:
        continuation = {}
        while True:
            page = objects.list_objects_v2(Bucket=name, **continuation)
            for entry in page.get("Contents", []):
                objects.delete_object(Bucket=name, Key=entry["Key"])
            if not page.get("IsTruncated"):
                break
            continuation = {"ContinuationToken": page["NextContinuationToken"]}
        objects.delete_bucket(Bucket=name)
        resources.bucket = previous


def test_cold_import_rollback_reuse_http_and_private_rights(
    client, csrf, registered_account, private_bucket
):
    data, owner = registered_account
    assert login(client, csrf, data).status_code == 200
    resources = private_bucket
    app = client.app.state.dishka_container
    metadata = VideoMetadata("Готовый природный ролик", "Локальный готовый набор", "public")

    async def import_video(actor, value):
        async with app() as operation:
            return await (await operation.get(ImportPreparedVideo))(actor, value, BUNDLE)

    async def counts():
        async with resources.sessions() as session:
            return tuple(
                [
                    await session.scalar(text(query))
                    for query in (
                        "SELECT count(*) FROM video_publications",
                        "SELECT count(*) FROM video_encoded_tracks",
                        "SELECT count(*) FROM procrastinate_jobs WHERE status IN ('todo','doing')",
                        "SELECT count(*) FROM procrastinate_jobs "
                        "WHERE task_name='video.import_prepared'",
                    )
                ]
            )

    before = client.portal.call(counts)
    assert before == (0, 0, 0, 0)

    def fail_plan(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO video_media_plans"):
            raise SQLAlchemyError("injected import transaction failure")

    event.listen(resources.engine.sync_engine, "before_cursor_execute", fail_plan)
    try:
        with pytest.raises(SQLAlchemyError):
            client.portal.call(import_video, owner["account_id"], metadata)
    finally:
        event.remove(resources.engine.sync_engine, "before_cursor_execute", fail_plan)
    assert client.portal.call(counts) == before
    assert resources.objects.list_objects_v2(Bucket=resources.bucket).get("KeyCount", 0) > 0

    with (
        patch(
            "asyncio.create_subprocess_exec",
            side_effect=AssertionError("Prepared import started a media process"),
        ),
        patch.object(
            resources.objects,
            "put_object",
            side_effect=AssertionError("Completed cache uploaded twice"),
        ),
    ):
        first = client.portal.call(import_video, owner["account_id"], metadata)
        after = client.portal.call(counts)
        repeated = client.portal.call(import_video, owner["account_id"], metadata)
    assert repeated.id == first.id and client.portal.call(counts) == after
    assert after[0] == 1 and after[1] > 0 and after[2] == 0 and after[3] > 0
    base = f"/v1/videos/{first.id}"
    status = client.get(base + "/processing").json()
    assert status["processing"] == "ready" and status["thumbnail"]["status"] == "ready"
    assert {row["quality"] for row in status["available_qualities"]} == {360, 480, 720}
    view = client.get(base)
    assert view.status_code == 200 and view.json()["hd"]
    master = client.get(base + "/hls/master.m3u8")
    assert master.status_code == 200 and "av01." in master.text and "1280x720" in master.text
    variant = next(line for line in master.text.splitlines() if line and not line.startswith("#"))
    variant_response = client.get(base + "/hls/" + variant)
    assert variant_response.status_code == 200 and "#EXT-X-ENDLIST" in variant_response.text
    for suffix in ("/preview.webp", "/thumbnail/generated/0", "/thumbnail/image"):
        response = client.get(base + suffix)
        assert response.status_code == 200
        assert "/prepared/" in response.headers["x-accel-redirect"]
    bundle = json.loads((BUNDLE / "bundle.json").read_text())
    output = next(row for row in bundle["outputs"] if row["spec"]["kind"] == "video")
    entry = next(row for row in output["artifact"]["files"] if row["name"].endswith(".m4s"))
    stored = resources.objects.get_object(
        Bucket=resources.bucket,
        Key=output["artifact"]["prefix"] + entry["name"],
        Range="bytes=0-31",
    )
    with stored["Body"] as content:
        actual = content.read()
    with (BUNDLE / output["profile_key"] / entry["name"]).open("rb") as content:
        assert actual == content.read(32)
    assert stored["ResponseMetadata"]["HTTPStatusCode"] == 206

    other_data = {**data, "email": uuid4().hex + "@example.com", "tag": "t" + uuid4().hex[:20]}
    other = client.post("/v1/auth/register", json=other_data, headers=csrf)
    assert other.status_code == 201
    with patch.object(
        resources.objects, "put_object", side_effect=AssertionError("Shared media uploaded twice")
    ):
        second = client.portal.call(
            import_video, other.json()["account_id"], VideoMetadata("private", "", "private")
        )
    assert second.id != first.id
    assert client.portal.call(counts)[1:3] == after[1:3]
    assert client.get(f"/v1/videos/{second.id}").status_code == 404

    async def maintenance():
        for scenario in (RepairSourceProcessing, RepairEncoding, RepairThumbnails):
            async with app() as operation:
                await (await operation.get(scenario))()

    client.portal.call(maintenance)
    assert client.portal.call(counts)[2] == 0
    assert login(client, csrf, other_data).status_code == 200
    assert client.get(f"/v1/videos/{second.id}").status_code == 200
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert client.get(f"/v1/videos/{second.id}/hls/master.m3u8").status_code == 404
    assert client.get(f"/v1/videos/{second.id}/preview.webp").status_code == 404
