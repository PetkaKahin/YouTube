"""Durable retry intentions and audit records independent of prunable queue rows."""

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common.infrastructure.database import Base


class JobRetryRequestRow(Base):
    __tablename__ = "background_job_retry_requests"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    selection: Mapped[list[int] | None] = mapped_column(JSONB(none_as_null=True))
    upper_error_id: Mapped[int] = mapped_column(BigInteger)
    after_job_id: Mapped[int] = mapped_column(BigInteger, server_default="0")
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    served_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "actor_id > 0 AND upper_error_id >= 0 AND after_job_id >= 0", name="positive_ids"
        ),
        CheckConstraint("status IN ('pending','submitted','cancelled')", name="valid_status"),
        CheckConstraint(
            "selection IS NULL OR (jsonb_typeof(selection)='array' "
            "AND jsonb_array_length(selection)>0)",
            name="valid_selection",
        ),
        Index("ix_background_job_retry_requests_pending", "status", "served_at", "id"),
    )


class JobRetryItemRow(Base):
    __tablename__ = "background_job_retry_items"

    request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("background_job_retry_requests.id"), primary_key=True
    )
    job_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    replacement_job_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "job_id > 0 AND (replacement_job_id IS NULL OR replacement_job_id > 0)",
            name="positive_ids",
        ),
        CheckConstraint("status IN ('queued','skipped','unavailable')", name="valid_status"),
        CheckConstraint("status<>'queued' OR replacement_job_id IS NOT NULL", name="queued_target"),
        Index(
            "uq_background_job_retry_items_queued",
            "job_id",
            unique=True,
            postgresql_where=text("status='queued'"),
        ),
    )
