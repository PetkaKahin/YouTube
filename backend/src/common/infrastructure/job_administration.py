"""History and bounded durable intake; module-owned work is restarted through ports."""

import json
from bisect import bisect_right
from datetime import UTC, datetime

from sqlalchemy import func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from common.contracts.job_administration import QUEUES, ErrorCursor, JobAdminNotFound
from common.infrastructure.job_admin_models import JobRetryItemRow, JobRetryRequestRow
from common.infrastructure.job_error_model import JobErrorRow


class SqlJobAdministration:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def errors(self, after: ErrorCursor | None) -> dict[str, object]:
        query = select(JobErrorRow)
        if after is not None:
            query = query.where(
                tuple_(JobErrorRow.occurred_at, JobErrorRow.id) < (after.at, after.id)
            )
        rows = list(
            await self.session.scalars(
                query.order_by(JobErrorRow.occurred_at.desc(), JobErrorRow.id.desc()).limit(21)
            )
        )
        ids = [row.job_id for row in rows[:20]]
        native = {
            row.id: row
            for row in (
                await self.session.execute(
                    text(
                        "SELECT id,status,task_name FROM procrastinate_jobs "
                        "WHERE id=ANY(CAST(:ids AS bigint[]))"
                    ),
                    {"ids": ids},
                )
            ).all()
        }
        replacements = dict(
            (
                await self.session.execute(
                    select(JobRetryItemRow.job_id, JobRetryItemRow.replacement_job_id).where(
                        JobRetryItemRow.job_id.in_(ids), JobRetryItemRow.status == "queued"
                    )
                )
            ).all()
        )
        items = [
            {
                "id": row.id,
                "job_id": row.job_id,
                "attempt": row.attempt,
                "stage": row.stage,
                "occurred_at": row.occurred_at,
                "reason": row.reason,
                "exception_type": row.exception_type,
                "trace": row.trace,
                "error_code": row.error_code,
                "task": native[row.job_id].task_name
                if row.job_id in native and native[row.job_id].task_name in QUEUES
                else None,
                "job_status": native[row.job_id].status if row.job_id in native else None,
                "replacement_job_id": replacements.get(row.job_id),
            }
            for row in rows[:20]
        ]
        return {
            "items": items,
            "next_cursor": ErrorCursor(rows[19].occurred_at, rows[19].id).encode()
            if len(rows) > 20
            else None,
        }

    async def create(self, actor_id: int, selection: list[int] | None) -> dict[str, object]:
        upper = await self.session.scalar(select(func.coalesce(func.max(JobErrorRow.id), 0)))
        row = JobRetryRequestRow(
            actor_id=actor_id, selection=selection, upper_error_id=int(upper or 0)
        )
        self.session.add(row)
        await self.session.flush()
        return await self.details(row.id, 0)

    async def details(self, request_id: int, after: int) -> dict[str, object]:
        row = await self.session.get(JobRetryRequestRow, request_id)
        if row is None:
            raise JobAdminNotFound
        items = list(
            await self.session.scalars(
                select(JobRetryItemRow)
                .where(JobRetryItemRow.request_id == request_id, JobRetryItemRow.job_id > after)
                .order_by(JobRetryItemRow.job_id)
                .limit(21)
            )
        )
        counts = dict(
            (
                await self.session.execute(
                    select(JobRetryItemRow.status, func.count())
                    .where(JobRetryItemRow.request_id == request_id)
                    .group_by(JobRetryItemRow.status)
                )
            ).all()
        )
        return {
            "id": row.id,
            "actor_id": row.actor_id,
            "mode": "all" if row.selection is None else "selected",
            "status": row.status,
            "created_at": row.created_at,
            "queued": counts.get("queued", 0),
            "skipped": counts.get("skipped", 0),
            "unavailable": counts.get("unavailable", 0),
            "items": [
                {
                    "job_id": item.job_id,
                    "replacement_job_id": item.replacement_job_id,
                    "status": item.status,
                    "reason": item.reason,
                }
                for item in items[:20]
            ],
            "next_cursor": f"job-retry-v1:{request_id}:{items[19].job_id}"
            if len(items) > 20
            else None,
        }

    async def next_request(self) -> JobRetryRequestRow | None:
        return await self.session.scalar(
            select(JobRetryRequestRow)
            .where(JobRetryRequestRow.status == "pending")
            .order_by(JobRetryRequestRow.served_at.asc().nulls_first(), JobRetryRequestRow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    async def candidates(self, request: JobRetryRequestRow, size: int) -> list[int]:
        if request.selection is not None:
            start = bisect_right(request.selection, request.after_job_id)
            return request.selection[start : start + size]
        return list(
            await self.session.scalars(
                text(
                    "SELECT DISTINCT ON (e.job_id) e.job_id FROM background_job_errors e "
                    "LEFT JOIN procrastinate_jobs j ON j.id=e.job_id "
                    "WHERE e.id<=:upper AND e.job_id>:after "
                    "AND (j.id IS NULL OR j.status='failed') "
                    "ORDER BY e.job_id LIMIT :size"
                ),
                {"upper": request.upper_error_id, "after": request.after_job_id, "size": size},
            )
        )

    async def task(self, job_id: int, upper: int) -> tuple[str | None, str | None]:
        latest = await self.session.scalar(
            select(JobErrorRow.id)
            .where(JobErrorRow.job_id == job_id)
            .order_by(JobErrorRow.id.desc())
            .limit(1)
        )
        if latest is None or latest > upper:
            return None, "changed"
        row = (
            await self.session.execute(
                text("SELECT status,task_name,queue_name FROM procrastinate_jobs WHERE id=:id"),
                {"id": job_id},
            )
        ).one_or_none()
        if row is None:
            return None, "pruned"
        if row.status != "failed":
            return None, "not_failed"
        if row.task_name not in QUEUES or QUEUES[row.task_name] != row.queue_name:
            return None, "unregistered"
        return row.task_name, None

    async def previous(self, job_id: int) -> int | None:
        return await self.session.scalar(
            select(JobRetryItemRow.replacement_job_id).where(
                JobRetryItemRow.job_id == job_id, JobRetryItemRow.status == "queued"
            )
        )

    async def capacity(self, queue: str, size: int) -> int:
        active = await self.session.scalar(
            text(
                "SELECT count(*) FROM (SELECT 1 FROM procrastinate_jobs WHERE queue_name=:queue "
                "AND status IN ('todo','doing') LIMIT :size) occupied"
            ),
            {"queue": queue, "size": size},
        )
        return max(0, size - int(active or 0))

    def record(
        self, request_id: int, job_id: int, replacement: int | None, status: str, reason: str | None
    ) -> None:
        self.session.add(
            JobRetryItemRow(
                request_id=request_id,
                job_id=job_id,
                replacement_job_id=replacement,
                status=status,
                reason=reason,
            )
        )

    async def maintenance(self, job_id: int, task: str) -> tuple[int | None, str | None]:
        # Lock only after the module has had a chance to take its own canonical lock.
        row = (
            await self.session.execute(
                text(
                    "SELECT status,task_name,queue_name FROM procrastinate_jobs "
                    "WHERE id=:id FOR UPDATE"
                ),
                {"id": job_id},
            )
        ).one_or_none()
        if (
            row is None
            or row.status != "failed"
            or row.task_name != task
            or row.queue_name != "maintenance"
        ):
            return None, "not_failed"
        if (
            await self.session.scalar(
                text(
                    "SELECT id FROM procrastinate_jobs WHERE task_name=:task "
                    "AND status IN ('todo','doing') LIMIT 1"
                ),
                {"task": task},
            )
            is not None
        ):
            return None, "already_running"
        cleanup = task == "video.cleanup_originals"
        args = {} if cleanup else {"timestamp": int(datetime.now(UTC).timestamp())}
        value = await self.session.scalar(
            text(
                "SELECT unnest(procrastinate_defer_jobs_v1(ARRAY[ROW("
                "'maintenance',:task,0,:lock,:lock,CAST(:args AS jsonb),NULL"
                ")::procrastinate_job_to_defer_v1]))"
            ),
            {
                "task": task,
                "lock": "originals.cleanup" if cleanup else "uploads.cleanup",
                "args": json.dumps(args),
            },
        )
        if type(value) is not int:
            raise RuntimeError("Queue admission is unavailable")
        return value, None
