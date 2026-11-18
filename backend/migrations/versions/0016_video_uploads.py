"""Initial schema: video_uploads."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_016"
down_revision = "initial_015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_uploads",
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
        sa.Column("video_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("object_key", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("multipart_id", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column("size", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column(
            "status",
            sa.TEXT(),
            server_default=sa.text("'creating'::text"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "activity_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "original_cleanup_started_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "original_cleanup_attempted_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "original_deleted_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.CheckConstraint(
            (
                "(original_cleanup_started_at IS NULL OR status = 'uploaded'::tex"
                "t) AND (original_deleted_at IS NULL OR original_cleanup_started_"
                "at IS NOT NULL)"
            ),
            name=op.f("ck_video_uploads_original_cleanup_state"),
        ),
        sa.CheckConstraint(
            "size >= 1 AND size <= '5000000000'::bigint", name=op.f("ck_video_uploads_valid_size")
        ),
        sa.CheckConstraint(
            (
                "status = ANY (ARRAY['creating'::text, 'uploading'::text, 'upload"
                "ed'::text, 'expired'::text])"
            ),
            name=op.f("ck_video_uploads_valid_status"),
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["video_publications.id"],
            name=op.f("fk_video_uploads_video_id_video_publications"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_uploads")),
        sa.UniqueConstraint(
            "object_key",
            name=op.f("uq_video_uploads_object_key"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        op.f("ix_video_uploads_activity_at"),
        "video_uploads",
        ["activity_at"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        op.f("ix_video_uploads_original_cleanup"),
        "video_uploads",
        ["original_cleanup_attempted_at", "id"],
        unique=False,
        postgresql_where="((status = 'uploaded'::text) AND (original_deleted_at IS NULL))",
        postgresql_include=[],
    )
    op.create_index(
        op.f("ix_video_uploads_video_id"),
        "video_uploads",
        ["video_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        op.f("uq_video_uploads_active"),
        "video_uploads",
        ["video_id"],
        unique=True,
        postgresql_where="(status <> 'expired'::text)",
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("uq_video_uploads_active"),
        table_name="video_uploads",
        postgresql_where="(status <> 'expired'::text)",
        postgresql_include=[],
    )
    op.drop_index(
        op.f("ix_video_uploads_video_id"), table_name="video_uploads", postgresql_include=[]
    )
    op.drop_index(
        op.f("ix_video_uploads_original_cleanup"),
        table_name="video_uploads",
        postgresql_where="((status = 'uploaded'::text) AND (original_deleted_at IS NULL))",
        postgresql_include=[],
    )
    op.drop_index(
        op.f("ix_video_uploads_activity_at"), table_name="video_uploads", postgresql_include=[]
    )
    op.drop_table("video_uploads")
