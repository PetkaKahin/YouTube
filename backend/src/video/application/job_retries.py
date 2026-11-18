"""Administrator-authorized retries keep all Video state changes inside Video."""

from typing import Protocol

from video.domain.publications import valid_identifier


class VideoJobAdministrators(Protocol):
    async def require(self, account_id: int) -> None: ...


class VideoJobRetries(Protocol):
    async def restart(self, job_id: int) -> tuple[int | None, str | None]: ...
    async def task(self, job_id: int) -> str | None: ...


class RetryVideoJob:
    """Caller owns the transaction, as for User.GetStaffIdentity."""

    def __init__(self, actors: VideoJobAdministrators, jobs: VideoJobRetries) -> None:
        self.actors, self.jobs = actors, jobs

    async def __call__(self, account_id: int, job_id: int) -> tuple[int | None, str | None]:
        await self.actors.require(account_id)
        if not valid_identifier(job_id):
            return None, "not_found"
        return await self.jobs.restart(job_id)

    async def task(self, account_id: int, job_id: int) -> str | None:
        await self.actors.require(account_id)
        if not valid_identifier(job_id):
            return None
        return await self.jobs.task(job_id)
