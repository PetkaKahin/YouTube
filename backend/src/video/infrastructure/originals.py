"""Persistent deletion intent and bounded exact-key S3 removal."""

import re

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.contracts.storage import ObjectBucket, ObjectStorageClient
from video.domain.originals import OriginalDeletion
from video.domain.processing import SourceInspectionError
from video.domain.subtitles import subtitle_inventory
from video.infrastructure.hls import AacAudio
from video.infrastructure.models import (
    EncodedTrackRow,
    MediaPlanRow,
    PlanTrackRow,
    PublicationRow,
    SourceProcessingRow,
    SubtitleLinkRow,
    UploadRow,
)
from video.infrastructure.multipart import sdk
from video.infrastructure.original_guard import NAMESPACE
from video.infrastructure.readiness import (
    SqlProcessingViews,
    fraction,
    media_track,
    number,
    object_value,
    sequence,
    video_rendition,
)


def complete_thumbnail(output: EncodedTrackRow) -> bool:
    try:
        artifact = object_value(output.artifact)
        candidates = sequence(artifact["candidates"])
        files = [object_value(item) for item in sequence(artifact["files"])]
        names = {str(item["name"]) for item in files}
        return (
            output.status == "ready"
            and output.spec.get("kind") == artifact["kind"] == "thumbnail_set"
            and re.fullmatch(rf"encoded/{output.id}/[0-9a-f]{{32}}/", str(artifact["prefix"]))
            is not None
            and len(candidates) == 3
            and set(candidates) == names
            and len(names) == len(files)
            and all(re.fullmatch(r"candidate_[0-2]\.webp", name) for name in names)
            and all(
                number(item["size"]) > 0 and re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"]))
                for item in files
            )
        )
    except KeyError, ValueError, TypeError:
        return False


def complete_output(output: EncodedTrackRow) -> bool:
    kind = output.spec.get("kind")
    if output.status == "empty":
        return kind == "subtitle"
    if output.status != "ready":
        return False
    if kind == "thumbnail_set":
        return complete_thumbnail(output)
    try:
        artifact = object_value(output.artifact)
        descriptor: dict[str, object] = {"id": output.id, "spec": output.spec, "artifact": artifact}
        if kind == "video":
            video_rendition(descriptor, None)
        elif kind == "audio":
            AacAudio(
                media_track(descriptor, "audio"), bytes.fromhex(str(artifact["configuration"]))
            )
        elif kind == "subtitle":
            media_track(descriptor, "subtitle")
            if any(
                artifact[key] != output.spec[key] for key in ("segments", "presentation_origin")
            ):
                return False
        elif kind == "subtitle_clock":
            fraction(artifact["presentation_origin"])
        else:
            return False
        return True
    except KeyError, ValueError, TypeError, ZeroDivisionError, OverflowError:
        return False


class SqlOriginalDeletions:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def candidates(self) -> tuple[int, ...]:
        # The inexpensive SQL filter omits unfinished/failed AV work. Detailed
        # manifest, inventory and native-job checks run under admission fencing.
        result = await self.session.scalars(
            text("""
            SELECT u.id FROM video_uploads u
            JOIN video_source_processing s ON s.upload_id=u.id
            JOIN video_media_plans m ON m.upload_id=u.id
            WHERE u.status='uploaded' AND u.original_deleted_at IS NULL AND (
              u.original_cleanup_started_at IS NOT NULL OR (
                s.status='inspected' AND s.content_id IS NOT NULL
                AND s.inspection->>'version'='3' AND m.status='planned'
                AND m.config ? 'thumbnail' AND m.config->>'subtitles_inventoried'='true'
                AND NOT EXISTS (
                  SELECT 1 FROM video_encoded_tracks e WHERE
                    (e.source_upload_id=u.id OR EXISTS (
                       SELECT 1 FROM video_plan_tracks r
                       WHERE r.upload_id=u.id AND r.encoded_id=e.id))
                    AND e.status NOT IN ('ready','empty'))
              ))
            ORDER BY u.original_cleanup_attempted_at NULLS FIRST,u.id LIMIT 100
        """)
        )
        return tuple(result.all())

    async def complete(self, upload: UploadRow) -> bool:
        source = await self.session.get(SourceProcessingRow, upload.id, populate_existing=True)
        plan = await self.session.get(MediaPlanRow, upload.id, populate_existing=True)
        if (
            source is None
            or source.status != "inspected"
            or source.content_id is None
            or source.inspection is None
            or source.inspection.get("version") != 3
        ):
            return False
        if (
            plan is None
            or plan.status != "planned"
            or plan.config is None
            or plan.config.get("subtitles_inventoried") is not True
        ):
            return False
        config = plan.config
        variants = [object_value(item) for item in sequence(config["variants"])]
        video_ids = {number(item["video"]) for item in variants}
        av_ids = video_ids | {
            number(item["audio"]) for item in variants if item["audio"] is not None
        }
        references = set(
            (
                await self.session.scalars(
                    select(PlanTrackRow.encoded_id).where(PlanTrackRow.upload_id == upload.id)
                )
            ).all()
        )
        if references != av_ids:
            return False
        _, subtitles = subtitle_inventory(source.inspection)
        expected = {
            (int(str(track["stream_index"])), video_id)
            for track in subtitles
            for video_id in video_ids
        }
        links = (
            await self.session.scalars(
                select(SubtitleLinkRow).where(SubtitleLinkRow.upload_id == upload.id)
            )
        ).all()
        if {(link.stream_index, link.video_encoded_id) for link in links} != expected or any(
            link.status != "queued" for link in links
        ):
            return False
        thumbnail_id = number(config["thumbnail"])
        needed = (
            av_ids
            | {thumbnail_id}
            | {link.encoded_id for link in links if link.encoded_id is not None}
            | {link.clock_id for link in links if link.clock_id is not None}
        )
        # A shared output may belong to another plan but still consume OUR input.
        outputs = (
            await self.session.scalars(
                select(EncodedTrackRow)
                .where(
                    (EncodedTrackRow.id.in_(needed))
                    | (EncodedTrackRow.source_upload_id == upload.id)
                )
                .execution_options(populate_existing=True)
            )
        ).all()
        by_id = {row.id: row for row in outputs}
        if not needed.issubset(by_id) or not all(complete_output(row) for row in outputs):
            return False
        if not complete_thumbnail(by_id[thumbnail_id]):
            return False
        owner_id = await self.session.scalar(
            select(PublicationRow.owner_id).where(PublicationRow.id == upload.video_id)
        )
        if owner_id is None:
            return False
        prepared = await SqlProcessingViews(self.session).prepared(upload.video_id, owner_id)
        if (
            prepared is None
            or prepared.status.processing.value != "ready"
            or any(item.status not in {"ready", "empty"} for item in prepared.status.subtitles)
        ):
            return False
        jobs = {source.job_id, plan.job_id, *(row.job_id for row in outputs)}
        completed = await self.session.scalars(
            text("SELECT id FROM procrastinate_jobs WHERE id=ANY(:ids) AND status='succeeded'"),
            {"ids": sorted(jobs)},
        )
        return set(completed.all()) == jobs

    async def prepare(self, upload_id: int) -> OriginalDeletion | None:
        upload = await self.session.scalar(
            select(UploadRow)
            .where(
                UploadRow.id == upload_id,
                UploadRow.status == "uploaded",
                UploadRow.original_deleted_at.is_(None),
            )
            .with_for_update(key_share=True, skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if upload is None:
            return None
        locked = await self.session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:namespace,:key)"),
            {"namespace": NAMESPACE, "key": upload_id % 2**31},
        )
        if not locked:
            return None
        await self.session.execute(
            update(UploadRow)
            .where(UploadRow.id == upload.id)
            .values(original_cleanup_attempted_at=func.clock_timestamp())
        )
        try:
            intent = OriginalDeletion(upload.id, upload.object_key)
            if upload.original_cleanup_started_at is None:
                if not await self.complete(upload):
                    return None
                await self.session.execute(
                    update(UploadRow)
                    .where(UploadRow.id == upload.id)
                    .values(original_cleanup_started_at=func.clock_timestamp())
                )
        except KeyError, ValueError, TypeError, SourceInspectionError:
            return None  # Corrupt metadata never authorizes deletion.
        return intent

    async def finish(self, upload_id: int) -> None:
        await self.session.execute(
            update(UploadRow)
            .where(
                UploadRow.id == upload_id,
                UploadRow.original_cleanup_started_at.is_not(None),
                UploadRow.original_deleted_at.is_(None),
            )
            .values(original_deleted_at=func.clock_timestamp())
        )


class S3OriginalStorage:
    def __init__(self, objects: ObjectStorageClient, bucket: ObjectBucket) -> None:
        self.objects, self.bucket = objects, bucket.name

    async def delete(self, intent: OriginalDeletion) -> None:
        await sdk(lambda: self.objects.delete_object(Bucket=self.bucket, Key=intent.object_key))
