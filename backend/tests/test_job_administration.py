"""Real HTTP authority, bounded durable intake, overlap and private media retries."""

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from app.job_administration import JobAdministration
from app.settings import Settings
from common.infrastructure.job_administration import SqlJobAdministration
from common.infrastructure.job_error_model import JobErrorRow
from common.infrastructure.job_errors import DurableJobErrors, ErrorSpool, PostgresJobErrorStore
from common.infrastructure.job_monitor import error_history_middleware
from tests import test_video_access_http as access
from tests.test_moderation_http import role
from tests.test_uploads_http import send
from tests.test_videos_http import create, login
from video.public import InspectSource, TemporarySourceError

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture(scope="module", autouse=True)
def media_cleanup(backend_runtime):
    yield from access.clean_media.__wrapped__(backend_runtime)


@pytest.fixture(autouse=True)
def reset_admin_state(client):
    rows(client, "DELETE FROM background_job_retry_items")
    rows(client, "DELETE FROM background_job_retry_requests")
    rows(client, "DELETE FROM background_job_errors")
    # Only this module's isolated queue; no worker should race synthetic states.
    rows(
        client, "UPDATE procrastinate_jobs SET status='succeeded' WHERE status IN ('todo','doing')"
    )


@pytest.fixture
def admin(client, csrf, registered_account):
    data, account = registered_account
    role(client, data["email"], "admin")
    assert login(client, csrf, data).status_code == 200
    return data, account


@pytest.fixture
def owner(client, csrf, registered_account):
    return access.owner.__wrapped__(client, csrf, registered_account)


@pytest.fixture
def published(client, csrf, owner, source_queue):
    return access.published.__wrapped__(client, csrf, owner, source_queue)


def rows(client, statement, values=None):
    async def read():
        async with client.app.state.resources.sessions.begin() as session:
            result = await session.execute(text(statement), values or {})
            return [dict(row) for row in result.mappings()] if result.returns_rows else []

    return client.portal.call(read)


def record_error(client, job_id, *, at=None):
    async def write():
        async with client.app.state.resources.sessions.begin() as session:
            row = JobErrorRow(
                event_id=uuid4(),
                job_id=job_id,
                attempt=1,
                stage="execute",
                occurred_at=at or datetime.now(UTC),
                reason="task_failed",
                exception_type="RuntimeError",
                trace=[],
                error_code="encoding_failed",
            )
            session.add(row)
            await session.flush()
            return row.id

    return client.portal.call(write)


def pump(client, size=20):
    async def run():
        async with client.app.state.dishka_container() as operation:
            return await (await operation.get(JobAdministration)).dispatch(size)

    return client.portal.call(run)


def native(client, task="video.encode_track", status="failed"):
    return rows(
        client,
        "INSERT INTO procrastinate_jobs(task_name,queue_name,status,args) "
        "VALUES(:task,'media',CAST(:status AS procrastinate_job_status),"
        "CAST(:args AS jsonb)) RETURNING id",
        {
            "task": task,
            "status": status,
            "args": json.dumps({"secret": "must-not-leak", "url": "http://invalid.example"}),
        },
    )[0]["id"]


def failed_source(client, csrf, value=10):
    draft = create(client, csrf)
    base = f"/v1/videos/{draft['id']}"
    data = b"P6\n1 1\n255\n" + bytes((value, 20, 30))
    transfer = client.post(base + "/upload", json={"size": len(data)}, headers=csrf).json()
    assert send(transfer["parts"][0]["url"], data) == 200
    assert client.post(f"{base}/upload/{transfer['id']}/complete", headers=csrf).status_code == 200
    source = rows(
        client,
        "SELECT job_id FROM video_source_processing WHERE upload_id=:id",
        {"id": transfer["id"]},
    )[0]
    rows(
        client,
        "UPDATE video_source_processing SET status='failed',token=NULL,"
        "error_code='inspection_failed' WHERE job_id=:id",
        {"id": source["job_id"]},
    )
    rows(
        client,
        "UPDATE procrastinate_jobs SET status='failed' WHERE id=:id",
        {"id": source["job_id"]},
    )
    record_error(client, source["job_id"])
    return source["job_id"], transfer["id"]


def test_admin_checks_current_role_csrf_and_never_caches_authority(
    client, csrf, registered_account
):
    data, _ = registered_account
    assert login(client, csrf, data).status_code == 200
    for value in ("user", "moderator"):
        role(client, data["email"], value)
        assert client.get("/v1/admin/job-errors").status_code == 403
        assert (
            client.post("/v1/admin/job-retries", json={"mode": "all"}, headers=csrf).status_code
            == 403
        )
    role(client, data["email"], "admin")
    response = client.get("/v1/admin/job-errors")
    assert response.status_code == 200 and response.json() == {"items": [], "next_cursor": None}
    assert (
        response.headers["cache-control"] == "no-store"
        and "cookie" in response.headers["vary"].lower()
    )
    assert client.post("/v1/admin/job-retries", json={"mode": "all"}).status_code == 403
    accepted = client.post("/v1/admin/job-retries", json={"mode": "all"}, headers=csrf)
    assert accepted.status_code == 202
    path = f"/v1/admin/job-retries/{accepted.json()['id']}"
    role(client, data["email"], "user")
    assert client.get(path).status_code == 403
    assert pump(client) == 0
    assert (
        rows(client, "SELECT status FROM background_job_retry_requests")[0]["status"] == "cancelled"
    )
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert client.get("/v1/admin/job-errors").status_code == 401
    assert client.get(path).status_code == 401
    assert client.post("/v1/admin/jobs/1/retry", headers=csrf).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"mode": "selected", "job_ids": []},
        {"mode": "selected", "job_ids": [True]},
        {"mode": "selected", "job_ids": [0]},
        {"mode": "selected", "job_ids": [-1]},
        {"mode": "selected", "job_ids": [2**63]},
        {"mode": "selected", "job_ids": ["1"]},
        {"mode": "all", "task": "os.system", "args": {"cmd": "bad"}},
        {"mode": "all", "role": "admin"},
        {"mode": "selected", "job_ids": "1,2"},
    ],
)
def test_invalid_mutations_are_atomic_and_accept_no_execution_payload(client, csrf, admin, body):
    assert client.post("/v1/admin/job-retries", json=body, headers=csrf).status_code == 422
    assert rows(client, "SELECT id FROM background_job_retry_requests") == []
    assert rows(client, "SELECT job_id FROM background_job_retry_items") == []


def test_history_is_stable_newest_first_after_success_pruning_and_buffer_replay(client, admin):
    at = datetime.now(UTC)
    expected = []
    for _ in range(43):
        expected.append(record_error(client, native(client, status="succeeded"), at=at))
    old = record_error(client, native(client), at=at - timedelta(days=1))
    rows(client, "DELETE FROM procrastinate_jobs WHERE args ? 'secret'")
    response = client.get("/v1/admin/job-errors")
    found = []
    while True:
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["items"]) <= 20
        found.extend(item["id"] for item in payload["items"])
        assert "must-not-leak" not in response.text and "invalid.example" not in response.text
        if payload["next_cursor"] is None:
            break
        response = client.get("/v1/admin/job-errors", params={"cursor": payload["next_cursor"]})
    assert found == list(reversed(expected)) + [old]
    assert client.get("/v1/admin/job-errors?cursor=x&cursor=y").status_code == 422


@pytest.mark.parametrize(
    "cursor", ["", "bad", "../etc/passwd", "x" * 257, "W10", "bnVsbA", "JztEUk9QOwo"]
)
def test_bad_error_cursors_are_rejected(client, admin, cursor):
    response = client.get("/v1/admin/job-errors", params={"cursor": cursor})
    assert response.status_code == 422 and response.headers["cache-control"] == "no-store"


def test_all_is_durable_bounded_and_reports_every_result_with_own_cursor(client, csrf, admin):
    ids = [native(client) for _ in range(45)]
    for job_id in ids:
        record_error(client, job_id)
    response = client.post("/v1/admin/job-retries", json={"mode": "all"}, headers=csrf)
    assert response.status_code == 202 and response.json()["items"] == []
    request_id = response.json()["id"]
    assert rows(
        client,
        "SELECT selection FROM background_job_retry_requests WHERE id=:id",
        {"id": request_id},
    ) == [{"selection": None}]
    assert rows(client, "SELECT job_id FROM background_job_retry_items") == []
    late = native(client)
    record_error(client, late)
    for expected in (20, 40, 45):
        assert pump(client) == 0  # No canonical Video row: no arbitrary native payload is replayed.
        assert len(rows(client, "SELECT job_id FROM background_job_retry_items")) == expected
    assert pump(client) == 0
    path = f"/v1/admin/job-retries/{request_id}"
    value = client.get(path).json()
    assert value["status"] == "submitted" and value["unavailable"] == 45
    seen = []
    while True:
        seen.extend(item["job_id"] for item in value["items"])
        if value["next_cursor"] is None:
            break
        value = client.get(path, params={"cursor": value["next_cursor"]}).json()
    assert seen == ids and late not in seen
    assert client.get(path, params={"cursor": "job-retry-v1:999999:1"}).status_code == 422
    assert client.get("/v1/admin/job-retries/9223372036854775807").status_code == 404


def test_portions_wait_for_normal_queue_and_overlapping_requests_coalesce(client, csrf, admin):
    jobs = [failed_source(client, csrf, value)[0] for value in (31, 32, 33, 34)]
    normal = native(client, task="video.inspect_source", status="todo")
    selected = client.post(
        "/v1/admin/job-retries",
        json={"mode": "selected", "job_ids": [jobs[0], jobs[0]]},
        headers=csrf,
    ).json()
    all_request = client.post("/v1/admin/job-retries", json={"mode": "all"}, headers=csrf).json()
    assert pump(client, 2) == 1  # One ordinary job leaves one admission slot.
    assert pump(client, 2) == 0
    assert len(rows(client, "SELECT * FROM background_job_retry_items WHERE status='queued'")) == 1
    assert (
        len(
            rows(
                client,
                "SELECT id FROM procrastinate_jobs WHERE status='todo' AND queue_name='media'",
            )
        )
        == 2
    )
    rows(
        client,
        "UPDATE procrastinate_jobs SET status='succeeded' "
        "WHERE status='todo' AND queue_name='media'",
    )
    for _ in range(8):
        pump(client, 2)
        rows(
            client,
            "UPDATE procrastinate_jobs SET status='succeeded' "
            "WHERE status='todo' AND queue_name='media'",
        )
    queued = rows(
        client,
        "SELECT job_id,replacement_job_id FROM background_job_retry_items WHERE status='queued'",
    )
    assert sorted(item["job_id"] for item in queued) == jobs
    assert len({item["replacement_job_id"] for item in queued}) == 4
    assert normal not in {item["job_id"] for item in queued}
    for request in (selected, all_request):
        assert client.get(f"/v1/admin/job-retries/{request['id']}").json()["status"] == "submitted"
    assert rows(
        client,
        "SELECT status FROM background_job_retry_items WHERE request_id=:id AND job_id=:job",
        {"id": all_request["id"], "job": jobs[0]},
    ) == [{"status": "skipped"}]


def test_mid_batch_failure_rolls_back_queue_canonical_state_audit_and_cursor(client, csrf, admin):
    job_id, upload_id = failed_source(client, csrf)
    accepted = client.post(f"/v1/admin/jobs/{job_id}/retry", headers=csrf).json()

    def fail_audit(conn, cursor, statement, parameters, context, many):
        if "INSERT INTO background_job_retry_items" in statement:
            raise RuntimeError("synthetic audit outage")

    engine = client.app.state.resources.engine.sync_engine
    event.listen(engine, "before_cursor_execute", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="synthetic audit outage"):
            pump(client)
    finally:
        event.remove(engine, "before_cursor_execute", fail_audit)
    assert rows(
        client,
        "SELECT job_id,status FROM video_source_processing WHERE upload_id=:id",
        {"id": upload_id},
    ) == [{"job_id": job_id, "status": "failed"}]
    assert client.get(f"/v1/admin/job-retries/{accepted['id']}").json()["queued"] == 0
    assert rows(
        client,
        "SELECT after_job_id FROM background_job_retry_requests WHERE id=:id",
        {"id": accepted["id"]},
    ) == [{"after_job_id": 0}]
    assert pump(client) == 1


def test_concurrent_dispatchers_and_revoked_role_keep_admission_atomic(client, csrf, admin):
    job_id, _ = failed_source(client, csrf)
    client.post(f"/v1/admin/jobs/{job_id}/retry", headers=csrf)
    client.post("/v1/admin/job-retries", json={"mode": "all"}, headers=csrf)

    async def concurrent():
        async def dispatch():
            async with client.app.state.dishka_container() as operation:
                return await (await operation.get(JobAdministration)).dispatch(20)

        return await asyncio.gather(dispatch(), dispatch())

    assert sum(client.portal.call(concurrent)) == 1
    role(client, admin[0]["email"], "moderator")
    assert pump(client) == 0
    assert (
        len(rows(client, "SELECT job_id FROM background_job_retry_items WHERE status='queued'"))
        == 1
    )


def test_real_retry_decodes_again_but_preserves_ready_shared_media_dates_and_hidden_state(
    client, csrf, owner, published, source_queue, tmp_path
):
    base, draft = published
    role(client, owner[0]["email"], "admin")
    duplicate, _ = access.published.__wrapped__(client, csrf, owner, source_queue)
    rows(
        client,
        "UPDATE video_publications SET moderation='hidden' WHERE id=:id",
        {"id": draft["id"]},
    )
    before = client.get(base + "/settings").json()
    duplicate_before = client.get(duplicate + "/settings").json()
    output = rows(
        client,
        "SELECT id,job_id,artifact FROM video_encoded_tracks "
        "WHERE spec->>'kind'='video' ORDER BY id DESC LIMIT 1",
    )[0]
    # A native post-commit failure must only acknowledge an already ready package.
    rows(
        client,
        "UPDATE procrastinate_jobs SET status='failed' WHERE id=:id",
        {"id": output["job_id"]},
    )
    record_error(client, output["job_id"])
    client.post(f"/v1/admin/jobs/{output['job_id']}/retry", headers=csrf)
    assert pump(client) == 1
    current = rows(
        client,
        "SELECT job_id,artifact FROM video_encoded_tracks WHERE id=:id",
        {"id": output["id"]},
    )[0]
    assert current["job_id"] != output["job_id"] and current["artifact"] == output["artifact"]
    rows(
        client,
        "UPDATE procrastinate_jobs SET args=CAST(:args AS jsonb) WHERE id=:id",
        {
            "id": output["job_id"],
            "args": json.dumps({"encoded_id": 2**62, "command": "must-not-run"}),
        },
    )

    async def execute():
        queue = source_queue(client.app.state.dishka_container)
        journal = DurableJobErrors(
            ErrorSpool(tmp_path / "history"),
            PostgresJobErrorStore(client.app.state.resources.sessions),
        )
        async with queue.open_async():
            await queue.run_worker_async(
                queues=["media"],
                concurrency=1,
                wait=False,
                listen_notify=False,
                install_signal_handlers=False,
                worker_middleware=[error_history_middleware(journal)],
            )

    client.portal.call(execute)
    assert rows(
        client,
        "SELECT status,attempts,args FROM procrastinate_jobs WHERE id=:id",
        {"id": current["job_id"]},
    ) == [{"status": "succeeded", "attempts": 1, "args": {"encoded_id": output["id"]}}]
    assert client.get(base + "/settings").json() == before
    assert client.get(duplicate + "/settings").json() == duplicate_before
    assert client.get("/v1/admin/job-errors").json()["items"][0]["job_id"] == output["job_id"]
    # Now a true source failure is retried by the real inspector/encoder from RustFS.
    failed_job, source_id = failed_source(client, csrf, 79)
    client.post(f"/v1/admin/jobs/{failed_job}/retry", headers=csrf)
    for _ in range(3):
        pump(client)
    client.portal.call(execute)
    assert rows(
        client, "SELECT status FROM video_source_processing WHERE upload_id=:id", {"id": source_id}
    ) == [{"status": "inspected"}]
    assert rows(
        client,
        "SELECT count(*) AS count FROM background_job_errors WHERE job_id=:id",
        {"id": failed_job},
    ) == [{"count": 1}]


def test_pruned_native_job_can_only_restore_canonical_failed_video_work(client, csrf, admin):
    job_id, upload_id = failed_source(client, csrf)
    rows(client, "DELETE FROM procrastinate_jobs WHERE id=:id", {"id": job_id})
    client.post(f"/v1/admin/jobs/{job_id}/retry", headers=csrf)
    assert pump(client) == 1
    assert (
        rows(
            client,
            "SELECT job_id FROM video_source_processing WHERE upload_id=:id",
            {"id": upload_id},
        )[0]["job_id"]
        != job_id
    )
    assert (
        len(rows(client, "SELECT id FROM background_job_errors WHERE job_id=:id", {"id": job_id}))
        == 1
    )


def test_manual_retry_starts_a_full_three_run_cycle_and_preserves_every_error(
    client, csrf, admin, source_queue, tmp_path
):
    old_job, upload_id = failed_source(client, csrf, 119)
    rows(client, "UPDATE procrastinate_jobs SET attempts=3 WHERE id=:id", {"id": old_job})
    client.post(f"/v1/admin/jobs/{old_job}/retry", headers=csrf)
    assert pump(client) == 1
    new_job = rows(
        client, "SELECT job_id FROM video_source_processing WHERE upload_id=:id", {"id": upload_id}
    )[0]["job_id"]
    assert rows(
        client, "SELECT attempts FROM procrastinate_jobs WHERE id=:id", {"id": new_job}
    ) == [{"attempts": 0}]
    calls = []
    original = InspectSource.__call__

    async def transient(scenario, *args, **kwargs):
        if args[1] == new_job:
            calls.append(kwargs["attempt"])
            if len(calls) < 3:
                raise TemporarySourceError()
        return await original(scenario, *args, **kwargs)

    async def execute():
        queue = source_queue(client.app.state.dishka_container)
        strategy = queue.tasks["video.inspect_source"].retry_strategy
        assert (strategy.max_attempts, strategy.wait, strategy.linear_wait) == (2, 30, 90)
        # Only remove waits in this bounded integration test; retain the production budget.
        strategy.wait = strategy.linear_wait = 0
        journal = DurableJobErrors(
            ErrorSpool(tmp_path / "cycles"),
            PostgresJobErrorStore(client.app.state.resources.sessions),
        )
        with patch.object(InspectSource, "__call__", transient):
            async with queue.open_async():
                await queue.run_worker_async(
                    queues=["media"],
                    concurrency=1,
                    wait=False,
                    listen_notify=False,
                    install_signal_handlers=False,
                    worker_middleware=[error_history_middleware(journal)],
                )

    client.portal.call(execute)
    assert calls == [0, 1, 2]
    assert rows(
        client, "SELECT status,attempts FROM procrastinate_jobs WHERE id=:id", {"id": new_job}
    ) == [{"status": "succeeded", "attempts": 3}]
    assert rows(
        client,
        "SELECT count(*) AS count FROM background_job_errors WHERE job_id=:id",
        {"id": old_job},
    ) == [{"count": 1}]
    assert rows(
        client,
        "SELECT attempt FROM background_job_errors WHERE job_id=:id ORDER BY attempt",
        {"id": new_job},
    ) == [{"attempt": 1}, {"attempt": 2}]


@pytest.mark.parametrize("value", [0, -1, "invalid"])
def test_batch_configuration_requires_positive_integer(value):
    with pytest.raises(ValidationError):
        Settings(job_retry_batch_size=value)


def test_batch_configuration_default_and_override(monkeypatch):
    monkeypatch.delenv("JOB_RETRY_BATCH_SIZE", raising=False)
    assert Settings().job_retry_batch_size == 20
    assert Settings(job_retry_batch_size=3).job_retry_batch_size == 3


def test_new_process_resumes_durable_intention_without_request_state_or_cache(client, csrf, admin):
    job_id, upload_id = failed_source(client, csrf, 173)
    response = client.post(f"/v1/admin/jobs/{job_id}/retry", headers=csrf)
    assert response.status_code == 202 and response.json()["queued"] == 0
    script = """
import asyncio
from app.di import create_container
from app.resources import create_cache
from app.settings import Settings
from app.job_administration import JobAdministration
async def run():
    settings = Settings()
    container = create_container(settings, create_cache(settings, ''), require_cache=False)
    try:
        async with container() as operation:
            dispatcher = await operation.get(JobAdministration)
            print(await dispatcher.dispatch(settings.job_retry_batch_size))
    finally:
        await container.close()
asyncio.run(run())
"""
    result = subprocess.run(  # noqa: S603 -- fixed Python code in the isolated Docker database
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        timeout=30,
    )
    assert result.stdout.strip() == b"1"
    current = rows(
        client,
        "SELECT job_id,status FROM video_source_processing WHERE upload_id=:id",
        {"id": upload_id},
    )[0]
    assert current["job_id"] != job_id and current["status"] == "pending"


@pytest.mark.parametrize(
    "task,args", [("video.cleanup_originals", {}), ("video.expire_uploads", None)]
)
def test_housekeeping_retries_use_registered_payload_not_old_arguments(
    client, csrf, admin, task, args
):
    job_id = native(client, task=task)
    rows(
        client,
        "UPDATE procrastinate_jobs SET queue_name='maintenance' WHERE id=:id",
        {"id": job_id},
    )
    record_error(client, job_id)
    accepted = client.post(f"/v1/admin/jobs/{job_id}/retry", headers=csrf).json()
    assert pump(client) == 1
    item = client.get(f"/v1/admin/job-retries/{accepted['id']}").json()["items"][0]
    job = rows(
        client,
        "SELECT task_name,queue_name,args,attempts FROM procrastinate_jobs WHERE id=:id",
        {"id": item["replacement_job_id"]},
    )[0]
    assert job["task_name"] == task and job["queue_name"] == "maintenance" and job["attempts"] == 0
    if args is None:
        assert set(job["args"]) == {"timestamp"} and type(job["args"]["timestamp"]) is int
    else:
        assert job["args"] == args


def test_new_failure_is_outside_the_request_boundary_and_deleted_source_is_not_reopened(
    client, csrf, admin
):
    job_id, upload_id = failed_source(client, csrf, 149)
    response = client.post(f"/v1/admin/jobs/{job_id}/retry", headers=csrf).json()
    record_error(client, job_id)
    assert pump(client) == 0
    assert (
        client.get(f"/v1/admin/job-retries/{response['id']}").json()["items"][0]["reason"]
        == "changed"
    )
    rows(
        client,
        "UPDATE video_uploads SET original_cleanup_started_at=clock_timestamp() WHERE id=:id",
        {"id": upload_id},
    )
    accepted = client.post(f"/v1/admin/jobs/{job_id}/retry", headers=csrf).json()
    for _ in range(3):
        pump(client)
    value = client.get(f"/v1/admin/job-retries/{accepted['id']}").json()
    assert value["unavailable"] == 1 and value["items"][0]["reason"] == "source_unavailable"
    assert rows(
        client,
        "SELECT job_id,status FROM video_source_processing WHERE upload_id=:id",
        {"id": upload_id},
    ) == [{"job_id": job_id, "status": "failed"}]


def test_unique_admission_is_enforced_by_postgres_and_database_failures_do_not_leak(
    client, csrf, admin
):
    first = client.post("/v1/admin/job-retries", json={"mode": "all"}, headers=csrf).json()["id"]
    second = client.post("/v1/admin/job-retries", json={"mode": "all"}, headers=csrf).json()["id"]
    rows(
        client,
        "INSERT INTO background_job_retry_items(request_id,job_id,replacement_job_id,status) "
        "VALUES(:id,999,1000,'queued')",
        {"id": first},
    )
    with pytest.raises(IntegrityError):
        rows(
            client,
            "INSERT INTO background_job_retry_items(request_id,job_id,replacement_job_id,status) "
            "VALUES(:id,999,1001,'queued')",
            {"id": second},
        )
    assert len(rows(client, "SELECT job_id FROM background_job_retry_items")) == 1
    with patch.object(
        SqlJobAdministration, "errors", side_effect=RuntimeError("secret-diagnostic-marker")
    ):
        response = client.get("/v1/admin/job-errors")
    assert response.status_code == 503 and "secret-diagnostic-marker" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("pruned", [False, True])
def test_failed_encoded_track_is_reencoded_independently_from_original(
    client, csrf, owner, published, source_queue, pruned
):
    base, _ = published
    role(client, owner[0]["email"], "admin")
    before = client.get(base + "/settings").json()
    output = rows(
        client,
        "SELECT id,job_id,artifact FROM video_encoded_tracks "
        "WHERE spec->>'kind'='video' ORDER BY id DESC LIMIT 1",
    )[0]
    retained = rows(
        client,
        "SELECT id,artifact FROM video_encoded_tracks WHERE status='ready' AND id<>:id ORDER BY id",
        {"id": output["id"]},
    )
    rows(
        client,
        "UPDATE video_encoded_tracks SET status='failed',artifact=NULL,"
        "error_code='encoding_failed' WHERE id=:id",
        {"id": output["id"]},
    )
    rows(
        client,
        "UPDATE procrastinate_jobs SET status='failed' WHERE id=:id",
        {"id": output["job_id"]},
    )
    record_error(client, output["job_id"])
    priority = rows(
        client, "SELECT priority FROM procrastinate_jobs WHERE id=:id", {"id": output["job_id"]}
    )[0]["priority"]
    if pruned:
        rows(client, "DELETE FROM procrastinate_jobs WHERE id=:id", {"id": output["job_id"]})
    client.post(f"/v1/admin/jobs/{output['job_id']}/retry", headers=csrf)
    assert pump(client) == 1

    async def execute():
        queue = source_queue(client.app.state.dishka_container)
        async with queue.open_async():
            await queue.run_worker_async(
                queues=["media"],
                concurrency=1,
                wait=False,
                listen_notify=False,
                install_signal_handlers=False,
            )
        # Remove only this test's old, deliberately invalidated package.
        resources = client.app.state.resources
        for item in output["artifact"]["files"]:
            await asyncio.to_thread(
                resources.objects.delete_object,
                Bucket=resources.bucket,
                Key=output["artifact"]["prefix"] + item["name"],
            )

    client.portal.call(execute)
    current = rows(
        client,
        "SELECT job_id,status,artifact FROM video_encoded_tracks WHERE id=:id",
        {"id": output["id"]},
    )[0]
    assert current["status"] == "ready" and current["job_id"] != output["job_id"]
    assert rows(
        client, "SELECT priority FROM procrastinate_jobs WHERE id=:id", {"id": current["job_id"]}
    ) == [{"priority": priority}]
    assert current["artifact"]["prefix"] != output["artifact"]["prefix"]
    assert (
        rows(
            client,
            "SELECT id,artifact FROM video_encoded_tracks "
            "WHERE status='ready' AND id<>:id ORDER BY id",
            {"id": output["id"]},
        )
        == retained
    )
    assert client.get(base + "/settings").json() == before
