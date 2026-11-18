"""Initial schema: moderation_reports."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_013"
down_revision = "initial_012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "moderation_reports",
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
        sa.Column("reporter_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column(
            "revision",
            sa.BIGINT(),
            server_default=sa.text("'1'::bigint"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("reason", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "state",
            sa.TEXT(),
            server_default=sa.text("'pending'::text"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "submitted_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            (
                "reason = ANY (ARRAY['spam'::text, 'harassment'::text, 'violence'"
                "::text, 'sexual_content'::text, 'copyright'::text, 'other'::text"
                "])"
            ),
            name=op.f("ck_moderation_reports_valid_reason"),
        ),
        sa.CheckConstraint(
            "state = ANY (ARRAY['pending'::text, 'reviewed'::text])",
            name=op.f("ck_moderation_reports_valid_state"),
        ),
        sa.CheckConstraint("revision > 0", name=op.f("ck_moderation_reports_positive_revision")),
        sa.ForeignKeyConstraint(
            ["reporter_id"],
            ["user_accounts.id"],
            name=op.f("fk_moderation_reports_reporter_id_user_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["video_publications.id"],
            name=op.f("fk_moderation_reports_video_id_video_publications"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_moderation_reports")),
        sa.UniqueConstraint(
            "reporter_id",
            "video_id",
            name=op.f("uq_moderation_reports_reporter_id"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        op.f("ix_moderation_reports_queue"),
        "moderation_reports",
        ["submitted_at", "id"],
        unique=False,
        postgresql_where="(state = 'pending'::text)",
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_moderation_reports_queue"),
        table_name="moderation_reports",
        postgresql_where="(state = 'pending'::text)",
        postgresql_include=[],
    )
    op.drop_table("moderation_reports")
