"""Initial schema: channel_playlists."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_010"
down_revision = "initial_009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_playlists",
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
        sa.Column("channel_id", sa.BIGINT(), autoincrement=False, nullable=False),
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
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "visibility = ANY (ARRAY['public'::text, 'unlisted'::text, 'private'::text])",
            name=op.f("ck_channel_playlists_valid_visibility"),
        ),
        sa.CheckConstraint(
            "char_length(description) <= 5000", name=op.f("ck_channel_playlists_description_length")
        ),
        sa.CheckConstraint(
            "char_length(title) >= 1 AND char_length(title) <= 100",
            name=op.f("ck_channel_playlists_title_length"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channel_profiles.id"],
            name=op.f("fk_channel_playlists_channel_id_channel_profiles"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_playlists")),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        op.f("ix_channel_playlists_owner_order"),
        "channel_playlists",
        ["channel_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_channel_playlists_owner_order"),
        table_name="channel_playlists",
        postgresql_include=[],
    )
    op.drop_table("channel_playlists")
