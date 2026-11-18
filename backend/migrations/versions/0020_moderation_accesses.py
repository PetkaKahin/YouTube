"""Initial schema: moderation_accesses."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_020"
down_revision = "initial_019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "moderation_accesses",
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
        sa.Column("report_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("revision", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("actor_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("reason", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("resource", sa.TEXT(), autoincrement=False, nullable=False),
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
            name=op.f("ck_moderation_accesses_valid_reason"),
        ),
        sa.CheckConstraint(
            "resource = ANY (ARRAY['video'::text, 'master'::text, 'preview'::text, 'track'::text])",
            name=op.f("ck_moderation_accesses_valid_resource"),
        ),
        sa.CheckConstraint("revision > 0", name=op.f("ck_moderation_accesses_positive_revision")),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["user_accounts.id"],
            name=op.f("fk_moderation_accesses_actor_id_user_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["moderation_reports.id"],
            name=op.f("fk_moderation_accesses_report_id_moderation_reports"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_moderation_accesses")),
    )


def downgrade() -> None:
    op.drop_table("moderation_accesses")
