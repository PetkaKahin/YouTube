"""Durable image receipts, shared output admission and publication-local choices."""

from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from video.domain.delivery import StoredMedia
from video.domain.encoding import PlannedTrack
from video.domain.processing import SourceInspectionError
from video.domain.publications import VideoNotFound
from video.domain.thumbnails import (
    PREPARE_THUMBNAIL_TASK,
    THUMBNAIL_PROFILE,
    PreparedThumbnail,
    ThumbnailClaim,
    ThumbnailError,
    ThumbnailOptions,
)
from video.domain.uploads import StoredObject, Transfer
from video.infrastructure.encoding_jobs import SqlEncodingJobs, defer, ensure_output
from video.infrastructure.media_resources import MediaLimits
from video.infrastructure.models import (
    EncodedTrackRow,
    MediaPlanRow,
    MediaTrackRow,
    PublicationRow,
    ThumbnailChoiceRow,
    ThumbnailPreparationRow,
    ThumbnailUploadRow,
    UploadRow,
)
from video.infrastructure.readiness import number, object_value, sequence


def transfer(row: ThumbnailUploadRow) -> Transfer:
    return Transfer(
        row.id,
        row.video_id,
        row.object_key,
        row.multipart_id,
        row.size,
        row.status,
        row.activity_at,
    )


class SqlThumbnailUploads:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def active(self, video_id: int) -> Transfer | None:
        row = await self.session.scalar(
            select(ThumbnailUploadRow)
            .where(
                ThumbnailUploadRow.video_id == video_id,
                ThumbnailUploadRow.status.in_(("creating", "uploading")),
            )
            .order_by(ThumbnailUploadRow.id.desc())
            .limit(1)
        )
        return transfer(row) if row else None

    async def get(self, video_id: int, upload_id: int) -> Transfer | None:
        row = await self.session.scalar(
            select(ThumbnailUploadRow)
            .where(ThumbnailUploadRow.video_id == video_id, ThumbnailUploadRow.id == upload_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return transfer(row) if row else None

    async def create(self, video_id: int, size: int) -> Transfer:
        row = ThumbnailUploadRow(
            video_id=video_id, size=size, object_key="thumbnail-incoming/" + uuid4().hex
        )
        self.session.add(row)
        await self.session.flush()
        return transfer(row)

    async def activate(self, value: Transfer, multipart_id: str, now: datetime) -> Transfer:
        row = await self.session.get(ThumbnailUploadRow, value.id)
        if row is None:
            raise VideoNotFound
        row.multipart_id, row.status, row.activity_at = multipart_id, "uploading", now
        return transfer(row)

    async def touch(self, value: Transfer, now: datetime) -> None:
        await self.session.execute(
            update(ThumbnailUploadRow)
            .where(ThumbnailUploadRow.id == value.id)
            .values(activity_at=now)
        )

    async def complete(self, value: Transfer, result: StoredObject) -> None:
        await self.session.execute(
            update(ThumbnailUploadRow)
            .where(ThumbnailUploadRow.id == value.id)
            .values(status="uploaded")
        )

    async def expire(self, value: Transfer) -> None:
        await self.session.execute(
            update(ThumbnailUploadRow)
            .where(ThumbnailUploadRow.id == value.id)
            .values(status="expired")
        )

    async def candidates(self, before: datetime) -> tuple[tuple[int, int], ...]:
        rows = await self.session.execute(
            select(ThumbnailUploadRow.video_id, ThumbnailUploadRow.id)
            .where(
                ThumbnailUploadRow.status.in_(("creating", "uploading")),
                ThumbnailUploadRow.activity_at <= before,
            )
            .order_by(ThumbnailUploadRow.activity_at)
            .limit(100)
        )
        return tuple((row.video_id, row.id) for row in rows)

    async def lock_video(self, video_id: int) -> bool:
        return (
            await self.session.scalar(
                select(PublicationRow.id)
                .where(PublicationRow.id == video_id)
                .with_for_update(skip_locked=True)
            )
            is not None
        )


class SqlThumbnailJobs:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def choice(self, video_id: int) -> ThumbnailChoiceRow:
        await self.session.flush()
        await self.session.execute(
            insert(ThumbnailChoiceRow).values(video_id=video_id).on_conflict_do_nothing()
        )
        row = await self.session.get(ThumbnailChoiceRow, video_id, populate_existing=True)
        if row is None:
            raise VideoNotFound
        return row

    async def admit(self, upload_id: int) -> None:
        row = await self.session.get(ThumbnailUploadRow, upload_id)
        if row is None or row.status != "uploaded" or row.job_id is not None:
            return
        row.job_id = await defer(
            self.session,
            PREPARE_THUMBNAIL_TASK,
            f"video.thumbnail:{upload_id}",
            {"upload_id": upload_id},
            3,
        )

    async def new_intent(self, upload_id: int) -> None:
        row = await self.session.get(ThumbnailUploadRow, upload_id)
        if row is None:
            raise VideoNotFound
        choice = await self.choice(row.video_id)
        await self.sync_choice(choice)
        choice.generation += 1
        choice.requested_upload_id = None
        row.generation = choice.generation

    async def confirm_selection(self, upload_id: int) -> None:
        row = await self.session.get(ThumbnailUploadRow, upload_id)
        if row is None or row.job_id is None or row.selection_confirmed:
            return
        choice = await self.choice(row.video_id)
        row.selection_confirmed = True
        if row.generation == choice.generation:
            choice.requested_upload_id = row.id
            await self.session.execute(
                update(PublicationRow)
                .where(PublicationRow.id == row.video_id)
                .values(updated_at=func.clock_timestamp())
            )

    async def current(self, upload_id: int, token: UUID) -> ThumbnailUploadRow | None:
        row = await self.session.scalar(
            select(ThumbnailUploadRow)
            .where(
                ThumbnailUploadRow.id == upload_id,
                ThumbnailUploadRow.token == token,
                ThumbnailUploadRow.status == "preparing",
            )
            .with_for_update()
        )
        if (
            row is None
            or row.job_id is None
            or row.queue_attempt is None
            or not await SqlEncodingJobs(self.session).active(row.job_id, row.queue_attempt)
        ):
            return None
        return row

    async def claim(self, upload_id: int, job_id: int, attempt: int) -> ThumbnailClaim | None:
        row = await self.session.scalar(
            select(ThumbnailUploadRow)
            .where(ThumbnailUploadRow.id == upload_id, ThumbnailUploadRow.job_id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            row is None
            or row.status == "prepared"
            or not await SqlEncodingJobs(self.session).active(job_id, attempt)
        ):
            return None
        token = uuid4()
        prefix = f"thumbnail-pixels/{upload_id}/{token.hex}/"
        row.status, row.token, row.queue_attempt = "preparing", token, attempt
        self.session.add(
            ThumbnailPreparationRow(
                token=token,
                upload_id=upload_id,
                prefix=prefix,
                expires_at=func.now()
                + func.make_interval(
                    0, 0, 0, 0, 0, 0, MediaLimits.from_environment().attempt_seconds + 60
                ),
            )
        )
        return ThumbnailClaim(row.id, token, row.object_key, row.size, prefix)

    async def finish(self, upload_id: int, token: UUID, prepared: PreparedThumbnail) -> bool:
        row = await self.current(upload_id, token)
        if row is None:
            return False
        if (
            prepared.object_key != f"thumbnail-pixels/{upload_id}/{token.hex}/pixels.rgba"
            or prepared.size != prepared.width * prepared.height * 4
        ):
            raise SourceInspectionError("invalid_thumbnail")
        track_id = await self.session.scalar(
            insert(MediaTrackRow)
            .values(
                fingerprint=prepared.fingerprint,
                properties={
                    "kind": "thumbnail_pixels",
                    "width": prepared.width,
                    "height": prepared.height,
                },
                packet_count=1,
            )
            .on_conflict_do_nothing()
            .returning(MediaTrackRow.id)
        )
        if track_id is None:
            track_id = await self.session.scalar(
                select(MediaTrackRow.id).where(MediaTrackRow.fingerprint == prepared.fingerprint)
            )
        if track_id is None:
            raise SourceInspectionError("invalid_thumbnail")
        spec: dict[str, object] = {
            "profile": THUMBNAIL_PROFILE,
            "kind": "thumbnail_image",
            "width": prepared.width,
            "height": prepared.height,
        }
        encoded_id = await ensure_output(
            self.session,
            PlannedTrack(0, prepared.fingerprint, spec, 2),
            track_id,
            thumbnail_id=row.id,
        )
        row.normalized_key, row.normalized_size, row.encoded_id = (
            prepared.object_key,
            prepared.size,
            encoded_id,
        )
        row.status, row.token, row.error_code = "prepared", None, None
        return True

    async def fail(self, upload_id: int, token: UUID, code: str, *, terminal: bool) -> None:
        row = await self.current(upload_id, token)
        if row is not None:
            row.status, row.token, row.error_code = "failed" if terminal else "uploaded", None, code

    async def generated(self, video_id: int) -> EncodedTrackRow | None:
        plan = await self.session.scalar(
            select(MediaPlanRow)
            .join(UploadRow, UploadRow.id == MediaPlanRow.upload_id)
            .where(UploadRow.video_id == video_id)
        )
        if plan is None or plan.config is None or type(plan.config.get("thumbnail")) is not int:
            return None
        return await self.session.get(EncodedTrackRow, cast(int, plan.config["thumbnail"]))

    async def sync_choice(self, row: ThumbnailChoiceRow) -> ThumbnailUploadRow | None:
        if row.requested_upload_id is None:
            return None
        requested = await self.session.get(ThumbnailUploadRow, row.requested_upload_id)
        if requested is not None and requested.encoded_id is not None:
            encoded = await self.session.get(EncodedTrackRow, requested.encoded_id)
            if encoded is not None and encoded.status == "ready":
                row.custom_encoded_id = encoded.id
        return requested

    async def options(self, video_id: int) -> ThumbnailOptions:
        choice = await self.choice(video_id)
        requested = await self.sync_choice(choice)
        generated = await self.generated(video_id)
        base = f"/api/v1/videos/{video_id}/thumbnail"
        ready = generated is not None and generated.status == "ready"
        custom_status = requested.status if requested else None
        error = requested.error_code if requested else None
        if requested is not None and requested.encoded_id is not None:
            encoded = await self.session.get(EncodedTrackRow, requested.encoded_id)
            if encoded is not None:
                custom_status, error = encoded.status, encoded.error_code
        return ThumbnailOptions(
            generated.status if generated else "pending",
            "custom" if choice.custom_encoded_id else "generated",
            choice.slot,
            tuple(f"{base}/generated/{slot}" for slot in range(3)) if ready else (),
            f"{base}/image" if ready or choice.custom_encoded_id else None,
            custom_status,
            error,
        )

    async def select(self, video_id: int, slot: int) -> None:
        generated = await self.generated(video_id)
        if generated is None or generated.status != "ready":
            raise ThumbnailError("thumbnails_not_ready")
        row = await self.choice(video_id)
        row.generation += 1
        if (
            row.slot != slot
            or row.custom_encoded_id is not None
            or row.requested_upload_id is not None
        ):
            row.slot, row.custom_encoded_id, row.requested_upload_id = slot, None, None
            await self.session.execute(
                update(PublicationRow)
                .where(PublicationRow.id == video_id)
                .values(updated_at=func.clock_timestamp())
            )

    async def image_key(self, video_id: int, slot: int | None) -> str:
        choice = await self.choice(video_id)
        await self.sync_choice(choice)
        if slot is None and choice.custom_encoded_id is not None:
            encoded = await self.session.get(EncodedTrackRow, choice.custom_encoded_id)
            name = "image.webp"
        else:
            encoded = await self.generated(video_id)
            name = ""
        if encoded is None or encoded.status != "ready" or encoded.artifact is None:
            raise VideoNotFound
        if not name:
            names = cast(list[str], encoded.artifact["candidates"])
            name = names[choice.slot if slot is None else slot]
        return str(encoded.artifact["prefix"]) + name

    async def image_media(self, video_id: int, slot: int | None) -> StoredMedia:
        key = await self.image_key(video_id, slot)
        try:
            choice = await self.choice(video_id)
            encoded = (
                await self.session.get(EncodedTrackRow, choice.custom_encoded_id)
                if slot is None and choice.custom_encoded_id is not None
                else await self.generated(video_id)
            )
            if encoded is None or encoded.artifact is None:
                raise VideoNotFound
            artifact = object_value(encoded.artifact)
            entry = next(
                object_value(item)
                for item in sequence(artifact["files"])
                if str(artifact["prefix"]) + str(object_value(item)["name"]) == key
            )
            return StoredMedia(key, number(entry["size"]), "image/webp")
        except KeyError, ValueError, TypeError, IndexError, StopIteration:
            raise VideoNotFound from None

    async def repair(self) -> int:
        # These technical rows belong to Video; no foreign business tables are queried.
        result = await self.session.execute(
            text(
                "UPDATE video_thumbnail_uploads t SET status='failed',token=NULL,"
                "error_code=coalesce(t.error_code,'thumbnail_failed') FROM procrastinate_jobs q "
                "WHERE t.job_id=q.id AND t.status IN ('uploaded','preparing') "
                "AND q.status::text IN ('failed','aborted','cancelled') RETURNING t.id"
            )
        )
        return len(result.all())

    async def expired_preparation(self) -> tuple[UUID, str] | None:
        row = (
            await self.session.execute(
                select(ThumbnailPreparationRow.token, ThumbnailPreparationRow.prefix)
                .join(
                    ThumbnailUploadRow, ThumbnailUploadRow.id == ThumbnailPreparationRow.upload_id
                )
                .where(
                    ThumbnailPreparationRow.expires_at < func.now(),
                    (
                        ThumbnailUploadRow.token.is_(None)
                        | (ThumbnailUploadRow.token != ThumbnailPreparationRow.token)
                    ),
                    (
                        ThumbnailUploadRow.normalized_key.is_(None)
                        | (
                            ThumbnailUploadRow.normalized_key
                            != ThumbnailPreparationRow.prefix + "pixels.rgba"
                        )
                    ),
                )
                .limit(1)
                .with_for_update(of=(ThumbnailPreparationRow, ThumbnailUploadRow), skip_locked=True)
            )
        ).one_or_none()
        return (row.token, row.prefix) if row else None

    async def forget_preparation(self, token: UUID) -> None:
        await self.session.execute(
            delete(ThumbnailPreparationRow).where(ThumbnailPreparationRow.token == token)
        )

    async def completed_source(self) -> tuple[int, tuple[str, ...]] | None:
        row = await self.session.scalar(
            select(ThumbnailUploadRow)
            .join(EncodedTrackRow, EncodedTrackRow.id == ThumbnailUploadRow.encoded_id)
            .where(EncodedTrackRow.status == "ready", ~ThumbnailUploadRow.source_deleted)
            .limit(1)
            .with_for_update(of=ThumbnailUploadRow, skip_locked=True)
        )
        if row is None or row.normalized_key is None:
            return None
        return row.id, (row.object_key, row.normalized_key.rsplit("/", 1)[0] + "/")

    async def forget_source(self, upload_id: int) -> None:
        row = await self.session.get(ThumbnailUploadRow, upload_id)
        if row is not None and row.normalized_key is not None:
            row.source_deleted = True
            await self.session.execute(
                delete(ThumbnailPreparationRow).where(
                    ThumbnailPreparationRow.prefix == row.normalized_key.rsplit("/", 1)[0] + "/"
                )
            )
