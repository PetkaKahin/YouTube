"""Initial schema: background_job_errors."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_002"
down_revision = "initial_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_job_errors",
        sa.Column(
            "id",
            sa.BIGINT(),
            sa.Identity(
                always=False,
                start=1,
                increment=1,
                minvalue=1,
                maxvalue=9223372036854775807,
                cycle=False,
                cache=1,
            ),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("event_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("job_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("attempt", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("stage", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "occurred_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column("reason", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("exception_type", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "trace", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column("error_code", sa.TEXT(), autoincrement=False, nullable=True),
        sa.CheckConstraint(
            "reason = ANY (ARRAY['task_failed'::text, 'worker_lost'::text, 'cancelled'::text])",
            name=op.f("ck_background_job_errors_valid_reason"),
        ),
        sa.CheckConstraint(
            "stage ~ '^[a-z][a-z0-9_.]*$'::text", name=op.f("ck_background_job_errors_valid_stage")
        ),
        sa.CheckConstraint(
            "job_id > 0 AND attempt > 0", name=op.f("ck_background_job_errors_positive_job_attempt")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_job_errors")),
        sa.UniqueConstraint(
            "event_id",
            name=op.f("uq_background_job_errors_event_id"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_background_job_errors_job_id"),
        "background_job_errors",
        ["job_id", "occurred_at"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        op.f("ix_background_job_errors_order"),
        "background_job_errors",
        ["occurred_at", "id"],
        unique=False,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_background_job_errors_order"),
        table_name="background_job_errors",
        postgresql_include=[],
    )
    op.drop_index(
        op.f("ix_background_job_errors_job_id"),
        table_name="background_job_errors",
        postgresql_include=[],
    )
    op.drop_table("background_job_errors")
