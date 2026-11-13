"""Accounts and empty channels.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_accounts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(9), nullable=False, server_default="user"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role IN ('user', 'moderator', 'admin')", name="valid_role"),
        sa.CheckConstraint("char_length(name) BETWEEN 2 AND 50", name="name_length"),
    )
    op.create_index(
        "uq_user_accounts_email", "user_accounts", [sa.text("lower(email)")], unique=True
    )
    op.create_table(
        "channel_profiles",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.BigInteger(),
            sa.ForeignKey("user_accounts.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("tag", sa.String(30), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("tag ~ '^[A-Za-z0-9_.-]{3,30}$'", name="valid_tag"),
    )
    op.create_index(
        "uq_channel_profiles_tag", "channel_profiles", [sa.text("lower(tag)")], unique=True
    )


def downgrade():
    op.drop_table("channel_profiles")
    op.drop_table("user_accounts")
