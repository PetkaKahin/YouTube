"""Durable original deletion intent, separate from accepted upload state."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    for name in (
        "original_cleanup_started_at",
        "original_cleanup_attempted_at",
        "original_deleted_at",
    ):
        op.add_column("video_uploads", sa.Column(name, sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "original_cleanup_state",
        "video_uploads",
        "(original_cleanup_started_at IS NULL OR status='uploaded') AND "
        "(original_deleted_at IS NULL OR original_cleanup_started_at IS NOT NULL)",
    )
    op.create_index(
        "ix_video_encoded_tracks_source_upload_id", "video_encoded_tracks", ["source_upload_id"]
    )
    op.create_index(
        "ix_video_uploads_original_cleanup",
        "video_uploads",
        ["original_cleanup_attempted_at", "id"],
        postgresql_where=sa.text("status='uploaded' AND original_deleted_at IS NULL"),
    )


def downgrade():
    op.drop_index("ix_video_uploads_original_cleanup", "video_uploads")
    op.drop_index("ix_video_encoded_tracks_source_upload_id", "video_encoded_tracks")
    op.drop_constraint(
        op.f("ck_video_uploads_original_cleanup_state"), "video_uploads", type_="check"
    )
    for name in (
        "original_deleted_at",
        "original_cleanup_attempted_at",
        "original_cleanup_started_at",
    ):
        op.drop_column("video_uploads", name)
