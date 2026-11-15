"""One active subscription per authenticated account and destination channel."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "channel_subscriptions",
        sa.Column(
            "subscriber_id", sa.BigInteger(), sa.ForeignKey("user_accounts.id"), primary_key=True
        ),
        sa.Column(
            "channel_id", sa.BigInteger(), sa.ForeignKey("channel_profiles.id"), primary_key=True
        ),
    )


def downgrade():
    op.drop_table("channel_subscriptions")
