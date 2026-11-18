"""Initial schema: video_source_processing."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_023"
down_revision = "initial_022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_source_processing",
        sa.Column("upload_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("job_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column(
            "status",
            sa.TEXT(),
            server_default=sa.text("'pending'::text"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("token", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column("error_code", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column(
            "inspection",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "inspected_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("queue_attempt", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.Column("content_id", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.CheckConstraint(
            "(status = 'inspected'::text) = (inspection IS NOT NULL AND inspected_at IS NOT NULL)",
            name=op.f("ck_video_source_processing_inspection_result"),
        ),
        sa.CheckConstraint(
            "(status = 'inspecting'::text) = (token IS NOT NULL)",
            name=op.f("ck_video_source_processing_active_token"),
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]*$'::text",
            name=op.f("ck_video_source_processing_valid_error_code"),
        ),
        sa.CheckConstraint(
            (
                "status = ANY (ARRAY['pending'::text, 'inspecting'::text, 'inspec"
                "ted'::text, 'failed'::text])"
            ),
            name=op.f("ck_video_source_processing_valid_status"),
        ),
        sa.CheckConstraint("job_id > 0", name=op.f("ck_video_source_processing_positive_job")),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["video_media_contents.id"],
            name=op.f("fk_video_source_processing_content_id_video_media_contents"),
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["video_uploads.id"],
            name=op.f("fk_video_source_processing_upload_id_video_uploads"),
        ),
        sa.PrimaryKeyConstraint("upload_id", name=op.f("pk_video_source_processing")),
        sa.UniqueConstraint(
            "job_id",
            name=op.f("uq_video_source_processing_job_id"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        op.f("ix_video_source_processing_content_id"),
        "video_source_processing",
        ["content_id"],
        unique=False,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_video_source_processing_content_id"),
        table_name="video_source_processing",
        postgresql_include=[],
    )
    op.drop_table("video_source_processing")
