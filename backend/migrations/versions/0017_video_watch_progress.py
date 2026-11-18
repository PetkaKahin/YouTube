"""Initial schema: video_watch_progress."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_017"
down_revision = "initial_016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_watch_progress",
        sa.Column("account_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("video_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column(
            "position_seconds",
            sa.DOUBLE_PRECISION(precision=53),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "duration_seconds",
            sa.DOUBLE_PRECISION(precision=53),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "duration_seconds > 0::double precision AND duration_seconds <= 3600::double precision",
            name=op.f("ck_video_watch_progress_valid_duration"),
        ),
        sa.CheckConstraint(
            "position_seconds >= 0::double precision AND position_seconds <= duration_seconds",
            name=op.f("ck_video_watch_progress_valid_position"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["user_accounts.id"],
            name=op.f("fk_video_watch_progress_account_id_user_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["video_publications.id"],
            name=op.f("fk_video_watch_progress_video_id_video_publications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", "video_id", name=op.f("pk_video_watch_progress")),
    )
    op.create_index(
        op.f("ix_video_watch_progress_account_order"),
        "video_watch_progress",
        ["account_id", sa.literal_column("updated_at DESC"), sa.literal_column("video_id DESC")],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        op.f("ix_video_watch_progress_video_id"),
        "video_watch_progress",
        ["video_id"],
        unique=False,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_video_watch_progress_video_id"),
        table_name="video_watch_progress",
        postgresql_include=[],
    )
    op.drop_index(
        op.f("ix_video_watch_progress_account_order"),
        table_name="video_watch_progress",
        postgresql_include=[],
    )
    op.drop_table("video_watch_progress")
