"""Keep each attempt's actual deadline independent of later worker configuration.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    # Legacy attempts have no proven budget; NULL never authorizes their cleanup.
    op.add_column(
        "video_encoding_attempts",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_video_encoding_attempts_expires_at", "video_encoding_attempts", ["expires_at"]
    )


def downgrade():
    op.drop_index("ix_video_encoding_attempts_expires_at", table_name="video_encoding_attempts")
    op.drop_column("video_encoding_attempts", "expires_at")
