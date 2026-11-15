"""Durable plans, shared encoded tracks and isolated attempt prefixes.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "video_media_plans",
        sa.Column(
            "upload_id",
            sa.BigInteger(),
            sa.ForeignKey("video_source_processing.upload_id"),
            primary_key=True,
        ),
        sa.Column("job_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("config", JSONB(none_as_null=True)),
        sa.Column("error_code", sa.Text()),
        sa.CheckConstraint("status IN ('pending','planned','failed')", name="valid_status"),
    )
    op.create_table(
        "video_encoded_tracks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "track_id", sa.BigInteger(), sa.ForeignKey("video_media_tracks.id"), nullable=False
        ),
        sa.Column("profile_key", sa.Text(), nullable=False, unique=True),
        sa.Column("spec", JSONB(), nullable=False),
        sa.Column(
            "source_upload_id", sa.BigInteger(), sa.ForeignKey("video_uploads.id"), nullable=False
        ),
        sa.Column("stream_index", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("token", sa.Uuid()),
        sa.Column("queue_attempt", sa.BigInteger()),
        sa.Column("artifact", JSONB(none_as_null=True)),
        sa.Column("error_code", sa.Text()),
        sa.CheckConstraint("profile_key ~ '^[0-9a-f]{64}$'", name="valid_profile_key"),
        sa.CheckConstraint("status IN ('pending','running','ready','failed')", name="valid_status"),
        sa.CheckConstraint("(status='running') = (token IS NOT NULL)", name="active_token"),
        sa.CheckConstraint("(status='ready') = (artifact IS NOT NULL)", name="ready_artifact"),
    )
    op.create_index("ix_video_encoded_tracks_track_id", "video_encoded_tracks", ["track_id"])
    op.create_table(
        "video_plan_tracks",
        sa.Column(
            "upload_id",
            sa.BigInteger(),
            sa.ForeignKey("video_media_plans.upload_id"),
            primary_key=True,
        ),
        sa.Column(
            "encoded_id",
            sa.BigInteger(),
            sa.ForeignKey("video_encoded_tracks.id"),
            primary_key=True,
        ),
    )
    op.create_index("ix_video_plan_tracks_encoded_id", "video_plan_tracks", ["encoded_id"])
    op.create_table(
        "video_encoding_attempts",
        sa.Column("token", sa.Uuid(), primary_key=True),
        sa.Column(
            "encoded_id", sa.BigInteger(), sa.ForeignKey("video_encoded_tracks.id"), nullable=False
        ),
        sa.Column("prefix", sa.Text(), unique=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_video_encoding_attempts_encoded_id", "video_encoding_attempts", ["encoded_id"]
    )


def downgrade():
    op.drop_table("video_encoding_attempts")
    op.drop_table("video_plan_tracks")
    op.drop_table("video_encoded_tracks")
    op.drop_table("video_media_plans")
