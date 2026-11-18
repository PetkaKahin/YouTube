"""Initial schema: video_media_contents."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_006"
down_revision = "initial_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_media_contents",
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
            "manifest", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'::text",
            name=op.f("ck_video_media_contents_valid_fingerprint"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_media_contents")),
        sa.UniqueConstraint(
            "fingerprint",
            name=op.f("uq_video_media_contents_fingerprint"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("video_media_contents")
