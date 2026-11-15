"""Playlist metadata and ordered references; Video retains all viewing authority."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "channel_playlists",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "channel_id", sa.BigInteger(), sa.ForeignKey("channel_profiles.id"), nullable=False
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 100", name="title_length"),
        sa.CheckConstraint("char_length(description) <= 5000", name="description_length"),
        sa.CheckConstraint(
            "visibility IN ('public','unlisted','private')", name="valid_visibility"
        ),
    )
    op.create_index(
        "ix_channel_playlists_owner_order",
        "channel_playlists",
        ["channel_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_table(
        "channel_playlist_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "playlist_id", sa.BigInteger(), sa.ForeignKey("channel_playlists.id"), nullable=False
        ),
        sa.Column(
            "video_id", sa.BigInteger(), sa.ForeignKey("video_publications.id"), nullable=False
        ),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.UniqueConstraint("playlist_id", "video_id", name="uq_channel_playlist_entries_video"),
    )
    op.create_index(
        "ix_channel_playlist_entries_video_id", "channel_playlist_entries", ["video_id"]
    )
    op.create_index(
        "ix_channel_playlist_entries_order", "channel_playlist_entries", ["playlist_id", "id"]
    )


def downgrade():
    op.drop_table("channel_playlist_entries")
    op.drop_table("channel_playlists")
