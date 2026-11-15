"""A public feed seeks by the immutable upload-completion date and ID."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_video_publications_public_feed",
        "video_publications",
        [sa.text("added_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text(
            "visibility = 'public' AND publication = 'published' "
            "AND moderation = 'visible' AND added_at IS NOT NULL"
        ),
    )


def downgrade():
    op.drop_index("ix_video_publications_public_feed", table_name="video_publications")
