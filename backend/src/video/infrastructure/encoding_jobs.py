"""Shared output identity and queue writes use one PostgreSQL transaction."""

import json
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Integer,
    Text,
    column,
    delete,
    exists,
    func,
    literal_column,
    or_,
    select,
    text,
    update,
)
from sqlalchemy import table as sql_table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from video.application.processing import MEDIA_QUEUE
from video.domain.encoding import ENCODE_TASK, PLAN_TASK, EncodingClaim, EncodingPlan, PlannedTrack
from video.domain.processing import SourceInspectionError
from video.domain.thumbnails import thumbnail_plan
from video.infrastructure.media_resources import MediaLimits
from video.infrastructure.models import (
    EncodedTrackRow,
    EncodingAttemptRow,
    MediaPlanRow,
    PlanTrackRow,
    PublicationRow,
    SourceProcessingRow,
    SourceTrackRow,
    ThumbnailUploadRow,
    UploadRow,
)
from video.infrastructure.original_guard import require_original


async def defer(
    session: AsyncSession, task: str, lock: str, args: dict[str, int], priority: int = 0
) -> int:
    job_id = await session.scalar(
        text(
            "SELECT unnest(procrastinate_defer_jobs_v1(ARRAY[ROW("
            ":queue,:task,:priority,:lock,:lock,CAST(:args AS jsonb),NULL"
            ")::procrastinate_job_to_defer_v1]))"
        ),
        {
            "queue": MEDIA_QUEUE,
            "task": task,
            "priority": priority,
            "lock": lock,
            "args": json.dumps(args),
        },
    )
    if type(job_id) is not int:
        raise SourceInspectionError("source_queue_unavailable")
    return job_id


async def admit_plan(session: AsyncSession, upload_id: int) -> None:
    if await session.get(MediaPlanRow, upload_id) is None:
        await require_original(session, upload_id)
        job_id = await defer(
            session, PLAN_TASK, f"video.plan:{upload_id}", {"upload_id": upload_id}, 1
        )
        session.add(MediaPlanRow(upload_id=upload_id, job_id=job_id))
        await session.flush()


async def ensure_output(
    session: AsyncSession,
    track: PlannedTrack,
    track_id: int,
    *,
    upload_id: int | None = None,
    thumbnail_id: int | None = None,
) -> int:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(track.key[:16], 16) % 2**63}
    )
    row = await session.scalar(
        select(EncodedTrackRow).where(EncodedTrackRow.profile_key == track.key)
    )
    if row is None:
        if upload_id is not None and track.spec.get("kind") != "subtitle_clock":
            await require_original(session, upload_id)
        encoded_id = await session.scalar(text("SELECT nextval('video_encoded_tracks_id_seq')"))
        if type(encoded_id) is not int:
            raise SourceInspectionError("invalid_media_plan")
        job_id = await defer(
            session,
            ENCODE_TASK,
            f"video.encode:{encoded_id}",
            {"encoded_id": encoded_id},
            track.priority,
        )
        row = EncodedTrackRow(
            id=encoded_id,
            track_id=track_id,
            profile_key=track.key,
            spec=track.spec,
            source_upload_id=upload_id,
            source_thumbnail_id=thumbnail_id,
            stream_index=track.stream_index,
            job_id=job_id,
        )
        session.add(row)
        await session.flush()
    return row.id


async def ensure_thumbnail(
    session: AsyncSession, upload_id: int, payload: dict[str, object]
) -> int:
    track = thumbnail_plan(payload)
    track_id = await session.scalar(
        select(SourceTrackRow.track_id).where(
            SourceTrackRow.upload_id == upload_id, SourceTrackRow.stream_index == track.stream_index
        )
    )
    if track_id is None:
        raise SourceInspectionError("invalid_media_plan")
    return await ensure_output(session, track, track_id, upload_id=upload_id)


class SqlEncodingJobs:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def active(self, job_id: int, attempt: int) -> bool:
        return (
            await self.session.scalar(
                text(
                    "SELECT id FROM procrastinate_jobs WHERE id=:id AND status='doing' "
                    "AND attempts=:attempt FOR SHARE"
                ),
                {"id": job_id, "attempt": attempt},
            )
            is not None
        )

    async def source(self, upload_id: int, job_id: int, attempt: int) -> dict[str, object] | None:
        row = await self.session.scalar(
            select(MediaPlanRow)
            .where(MediaPlanRow.upload_id == upload_id, MediaPlanRow.job_id == job_id)
            .with_for_update()
        )
        if row is None or row.status != "pending" or not await self.active(job_id, attempt):
            return None
        await require_original(self.session, upload_id)
        source = await self.session.get(SourceProcessingRow, upload_id)
        if source is None or source.inspection is None or source.status != "inspected":
            raise SourceInspectionError("source_not_uploaded")
        return source.inspection

    async def save_plan(self, upload_id: int, plan: EncodingPlan) -> None:
        await require_original(self.session, upload_id)
        row = await self.session.get(MediaPlanRow, upload_id)
        if row is None:
            raise SourceInspectionError("invalid_media_plan")
        source = await self.session.get(SourceProcessingRow, upload_id)
        if source is None or source.inspection is None:
            raise SourceInspectionError("invalid_media_plan")
        thumbnail = thumbnail_plan(source.inspection)
        locks = sorted({int(track.key[:16], 16) % 2**63 for track in (*plan.tracks, thumbnail)})
        for key in locks:
            await self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        ids: dict[str, int] = {}
        # Sort actual advisory lock numbers, including hash collisions, to avoid cycles.
        ordered = sorted(
            plan.tracks, key=lambda track: (int(track.key[:16], 16) % 2**63, track.key)
        )
        for track in ordered:
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(track.key[:16], 16) % 2**63}
            )
            encoded = await self.session.scalar(
                select(EncodedTrackRow).where(EncodedTrackRow.profile_key == track.key)
            )
            if encoded is None:
                encoded_id = await self.session.scalar(
                    text("SELECT nextval('video_encoded_tracks_id_seq')")
                )
                if type(encoded_id) is not int:
                    raise SourceInspectionError("invalid_media_plan")
                track_id = await self.session.scalar(
                    select(SourceTrackRow.track_id).where(
                        SourceTrackRow.upload_id == upload_id,
                        SourceTrackRow.stream_index == track.stream_index,
                    )
                )
                if track_id is None:
                    raise SourceInspectionError("invalid_media_plan")
                job_id = await defer(
                    self.session,
                    ENCODE_TASK,
                    f"video.encode:{encoded_id}",
                    {"encoded_id": encoded_id},
                    track.priority,
                )
                encoded = EncodedTrackRow(
                    id=encoded_id,
                    track_id=track_id,
                    profile_key=track.key,
                    spec=track.spec,
                    source_upload_id=upload_id,
                    stream_index=track.stream_index,
                    job_id=job_id,
                )
                self.session.add(encoded)
                await self.session.flush()
            ids[track.key] = encoded.id
            await self.session.execute(
                insert(PlanTrackRow)
                .values(upload_id=upload_id, encoded_id=encoded.id)
                .on_conflict_do_nothing()
            )
        row.status = "planned"
        row.config = {
            "version": "av1-aac-v1",
            "video_index": plan.video_index,
            "audio_index": plan.audio_index,
            "variants": [
                {
                    **variant,
                    "video": ids[str(variant["video"])],
                    "audio": ids[str(variant["audio"])] if variant["audio"] is not None else None,
                }
                for variant in plan.variants
            ],
        }
        source = await self.session.get(SourceProcessingRow, upload_id)
        if source is None or source.inspection is None:
            raise SourceInspectionError("invalid_media_plan")
        row.config = {
            **cast(dict[str, object], row.config),
            "thumbnail": await ensure_thumbnail(self.session, upload_id, source.inspection),
            "subtitles_inventoried": True,
        }
        await self.session.flush()
        from video.infrastructure.subtitle_jobs import admit_plan_subtitles

        await admit_plan_subtitles(self.session, row)
        await self.refresh_failures(upload_id=upload_id)

    async def reject_plan(self, upload_id: int, code: str) -> None:
        await self.session.execute(
            update(MediaPlanRow)
            .where(MediaPlanRow.upload_id == upload_id)
            .values(status="failed", error_code=code)
        )
        video_id = await self.session.scalar(
            select(UploadRow.video_id).where(UploadRow.id == upload_id)
        )
        await self.session.execute(
            update(PublicationRow).where(PublicationRow.id == video_id).values(processing="failed")
        )

    async def claim(self, encoded_id: int, job_id: int, attempt: int) -> EncodingClaim | None:
        row = await self.session.scalar(
            select(EncodedTrackRow)
            .where(EncodedTrackRow.id == encoded_id, EncodedTrackRow.job_id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            row is None
            or row.status in {"ready", "empty"}
            or not await self.active(job_id, attempt)
        ):
            return None
        if row.source_upload_id is not None:
            upload = (
                await self.session.get(UploadRow, row.source_upload_id)
                if row.spec.get("kind") == "subtitle_clock"
                else await require_original(self.session, row.source_upload_id)
            )
            if upload is None or upload.status != "uploaded":
                raise SourceInspectionError("source_not_uploaded")
            key, size = upload.object_key, upload.size
        else:
            image = await self.session.get(ThumbnailUploadRow, row.source_thumbnail_id)
            if (
                image is None
                or image.status != "prepared"
                or image.normalized_key is None
                or image.normalized_size is None
            ):
                raise SourceInspectionError("source_not_uploaded")
            key, size = image.normalized_key, image.normalized_size
        token = uuid4()
        prefix = f"encoded/{encoded_id}/{token.hex}/"
        row.status, row.token, row.queue_attempt, row.error_code = "running", token, attempt, None
        self.session.add(
            EncodingAttemptRow(
                token=token,
                encoded_id=encoded_id,
                prefix=prefix,
                expires_at=func.now()
                + func.make_interval(
                    0, 0, 0, 0, 0, 0, MediaLimits.from_environment().attempt_seconds + 60
                ),
            )
        )
        return EncodingClaim(encoded_id, token, key, size, row.stream_index, row.spec, prefix)

    async def current(self, encoded_id: int, token: UUID) -> EncodedTrackRow | None:
        row = await self.session.scalar(
            select(EncodedTrackRow)
            .where(
                EncodedTrackRow.id == encoded_id,
                EncodedTrackRow.token == token,
                EncodedTrackRow.status == "running",
            )
            .with_for_update()
        )
        if (
            row is None
            or row.queue_attempt is None
            or not await self.active(row.job_id, row.queue_attempt)
        ):
            return None
        return row

    async def finish(
        self, encoded_id: int, token: UUID, artifact: dict[str, object] | None
    ) -> bool:
        row = await self.current(encoded_id, token)
        if row is None:
            return False
        if artifact is None and row.spec.get("kind") != "subtitle":
            raise SourceInspectionError("invalid_encoded_media")
        row.status, row.token, row.artifact, row.error_code = (
            "ready" if artifact is not None else "empty",
            None,
            artifact,
            None,
        )
        await self.session.execute(
            delete(EncodingAttemptRow).where(EncodingAttemptRow.token == token)
        )
        from video.infrastructure.subtitle_jobs import admit_video_subtitles

        await self.session.flush()
        if row.spec.get("kind") == "video":
            await admit_video_subtitles(self.session, row.id)
        elif row.spec.get("kind") == "subtitle_clock" and artifact is not None:
            video = await self.session.get(EncodedTrackRow, int(str(row.spec["video_id"])))
            expected = row.spec["video_artifact"]
            if video is not None and video.artifact is not None and video.artifact == expected:
                video.artifact = {
                    **video.artifact,
                    "presentation_origin": artifact["presentation_origin"],
                }
                await self.session.flush()
                await admit_video_subtitles(self.session, video.id)
        return True

    async def fail(self, encoded_id: int, token: UUID, code: str, *, terminal: bool) -> None:
        row = await self.current(encoded_id, token)
        if row is not None:
            row.status, row.token, row.error_code = "failed" if terminal else "pending", None, code
            if terminal:
                await self.refresh_failures(encoded_id=encoded_id)

    async def refresh_failures(
        self, *, upload_id: int | None = None, encoded_id: int | None = None
    ) -> None:
        await self.session.flush()
        query = select(MediaPlanRow).where(MediaPlanRow.status.in_(("planned", "failed")))
        if upload_id is not None:
            query = query.where(MediaPlanRow.upload_id == upload_id)
        elif encoded_id is not None:
            query = query.where(
                exists(
                    select(PlanTrackRow.upload_id).where(
                        PlanTrackRow.upload_id == MediaPlanRow.upload_id,
                        PlanTrackRow.encoded_id == encoded_id,
                    )
                )
            )
        else:
            return
        plans = await self.session.scalars(query)
        for plan in plans:
            failed = plan.status == "failed"
            if not failed and plan.config is not None:
                outputs = (
                    await self.session.scalars(
                        select(EncodedTrackRow)
                        .join(PlanTrackRow, PlanTrackRow.encoded_id == EncodedTrackRow.id)
                        .where(PlanTrackRow.upload_id == plan.upload_id)
                    )
                ).all()
                statuses = {output.id: output.status for output in outputs}
                variants = cast(list[dict[str, object]], plan.config["variants"])
                playable = any(
                    statuses.get(cast(int, variant["video"])) == "ready"
                    and (
                        variant["audio"] is None
                        or statuses.get(cast(int, variant["audio"])) == "ready"
                    )
                    for variant in variants
                )
                failed = (
                    bool(statuses)
                    and not playable
                    and all(status in {"ready", "failed"} for status in statuses.values())
                )
            if failed:
                video_id = await self.session.scalar(
                    select(UploadRow.video_id).where(UploadRow.id == plan.upload_id)
                )
                await self.session.execute(
                    update(PublicationRow)
                    .where(
                        PublicationRow.id == video_id,
                        PublicationRow.processing.not_in(("ready", "partial", "failed")),
                    )
                    .values(processing="failed")
                )

    async def repair(self) -> int:
        from video.infrastructure.subtitle_jobs import repair_subtitles

        queue_table = sql_table(
            "procrastinate_jobs", column("id", BigInteger), column("status", Text)
        )
        count = 0
        sources = await self.session.scalars(
            select(SourceProcessingRow)
            .where(
                SourceProcessingRow.status == "inspected",
                ~exists(
                    select(MediaPlanRow.upload_id).where(
                        MediaPlanRow.upload_id == SourceProcessingRow.upload_id
                    )
                ),
            )
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        for source in sources:
            if source.inspection is not None and source.inspection.get("version") == 3:
                await admit_plan(self.session, source.upload_id)
                count += 1
        legacy = await self.session.scalars(
            select(MediaPlanRow)
            .where(
                MediaPlanRow.status == "planned",
                MediaPlanRow.config.is_not(None),
                ~MediaPlanRow.config.has_key("thumbnail"),
            )
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        for plan in legacy:
            source = await self.session.get(SourceProcessingRow, plan.upload_id)
            if source is not None and source.inspection is not None and plan.config is not None:
                plan.config = {
                    **plan.config,
                    "thumbnail": await ensure_thumbnail(
                        self.session, plan.upload_id, source.inspection
                    ),
                }
                count += 1
        for table in (MediaPlanRow, EncodedTrackRow):
            rows = await self.session.scalars(
                select(table)
                .where(
                    table.status.in_(("pending", "running")),
                    exists(
                        select(literal_column("1", Integer))
                        .select_from(queue_table)
                        .where(
                            queue_table.c.id == table.job_id,
                            queue_table.c.status.cast(Text).in_(("failed", "aborted", "cancelled")),
                        )
                    ),
                )
                .limit(100)
                .with_for_update(skip_locked=True)
            )
            for row in rows:
                row.status = "failed"
                row.error_code = row.error_code or "encoding_failed"
                if isinstance(row, EncodedTrackRow):
                    row.token = None
                    await self.refresh_failures(encoded_id=row.id)
                else:
                    await self.refresh_failures(upload_id=row.upload_id)
        return count + await repair_subtitles(self.session)

    async def expired_attempt(self) -> tuple[UUID, str] | None:
        row = (
            await self.session.execute(
                select(EncodingAttemptRow.token, EncodingAttemptRow.prefix)
                .join(EncodedTrackRow, EncodedTrackRow.id == EncodingAttemptRow.encoded_id)
                .where(
                    EncodingAttemptRow.expires_at < func.now(),
                    or_(
                        EncodedTrackRow.token.is_(None),
                        EncodedTrackRow.token != EncodingAttemptRow.token,
                    ),
                    or_(
                        EncodedTrackRow.artifact.is_(None),
                        EncodedTrackRow.artifact["prefix"].astext != EncodingAttemptRow.prefix,
                    ),
                )
                .order_by(EncodingAttemptRow.created_at)
                .limit(1)
                .with_for_update(of=(EncodingAttemptRow, EncodedTrackRow), skip_locked=True),
            )
        ).one_or_none()
        return (row.token, row.prefix) if row is not None else None

    async def forget_attempt(self, token: UUID) -> None:
        await self.session.execute(
            delete(EncodingAttemptRow).where(EncodingAttemptRow.token == token)
        )
