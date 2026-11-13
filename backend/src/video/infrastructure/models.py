"""Video owns publication metadata; physical media will have separate identities."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    func,
    text,
)
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

    __table_args__ = (
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
