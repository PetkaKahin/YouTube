"""Canonical rows supply every argument; native queue payloads are never replayed."""

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from user.public import GetStaffIdentity, Role, StaffForbidden
from video.application.processing import INSPECTION_TASK
from video.domain.encoding import ENCODE_TASK, PLAN_TASK, make_plan
from video.domain.processing import SourceInspectionError
from video.domain.thumbnails import PREPARE_THUMBNAIL_TASK, thumbnail_plan
from video.infrastructure.encoding_jobs import defer
from video.infrastructure.models import (
    EncodedTrackRow,
    MediaPlanRow,
    PublicationRow,
    SourceProcessingRow,
    ThumbnailChoiceRow,
    ThumbnailUploadRow,
)
from video.infrastructure.original_guard import require_original


class UserJobAdministrators:
    def __init__(self, identities: GetStaffIdentity) -> None:
        self.identities = identities

    async def require(self, account_id: int) -> None:
        if (await self.identities(account_id)).role != Role.ADMIN:
            raise StaffForbidden


class SqlVideoJobRetries:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def priority(self, row: EncodedTrackRow) -> int:
        if row.source_upload_id is None:
            return 0
        source = await self.session.get(SourceProcessingRow, row.source_upload_id)
        if source is None or source.inspection is None:
            return 0
        try:
            tracks = (*make_plan(source.inspection).tracks, thumbnail_plan(source.inspection))
            return next((track.priority for track in tracks if track.key == row.profile_key), 0)
        except SourceInspectionError:
            return 0

    async def task(self, job_id: int) -> str | None:
        for model, task in (
            (SourceProcessingRow, INSPECTION_TASK),
            (MediaPlanRow, PLAN_TASK),
            (EncodedTrackRow, ENCODE_TASK),
            (ThumbnailUploadRow, PREPARE_THUMBNAIL_TASK),
        ):
            if (
                await self.session.scalar(
                    select(model.job_id).where(model.job_id == job_id, model.status == "failed")
                )
                is not None
            ):
                return task
        return None

    async def restart(self, job_id: int) -> tuple[int | None, str | None]:
        for model, task, argument, lock in (
            (SourceProcessingRow, INSPECTION_TASK, "upload_id", "video.source"),
            (MediaPlanRow, PLAN_TASK, "upload_id", "video.plan"),
            (EncodedTrackRow, ENCODE_TASK, "encoded_id", "video.encode"),
            (ThumbnailUploadRow, PREPARE_THUMBNAIL_TASK, "upload_id", "video.thumbnail"),
        ):
            row = await self.session.scalar(
                select(model)
                .where(model.job_id == job_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                continue
            native = (
                await self.session.execute(
                    text(
                        "SELECT status,task_name,queue_name,priority FROM procrastinate_jobs "
                        "WHERE id=:id FOR UPDATE"
                    ),
                    {"id": job_id},
                )
            ).one_or_none()
            if native is not None and (
                native.status != "failed"
                or native.task_name != task
                or native.queue_name != "media"
            ):
                return None, "not_failed"
            ready = row.status in {"inspected", "planned", "ready", "empty", "prepared"}
            if native is None and (ready or row.status != "failed"):
                return None, "not_failed"
            video_id: int | None = None
            # A committed successful result needs only a fresh native acknowledgement.
            # It must never be cleared/re-encoded after a post-commit worker failure.
            if not ready:
                try:
                    if isinstance(row, EncodedTrackRow):
                        if row.source_upload_id is not None:
                            await require_original(self.session, row.source_upload_id)
                        else:
                            image = await self.session.get(
                                ThumbnailUploadRow, row.source_thumbnail_id
                            )
                            if (
                                image is None
                                or image.normalized_key is None
                                or image.normalized_size is None
                            ):
                                return None, "source_unavailable"
                    elif isinstance(row, ThumbnailUploadRow):
                        choice = await self.session.get(ThumbnailChoiceRow, row.video_id)
                        if row.source_deleted or row.status not in {
                            "uploaded",
                            "preparing",
                            "failed",
                        }:
                            return None, "source_unavailable"
                        if (
                            choice is None
                            or choice.requested_upload_id != row.id
                            or choice.generation != row.generation
                        ):
                            return None, "superseded"
                    else:
                        video_id = (await require_original(self.session, row.upload_id)).video_id
                except SourceInspectionError:
                    return None, "source_unavailable"
            target_id = (
                row.id if isinstance(row, (EncodedTrackRow, ThumbnailUploadRow)) else row.upload_id
            )
            priority = (
                native.priority
                if native is not None
                else (
                    await self.priority(row)
                    if isinstance(row, EncodedTrackRow)
                    else 1
                    if isinstance(row, MediaPlanRow)
                    else 0
                )
            )
            new_id = await defer(
                self.session, task, f"{lock}:{target_id}", {argument: target_id}, priority
            )
            row.job_id = new_id
            if not ready:
                row.status = "uploaded" if isinstance(row, ThumbnailUploadRow) else "pending"
                row.error_code = None
                if isinstance(row, (SourceProcessingRow, EncodedTrackRow, ThumbnailUploadRow)):
                    row.token, row.queue_attempt = None, None
                if video_id is not None:
                    await self.session.execute(
                        update(PublicationRow)
                        .where(PublicationRow.id == video_id)
                        .values(processing="processing")
                    )
            await self.session.flush()
            return new_id, None
        return None, "superseded"
