"""Initial schema: video_source_tracks."""

import sqlalchemy as sa
from alembic import op

revision = "initial_028"
down_revision = "initial_027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_source_tracks",
        sa.Column("upload_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("stream_index", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("track_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.CheckConstraint(
            "stream_index >= 0", name=op.f("ck_video_source_tracks_valid_stream_index")
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["video_media_tracks.id"],
            name=op.f("fk_video_source_tracks_track_id_video_media_tracks"),
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["video_source_processing.upload_id"],
            name=op.f("fk_video_source_tracks_upload_id_video_source_processing"),
        ),
        sa.PrimaryKeyConstraint("upload_id", "stream_index", name=op.f("pk_video_source_tracks")),
    )
    op.create_index(
        op.f("ix_video_source_tracks_track_id"),
        "video_source_tracks",
        ["track_id"],
        unique=False,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_video_source_tracks_track_id"),
        table_name="video_source_tracks",
        postgresql_include=[],
    )
    op.drop_table("video_source_tracks")
