"""Initial schema: channel_profiles."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_008"
down_revision = "initial_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_profiles",
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
        sa.Column("tag", sa.VARCHAR(length=30), autoincrement=False, nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "tag::text ~ '^[A-Za-z0-9_.-]{3,30}$'::text", name=op.f("ck_channel_profiles_valid_tag")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["user_accounts.id"],
            name=op.f("fk_channel_profiles_owner_id_user_accounts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_profiles")),
        sa.UniqueConstraint(
            "owner_id",
            name=op.f("uq_channel_profiles_owner_id"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        op.f("uq_channel_profiles_tag"),
        "channel_profiles",
        [sa.literal_column("lower(tag::text)")],
        unique=True,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("uq_channel_profiles_tag"), table_name="channel_profiles", postgresql_include=[]
    )
    op.drop_table("channel_profiles")
