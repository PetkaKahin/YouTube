"""One active like for an account and logical video publication."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "video_likes",
        sa.Column(
            "video_id", sa.BigInteger(), sa.ForeignKey("video_publications.id"), primary_key=True
        ),
        sa.Column(
            "account_id", sa.BigInteger(), sa.ForeignKey("user_accounts.id"), primary_key=True
        ),
    )


def downgrade():
    op.drop_table("video_likes")
