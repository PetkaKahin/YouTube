"""Moderation owns reports, revision decisions and staff access evidence."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from common.infrastructure.database import Base

REASONS = "'spam','harassment','violence','sexual_content','copyright','other'"


class ReportRow(Base):
    __tablename__ = "moderation_reports"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    video_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("video_publications.id"))
    reporter_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user_accounts.id"))
    revision: Mapped[int] = mapped_column(BigInteger, server_default="1")
    reason: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="pending")
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )

    __table_args__ = (
        UniqueConstraint("reporter_id", "video_id"),
        CheckConstraint("revision > 0", name="positive_revision"),
        CheckConstraint(f"reason IN ({REASONS})", name="valid_reason"),
        CheckConstraint("state IN ('pending','reviewed')", name="valid_state"),
        Index(
            "ix_moderation_reports_queue",
            submitted_at,
            id,
            postgresql_where=text("state='pending'"),
        ),
    )


class DecisionRow(Base):
    __tablename__ = "moderation_decisions"

    report_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("moderation_reports.id"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user_accounts.id"))
    action: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )

    __table_args__ = (
        CheckConstraint("revision > 0", name="positive_revision"),
        CheckConstraint("action IN ('dismiss','hide')", name="valid_action"),
        CheckConstraint(
            "(action='dismiss' AND reason IS NULL) OR "
            f"(action='hide' AND reason IS NOT NULL AND reason IN ({REASONS}))",
            name="valid_reason",
        ),
    )


class ReviewAccessRow(Base):
    __tablename__ = "moderation_accesses"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    report_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("moderation_reports.id"))
    revision: Mapped[int] = mapped_column(BigInteger)
    actor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user_accounts.id"))
    reason: Mapped[str] = mapped_column(Text)
    resource: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )

    __table_args__ = (
        CheckConstraint("revision > 0", name="positive_revision"),
        CheckConstraint(f"reason IN ({REASONS})", name="valid_reason"),
        CheckConstraint("resource IN ('video','master','preview','track')", name="valid_resource"),
    )
