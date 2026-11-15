"""SQL-only source admission shares the authoritative completion transaction."""

from uuid import UUID, uuid4

from sqlalchemy import delete, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from video.application.processing import INSPECTION_TASK, MEDIA_QUEUE
from video.domain.processing import SourceClaim, SourceInspection, SourceInspectionError
from video.infrastructure.encoding_jobs import admit_plan
from video.infrastructure.models import (
    MediaContentRow,
    MediaTrackRow,
    PublicationRow,
    SourceProcessingRow,
    SourceTrackRow,
    UploadRow,
)
from video.infrastructure.original_guard import require_original

_ACTIVE_ATTEMPT = text(
    "EXISTS (SELECT 1 FROM procrastinate_jobs j "
    "WHERE j.id=video_source_processing.job_id AND j.status='doing' "
    "AND j.attempts=video_source_processing.queue_attempt FOR SHARE)"
)


class SqlSourceJobs:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def admit(self, upload_id: int) -> None:
        upload = await self.session.scalar(
            select(UploadRow).where(UploadRow.id == upload_id).with_for_update()
        )
        if upload is None or upload.status != "uploaded":
            raise SourceInspectionError("source_not_uploaded")
        if await self.session.get(SourceProcessingRow, upload_id) is not None:
            return
        await require_original(self.session, upload_id)
        job_id = await self.defer(upload_id)
        self.session.add(SourceProcessingRow(upload_id=upload_id, job_id=job_id))
        await self.session.flush()

    async def defer(self, upload_id: int) -> int:
        # Library schema/function pinned in migration 0004; no independent queue commit.
        job_id = await self.session.scalar(
            text(
                "SELECT unnest(procrastinate_defer_jobs_v1(ARRAY[ROW("
                ":queue,:task,0,:lock,:lock,"
                "jsonb_build_object('upload_id', CAST(:id AS bigint)),NULL"
                ")::procrastinate_job_to_defer_v1]))"
            ),
            {
                "lock": f"video.source:{upload_id}",
                "id": upload_id,
                "queue": MEDIA_QUEUE,
                "task": INSPECTION_TASK,
            },
        )
        if not isinstance(job_id, int):
            raise SourceInspectionError("source_queue_unavailable")
        return job_id

    async def repair(self) -> int:
        candidates = await self.session.scalars(
            select(PublicationRow.id)
            .where(
                exists(
                    select(UploadRow.id).where(
                        UploadRow.video_id == PublicationRow.id,
                        UploadRow.status == "uploaded",
                        UploadRow.original_cleanup_started_at.is_(None),
                        ~exists(
                            select(SourceProcessingRow.upload_id).where(
                                SourceProcessingRow.upload_id == UploadRow.id
                            )
                        ),
                    )
                )
            )
            .order_by(PublicationRow.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        count = 0
        for video_id in candidates:
            uploads = await self.session.scalars(
                select(UploadRow.id).where(
                    UploadRow.video_id == video_id,
                    UploadRow.status == "uploaded",
                    UploadRow.original_cleanup_started_at.is_(None),
                    ~exists(
                        select(SourceProcessingRow.upload_id).where(
                            SourceProcessingRow.upload_id == UploadRow.id
                        )
                    ),
                )
            )
            for upload_id in uploads:
                await self.admit(upload_id)
                count += 1
        # Old header-only success must not masquerade as a verified fingerprint.
        legacy = await self.session.scalars(
            select(SourceProcessingRow)
            .where(
                SourceProcessingRow.status == "inspected",
                exists(
                    select(UploadRow.id).where(
                        UploadRow.id == SourceProcessingRow.upload_id,
                        UploadRow.original_cleanup_started_at.is_(None),
                    )
                ),
                or_(
                    SourceProcessingRow.content_id.is_(None),
                    SourceProcessingRow.inspection["version"].as_integer() < 3,
                ),
                text(
                    "EXISTS (SELECT 1 FROM procrastinate_jobs j "
                    "WHERE j.id=video_source_processing.job_id AND j.status='succeeded')"
                ),
            )
            .order_by(SourceProcessingRow.upload_id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        for row in legacy:
            row.job_id = await self.defer(row.upload_id)
            row.status, row.inspection, row.inspected_at = "pending", None, None
            row.token, row.queue_attempt, row.error_code = None, None, None
            count += 1
        failed = (
            await self.session.execute(
                text(
                    "SELECT s.upload_id,u.video_id FROM video_source_processing s "
                    "JOIN video_uploads u ON u.id=s.upload_id "
                    "JOIN procrastinate_jobs j ON j.id=s.job_id "
                    "WHERE s.status IN ('pending','inspecting') "
                    "AND j.status IN ('failed','cancelled','aborted') "
                    "FOR UPDATE OF s SKIP LOCKED"
                )
            )
        ).all()
        for upload_id, video_id in failed:
            await self.session.execute(
                update(SourceProcessingRow)
                .where(SourceProcessingRow.upload_id == upload_id)
                .values(
                    status="failed",
                    token=None,
                    error_code=func.coalesce(SourceProcessingRow.error_code, "inspection_failed"),
                )
            )
            await self.session.execute(
                update(PublicationRow)
                .where(
                    PublicationRow.id == video_id,
                    PublicationRow.processing.not_in(("ready", "partial")),
                )
                .values(processing="failed")
            )
        return count

    async def claim(self, upload_id: int, job_id: int, attempt: int) -> SourceClaim | None:
        row = await self.session.scalar(
            select(SourceProcessingRow)
            .where(SourceProcessingRow.upload_id == upload_id, SourceProcessingRow.job_id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.status == "inspected":
            return None
        active = await self.session.scalar(
            text(
                "SELECT id FROM procrastinate_jobs WHERE id=:id "
                "AND status='doing' AND attempts=:attempt FOR SHARE"
            ),
            {"id": job_id, "attempt": attempt},
        )
        if active is None:
            return None
        upload = await require_original(self.session, upload_id)
        token = uuid4()
        row.status, row.token, row.error_code = "inspecting", token, None
        row.queue_attempt = attempt
        await self.session.execute(
            update(PublicationRow)
            .where(PublicationRow.id == upload.video_id)
            .values(processing="processing")
        )
        return SourceClaim(upload_id, upload.video_id, upload.object_key, upload.size, token)

    async def finish(self, upload_id: int, token: UUID, result: SourceInspection) -> None:
        changed = await self.session.scalar(
            update(SourceProcessingRow)
            .where(
                SourceProcessingRow.upload_id == upload_id,
                SourceProcessingRow.token == token,
                SourceProcessingRow.status == "inspecting",
                _ACTIVE_ATTEMPT,
            )
            .values(
                status="inspected",
                token=None,
                inspection=result.payload(),
                inspected_at=func.now(),
                error_code=None,
            )
            .returning(SourceProcessingRow.upload_id)
        )
        if changed is None or result.fingerprint is None:
            return
        # All workers acquire shared identities in digest order, independent of source order.
        track_ids: dict[str, int] = {}
        for track in sorted(result.tracks, key=lambda value: value.fingerprint):
            if track.fingerprint in track_ids:
                continue
            await self.session.execute(
                insert(MediaTrackRow)
                .values(
                    fingerprint=track.fingerprint,
                    properties=track.properties,
                    packet_count=track.packet_count,
                )
                .on_conflict_do_nothing(index_elements=[MediaTrackRow.fingerprint])
            )
            track_id = await self.session.scalar(
                select(MediaTrackRow.id).where(MediaTrackRow.fingerprint == track.fingerprint)
            )
            if track_id is None:
                raise SourceInspectionError("inspection_failed")
            track_ids[track.fingerprint] = track_id
        await self.session.execute(
            insert(MediaContentRow)
            .values(fingerprint=result.fingerprint, manifest=result.manifest())
            .on_conflict_do_nothing(index_elements=[MediaContentRow.fingerprint])
        )
        content_id = await self.session.scalar(
            select(MediaContentRow.id).where(MediaContentRow.fingerprint == result.fingerprint)
        )
        if content_id is None:
            raise SourceInspectionError("inspection_failed")
        await self.session.execute(
            update(SourceProcessingRow)
            .where(SourceProcessingRow.upload_id == upload_id)
            .values(content_id=content_id)
        )
        await self.session.execute(
            delete(SourceTrackRow).where(SourceTrackRow.upload_id == upload_id)
        )
        for track in result.tracks:
            self.session.add(
                SourceTrackRow(
                    upload_id=upload_id,
                    stream_index=track.stream_index,
                    track_id=track_ids[track.fingerprint],
                )
            )
        await self.session.flush()
        if result.payload().get("version") == 3:
            await admit_plan(self.session, upload_id)

    async def fail(self, upload_id: int, token: UUID, code: str, *, terminal: bool) -> None:
        changed = await self.session.scalar(
            update(SourceProcessingRow)
            .where(
                SourceProcessingRow.upload_id == upload_id,
                SourceProcessingRow.token == token,
                SourceProcessingRow.status == "inspecting",
                _ACTIVE_ATTEMPT,
            )
            .values(status="failed" if terminal else "pending", token=None, error_code=code)
            .returning(SourceProcessingRow.upload_id)
        )
        if changed is not None and terminal:
            video_id = await self.session.scalar(
                select(UploadRow.video_id).where(UploadRow.id == upload_id)
            )
            await self.session.execute(
                update(PublicationRow)
                .where(PublicationRow.id == video_id)
                .values(processing="failed")
            )
