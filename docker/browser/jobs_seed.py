"""Synthetic queue failures in the isolated browser database; real admin requests/dispatch."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text
from watch_seed import container

from app.job_administration import JobAdministration
from app.resources import Resources
from common.infrastructure.job_error_model import JobErrorRow
from user.public import SetRole


async def seed_jobs(manifest):
    app = container()
    try:
        async with app() as operation:
            role = await operation.get(SetRole)
            await role(manifest["people"]["owner"]["email"], "admin")
            await role(manifest["people"]["stranger"]["email"], "moderator")
        resources = await app.get(Resources)
        ids = []
        async with resources.sessions.begin() as session:
            for table in (
                "background_job_errors",
                "background_job_retry_requests",
                "procrastinate_jobs",
            ):
                await session.execute(
                    text(
                        "SELECT setval(pg_get_serial_sequence(:table,'id'),9007199254750000,false)"
                    ),
                    {"table": table},
                )
            for i in range(41):
                job = await session.scalar(
                    text(
                        "INSERT INTO procrastinate_jobs(task_name,queue_name,status,args) "
                        "VALUES(:task,'maintenance',CAST(:status AS procrastinate_job_status),"
                        "CAST(:args AS jsonb)) RETURNING id"
                    ),
                    {
                        "task": "video.expire_uploads"
                        if i < 10 or i == 40
                        else "fixture.unsupported",
                        "status": "succeeded" if i == 40 else "failed",
                        "args": '{"secret":"fixture-private-arguments","url":"http://invalid.example"}',
                    },
                )
                ids.append(str(job))
                for attempt in [1, 2] if i == 0 else [1]:
                    session.add(
                        JobErrorRow(
                            event_id=uuid4(),
                            job_id=job,
                            attempt=attempt,
                            stage="execute",
                            occurred_at=datetime.now(UTC),
                            reason="task_failed",
                            exception_type="<img src=x onerror=window.jobsXss=1>",
                            trace=[{"file": "worker.py", "function": "run", "line": 3}],
                            error_code="test_failure",
                        )
                    )
        manifest["jobs"] = ids
    finally:
        await app.close()


async def control(manifest, action):
    app = container()
    try:
        async with app() as operation:
            if action in ("user", "moderator", "admin"):
                await (await operation.get(SetRole))(manifest["people"]["owner"]["email"], action)
            elif action == "pump":
                await (await operation.get(JobAdministration)).dispatch(20)
            else:
                raise ValueError("Invalid fixture command")
        return {"done": True}
    finally:
        await app.close()
