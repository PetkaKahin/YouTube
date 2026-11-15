"""Fence late source results by both token and durable queue generation.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "video_source_processing", sa.Column("queue_attempt", sa.BigInteger(), nullable=True)
    )


def downgrade():
    op.drop_column("video_source_processing", "queue_attempt")
