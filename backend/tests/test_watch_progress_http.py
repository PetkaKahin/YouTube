"""Persistent main-player positions and their ordinary authorised page projections."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError

from app.main import create_app
from app.settings import Settings
from channel.public import Register, Registration
from user.public import SetRole
from video.public import ImportPreparedVideo, VideoMetadata

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture(scope="module")
def media(backend_runtime):
    bucket = "watch-progress-test-" + uuid4().hex

    async def prepare():
        async with backend_runtime() as (container, resources):
            resources.objects.create_bucket(Bucket=bucket)
            resources.bucket = bucket
            async with resources.sessions.begin() as session:
                await session.execute(
                    text(
                        "SELECT setval(pg_get_serial_sequence('video_publications','id'),"
                        ":start,false)"
                    ),
                    {"start": 2**53 + 101},
                )
            people, videos = [], []
            for _ in range(2):
                token = uuid4().hex
                person = SimpleNamespace(
                    email=token + "@example.com",
                    tag="t" + token[:24],
                    password="abcdef",  # noqa: S106 -- test account
                )
                async with container() as operation:
                    registration = await (await operation.get(Register))(
                        Registration(
                            "Progress author",
                            person.email,
                            person.tag,
                            person.password,
                            person.password,
                        )
                    )
                person.id = registration.account_id
                async with container() as operation:
                    video = await (await operation.get(ImportPreparedVideo))(
                        person.id,
                        VideoMetadata("Progress forest", "Description", "public"),
                        Path("/app/seed-media/sun-in-canopy"),
                    )
                people.append(person)
                videos.append(video.id)
            return SimpleNamespace(people=people, videos=videos, bucket=bucket)

    try:
        yield asyncio.run(prepare())
    finally:

        async def cleanup():
            async with backend_runtime() as (_, resources):
                keys = resources.objects.list_objects_v2(Bucket=bucket)
                while True:
                    for item in keys.get("Contents", []):
                        resources.objects.delete_object(Bucket=bucket, Key=item["Key"])
                    if not keys.get("IsTruncated"):
                        break
                    keys = resources.objects.list_objects_v2(
                        Bucket=bucket, ContinuationToken=keys["NextContinuationToken"]
                    )
                resources.objects.delete_bucket(Bucket=bucket)

        asyncio.run(cleanup())


def sql(client, statement, parameters=None):
    async def execute():
        async with client.app.state.resources.sessions.begin() as session:
            result = await session.execute(text(statement), parameters or {})
            return result.scalar() if result.returns_rows else None

    return client.portal.call(execute)


@pytest.fixture(autouse=True)
def reset(media, client):
    client.app.state.resources.bucket = media.bucket
    sql(client, "DELETE FROM video_watch_progress")
    sql(client, "DELETE FROM video_private_grants")
    sql(
        client,
        "UPDATE video_publications SET visibility='public',moderation='visible',"
        "publication='published'",
    )
    sql(client, "UPDATE video_media_plans SET status='planned'")


def login(client, csrf, person):
    response = client.post(
        "/v1/auth/login", json={"email": person.email, "password": person.password}, headers=csrf
    )
    assert response.status_code == 200


def put(client, csrf, video, position):
    return client.put(
        f"/v1/videos/{video}/progress", json={"position_seconds": position}, headers=csrf
    )


def check(response, status=200):
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "cookie" in response.headers["vary"].lower()


def details(client, video):
    response = client.get(f"/v1/videos/{video}")
    check(response)
    return response.json()


def test_last_position_clamp_zero_and_no_metadata_or_job_changes(client, csrf, media):
    video = media.videos[0]
    login(client, csrf, media.people[0])
    before = client.get(f"/v1/videos/{video}/settings").json()
    jobs = sql(client, "SELECT count(*) FROM procrastinate_jobs")
    saved = []
    for position in (5.5, 2, 0, 1e308):
        response = put(client, csrf, video, position)
        check(response)
        value = response.json()
        assert value["position_seconds"] == min(position, value["duration_seconds"])
        assert 0 < value["duration_seconds"] <= 3600
        assert set(value) == {"position_seconds", "duration_seconds", "updated_at"}
        assert details(client, video)["watch_progress"] == value
        saved.append(value)
    assert saved[1]["updated_at"] > saved[0]["updated_at"]
    assert client.get(f"/v1/videos/{video}/settings").json() == before
    assert sql(client, "SELECT count(*) FROM video_watch_progress") == 1
    assert sql(client, "SELECT count(*) FROM procrastinate_jobs") == jobs


def test_guests_batch_pages_account_isolation_and_new_device(client, csrf, media):
    video = media.videos[0]
    assert details(client, video)["watch_progress"] is None
    assert client.get(f"/v1/videos/{video}/hls/master.m3u8").status_code == 200
    assert client.get(f"/v1/videos/{video}/preview.webp").status_code == 200
    assert sql(client, "SELECT count(*) FROM video_watch_progress") == 0
    login(client, csrf, media.people[0])
    for identity in media.videos:
        check(put(client, csrf, identity, 4))
    login(client, csrf, media.people[1])
    assert details(client, video)["watch_progress"] is None
    saved = put(client, csrf, video, 1.25).json()
    engine = client.app.state.resources.engine.sync_engine
    for path in (
        "/v1/videos/feed",
        "/v1/search?q=Progress&type=video",
        f"/v1/channels/{media.people[0].tag}/videos",
    ):
        statements = []

        def capture(
            connection, cursor, statement, parameters, context, many, statements=statements
        ):
            if "video_watch_progress" in statement:
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            response = client.get(path)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        check(response)
        rows = [row.get("video", row) for row in response.json()["items"]]
        assert len(statements) == 1
        current = next(row for row in rows if row["id"] == video)
        assert type(current["id"]) is int and current["id"] > 2**53
        assert current["watch_progress"] == saved
        assert all(row["watch_progress"] is None for row in rows if row["id"] != video)
    with TestClient(create_app(Settings(cookie_secure=False))) as other:
        other.get("/docs")
        other_csrf = {"X-CSRFToken": other.cookies["csrftoken"]}
        login(other, other_csrf, media.people[1])
        assert details(other, video)["watch_progress"] == saved
        cache = client.app.state.resources.cache
        client.portal.call(cache.delete, "sessions." + client.cookies["session"])
        assert details(client, video)["watch_progress"] is None
        assert details(other, video)["watch_progress"] == saved


def test_invalid_bodies_csrf_and_guest_do_not_store_progress(client, csrf, media):
    video = media.videos[0]
    check(put(client, csrf, video, 1), 401)
    login(client, csrf, media.people[0])
    check(client.put(f"/v1/videos/{video}/progress", json={"position_seconds": 1}), 403)
    for body in (
        "{}",
        '{"position_seconds":-1}',
        '{"position_seconds":true}',
        '{"position_seconds":"1"}',
        '{"position_seconds":NaN}',
        '{"position_seconds":Infinity}',
        '{"position_seconds":null}',
        '{"position_seconds":1,"user_id":1}',
        '{"position_seconds":1,"duration_seconds":999}',
        '{"position_seconds":1,"role":"admin"}',
    ):
        response = client.put(
            f"/v1/videos/{video}/progress",
            content=body,
            headers={**csrf, "Content-Type": "application/json"},
        )
        check(response, 422)
    check(
        client.put(
            f"/v1/videos/{video}/progress?user_id=1", json={"position_seconds": 1}, headers=csrf
        ),
        422,
    )
    assert sql(client, "SELECT count(*) FROM video_watch_progress") == 0


def test_private_grant_revocation_hidden_draft_and_unready(client, csrf, media):
    video, owner, viewer = media.videos[0], media.people[0], media.people[1]
    sql(client, "UPDATE video_publications SET visibility='private' WHERE id=:id", {"id": video})
    login(client, csrf, viewer)
    check(put(client, csrf, video, 1), 404)

    async def make_admin():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(viewer.email, "admin")

    client.portal.call(make_admin)
    check(put(client, csrf, video, 1), 404)
    login(client, csrf, owner)
    assert (
        client.put(
            f"/v1/videos/{video}/permissions",
            json={"email": viewer.email, "allowed": True},
            headers=csrf,
        ).status_code
        == 200
    )
    login(client, csrf, viewer)
    check(put(client, csrf, video, 2))
    login(client, csrf, owner)
    assert details(client, video)["watch_progress"] is None
    assert (
        client.put(
            f"/v1/videos/{video}/permissions",
            json={"email": viewer.email, "allowed": False},
            headers=csrf,
        ).status_code
        == 200
    )
    login(client, csrf, viewer)
    check(put(client, csrf, video, 3), 404)
    check(client.get(f"/v1/videos/{video}"), 404)
    login(client, csrf, owner)
    sql(client, "UPDATE video_publications SET moderation='hidden' WHERE id=:id", {"id": video})
    check(put(client, csrf, video, 1), 404)
    sql(
        client,
        "UPDATE video_publications SET moderation='visible',publication='draft' WHERE id=:id",
        {"id": video},
    )
    check(put(client, csrf, video, 1), 404)
    sql(client, "UPDATE video_publications SET publication='published' WHERE id=:id", {"id": video})
    sql(client, "UPDATE video_media_plans SET status='pending'")
    check(put(client, csrf, video, 1), 404)


def test_database_failure_rolls_back_without_partial_progress(client, csrf, media):
    video = media.videos[0]
    login(client, csrf, media.people[0])
    engine = client.app.state.resources.engine.sync_engine

    def fail(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO video_watch_progress"):
            raise SQLAlchemyError("private-progress-error-marker")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        response = put(client, csrf, video, 1)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    check(response, 500)
    assert (
        "position_seconds" not in response.text
        and "private-progress-error-marker" not in response.text
    )
    assert sql(client, "SELECT count(*) FROM video_watch_progress") == 0


def test_concurrent_writes_keep_last_accepted_value(client, csrf, media):
    video = media.videos[0]
    login(client, csrf, media.people[0])
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda position: put(client, csrf, video, position), (6, 2, 5, 1))
        )
    for response in responses:
        check(response)
    last = max((response.json() for response in responses), key=lambda value: value["updated_at"])
    assert details(client, video)["watch_progress"] == last
    assert sql(client, "SELECT count(*) FROM video_watch_progress") == 1


def test_visibility_change_winning_the_lock_prevents_write(client, csrf, media):
    video = media.videos[0]
    login(client, csrf, media.people[1])
    resources = client.app.state.resources

    async def lock():
        session = resources.sessions()
        await session.begin()
        await session.execute(
            text("UPDATE video_publications SET visibility='private' WHERE id=:id"), {"id": video}
        )
        return session

    session = client.portal.call(lock)
    waiting = Event()
    engine = resources.engine.sync_engine

    def capture(connection, cursor, statement, parameters, context, many):
        if "FOR SHARE" in statement and "video_publications" in statement:
            waiting.set()

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(put, client, csrf, video, 1)
            try:
                assert waiting.wait(10)
            finally:
                client.portal.call(session.commit)
            check(pending.result(timeout=10), 404)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        client.portal.call(session.close)
    assert sql(client, "SELECT count(*) FROM video_watch_progress") == 0


def test_openapi_progress_is_only_on_ordinary_cards_and_details(client):
    schema = client.get("/openapi.json").json()
    route = schema["paths"]["/v1/videos/{video_id}/progress"]["put"]
    assert route["security"] == [{"SessionCookie": [], "CSRFToken": []}]
    definitions = schema["components"]["schemas"]
    assert definitions["WatchProgressBody"]["additionalProperties"] is False
    assert "watch_progress" in definitions["FeedCardResponse"]["required"]
    assert "watch_progress" in definitions["VideoDetailsResponse"]["required"]
    assert "watch_progress" not in definitions["VideoViewResponse"]["properties"]
