"""Initial schema: video_encoding_and_thumbnails."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_022"
down_revision = "initial_021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_encoded_tracks",
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
        sa.Column("track_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("profile_key", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "spec", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column("source_upload_id", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.Column("stream_index", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("job_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column(
            "status",
            sa.TEXT(),
            server_default=sa.text("'pending'::text"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("token", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column("queue_attempt", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.Column(
            "artifact", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True
        ),
        sa.Column("error_code", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column("source_thumbnail_id", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.CheckConstraint(
            "(status = 'ready'::text) = (artifact IS NOT NULL)",
            name=op.f("ck_video_encoded_tracks_ready_artifact"),
        ),
        sa.CheckConstraint(
            "(status = 'running'::text) = (token IS NOT NULL)",
            name=op.f("ck_video_encoded_tracks_active_token"),
        ),
        sa.CheckConstraint(
            "profile_key ~ '^[0-9a-f]{64}$'::text",
            name=op.f("ck_video_encoded_tracks_valid_profile_key"),
        ),
        sa.CheckConstraint(
            (
                "status = ANY (ARRAY['pending'::text, 'running'::text, 'ready'::t"
                "ext, 'empty'::text, 'failed'::text])"
            ),
            name=op.f("ck_video_encoded_tracks_valid_status"),
        ),
        sa.CheckConstraint(
            "(source_upload_id IS NULL) <> (source_thumbnail_id IS NULL)",
            name=op.f("ck_video_encoded_tracks_one_source"),
        ),
        sa.ForeignKeyConstraint(
            ["source_upload_id"],
            ["video_uploads.id"],
            name=op.f("fk_video_encoded_tracks_source_upload_id_video_uploads"),
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["video_media_tracks.id"],
            name=op.f("fk_video_encoded_tracks_track_id_video_media_tracks"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_encoded_tracks")),
        sa.UniqueConstraint(
            "job_id",
            name=op.f("uq_video_encoded_tracks_job_id"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
        sa.UniqueConstraint(
            "profile_key",
            name=op.f("uq_video_encoded_tracks_profile_key"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_video_encoded_tracks_source_upload_id"),
        "video_encoded_tracks",
        ["source_upload_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        op.f("ix_video_encoded_tracks_track_id"),
        "video_encoded_tracks",
        ["track_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_table(
        "video_thumbnail_uploads",
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
        sa.Column("job_id", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.Column("token", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column("queue_attempt", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.Column("error_code", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column("normalized_key", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column("normalized_size", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.Column("encoded_id", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.Column(
            "source_deleted",
            sa.BOOLEAN(),
            server_default=sa.text("false"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "generation",
            sa.BIGINT(),
            server_default=sa.text("'0'::bigint"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "selection_confirmed",
            sa.BOOLEAN(),
            server_default=sa.text("false"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'preparing'::text) = (token IS NOT NULL)",
            name=op.f("ck_video_thumbnail_uploads_active_token"),
        ),
        sa.CheckConstraint(
            (
                "status = ANY (ARRAY['creating'::text, 'uploading'::text, 'upload"
                "ed'::text, 'preparing'::text, 'prepared'::text, 'failed'::text, "
                "'expired'::text])"
            ),
            name=op.f("ck_video_thumbnail_uploads_valid_status"),
        ),
        sa.CheckConstraint(
            "generation >= 0", name=op.f("ck_video_thumbnail_uploads_valid_generation")
        ),
        sa.CheckConstraint(
            "size >= 1 AND size <= 10485760", name=op.f("ck_video_thumbnail_uploads_valid_size")
        ),
        sa.ForeignKeyConstraint(
            ["encoded_id"],
            ["video_encoded_tracks.id"],
            name=op.f("fk_video_thumbnail_uploads_encoded_id_video_encoded_tracks"),
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["video_publications.id"],
            name=op.f("fk_video_thumbnail_uploads_video_id_video_publications"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_thumbnail_uploads")),
        sa.UniqueConstraint(
            "job_id",
            name=op.f("uq_video_thumbnail_uploads_job_id"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
        sa.UniqueConstraint(
            "object_key",
            name=op.f("uq_video_thumbnail_uploads_object_key"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        op.f("ix_video_thumbnail_uploads_video_id"),
        "video_thumbnail_uploads",
        ["video_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_foreign_key(
        op.f("fk_encoded_thumbnail_source"),
        "video_encoded_tracks",
        "video_thumbnail_uploads",
        ["source_thumbnail_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_encoded_thumbnail_source"), "video_encoded_tracks", type_="foreignkey"
    )
    op.drop_index(
        op.f("ix_video_thumbnail_uploads_video_id"),
        table_name="video_thumbnail_uploads",
        postgresql_include=[],
    )
    op.drop_table("video_thumbnail_uploads")
    op.drop_index(
        op.f("ix_video_encoded_tracks_track_id"),
        table_name="video_encoded_tracks",
        postgresql_include=[],
    )
    op.drop_index(
        op.f("ix_video_encoded_tracks_source_upload_id"),
        table_name="video_encoded_tracks",
        postgresql_include=[],
    )
    op.drop_table("video_encoded_tracks")
