"""Initial schema: video_likes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_014"
down_revision = "initial_013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_likes",
        sa.Column("video_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("account_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "reaction",
            sa.TEXT(),
            server_default=sa.text("'like'::text"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "reaction = ANY (ARRAY['like'::text, 'dislike'::text])",
            name=op.f("ck_video_likes_valid_reaction"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["user_accounts.id"],
            name=op.f("fk_video_likes_account_id_user_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["video_publications.id"],
            name=op.f("fk_video_likes_video_id_video_publications"),
        ),
        sa.PrimaryKeyConstraint("video_id", "account_id", name=op.f("pk_video_likes")),
    )
    op.create_index(
        op.f("ix_video_likes_account_order"),
        "video_likes",
        ["account_id", sa.literal_column("created_at DESC"), sa.literal_column("video_id DESC")],
        unique=False,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_video_likes_account_order"), table_name="video_likes", postgresql_include=[]
    )
    op.drop_table("video_likes")
