"""Durable job error history independent of queue cleanup.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "background_job_errors",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt", sa.BigInteger(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("exception_type", sa.Text(), nullable=False),
        sa.Column("trace", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("job_id > 0 AND attempt > 0", name="positive_job_attempt"),
        sa.CheckConstraint("stage ~ '^[a-z][a-z0-9_.]*$'", name="valid_stage"),
        sa.CheckConstraint(
            "reason IN ('task_failed', 'worker_lost', 'cancelled')", name="valid_reason"
        ),
    )
    op.create_index(
        "ix_background_job_errors_job_id", "background_job_errors", ["job_id", "occurred_at"]
    )


def downgrade():
    op.drop_table("background_job_errors")
