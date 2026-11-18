"""Ordered owner lists for published videos and drafts."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_video_publications_owner_list",
        "video_publications",
        ["owner_id", "publication", sa.text("created_at DESC"), sa.text("id DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_video_publications_owner_list", table_name="video_publications")
