"""Initial schema: video_media_plans."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_027"
down_revision = "initial_026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_media_plans",
        sa.Column("upload_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("job_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column(
            "status",
            sa.TEXT(),
            server_default=sa.text("'pending'::text"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "config", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True
        ),
        sa.Column("error_code", sa.TEXT(), autoincrement=False, nullable=True),
        sa.CheckConstraint(
            "status = ANY (ARRAY['pending'::text, 'planned'::text, 'failed'::text])",
            name=op.f("ck_video_media_plans_valid_status"),
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["video_source_processing.upload_id"],
            name=op.f("fk_video_media_plans_upload_id_video_source_processing"),
        ),
        sa.PrimaryKeyConstraint("upload_id", name=op.f("pk_video_media_plans")),
        sa.UniqueConstraint(
            "job_id",
            name=op.f("uq_video_media_plans_job_id"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("video_media_plans")
