"""Initial schema: video_subtitle_links."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_030"
down_revision = "initial_029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_subtitle_links",
        sa.Column("upload_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("stream_index", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("video_encoded_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("encoded_id", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.Column("clock_id", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.Column(
            "status",
            sa.TEXT(),
            server_default=sa.text("'waiting'::text"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("language", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "disposition",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("error_code", sa.TEXT(), autoincrement=False, nullable=True),
        sa.CheckConstraint(
            "(status = 'queued'::text) = (encoded_id IS NOT NULL)",
            name=op.f("ck_video_subtitle_links_queued_output"),
        ),
        sa.CheckConstraint(
            "status = ANY (ARRAY['waiting'::text, 'queued'::text, 'failed'::text])",
            name=op.f("ck_video_subtitle_links_valid_status"),
        ),
        sa.CheckConstraint("stream_index >= 0", name=op.f("ck_video_subtitle_links_valid_stream")),
        sa.ForeignKeyConstraint(
            ["clock_id"],
            ["video_encoded_tracks.id"],
            name=op.f("fk_video_subtitle_links_clock_id_video_encoded_tracks"),
        ),
        sa.ForeignKeyConstraint(
            ["encoded_id"],
            ["video_encoded_tracks.id"],
            name=op.f("fk_video_subtitle_links_encoded_id_video_encoded_tracks"),
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["video_media_plans.upload_id"],
            name=op.f("fk_video_subtitle_links_upload_id_video_media_plans"),
        ),
        sa.ForeignKeyConstraint(
            ["video_encoded_id"],
            ["video_encoded_tracks.id"],
            name=op.f("fk_video_subtitle_links_video_encoded_id_video_encoded_tracks"),
        ),
        sa.PrimaryKeyConstraint(
            "upload_id", "stream_index", "video_encoded_id", name=op.f("pk_video_subtitle_links")
        ),
    )
    op.create_index(
        op.f("ix_video_subtitle_links_encoded_id"),
        "video_subtitle_links",
        ["encoded_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        op.f("ix_video_subtitle_links_video_encoded_id"),
        "video_subtitle_links",
        ["video_encoded_id"],
        unique=False,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_video_subtitle_links_video_encoded_id"),
        table_name="video_subtitle_links",
        postgresql_include=[],
    )
    op.drop_index(
        op.f("ix_video_subtitle_links_encoded_id"),
        table_name="video_subtitle_links",
        postgresql_include=[],
    )
    op.drop_table("video_subtitle_links")
