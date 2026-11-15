"""Seek one author's public videos without scanning the global feed."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_video_publications_channel_feed",
        "video_publications",
        ["owner_id", sa.text("added_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text(
            "visibility = 'public' AND publication = 'published' "
            "AND moderation = 'visible' AND added_at IS NOT NULL"
        ),
    )


def downgrade():
    op.drop_index("ix_video_publications_channel_feed", table_name="video_publications")
