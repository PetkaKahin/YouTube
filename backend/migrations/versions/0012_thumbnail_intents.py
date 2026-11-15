"""Fence old upload confirmations against newer owner choices."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("uq_video_thumbnail_uploads_active", table_name="video_thumbnail_uploads")
    op.add_column(
        "video_thumbnail_uploads",
        sa.Column("generation", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "video_thumbnail_uploads",
        sa.Column(
            "selection_confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column(
        "video_thumbnail_choices",
        sa.Column("generation", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.create_check_constraint("valid_generation", "video_thumbnail_uploads", "generation >= 0")
    op.create_check_constraint("valid_generation", "video_thumbnail_choices", "generation >= 0")
    op.execute("UPDATE video_thumbnail_choices SET generation=1")


def downgrade():
    op.drop_constraint(
        "ck_video_thumbnail_choices_valid_generation", "video_thumbnail_choices", type_="check"
    )
    op.drop_constraint(
        "ck_video_thumbnail_uploads_valid_generation", "video_thumbnail_uploads", type_="check"
    )
    op.drop_column("video_thumbnail_choices", "generation")
    op.drop_column("video_thumbnail_uploads", "selection_confirmed")
    op.drop_column("video_thumbnail_uploads", "generation")
    op.create_index(
        "uq_video_thumbnail_uploads_active",
        "video_thumbnail_uploads",
        ["video_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('creating','uploading')"),
    )
