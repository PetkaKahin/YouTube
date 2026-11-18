"""Initial schema: video_plan_tracks."""

import sqlalchemy as sa
from alembic import op

revision = "initial_029"
down_revision = "initial_028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_plan_tracks",
        sa.Column("upload_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("encoded_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["encoded_id"],
            ["video_encoded_tracks.id"],
            name=op.f("fk_video_plan_tracks_encoded_id_video_encoded_tracks"),
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["video_media_plans.upload_id"],
            name=op.f("fk_video_plan_tracks_upload_id_video_media_plans"),
        ),
        sa.PrimaryKeyConstraint("upload_id", "encoded_id", name=op.f("pk_video_plan_tracks")),
    )
    op.create_index(
        op.f("ix_video_plan_tracks_encoded_id"),
        "video_plan_tracks",
        ["encoded_id"],
        unique=False,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_video_plan_tracks_encoded_id"),
        table_name="video_plan_tracks",
        postgresql_include=[],
    )
    op.drop_table("video_plan_tracks")
