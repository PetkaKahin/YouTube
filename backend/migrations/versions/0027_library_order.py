"""Stable timestamps for current likes and indexed personal list order."""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Old pairs share the migration timestamp; no historical date is claimed.
    op.add_column(
        "video_likes",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.alter_column("video_likes", "created_at", server_default=sa.func.clock_timestamp())
    op.create_index(
        "ix_video_likes_account_order",
        "video_likes",
        ["account_id", sa.text("created_at DESC"), sa.text("video_id DESC")],
    )
    op.create_index(
        "ix_video_watch_progress_account_order",
        "video_watch_progress",
        ["account_id", sa.text("updated_at DESC"), sa.text("video_id DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_video_watch_progress_account_order", table_name="video_watch_progress")
    op.drop_index("ix_video_likes_account_order", table_name="video_likes")
    op.drop_column("video_likes", "created_at")
