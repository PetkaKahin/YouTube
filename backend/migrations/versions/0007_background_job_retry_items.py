"""Initial schema: background_job_retry_items."""

import sqlalchemy as sa
from alembic import op

revision = "initial_007"
down_revision = "initial_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_job_retry_items",
        sa.Column("request_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("job_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("replacement_job_id", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.Column("status", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("reason", sa.TEXT(), autoincrement=False, nullable=True),
        sa.CheckConstraint(
            "status <> 'queued'::text OR replacement_job_id IS NOT NULL",
            name=op.f("ck_background_job_retry_items_queued_target"),
        ),
        sa.CheckConstraint(
            "status = ANY (ARRAY['queued'::text, 'skipped'::text, 'unavailable'::text])",
            name=op.f("ck_background_job_retry_items_valid_status"),
        ),
        sa.CheckConstraint(
            "job_id > 0 AND (replacement_job_id IS NULL OR replacement_job_id > 0)",
            name=op.f("ck_background_job_retry_items_positive_ids"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["background_job_retry_requests.id"],
            name=op.f("fk_background_job_retry_items_request_id_background_job_d073"),
        ),
        sa.PrimaryKeyConstraint("request_id", "job_id", name=op.f("pk_background_job_retry_items")),
    )
    op.create_index(
        op.f("uq_background_job_retry_items_queued"),
        "background_job_retry_items",
        ["job_id"],
        unique=True,
        postgresql_where="(status = 'queued'::text)",
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("uq_background_job_retry_items_queued"),
        table_name="background_job_retry_items",
        postgresql_where="(status = 'queued'::text)",
        postgresql_include=[],
    )
    op.drop_table("background_job_retry_items")
