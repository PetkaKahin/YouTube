"""One current report per pair, durable decisions and accountable staff reads."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

REASONS = "'spam','harassment','violence','sexual_content','copyright','other'"


def upgrade():
    op.create_table(
        "moderation_reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "video_id", sa.BigInteger(), sa.ForeignKey("video_publications.id"), nullable=False
        ),
        sa.Column(
            "reporter_id", sa.BigInteger(), sa.ForeignKey("user_accounts.id"), nullable=False
        ),
        sa.Column("revision", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="pending", nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.UniqueConstraint("reporter_id", "video_id"),
        sa.CheckConstraint("revision > 0", name="positive_revision"),
        sa.CheckConstraint(f"reason IN ({REASONS})", name="valid_reason"),
        sa.CheckConstraint("state IN ('pending','reviewed')", name="valid_state"),
    )
    op.create_index(
        "ix_moderation_reports_queue",
        "moderation_reports",
        ["submitted_at", "id"],
        postgresql_where=sa.text("state='pending'"),
    )
    op.create_table(
        "moderation_decisions",
        sa.Column(
            "report_id", sa.BigInteger(), sa.ForeignKey("moderation_reports.id"), primary_key=True
        ),
        sa.Column("revision", sa.BigInteger(), primary_key=True),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("user_accounts.id"), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint("revision > 0", name="positive_revision"),
        sa.CheckConstraint("action IN ('dismiss','hide')", name="valid_action"),
        sa.CheckConstraint(
            "(action='dismiss' AND reason IS NULL) OR "
            f"(action='hide' AND reason IS NOT NULL AND reason IN ({REASONS}))",
            name="valid_reason",
        ),
    )
    op.create_table(
        "moderation_accesses",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "report_id", sa.BigInteger(), sa.ForeignKey("moderation_reports.id"), nullable=False
        ),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("user_accounts.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint("revision > 0", name="positive_revision"),
        sa.CheckConstraint(f"reason IN ({REASONS})", name="valid_reason"),
        sa.CheckConstraint(
            "resource IN ('video','master','preview','track')", name="valid_resource"
        ),
    )


def downgrade():
    op.drop_table("moderation_accesses")
    op.drop_table("moderation_decisions")
    op.drop_index("ix_moderation_reports_queue", table_name="moderation_reports")
    op.drop_table("moderation_reports")
