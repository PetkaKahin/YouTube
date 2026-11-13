"""Owned video drafts and independent lifecycle states.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "video_publications",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("user_accounts.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("publication", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("upload", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("processing", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("moderation", sa.Text(), nullable=False, server_default="visible"),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 100", name="title_length"),
        sa.CheckConstraint("char_length(description) <= 5000", name="description_length"),
        sa.CheckConstraint("visibility IN ('public', 'private')", name="valid_visibility"),
        sa.CheckConstraint("publication IN ('draft', 'published')", name="valid_publication"),
        sa.CheckConstraint(
            "upload IN ('pending', 'uploading', 'uploaded', 'failed')", name="valid_upload"
        ),
        sa.CheckConstraint(
            "processing IN ('pending', 'processing', 'ready', 'partial', 'failed')",
            name="valid_processing",
        ),
        sa.CheckConstraint("moderation IN ('visible', 'hidden')", name="valid_moderation"),
    )
    op.create_index("ix_video_publications_owner_id", "video_publications", ["owner_id"])


def downgrade():
    op.drop_table("video_publications")
