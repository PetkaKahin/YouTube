"""Initial schema: comment_likes."""

import sqlalchemy as sa
from alembic import op

revision = "initial_019"
down_revision = "initial_018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "comment_likes",
        sa.Column("comment_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("account_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["user_accounts.id"],
            name=op.f("fk_comment_likes_account_id_user_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["comment_id"],
            ["comment_entries.id"],
            name=op.f("fk_comment_likes_comment_id_comment_entries"),
        ),
        sa.PrimaryKeyConstraint("comment_id", "account_id", name=op.f("pk_comment_likes")),
    )


def downgrade() -> None:
    op.drop_table("comment_likes")
