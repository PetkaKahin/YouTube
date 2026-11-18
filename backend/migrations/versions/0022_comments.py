"""Persistent discussions and one active like per comment and account."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "comment_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "video_id", sa.BigInteger(), sa.ForeignKey("video_publications.id"), nullable=False
        ),
        sa.Column("author_id", sa.BigInteger(), sa.ForeignKey("user_accounts.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint("char_length(text) BETWEEN 1 AND 2000", name="text_length"),
        sa.CheckConstraint("text !~ '^[[:space:]]*$'", name="nonempty_text"),
    )
    op.create_index(
        "ix_comment_entries_video_order",
        "comment_entries",
        ["video_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_table(
        "comment_likes",
        sa.Column(
            "comment_id", sa.BigInteger(), sa.ForeignKey("comment_entries.id"), primary_key=True
        ),
        sa.Column(
            "account_id", sa.BigInteger(), sa.ForeignKey("user_accounts.id"), primary_key=True
        ),
    )


def downgrade():
    op.drop_table("comment_likes")
    op.drop_index("ix_comment_entries_video_order", table_name="comment_entries")
    op.drop_table("comment_entries")
