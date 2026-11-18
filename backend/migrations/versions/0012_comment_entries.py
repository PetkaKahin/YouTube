"""Initial schema: comment_entries."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_012"
down_revision = "initial_011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "comment_entries",
        sa.Column(
            "id",
            sa.BIGINT(),
            sa.Identity(
                always=False,
                start=1,
                increment=1,
                minvalue=1,
                maxvalue=9223372036854775807,
                cycle=False,
                cache=1,
            ),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("video_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("author_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("text", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "text !~ '^[[:space:]]*$'::text", name=op.f("ck_comment_entries_nonempty_text")
        ),
        sa.CheckConstraint(
            "char_length(text) >= 1 AND char_length(text) <= 2000",
            name=op.f("ck_comment_entries_text_length"),
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["user_accounts.id"],
            name=op.f("fk_comment_entries_author_id_user_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["video_publications.id"],
            name=op.f("fk_comment_entries_video_id_video_publications"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comment_entries")),
    )
    op.create_index(
        op.f("ix_comment_entries_video_order"),
        "comment_entries",
        ["video_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_comment_entries_video_order"), table_name="comment_entries", postgresql_include=[]
    )
    op.drop_table("comment_entries")
