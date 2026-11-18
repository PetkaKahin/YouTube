"""Initial schema: video_thumbnail_preparations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_026"
down_revision = "initial_025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_thumbnail_preparations",
        sa.Column("token", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("upload_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("prefix", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "expires_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["video_thumbnail_uploads.id"],
            name=op.f("fk_video_thumbnail_preparations_upload_id_video_thumbna_ea44"),
        ),
        sa.PrimaryKeyConstraint("token", name=op.f("pk_video_thumbnail_preparations")),
        sa.UniqueConstraint(
            "prefix",
            name=op.f("uq_video_thumbnail_preparations_prefix"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_video_thumbnail_preparations_expires_at"),
        "video_thumbnail_preparations",
        ["expires_at"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        op.f("ix_video_thumbnail_preparations_upload_id"),
        "video_thumbnail_preparations",
        ["upload_id"],
        unique=False,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_video_thumbnail_preparations_upload_id"),
        table_name="video_thumbnail_preparations",
        postgresql_include=[],
    )
    op.drop_index(
        op.f("ix_video_thumbnail_preparations_expires_at"),
        table_name="video_thumbnail_preparations",
        postgresql_include=[],
    )
    op.drop_table("video_thumbnail_preparations")
