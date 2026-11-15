"""Private access is addressed to email, independently of account registration."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "video_private_grants",
        sa.Column(
            "video_id", sa.BigInteger(), sa.ForeignKey("video_publications.id"), primary_key=True
        ),
        sa.Column("email", sa.Text(), primary_key=True),
        sa.CheckConstraint("char_length(email) > 0", name="nonempty_email"),
    )


def downgrade():
    op.drop_table("video_private_grants")
