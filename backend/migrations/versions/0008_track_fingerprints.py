"""Shared compressed track identities and per-source references.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    for name, column in (
        ("video_media_contents", "manifest"),
        ("video_media_tracks", "properties"),
    ):
        columns = [
            sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
            sa.Column("fingerprint", sa.Text(), nullable=False, unique=True),
            sa.Column(column, postgresql.JSONB(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="valid_fingerprint"),
        ]
        if column == "properties":
            columns.extend(
                [
                    sa.Column("packet_count", sa.BigInteger(), nullable=False),
                    sa.CheckConstraint("packet_count >= 0", name="valid_packet_count"),
                ]
            )
        op.create_table(name, *columns)
    op.add_column(
        "video_source_processing", sa.Column("content_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        "fk_video_source_processing_content_id_video_media_contents",
        "video_source_processing",
        "video_media_contents",
        ["content_id"],
        ["id"],
    )
    op.create_index(
        "ix_video_source_processing_content_id", "video_source_processing", ["content_id"]
    )
    op.create_table(
        "video_source_tracks",
        sa.Column(
            "upload_id",
            sa.BigInteger(),
            sa.ForeignKey("video_source_processing.upload_id"),
            primary_key=True,
        ),
        sa.Column("stream_index", sa.BigInteger(), primary_key=True),
        sa.Column(
            "track_id", sa.BigInteger(), sa.ForeignKey("video_media_tracks.id"), nullable=False
        ),
        sa.CheckConstraint("stream_index >= 0", name="valid_stream_index"),
    )
    op.create_index("ix_video_source_tracks_track_id", "video_source_tracks", ["track_id"])


def downgrade():
    op.drop_table("video_source_tracks")
    op.drop_index("ix_video_source_processing_content_id", table_name="video_source_processing")
    op.drop_constraint(
        "fk_video_source_processing_content_id_video_media_contents",
        "video_source_processing",
        type_="foreignkey",
    )
    op.drop_column("video_source_processing", "content_id")
    op.drop_table("video_media_tracks")
    op.drop_table("video_media_contents")
