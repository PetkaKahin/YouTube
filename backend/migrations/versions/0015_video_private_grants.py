"""Initial schema: video_private_grants."""

import sqlalchemy as sa
from alembic import op

revision = "initial_015"
down_revision = "initial_014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_private_grants",
        sa.Column("video_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("email", sa.TEXT(), autoincrement=False, nullable=False),
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
        sa.CheckConstraint(
            "char_length(email) > 0", name=op.f("ck_video_private_grants_nonempty_email")
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["video_publications.id"],
            name=op.f("fk_video_private_grants_video_id_video_publications"),
        ),
        sa.PrimaryKeyConstraint("video_id", "email", name=op.f("pk_video_private_grants")),
        sa.UniqueConstraint(
            "id",
            name=op.f("uq_video_private_grants_id"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_video_private_grants_order"),
        "video_private_grants",
        ["video_id", sa.literal_column("id DESC")],
        unique=False,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_video_private_grants_order"),
        table_name="video_private_grants",
        postgresql_include=[],
    )
    op.drop_table("video_private_grants")
