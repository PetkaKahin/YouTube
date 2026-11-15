"""Atomic media admission after authoritative upload completion.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "video_source_processing",
        sa.Column(
            "upload_id", sa.BigInteger(), sa.ForeignKey("video_uploads.id"), primary_key=True
        ),
        sa.Column("job_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("token", sa.Uuid(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("inspection", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("inspected_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("job_id > 0", name="positive_job"),
        sa.CheckConstraint(
            "status IN ('pending','inspecting','inspected','failed')", name="valid_status"
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]*$'", name="valid_error_code"
        ),
        sa.CheckConstraint("(status = 'inspecting') = (token IS NOT NULL)", name="active_token"),
        sa.CheckConstraint(
            "(status = 'inspected') = (inspection IS NOT NULL AND inspected_at IS NOT NULL)",
            name="inspection_result",
        ),
    )


def downgrade():
    op.drop_table("video_source_processing")
