"""Fence worker acknowledgements against recovery in pinned Procrastinate 3.11.0."""

from datetime import datetime

from procrastinate import jobs, utils
from procrastinate.manager import JobManager

_FINISH = """
WITH current_job AS MATERIALIZED (
    SELECT id FROM procrastinate_jobs
    WHERE id=%(job_id)s AND status='doing' AND attempts=%(attempt)s
      AND worker_id IS NOT DISTINCT FROM %(worker_id)s
    FOR UPDATE
)
SELECT procrastinate_finish_job_v1(id, %(status)s, %(delete_job)s) FROM current_job
"""
_RETRY = """
WITH current_job AS MATERIALIZED (
    SELECT id FROM procrastinate_jobs
    WHERE id=%(job_id)s AND status='doing' AND attempts=%(attempt)s
      AND worker_id IS NOT DISTINCT FROM %(worker_id)s
    FOR UPDATE
)
SELECT procrastinate_retry_job_v2(id, %(retry_at)s, %(new_priority)s,
    %(new_queue_name)s, %(new_lock)s) FROM current_job
"""


class GenerationJobManager(JobManager):
    """Keep the row locked through the library's own state-transition function."""

    async def finish_job(self, job: jobs.Job, status: jobs.Status, delete_job: bool) -> None:
        if job.id is None:
            raise ValueError("Persisted job identity required")
        await self.connector.execute_query_async(
            query=_FINISH,
            job_id=job.id,
            attempt=job.attempts,
            worker_id=job.worker_id,
            status=status.value,
            delete_job=delete_job,
        )

    async def retry_job(
        self,
        job: jobs.Job,
        retry_at: datetime | None = None,
        priority: int | None = None,
        queue: str | None = None,
        lock: str | None = None,
    ) -> None:
        if job.id is None:
            raise ValueError("Persisted job identity required")
        await self.connector.execute_query_async(
            query=_RETRY,
            job_id=job.id,
            attempt=job.attempts,
            worker_id=job.worker_id,
            retry_at=retry_at or utils.utcnow(),
            new_priority=priority,
            new_queue_name=queue,
            new_lock=lock,
        )
