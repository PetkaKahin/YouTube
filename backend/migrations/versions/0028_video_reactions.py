"""Keep one active like or dislike for each existing video/account pair."""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video_likes", sa.Column("reaction", sa.Text(), nullable=False, server_default="like")
    )
    op.create_check_constraint("valid_reaction", "video_likes", "reaction IN ('like','dislike')")


def downgrade() -> None:
    op.execute("DELETE FROM video_likes WHERE reaction='dislike'")
    op.drop_constraint(op.f("ck_video_likes_valid_reaction"), "video_likes", type_="check")
    op.drop_column("video_likes", "reaction")
