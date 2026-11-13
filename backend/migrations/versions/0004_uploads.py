"""Persistent multipart attempts and the approved PostgreSQL queue schema.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from procrastinate.schema import SchemaManager

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "video_uploads",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "video_id", sa.BigInteger(), sa.ForeignKey("video_publications.id"), nullable=False
        ),
        sa.Column("object_key", sa.Text(), nullable=False, unique=True),
        sa.Column("multipart_id", sa.Text(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="creating"),
        sa.Column(
            "activity_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("size BETWEEN 1 AND 5000000000", name="valid_size"),
        sa.CheckConstraint(
            "status IN ('creating','uploading','uploaded','expired')", name="valid_status"
        ),
    )
    op.create_index("ix_video_uploads_video_id", "video_uploads", ["video_id"])
    op.create_index("ix_video_uploads_activity_at", "video_uploads", ["activity_at"])
    op.create_index(
        "uq_video_uploads_active",
        "video_uploads",
        ["video_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'expired'"),
    )
    # Vendored schema is pinned by procrastinate==3.11.0 in the lockfile.
    if op.get_bind().scalar(sa.text("SELECT to_regclass('procrastinate_jobs')")) is None:
        op.get_bind().exec_driver_sql(SchemaManager.get_schema().replace("%", "%%"))


def downgrade():
    # Library schema is persistent operational history; never cascade-drop it here.
    op.drop_table("video_uploads")
