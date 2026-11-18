"""Initial schema: user_accounts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_004"
down_revision = "initial_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_accounts",
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
        sa.Column("name", sa.VARCHAR(length=50), autoincrement=False, nullable=False),
        sa.Column("email", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("password_hash", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "role",
            sa.VARCHAR(length=9),
            server_default=sa.text("'user'::character varying"),
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
        sa.CheckConstraint(
            "role IN ('user', 'moderator', 'admin')",
            name=op.f("ck_user_accounts_valid_role"),
        ),
        sa.CheckConstraint(
            "char_length(name::text) >= 2 AND char_length(name::text) <= 50",
            name=op.f("ck_user_accounts_name_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_accounts")),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        op.f("uq_user_accounts_email"),
        "user_accounts",
        [sa.literal_column("lower(email)")],
        unique=True,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(op.f("uq_user_accounts_email"), table_name="user_accounts", postgresql_include=[])
    op.drop_table("user_accounts")
