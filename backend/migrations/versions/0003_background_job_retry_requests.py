"""Initial schema: background_job_retry_requests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_003"
down_revision = "initial_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_job_retry_requests",
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
        sa.Column("actor_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column(
            "selection", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True
        ),
        sa.Column("upper_error_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column(
            "after_job_id",
            sa.BIGINT(),
            server_default=sa.text("'0'::bigint"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.TEXT(),
            server_default=sa.text("'pending'::text"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "served_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.CheckConstraint(
            (
                "selection IS NULL OR jsonb_typeof(selection) = 'array'::text AND"
                " jsonb_array_length(selection) > 0"
            ),
            name=op.f("ck_background_job_retry_requests_valid_selection"),
        ),
        sa.CheckConstraint(
            "status = ANY (ARRAY['pending'::text, 'submitted'::text, 'cancelled'::text])",
            name=op.f("ck_background_job_retry_requests_valid_status"),
        ),
        sa.CheckConstraint(
            "actor_id > 0 AND upper_error_id >= 0 AND after_job_id >= 0",
            name=op.f("ck_background_job_retry_requests_positive_ids"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_job_retry_requests")),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        op.f("ix_background_job_retry_requests_pending"),
        "background_job_retry_requests",
        ["status", "served_at", "id"],
        unique=False,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_background_job_retry_requests_pending"),
        table_name="background_job_retry_requests",
        postgresql_include=[],
    )
    op.drop_table("background_job_retry_requests")
