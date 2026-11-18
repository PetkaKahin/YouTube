"""Persist the last main-player position per account and video."""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_watch_progress",
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("user_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "video_id",
            sa.BigInteger(),
            sa.ForeignKey("video_publications.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position_seconds", sa.Double[float](), nullable=False),
        sa.Column("duration_seconds", sa.Double[float](), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.CheckConstraint(
            "duration_seconds > 0 AND duration_seconds <= 3600", name="valid_duration"
        ),
        sa.CheckConstraint(
            "position_seconds >= 0 AND position_seconds <= duration_seconds", name="valid_position"
        ),
    )
    op.create_index("ix_video_watch_progress_video_id", "video_watch_progress", ["video_id"])


def downgrade() -> None:
    op.drop_table("video_watch_progress")
