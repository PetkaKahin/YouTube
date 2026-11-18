"""Real sessions, report revisions, PostgreSQL races and every ordinary visibility path."""

import asyncio
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from moderation.public import (
    DecideReport,
    DecisionCommand,
    GetReportedMedia,
    GetReportedVideo,
    GetReports,
    ModerationForbidden,
    ReportConflict,
    ReportNotFound,
    SubmitReport,
)
from tests import test_feed_http as feed
from tests import test_video_access_http as access
from tests.test_accounts_concurrency import wait_for_database_waiter
from tests.test_channels_http import query_hook
from tests.test_comments_http import visibility
from tests.test_playlists_http import playlist
from tests.test_video_likes_http import register
from tests.test_videos_http import create, login
from user.public import CreateAccount, NewAccount, SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture(scope="module", autouse=True)
def clean_media(backend_runtime):
    yield from access.clean_media.__wrapped__(backend_runtime)


@pytest.fixture(autouse=True)
def reset_reports(client):
    async def reset():
        async with client.app.state.resources.sessions.begin() as session:
            for statement in (
                "DELETE FROM moderation_accesses",
                "DELETE FROM moderation_decisions",
                "DELETE FROM moderation_reports",
            ):
                await session.execute(text(statement))

    client.portal.call(reset)


@pytest.fixture
def owner(client, csrf, registered_account):
    return access.owner.__wrapped__(client, csrf, registered_account)


@pytest.fixture
def published(client, csrf, owner, source_queue):
    return access.published.__wrapped__(client, csrf, owner, source_queue)


def role(client, email, value):
    async def change():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(email, value)

    client.portal.call(change)


def staff_member(client, csrf, payload, value="moderator"):
    data, account = register(client, csrf, payload)
    role(client, data["email"], value)
    return data, account


def rows(client, statement, values=None):
    async def read():
        async with client.app.state.resources.sessions.begin() as session:
            return [
                dict(row)
                for row in (await session.execute(text(statement), values or {})).mappings()
            ]

    return client.portal.call(read)


def report_row(client, video_id):
    return rows(
        client, "SELECT * FROM moderation_reports WHERE video_id=:id ORDER BY id", {"id": video_id}
    )[0]


def submit(client, csrf, base, reason="spam", **extra):
    response = client.post(base + "/reports", headers=csrf, json={"reason": reason, **extra})
    assert response.status_code == 202 and response.json() == {"accepted": True}
    assert (
        response.headers["cache-control"] == "no-store"
        and "cookie" in response.headers["vary"].lower()
    )
    return response


def report_path(report):
    return f"/v1/moderation/reports/{report['id']}"


@pytest.mark.parametrize(
    "reason", ["spam", "harassment", "violence", "sexual_content", "copyright", "other"]
)
def test_submission_updates_single_pair_without_hiding_or_revealing_reporter(
    client, csrf, owner, published, reason, caplog
):
    base, draft = published
    before = client.get(base + "/settings").json()
    assert client.post(base + "/reports", json={"reason": reason}).status_code == 403
    first = submit(
        client, csrf, base, reason, reporter_id=2**62, user_id=2**62, role="admin", video_id=2**62
    )
    saved = report_row(client, draft["id"])
    assert (
        saved["reporter_id"] == owner[1]["account_id"]
        and saved["revision"] == 1
        and saved["state"] == "pending"
    )
    submit(client, csrf, base, "other")
    current = report_row(client, draft["id"])
    assert (
        current["id"] == saved["id"] and current["revision"] == 2 and current["reason"] == "other"
    )
    assert (
        current["created_at"] == saved["created_at"]
        and current["submitted_at"] > saved["submitted_at"]
    )
    assert client.get(base + "/settings").json() == before and client.get(base).status_code == 200
    assert client.get("/v1/moderation/reports", params={"role": "admin"}).status_code == 403
    assert client.get(report_path(saved)).status_code == 403
    assert not any(
        marker in first.text for marker in ("reporter", "revision", "state", owner[0]["email"])
    )
    assert owner[0]["email"] not in caplog.text
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert client.post(base + "/reports", headers=csrf, json={"reason": reason}).status_code == 401
    assert report_row(client, draft["id"])["revision"] == 2


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"reason": None},
        {"reason": 1},
        {"reason": True},
        {"reason": []},
        {"reason": "arbitrary"},
        {"reason": "SPAM"},
        {"reason": "spam "},
        {"reason": "'; DROP TABLE moderation_reports;--"},
        {"reason": "<script>alert(1)</script>"},
        {"reason": "../etc/passwd"},
    ],
)
def test_invalid_reason_never_writes_or_reflects_input(client, csrf, owner, published, body):
    base, draft = published
    response = client.post(base + "/reports", headers=csrf, json=body)
    assert response.status_code == 422 and response.headers["cache-control"] == "no-store"
    assert (
        rows(client, "SELECT id FROM moderation_reports WHERE video_id=:id", {"id": draft["id"]})
        == []
    )


@pytest.mark.parametrize("staff_role", ["user", "moderator", "admin"])
def test_reporting_private_requires_normal_permission_for_every_role(
    client, csrf, owner, published, account_payload, staff_role
):
    base, draft = published
    peer, _ = staff_member(client, csrf, account_payload, staff_role)
    assert login(client, csrf, peer).status_code == 200
    assert client.post(base + "/reports", headers=csrf, json={"reason": "other"}).status_code == 404
    assert login(client, csrf, owner[0]).status_code == 200
    assert (
        client.put(
            base + "/permissions", headers=csrf, json={"email": peer["email"], "allowed": True}
        ).status_code
        == 200
    )
    assert login(client, csrf, peer).status_code == 200
    submit(client, csrf, base)
    saved = report_row(client, draft["id"])
    assert login(client, csrf, owner[0]).status_code == 200
    assert (
        client.put(
            base + "/permissions", headers=csrf, json={"email": peer["email"], "allowed": False}
        ).status_code
        == 200
    )
    assert login(client, csrf, peer).status_code == 200
    assert client.post(base + "/reports", headers=csrf, json={"reason": "other"}).status_code == 404
    assert report_row(client, draft["id"])["revision"] == saved["revision"]
    unready = create(client, csrf)
    assert (
        client.post(
            f"/v1/videos/{unready['id']}/reports", headers=csrf, json={"reason": "other"}
        ).status_code
        == 404
    )


@pytest.mark.parametrize("staff_role", ["moderator", "admin"])
def test_staff_endpoints_live_role_changes_and_private_material_basis(
    client, csrf, owner, published, account_payload, staff_role
):
    base, draft = published
    submit(client, csrf, base)
    saved = report_row(client, draft["id"])
    path = report_path(saved)
    peer, account = register(client, csrf, account_payload)
    assert login(client, csrf, peer).status_code == 200
    reads = (
        "/v1/moderation/reports",
        path,
        path + "/video",
        path + "/hls/master.m3u8",
        path + "/preview.webp",
        path + "/hls/tracks/v1/init.mp4",
    )
    for url in reads:
        assert (
            client.get(
                url, params={"role": "admin", "reporter_id": owner[1]["account_id"]}
            ).status_code
            == 403
        )
    role(client, peer["email"], staff_role)
    assert client.get(base).status_code == 404
    current = client.get(path)
    assert current.status_code == 200 and current.json()["reporter_id"] == owner[1]["account_id"]
    assert client.get("/v1/moderation/reports").json()["items"][0]["id"] == saved["id"]
    material = client.get(path + "/video")
    assert material.status_code == 200 and material.json()["id"] == draft["id"]
    assert material.json()["hls_url"] == "/api" + path + "/hls/master.m3u8"
    assert not any(
        marker in material.text
        for marker in (
            owner[0]["email"],
            peer["email"],
            "owner_id",
            "reporter_id",
            "X-Amz",
            "encoded/",
        )
    )
    master = client.get(path + "/hls/master.m3u8")
    assert master.status_code == 200 and "#EXTM3U" in master.text
    assert "tracks/v1/index.m3u8" in master.text and "/videos/" not in master.text
    for url in (
        path + "/hls/tracks/v1/index.m3u8",
        path + "/hls/tracks/v1/init.mp4",
        path + "/preview.webp",
    ):
        assert client.get(url).status_code == 200
        assert client.head(url).status_code == 200
    accesses = rows(
        client,
        "SELECT * FROM moderation_accesses WHERE report_id=:id ORDER BY id",
        {"id": saved["id"]},
    )
    assert len(accesses) == 8 and all(
        row["actor_id"] == account["account_id"]
        and row["revision"] == 1
        and row["reason"] == "spam"
        for row in accesses
    )
    assert client.get(base).status_code == 404
    assert client.get("/v1/moderation/reports/4611686018427387904/video").status_code == 404
    role(client, peer["email"], "user")
    for url in reads:
        assert client.get(url).status_code == 403
    assert len(
        rows(client, "SELECT id FROM moderation_accesses WHERE report_id=:id", {"id": saved["id"]})
    ) == len(accesses)
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    for url in reads:
        assert client.get(url).status_code == 401


def test_oldest_first_pages_equal_times_and_resubmission_moves_existing_record(
    client, csrf, owner, published, account_payload
):
    base, draft = published
    assert feed.expose(client, csrf, base).status_code == 200
    staff, _ = staff_member(client, csrf, account_payload)

    async def seed():
        container = client.app.state.dishka_container
        authors = []
        for _ in range(43):
            async with container() as operation:
                async with (await operation.get(AsyncSession)).begin():
                    account = await (await operation.get(CreateAccount))(
                        NewAccount("Автор", uuid4().hex + "@example.com", "abcdef", "abcdef")
                    )
                    authors.append(account.id)
            async with container() as operation:
                await (await operation.get(SubmitReport))(account.id, draft["id"], "other")
        async with client.app.state.resources.sessions.begin() as session:
            await session.execute(
                text("UPDATE moderation_reports SET submitted_at='2026-01-02 03:04:05.123456+00'")
            )
        return authors

    authors = client.portal.call(seed)
    expected = rows(client, "SELECT id FROM moderation_reports ORDER BY id")
    assert login(client, csrf, staff).status_code == 200
    result, cursor, sizes = [], None, []
    while True:
        page = client.get(
            "/v1/moderation/reports", params={"cursor": cursor} if cursor else {}
        ).json()
        result.extend(item["id"] for item in page["items"])
        sizes.append(len(page["items"]))
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert result == [item["id"] for item in expected] and sizes == [20, 20, 3]

    async def repeat():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SubmitReport))(authors[0], draft["id"], "copyright")

    client.portal.call(repeat)
    first_page = client.get("/v1/moderation/reports").json()
    assert first_page["items"][0]["id"] == expected[1]["id"]
    moved = rows(
        client,
        "SELECT id,revision,reason FROM moderation_reports "
        "ORDER BY submitted_at DESC,id DESC LIMIT 1",
    )[0]
    assert moved == {"id": expected[0]["id"], "revision": 2, "reason": "copyright"}


def test_dismiss_repeat_new_revision_stale_decision_and_history(
    client, csrf, owner, published, account_payload
):
    base, draft = published
    submit(client, csrf, base)
    saved = report_row(client, draft["id"])
    path = report_path(saved)
    staff, account = staff_member(client, csrf, account_payload)
    assert login(client, csrf, staff).status_code == 200
    command = {
        "revision": 1,
        "action": "dismiss",
        "actor_id": owner[1]["account_id"],
        "role": "user",
    }
    result = client.put(path + "/decision", headers=csrf, json=command)
    assert result.status_code == 200 and result.json()["state"] == "reviewed"
    assert result.json()["decision"]["actor_id"] == account["account_id"]
    assert client.put(path + "/decision", headers=csrf, json=command).json() == result.json()
    assert client.get("/v1/moderation/reports").json() == {"items": [], "next_cursor": None}
    assert client.get(path + "/video").status_code == 404
    assert login(client, csrf, owner[0]).status_code == 200
    assert client.get(base).status_code == 200
    submit(client, csrf, base, "copyright")
    assert login(client, csrf, staff).status_code == 200
    renewed = client.get(path).json()
    assert (
        renewed["revision"] == 2 and renewed["state"] == "pending" and renewed["decision"] is None
    )
    assert client.put(path + "/decision", headers=csrf, json=command).status_code == 409
    hidden = client.put(
        path + "/decision", headers=csrf, json={"revision": 2, "action": "hide", "reason": "other"}
    )
    assert hidden.status_code == 200 and hidden.json()["decision"]["reason"] == "other"
    history = rows(
        client, "SELECT revision,action,reason,actor_id FROM moderation_decisions ORDER BY revision"
    )
    assert [(item["revision"], item["action"], item["reason"]) for item in history] == [
        (1, "dismiss", None),
        (2, "hide", "other"),
    ]
    assert client.get(path + "/hls/master.m3u8").status_code == 404
    assert login(client, csrf, owner[0]).status_code == 200
    assert client.get(base).status_code == 404
    assert client.post(base + "/reports", headers=csrf, json={"reason": "other"}).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"revision": 1, "action": "hide"},
        {"revision": 1, "action": "hide", "reason": "free text"},
        {"revision": 1, "action": "restore"},
        {"revision": True, "action": "dismiss"},
        {"revision": "1", "action": "dismiss"},
        {"revision": 0, "action": "dismiss"},
        {"revision": 2**63, "action": "dismiss"},
    ],
)
def test_invalid_decision_and_csrf_never_mutate(
    client, csrf, owner, published, account_payload, body
):
    base, draft = published
    submit(client, csrf, base)
    saved = report_row(client, draft["id"])
    staff, _ = staff_member(client, csrf, account_payload)
    assert login(client, csrf, staff).status_code == 200
    endpoint = report_path(saved) + "/decision"
    assert client.put(endpoint, json={"revision": 1, "action": "dismiss"}).status_code == 403
    assert client.put(endpoint, headers=csrf, json=body).status_code == 422
    assert report_row(client, draft["id"])["state"] == "pending"
    assert rows(client, "SELECT report_id FROM moderation_decisions") == []


def test_hidden_video_disappears_everywhere_but_other_pending_report_can_be_reviewed(
    client, csrf, owner, published, account_payload
):
    base, draft = published
    assert feed.expose(client, csrf, base).status_code == 200
    before = client.get(base + "/settings").json()
    cover = playlist(client, csrf, "public", title="Moderation playlist")
    assert (
        client.put(
            base + "/playlists", headers=csrf, json={"playlist_ids": [cover["id"]]}
        ).status_code
        == 200
    )
    comment = client.post(base + "/comments", headers=csrf, json={"text": "discussion"}).json()
    assert client.put(base + "/like", headers=csrf, json={"liked": True}).status_code == 200
    staff, _ = staff_member(client, csrf, account_payload)
    assert (
        client.put(
            base + "/permissions", headers=csrf, json={"email": staff["email"], "allowed": True}
        ).status_code
        == 200
    )
    submit(client, csrf, base)
    saved = report_row(client, draft["id"])
    expected_updated = client.get(base + "/settings").json()["updated_at"]
    assert login(client, csrf, staff).status_code == 200
    submit(client, csrf, base, "other")
    pending = rows(
        client,
        "SELECT id FROM moderation_reports WHERE video_id=:id AND id!=:first",
        {"id": draft["id"], "first": saved["id"]},
    )[0]
    command = {"revision": 1, "action": "hide", "reason": "violence"}
    assert (
        client.put(report_path(saved) + "/decision", headers=csrf, json=command).status_code == 200
    )
    assert (
        client.put(report_path(saved) + "/decision", headers=csrf, json=command).status_code == 200
    )
    assert client.get(report_path(pending) + "/video").status_code == 200
    assert client.get(report_path(pending) + "/hls/master.m3u8").status_code == 200
    for credentials in (staff, owner[0]):
        assert login(client, csrf, credentials).status_code == 200
        for suffix in (
            "",
            "/preview.webp",
            "/hls/master.m3u8",
            "/hls/tracks/v1/index.m3u8",
            "/hls/tracks/v1/init.mp4",
            "/hls/tracks/v1/segment_000000.m4s",
            "/like",
            "/comments",
            f"/comments/{comment['id']}/like",
        ):
            assert client.get(base + suffix).status_code == 404
        assert client.put(base + "/like", headers=csrf, json={"liked": False}).status_code == 404
        assert (
            client.post(base + "/comments", headers=csrf, json={"text": "blocked"}).status_code
            == 404
        )
        assert draft["id"] not in [
            item["id"] for item in client.get("/v1/videos/feed").json()["items"]
        ]
        assert draft["id"] not in [
            item["video"]["id"]
            for item in client.get(
                "/v1/search", params={"q": draft["title"], "type": "video"}
            ).json()["items"]
        ]
        assert client.get(f"/v1/channels/{owner[0]['tag']}/videos").json()["items"] == []
        assert client.get(f"/v1/playlists/{cover['id']}").json()["items"] == []
    after = client.get(base + "/settings").json()
    assert after["moderation"] == "hidden" and after["added_at"] == before["added_at"]
    assert after["updated_at"] == expected_updated
    visibility(client, csrf, base, "private")
    visibility(client, csrf, base, "public")
    assert client.post(base + "/publish", headers=csrf).json()["moderation"] == "hidden"
    assert client.get(base).status_code == 404
    assert login(client, csrf, staff).status_code == 200
    assert (
        client.put(
            report_path(pending) + "/decision",
            headers=csrf,
            json={"revision": 1, "action": "dismiss"},
        ).status_code
        == 200
    )
    assert client.get(base).status_code == 404


@pytest.mark.parametrize("stage", ["hide", "decision", "report"])
def test_failed_hiding_or_decision_rolls_back_all_state(
    client, csrf, owner, published, account_payload, stage
):
    base, draft = published
    submit(client, csrf, base)
    saved = report_row(client, draft["id"])
    staff, _ = staff_member(client, csrf, account_payload)
    assert login(client, csrf, staff).status_code == 200
    prefixes = {
        "hide": "UPDATE video_publications",
        "decision": "INSERT INTO moderation_decisions",
        "report": "UPDATE moderation_reports",
    }

    def fail(_, __, statement, *args):
        if statement.startswith(prefixes[stage]):
            raise SQLAlchemyError("sensitive-moderation-write")

    with query_hook(client, fail):
        response = client.put(
            report_path(saved) + "/decision",
            headers=csrf,
            json={"revision": 1, "action": "hide", "reason": "other"},
        )
        assert response.status_code == 500 and "sensitive-moderation-write" not in response.text
    assert report_row(client, draft["id"])["state"] == "pending"
    assert rows(client, "SELECT report_id FROM moderation_decisions") == []
    assert login(client, csrf, owner[0]).status_code == 200
    assert (
        client.get(base).status_code == 200
        and client.get(base + "/settings").json()["moderation"] == "visible"
    )


@pytest.mark.parametrize(
    "suffix", ["/video", "/hls/master.m3u8", "/hls/tracks/v1/init.mp4", "/preview.webp"]
)
def test_failed_access_evidence_never_returns_private_material(
    client, csrf, owner, published, account_payload, suffix
):
    base, draft = published
    submit(client, csrf, base)
    saved = report_row(client, draft["id"])
    staff, _ = staff_member(client, csrf, account_payload)
    assert login(client, csrf, staff).status_code == 200

    def fail(_, __, statement, *args):
        if statement.startswith("INSERT INTO moderation_accesses"):
            raise SQLAlchemyError("sensitive-audit-write")

    with query_hook(client, fail):
        response = client.get(report_path(saved) + suffix)
        assert (
            response.status_code == 500
            and "private-marker" not in response.text
            and "#EXTM3U" not in response.text
        )
        assert (
            "x-accel-redirect" not in response.headers
            and "sensitive-audit-write" not in response.text
        )
    assert rows(client, "SELECT id FROM moderation_accesses") == []


def test_concurrent_submissions_decisions_and_valkey_outage(
    client, csrf, owner, published, account_payload
):
    base, draft = published
    assert feed.expose(client, csrf, base).status_code == 200
    staff, staff_ids = staff_member(client, csrf, account_payload)

    async def exercise():
        container = client.app.state.dishka_container

        async def invoke(kind, *args):
            async with container() as operation:
                return await (await operation.get(kind))(*args)

        actor = owner[1]["account_id"]
        await asyncio.gather(*[invoke(SubmitReport, actor, draft["id"], "other") for _ in range(8)])
        async with client.app.state.resources.sessions() as session:
            report = (
                (await session.execute(text("SELECT * FROM moderation_reports"))).mappings().one()
            )
        assert report["revision"] == 8
        decision = DecisionCommand(8, "dismiss")
        results = await asyncio.gather(
            *[
                invoke(DecideReport, staff_ids["account_id"], report["id"], decision)
                for _ in range(8)
            ]
        )
        assert all(value == results[0] for value in results)
        with patch(
            "redis.asyncio.Redis.execute_command", side_effect=ConnectionError("synthetic outage")
        ):
            await invoke(SubmitReport, actor, draft["id"], "spam")
            page = await invoke(GetReports, staff_ids["account_id"])
            assert page.items[0].revision == 9 and page.items[0].decision is None
            await invoke(GetReportedVideo, staff_ids["account_id"], report["id"])
            outcomes = await asyncio.gather(
                invoke(
                    DecideReport,
                    staff_ids["account_id"],
                    report["id"],
                    DecisionCommand(9, "dismiss"),
                ),
                invoke(
                    DecideReport,
                    staff_ids["account_id"],
                    report["id"],
                    DecisionCommand(9, "hide", "other"),
                ),
                return_exceptions=True,
            )
            assert sum(isinstance(value, ReportConflict) for value in outcomes) == 1

    client.portal.call(exercise)
    assert len(rows(client, "SELECT * FROM moderation_decisions")) == 2


@pytest.mark.parametrize("kind", ["queue", "decision", "video", "media"])
@pytest.mark.parametrize("commit", [False, True])
def test_waiting_staff_request_rechecks_demotion_after_account_lock(
    client, csrf, owner, published, account_payload, kind, commit
):
    base, draft = published
    submit(client, csrf, base)
    saved = report_row(client, draft["id"])
    _, staff_ids = staff_member(client, csrf, account_payload)

    async def exercise():
        resources, container = client.app.state.resources, client.app.state.dishka_container

        async def call():
            async with container() as operation:
                actor = staff_ids["account_id"]
                if kind == "queue":
                    return await (await operation.get(GetReports))(actor)
                if kind == "decision":
                    return await (await operation.get(DecideReport))(
                        actor, saved["id"], DecisionCommand(1, "dismiss")
                    )
                if kind == "video":
                    return await (await operation.get(GetReportedVideo))(actor, saved["id"])
                return await (await operation.get(GetReportedMedia))(
                    actor, saved["id"], None, "master.m3u8"
                )

        async with resources.sessions() as blocker:
            await blocker.begin()
            pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
            await blocker.execute(
                text("UPDATE user_accounts SET role='user' WHERE id=:id"),
                {"id": staff_ids["account_id"]},
            )
            pending = asyncio.create_task(call())
            await wait_for_database_waiter(resources.engine, pid)
            assert not pending.done()
            await blocker.commit() if commit else await blocker.rollback()
            if commit:
                with pytest.raises(ModerationForbidden):
                    await pending
            else:
                await pending

    client.portal.call(exercise)
    if commit:
        assert report_row(client, draft["id"])["state"] == "pending"
        assert rows(client, "SELECT * FROM moderation_accesses") == []


@pytest.mark.parametrize("commit", [False, True])
def test_review_rechecks_revision_after_video_lock_not_initial_identity_map(
    client, csrf, owner, published, account_payload, commit
):
    base, draft = published
    submit(client, csrf, base)
    saved = report_row(client, draft["id"])
    _, staff_ids = staff_member(client, csrf, account_payload)

    async def exercise():
        resources, container = client.app.state.resources, client.app.state.dishka_container

        async def decide():
            async with container() as operation:
                return await (await operation.get(DecideReport))(
                    staff_ids["account_id"], saved["id"], DecisionCommand(1, "hide", "other")
                )

        async with resources.sessions() as blocker:
            await blocker.begin()
            pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
            await blocker.execute(
                text("SELECT id FROM video_publications WHERE id=:id FOR SHARE"),
                {"id": draft["id"]},
            )
            await blocker.execute(
                text(
                    "UPDATE moderation_reports SET revision=revision+1,reason='copyright' "
                    "WHERE id=:id"
                ),
                {"id": saved["id"]},
            )
            pending = asyncio.create_task(decide())
            await wait_for_database_waiter(resources.engine, pid)
            await blocker.commit() if commit else await blocker.rollback()
            if commit:
                with pytest.raises(ReportConflict):
                    await pending
            else:
                await pending

    client.portal.call(exercise)
    assert report_row(client, draft["id"])["state"] == ("pending" if commit else "reviewed")


@pytest.mark.parametrize("change", ["private", "hidden"])
@pytest.mark.parametrize("commit", [False, True])
def test_waiting_submission_rechecks_normal_video_access(
    client, csrf, owner, published, account_payload, change, commit
):
    base, draft = published
    assert feed.expose(client, csrf, base).status_code == 200
    _, peer = register(client, csrf, account_payload)

    async def exercise():
        resources, container = client.app.state.resources, client.app.state.dishka_container

        async def send():
            async with container() as operation:
                await (await operation.get(SubmitReport))(peer["account_id"], draft["id"], "other")

        async with resources.sessions() as blocker:
            await blocker.begin()
            pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
            statement = (
                "UPDATE video_publications SET visibility='private' WHERE id=:id"
                if change == "private"
                else "UPDATE video_publications SET moderation='hidden' WHERE id=:id"
            )
            await blocker.execute(text(statement), {"id": draft["id"]})
            pending = asyncio.create_task(send())
            await wait_for_database_waiter(resources.engine, pid)
            await blocker.commit() if commit else await blocker.rollback()
            if commit:
                with pytest.raises(ReportNotFound):
                    await pending
            else:
                await pending

    client.portal.call(exercise)
    assert len(rows(client, "SELECT id FROM moderation_reports")) == (0 if commit else 1)


@pytest.mark.parametrize(
    "token",
    ["", "x", "x" * 10000, "../etc/passwd", "';DROP TABLE moderation_reports;--", "<script>"],
)
def test_cursor_rejection_and_staff_schema(client, csrf, owner, published, account_payload, token):
    staff, _ = staff_member(client, csrf, account_payload)
    assert login(client, csrf, staff).status_code == 200
    response = client.get("/v1/moderation/reports", params={"cursor": token})
    assert response.status_code == 422 and response.json()["code"] == "invalid_cursor"
    assert token == "" or token not in response.text
    assert (
        client.get("/v1/moderation/reports", params=[("cursor", "x"), ("cursor", "x")]).status_code
        == 422
    )
    schema = client.get("/openapi.json").json()
    for path in (
        "/v1/moderation/reports",
        "/v1/moderation/reports/{report_id}",
        "/v1/moderation/reports/{report_id}/video",
        "/v1/moderation/reports/{report_id}/hls/master.m3u8",
    ):
        assert schema["paths"][path]["get"]["security"] == [{"SessionCookie": []}]
    action = schema["paths"]["/v1/moderation/reports/{report_id}/decision"]["put"]
    assert action["security"] == [{"SessionCookie": [], "CSRFToken": []}]
    body = action["requestBody"]["content"]["application/json"]["schema"]
    assert body["discriminator"]["propertyName"] == "action"
