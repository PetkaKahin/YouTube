"""Technical history owned by the journal, never cascaded from the queue."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common.infrastructure.database import Base


class JobErrorRow(Base):
    __tablename__ = "background_job_errors"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    job_id: Mapped[int] = mapped_column(BigInteger)
    attempt: Mapped[int] = mapped_column(BigInteger)
    stage: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text)
    exception_type: Mapped[str] = mapped_column(Text)
    trace: Mapped[list[dict[str, str | int]]] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("job_id > 0 AND attempt > 0", name="positive_job_attempt"),
        CheckConstraint("stage ~ '^[a-z][a-z0-9_.]*$'", name="valid_stage"),
        CheckConstraint(
            "reason IN ('task_failed', 'worker_lost', 'cancelled')", name="valid_reason"
        ),
        Index("ix_background_job_errors_job_id", "job_id", "occurred_at"),
        Index("ix_background_job_errors_order", "occurred_at", "id"),
    )
