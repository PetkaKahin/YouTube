"""Initial schema: video_thumbnail_choices."""

import sqlalchemy as sa
from alembic import op

revision = "initial_025"
down_revision = "initial_024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_thumbnail_choices",
        sa.Column("video_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column(
            "slot",
            sa.BIGINT(),
            server_default=sa.text("'0'::bigint"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("custom_encoded_id", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.Column("requested_upload_id", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.Column(
            "generation",
            sa.BIGINT(),
            server_default=sa.text("'0'::bigint"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "generation >= 0", name=op.f("ck_video_thumbnail_choices_valid_generation")
        ),
        sa.CheckConstraint(
            "slot >= 0 AND slot <= 2", name=op.f("ck_video_thumbnail_choices_valid_slot")
        ),
        sa.ForeignKeyConstraint(
            ["custom_encoded_id"],
            ["video_encoded_tracks.id"],
            name=op.f("fk_video_thumbnail_choices_custom_encoded_id_video_enco_7a26"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_upload_id"],
            ["video_thumbnail_uploads.id"],
            name=op.f("fk_video_thumbnail_choices_requested_upload_id_video_th_a034"),
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["video_publications.id"],
            name=op.f("fk_video_thumbnail_choices_video_id_video_publications"),
        ),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_video_thumbnail_choices")),
    )


def downgrade() -> None:
    op.drop_table("video_thumbnail_choices")
