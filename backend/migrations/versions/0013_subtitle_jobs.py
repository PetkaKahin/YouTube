"""Subtitle references, shared WebVTT and a successful empty result."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(
        op.f("ck_video_encoded_tracks_valid_status"), "video_encoded_tracks", type_="check"
    )
    op.create_check_constraint(
        "valid_status",
        "video_encoded_tracks",
        "status IN ('pending','running','ready','empty','failed')",
    )
    op.create_table(
        "video_subtitle_links",
        sa.Column(
            "upload_id",
            sa.BigInteger(),
            sa.ForeignKey("video_media_plans.upload_id"),
            primary_key=True,
        ),
        sa.Column("stream_index", sa.BigInteger(), primary_key=True),
        sa.Column(
            "video_encoded_id",
            sa.BigInteger(),
            sa.ForeignKey("video_encoded_tracks.id"),
            primary_key=True,
        ),
        sa.Column("encoded_id", sa.BigInteger(), sa.ForeignKey("video_encoded_tracks.id")),
        sa.Column("clock_id", sa.BigInteger(), sa.ForeignKey("video_encoded_tracks.id")),
        sa.Column("status", sa.Text(), server_default="waiting", nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("disposition", JSONB(), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.CheckConstraint("status IN ('waiting','queued','failed')", name="valid_status"),
        sa.CheckConstraint("stream_index >= 0", name="valid_stream"),
        sa.CheckConstraint("(status='queued') = (encoded_id IS NOT NULL)", name="queued_output"),
    )
    op.create_index(
        "ix_video_subtitle_links_video_encoded_id", "video_subtitle_links", ["video_encoded_id"]
    )
    op.create_index("ix_video_subtitle_links_encoded_id", "video_subtitle_links", ["encoded_id"])


def downgrade():
    op.drop_table("video_subtitle_links")
    op.drop_constraint(
        op.f("ck_video_encoded_tracks_valid_status"), "video_encoded_tracks", type_="check"
    )
    op.create_check_constraint(
        "valid_status", "video_encoded_tracks", "status IN ('pending','running','ready','failed')"
    )
