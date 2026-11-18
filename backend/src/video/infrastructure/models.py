"""Video owns publication metadata; physical media will have separate identities."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common.infrastructure.database import Base


class PublicationRow(Base):
    __tablename__ = "video_publications"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user_accounts.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    visibility: Mapped[str] = mapped_column(Text)
    publication: Mapped[str] = mapped_column(Text, server_default="draft")
    upload: Mapped[str] = mapped_column(Text, server_default="pending")
    processing: Mapped[str] = mapped_column(Text, server_default="pending")
    moderation: Mapped[str] = mapped_column(Text, server_default="visible")
    added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index(
            "ix_video_publications_owner_list",
            owner_id,
            publication,
            created_at.desc(),
            id.desc(),
        ),
        Index(
            "ix_video_publications_channel_feed",
            owner_id,
            added_at.desc(),
            id.desc(),
            postgresql_where=text(
                "visibility = 'public' AND publication = 'published' "
                "AND moderation = 'visible' AND added_at IS NOT NULL"
            ),
        ),
        Index(
            "ix_video_publications_public_feed",
            added_at.desc(),
            id.desc(),
            postgresql_where=text(
                "visibility = 'public' AND publication = 'published' "
                "AND moderation = 'visible' AND added_at IS NOT NULL"
            ),
        ),
        CheckConstraint("char_length(title) BETWEEN 1 AND 100", name="title_length"),
        CheckConstraint("char_length(description) <= 5000", name="description_length"),
        CheckConstraint("visibility IN ('public', 'private')", name="valid_visibility"),
        CheckConstraint("publication IN ('draft', 'published')", name="valid_publication"),
        CheckConstraint(
            "upload IN ('pending', 'uploading', 'uploaded', 'failed')", name="valid_upload"
        ),
        CheckConstraint(
            "processing IN ('pending', 'processing', 'ready', 'partial', 'failed')",
            name="valid_processing",
        ),
        CheckConstraint("moderation IN ('visible', 'hidden')", name="valid_moderation"),
    )




class LikeRow(Base):
    __tablename__ = "video_likes"

    video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_publications.id"), primary_key=True
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_accounts.id"), primary_key=True
    )


class PrivateGrantRow(Base):
    __tablename__ = "video_private_grants"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False, unique=True)
    video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_publications.id"), primary_key=True
    )
    email: Mapped[str] = mapped_column(Text, primary_key=True)

    __table_args__ = (
        CheckConstraint("char_length(email) > 0", name="nonempty_email"),
        Index("ix_video_private_grants_order", video_id, id.desc()),
    )


class UploadRow(Base):
    __tablename__ = "video_uploads"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_publications.id"), index=True
    )

    object_key: Mapped[str] = mapped_column(Text, unique=True)
    multipart_id: Mapped[str | None] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, server_default="creating")
    activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    original_cleanup_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    original_cleanup_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    original_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "(original_cleanup_started_at IS NULL OR status='uploaded') AND "
            "(original_deleted_at IS NULL OR original_cleanup_started_at IS NOT NULL)",
            name="original_cleanup_state",
        ),
        Index(
            "ix_video_uploads_original_cleanup",
            "original_cleanup_attempted_at",
            "id",
            postgresql_where=text("status='uploaded' AND original_deleted_at IS NULL"),
        ),
        CheckConstraint("size BETWEEN 1 AND 5000000000", name="valid_size"),
        CheckConstraint(
            "status IN ('creating','uploading','uploaded','expired')", name="valid_status"
        ),
        Index(
            "uq_video_uploads_active",
            "video_id",
            unique=True,
            postgresql_where=text("status <> 'expired'"),
        ),
    )


class SourceProcessingRow(Base):
    __tablename__ = "video_source_processing"

    upload_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_uploads.id"), primary_key=True
    )
    job_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    queue_attempt: Mapped[int | None] = mapped_column(BigInteger)
    content_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("video_media_contents.id"), index=True
    )
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    token: Mapped[UUID | None] = mapped_column(Uuid)
    error_code: Mapped[str | None] = mapped_column(Text)
    inspection: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    inspected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("job_id > 0", name="positive_job"),
        CheckConstraint(
            "status IN ('pending','inspecting','inspected','failed')", name="valid_status"
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]*$'", name="valid_error_code"
        ),
        CheckConstraint("(status = 'inspecting') = (token IS NOT NULL)", name="active_token"),
        CheckConstraint(
            "(status = 'inspected') = (inspection IS NOT NULL AND inspected_at IS NOT NULL)",
            name="inspection_result",
        ),
    )


class MediaContentRow(Base):
    __tablename__ = "video_media_contents"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(Text, unique=True)
    manifest: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="valid_fingerprint"),)


class MediaTrackRow(Base):
    __tablename__ = "video_media_tracks"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(Text, unique=True)
    properties: Mapped[dict[str, object]] = mapped_column(JSONB)
    packet_count: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="valid_fingerprint"),
        CheckConstraint("packet_count >= 0", name="valid_packet_count"),
    )


class SourceTrackRow(Base):
    __tablename__ = "video_source_tracks"

    upload_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_source_processing.upload_id"), primary_key=True
    )
    stream_index: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    track_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_media_tracks.id"), index=True
    )

    __table_args__ = (CheckConstraint("stream_index >= 0", name="valid_stream_index"),)


class MediaPlanRow(Base):
    __tablename__ = "video_media_plans"
    upload_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_source_processing.upload_id"), primary_key=True
    )
    job_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    config: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    error_code: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("status IN ('pending','planned','failed')", name="valid_status"),
    )


class EncodedTrackRow(Base):
    __tablename__ = "video_encoded_tracks"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    track_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_media_tracks.id"), index=True
    )
    profile_key: Mapped[str] = mapped_column(Text, unique=True)
    spec: Mapped[dict[str, object]] = mapped_column(JSONB)
    source_upload_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("video_uploads.id"), index=True
    )
    source_thumbnail_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "video_thumbnail_uploads.id", name="fk_encoded_thumbnail_source", use_alter=True
        ),
    )
    stream_index: Mapped[int] = mapped_column(BigInteger)
    job_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    token: Mapped[UUID | None] = mapped_column(Uuid)
    queue_attempt: Mapped[int | None] = mapped_column(BigInteger)
    artifact: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    error_code: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("profile_key ~ '^[0-9a-f]{64}$'", name="valid_profile_key"),
        CheckConstraint(
            "status IN ('pending','running','ready','empty','failed')", name="valid_status"
        ),
        CheckConstraint("(status='running') = (token IS NOT NULL)", name="active_token"),
        CheckConstraint("(status='ready') = (artifact IS NOT NULL)", name="ready_artifact"),
        CheckConstraint(
            "(source_upload_id IS NULL) <> (source_thumbnail_id IS NULL)", name="one_source"
        ),
    )


class PlanTrackRow(Base):
    __tablename__ = "video_plan_tracks"
    upload_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_media_plans.upload_id"), primary_key=True
    )
    encoded_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_encoded_tracks.id"), primary_key=True, index=True
    )


class EncodingAttemptRow(Base):
    __tablename__ = "video_encoding_attempts"
    token: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    encoded_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_encoded_tracks.id"), index=True
    )
    prefix: Mapped[str] = mapped_column(Text, unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SubtitleLinkRow(Base):
    __tablename__ = "video_subtitle_links"
    upload_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_media_plans.upload_id"), primary_key=True
    )
    stream_index: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    video_encoded_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_encoded_tracks.id"), primary_key=True, index=True
    )
    encoded_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("video_encoded_tracks.id"), index=True
    )
    clock_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("video_encoded_tracks.id"))
    status: Mapped[str] = mapped_column(Text, server_default="waiting")
    language: Mapped[str] = mapped_column(Text)
    disposition: Mapped[dict[str, object]] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("status IN ('waiting','queued','failed')", name="valid_status"),
        CheckConstraint("stream_index >= 0", name="valid_stream"),
        CheckConstraint("(status='queued') = (encoded_id IS NOT NULL)", name="queued_output"),
    )


class ThumbnailUploadRow(Base):
    __tablename__ = "video_thumbnail_uploads"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_publications.id"), index=True
    )
    object_key: Mapped[str] = mapped_column(Text, unique=True)
    multipart_id: Mapped[str | None] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, server_default="creating")
    activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    job_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    token: Mapped[UUID | None] = mapped_column(Uuid)
    queue_attempt: Mapped[int | None] = mapped_column(BigInteger)
    error_code: Mapped[str | None] = mapped_column(Text)
    normalized_key: Mapped[str | None] = mapped_column(Text)
    normalized_size: Mapped[int | None] = mapped_column(BigInteger)
    encoded_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("video_encoded_tracks.id")
    )
    source_deleted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    generation: Mapped[int] = mapped_column(BigInteger, server_default="0")
    selection_confirmed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    __table_args__ = (
        CheckConstraint("size BETWEEN 1 AND 10485760", name="valid_size"),
        CheckConstraint(
            "status IN ('creating','uploading','uploaded','preparing',"
            "'prepared','failed','expired')",
            name="valid_status",
        ),
        CheckConstraint("(status='preparing') = (token IS NOT NULL)", name="active_token"),
        CheckConstraint("generation >= 0", name="valid_generation"),
    )


class ThumbnailChoiceRow(Base):
    __tablename__ = "video_thumbnail_choices"
    video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_publications.id"), primary_key=True
    )
    slot: Mapped[int] = mapped_column(BigInteger, server_default="0")
    generation: Mapped[int] = mapped_column(BigInteger, server_default="0")
    custom_encoded_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("video_encoded_tracks.id")
    )
    requested_upload_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("video_thumbnail_uploads.id")
    )
    __table_args__ = (
        CheckConstraint("slot BETWEEN 0 AND 2", name="valid_slot"),
        CheckConstraint("generation >= 0", name="valid_generation"),
    )


class ThumbnailPreparationRow(Base):
    __tablename__ = "video_thumbnail_preparations"
    token: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    upload_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_thumbnail_uploads.id"), index=True
    )
    prefix: Mapped[str] = mapped_column(Text, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
