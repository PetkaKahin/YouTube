"""Initial schema: video_publications."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_009"
down_revision = "initial_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_publications",
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
        sa.Column("owner_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("title", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "description",
            sa.TEXT(),
            server_default=sa.text("''::text"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("visibility", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "publication",
            sa.TEXT(),
            server_default=sa.text("'draft'::text"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "upload",
            sa.TEXT(),
            server_default=sa.text("'pending'::text"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "processing",
            sa.TEXT(),
            server_default=sa.text("'pending'::text"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "moderation",
            sa.TEXT(),
            server_default=sa.text("'visible'::text"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "added_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "moderation = ANY (ARRAY['visible'::text, 'hidden'::text])",
            name=op.f("ck_video_publications_valid_moderation"),
        ),
        sa.CheckConstraint(
            (
                "processing = ANY (ARRAY['pending'::text, 'processing'::text, 're"
                "ady'::text, 'partial'::text, 'failed'::text])"
            ),
            name=op.f("ck_video_publications_valid_processing"),
        ),
        sa.CheckConstraint(
            "publication = ANY (ARRAY['draft'::text, 'published'::text])",
            name=op.f("ck_video_publications_valid_publication"),
        ),
        sa.CheckConstraint(
            (
                "upload = ANY (ARRAY['pending'::text, 'uploading'::text, 'uploade"
                "d'::text, 'failed'::text])"
            ),
            name=op.f("ck_video_publications_valid_upload"),
        ),
        sa.CheckConstraint(
            "visibility = ANY (ARRAY['public'::text, 'private'::text])",
            name=op.f("ck_video_publications_valid_visibility"),
        ),
        sa.CheckConstraint(
            "char_length(description) <= 5000",
            name=op.f("ck_video_publications_description_length"),
        ),
        sa.CheckConstraint(
            "char_length(title) >= 1 AND char_length(title) <= 100",
            name=op.f("ck_video_publications_title_length"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["user_accounts.id"],
            name=op.f("fk_video_publications_owner_id_user_accounts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_publications")),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        op.f("ix_video_publications_channel_feed"),
        "video_publications",
        ["owner_id", sa.literal_column("added_at DESC"), sa.literal_column("id DESC")],
        unique=False,
        postgresql_where=(
            "((visibility = 'public'::text) AND (publication = 'published'::t"
            "ext) AND (moderation = 'visible'::text) AND (added_at IS NOT NUL"
            "L))"
        ),
        postgresql_include=[],
    )
    op.create_index(
        op.f("ix_video_publications_owner_id"),
        "video_publications",
        ["owner_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        op.f("ix_video_publications_owner_list"),
        "video_publications",
        [
            "owner_id",
            "publication",
            sa.literal_column("created_at DESC"),
            sa.literal_column("id DESC"),
        ],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        op.f("ix_video_publications_public_feed"),
        "video_publications",
        [sa.literal_column("added_at DESC"), sa.literal_column("id DESC")],
        unique=False,
        postgresql_where=(
            "((visibility = 'public'::text) AND (publication = 'published'::t"
            "ext) AND (moderation = 'visible'::text) AND (added_at IS NOT NUL"
            "L))"
        ),
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_video_publications_public_feed"),
        table_name="video_publications",
        postgresql_where=(
            "((visibility = 'public'::text) AND (publication = 'published'::t"
            "ext) AND (moderation = 'visible'::text) AND (added_at IS NOT NUL"
            "L))"
        ),
        postgresql_include=[],
    )
    op.drop_index(
        op.f("ix_video_publications_owner_list"),
        table_name="video_publications",
        postgresql_include=[],
    )
    op.drop_index(
        op.f("ix_video_publications_owner_id"),
        table_name="video_publications",
        postgresql_include=[],
    )
    op.drop_index(
        op.f("ix_video_publications_channel_feed"),
        table_name="video_publications",
        postgresql_where=(
            "((visibility = 'public'::text) AND (publication = 'published'::t"
            "ext) AND (moderation = 'visible'::text) AND (added_at IS NOT NUL"
            "L))"
        ),
        postgresql_include=[],
    )
    op.drop_table("video_publications")
