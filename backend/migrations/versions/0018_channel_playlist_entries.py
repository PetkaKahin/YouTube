"""Initial schema: channel_playlist_entries."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_018"
down_revision = "initial_017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_playlist_entries",
        sa.Column(
            "id",
            sa.BIGINT(),
            sa.Identity(
                always=False,
                start=1,
                increment=1,
                minvalue=1,
                maxvalue=9223372036854775807,
                cycle=False,
                cache=1,
            ),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("playlist_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("video_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column(
            "added_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["playlist_id"],
            ["channel_playlists.id"],
            name=op.f("fk_channel_playlist_entries_playlist_id_channel_playlists"),
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["video_publications.id"],
            name=op.f("fk_channel_playlist_entries_video_id_video_publications"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_playlist_entries")),
        sa.UniqueConstraint(
            "playlist_id",
            "video_id",
            name=op.f("uq_channel_playlist_entries_video"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_channel_playlist_entries_order"),
        "channel_playlist_entries",
        ["playlist_id", "id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        op.f("ix_channel_playlist_entries_video_id"),
        "channel_playlist_entries",
        ["video_id"],
        unique=False,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_channel_playlist_entries_video_id"),
        table_name="channel_playlist_entries",
        postgresql_include=[],
    )
    op.drop_index(
        op.f("ix_channel_playlist_entries_order"),
        table_name="channel_playlist_entries",
        postgresql_include=[],
    )
    op.drop_table("channel_playlist_entries")
