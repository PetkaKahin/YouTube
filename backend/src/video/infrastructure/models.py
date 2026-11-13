"""Video owns publication metadata; physical media will have separate identities."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Text,
    func,
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
