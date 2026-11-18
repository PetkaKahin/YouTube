"""Technical composition of live User authority, durable intake and Video's public port."""

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from common.contracts.job_administration import (
    QUEUES,
    ErrorCursor,
    InvalidJobRequest,
    JobAdminAuthenticationRequired,
    JobAdminForbidden,
    JobAdminNotFound,
    item_cursor,
    selection_ids,
    valid_id,
)
from common.infrastructure.job_admin_models import JobRetryItemRow
from common.infrastructure.job_administration import SqlJobAdministration
from user.public import AccountNotFound, GetStaffIdentity, Role, StaffForbidden
from video.public import RetryVideoJob


class JobAdministration:
    def __init__(
        self, session: AsyncSession, identities: GetStaffIdentity, videos: RetryVideoJob
    ) -> None:
        self.session, self.identities, self.videos = session, identities, videos
        self.store = SqlJobAdministration(session)

    async def admin(self, account_id: int | None) -> int:
        if account_id is None:
            raise JobAdminAuthenticationRequired
        try:
            actor = await self.identities(account_id)
        except AccountNotFound:
            raise JobAdminAuthenticationRequired from None
        except StaffForbidden:
            raise JobAdminForbidden from None
        if actor.role != Role.ADMIN:
            raise JobAdminForbidden
        return actor.id

    async def errors(self, account_id: int | None, cursor: str | None) -> dict[str, object]:
        async with self.session.begin():
            await self.admin(account_id)
            return await self.store.errors(ErrorCursor.decode(cursor))

    async def request(
        self, account_id: int | None, selection: tuple[int, ...] | None
    ) -> dict[str, object]:
        async with self.session.begin():
            actor = await self.admin(account_id)
            return await self.store.create(actor, selection_ids(selection))

    async def details(
        self, account_id: int | None, request_id: int, cursor: str | None
    ) -> dict[str, object]:
        async with self.session.begin():
            await self.admin(account_id)
            if not valid_id(request_id):
                raise JobAdminNotFound
            return await self.store.details(request_id, item_cursor(cursor, request_id))

    async def dispatch(self, size: int) -> int:
        if type(size) is not int or size < 1:
            raise InvalidJobRequest
        async with self.session.begin():
            # One admission transaction globally, even with multiple maintenance replicas.
            if not await self.session.scalar(text("SELECT pg_try_advisory_xact_lock(73147, 1)")):
                return 0
            request = await self.store.next_request()
            if request is None:
                return 0
            request.served_at = datetime.now(UTC)
            try:
                await self.admin(request.actor_id)
            except JobAdminAuthenticationRequired, JobAdminForbidden:
                request.status = "cancelled"
                return 0
            candidates = await self.store.candidates(request, size)
            if not candidates:
                request.status = "submitted"
                return 0
            admitted = 0
            for job_id in candidates:
                previous = await self.store.previous(job_id)
                if previous is not None:
                    self.session.add(
                        JobRetryItemRow(
                            request_id=request.id,
                            job_id=job_id,
                            replacement_job_id=previous,
                            status="skipped",
                            reason="already_requested",
                        )
                    )
                    request.after_job_id = job_id
                    continue
                task, reason = await self.store.task(job_id, request.upper_error_id)
                if reason == "pruned":
                    task = await self.videos.task(request.actor_id, job_id)
                if task is None:
                    new_id, reason = None, reason or "unregistered"
                else:
                    queue = QUEUES[task]
                    if await self.store.capacity(queue, size) < 1:
                        break
                    if queue == "media":
                        new_id, reason = await self.videos(request.actor_id, job_id)
                    else:
                        new_id, reason = await self.store.maintenance(job_id, task)
                self.session.add(
                    JobRetryItemRow(
                        request_id=request.id,
                        job_id=job_id,
                        replacement_job_id=new_id,
                        status="queued" if new_id is not None else "unavailable",
                        reason=reason,
                    )
                )
                request.after_job_id = job_id
                admitted += new_id is not None
                await self.session.flush()
            return admitted
