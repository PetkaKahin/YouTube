"""Bounded durable retry requests and permanent administrator audit."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "background_job_retry_requests",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("selection", postgresql.JSONB(none_as_null=True)),
        sa.Column("upper_error_id", sa.BigInteger(), nullable=False),
        sa.Column("after_job_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("served_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "actor_id > 0 AND upper_error_id >= 0 AND after_job_id >= 0", name="positive_ids"
        ),
        sa.CheckConstraint("status IN ('pending','submitted','cancelled')", name="valid_status"),
        sa.CheckConstraint(
            "selection IS NULL OR (jsonb_typeof(selection)='array' "
            "AND jsonb_array_length(selection)>0)",
            name="valid_selection",
        ),
    )
    op.create_index(
        "ix_background_job_retry_requests_pending",
        "background_job_retry_requests",
        ["status", "served_at", "id"],
    )
    op.create_table(
        "background_job_retry_items",
        sa.Column(
            "request_id",
            sa.BigInteger(),
            sa.ForeignKey("background_job_retry_requests.id"),
            primary_key=True,
        ),
        sa.Column("job_id", sa.BigInteger(), primary_key=True),
        sa.Column("replacement_job_id", sa.BigInteger()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.CheckConstraint(
            "job_id > 0 AND (replacement_job_id IS NULL OR replacement_job_id > 0)",
            name="positive_ids",
        ),
        sa.CheckConstraint("status IN ('queued','skipped','unavailable')", name="valid_status"),
        sa.CheckConstraint(
            "status<>'queued' OR replacement_job_id IS NOT NULL", name="queued_target"
        ),
    )
    op.create_index(
        "uq_background_job_retry_items_queued",
        "background_job_retry_items",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("status='queued'"),
    )
    op.create_index(
        "ix_background_job_errors_order", "background_job_errors", ["occurred_at", "id"]
    )


def downgrade():
    op.drop_index("ix_background_job_errors_order", table_name="background_job_errors")
    op.drop_table("background_job_retry_items")
    op.drop_table("background_job_retry_requests")
