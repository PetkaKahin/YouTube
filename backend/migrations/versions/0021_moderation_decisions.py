"""Initial schema: moderation_decisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_021"
down_revision = "initial_020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "moderation_decisions",
        sa.Column("report_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("revision", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("actor_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("action", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("reason", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            (
                "action = 'dismiss'::text AND reason IS NULL OR action = 'hide'::"
                "text AND reason IS NOT NULL AND (reason = ANY (ARRAY['spam'::tex"
                "t, 'harassment'::text, 'violence'::text, 'sexual_content'::text,"
                " 'copyright'::text, 'other'::text]))"
            ),
            name=op.f("ck_moderation_decisions_valid_reason"),
        ),
        sa.CheckConstraint(
            "action = ANY (ARRAY['dismiss'::text, 'hide'::text])",
            name=op.f("ck_moderation_decisions_valid_action"),
        ),
        sa.CheckConstraint("revision > 0", name=op.f("ck_moderation_decisions_positive_revision")),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["user_accounts.id"],
            name=op.f("fk_moderation_decisions_actor_id_user_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["moderation_reports.id"],
            name=op.f("fk_moderation_decisions_report_id_moderation_reports"),
        ),
        sa.PrimaryKeyConstraint("report_id", "revision", name=op.f("pk_moderation_decisions")),
    )


def downgrade() -> None:
    op.drop_table("moderation_decisions")
