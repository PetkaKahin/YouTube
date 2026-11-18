"""Initial schema: channel_subscriptions."""

import sqlalchemy as sa
from alembic import op

revision = "initial_011"
down_revision = "initial_010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_subscriptions",
        sa.Column("subscriber_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("channel_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channel_profiles.id"],
            name=op.f("fk_channel_subscriptions_channel_id_channel_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["subscriber_id"],
            ["user_accounts.id"],
            name=op.f("fk_channel_subscriptions_subscriber_id_user_accounts"),
        ),
        sa.PrimaryKeyConstraint(
            "subscriber_id", "channel_id", name=op.f("pk_channel_subscriptions")
        ),
    )


def downgrade() -> None:
    op.drop_table("channel_subscriptions")
