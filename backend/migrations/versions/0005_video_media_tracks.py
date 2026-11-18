"""Initial schema: video_media_tracks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_005"
down_revision = "initial_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_media_tracks",
        sa.Column(
            "id",
            sa.BIGINT(),
            sa.Identity(
                always=False,
                start=1,
                increment=1,
                minvalue=1,
                maxvalue=9223372036854775807,
                cycle=False,
                cache=1,
            ),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("fingerprint", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("packet_count", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'::text",
            name=op.f("ck_video_media_tracks_valid_fingerprint"),
        ),
        sa.CheckConstraint(
            "packet_count >= 0", name=op.f("ck_video_media_tracks_valid_packet_count")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_media_tracks")),
        sa.UniqueConstraint(
            "fingerprint",
            name=op.f("uq_video_media_tracks_fingerprint"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
        postgresql_ignore_search_path=False,
    )


def downgrade() -> None:
    op.drop_table("video_media_tracks")
