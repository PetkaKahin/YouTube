"""Initial schema: video_encoding_attempts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "initial_024"
down_revision = "initial_023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_encoding_attempts",
        sa.Column("token", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("encoded_id", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("prefix", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "expires_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["encoded_id"],
            ["video_encoded_tracks.id"],
            name=op.f("fk_video_encoding_attempts_encoded_id_video_encoded_tracks"),
        ),
        sa.PrimaryKeyConstraint("token", name=op.f("pk_video_encoding_attempts")),
        sa.UniqueConstraint(
            "prefix",
            name=op.f("uq_video_encoding_attempts_prefix"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_video_encoding_attempts_encoded_id"),
        "video_encoding_attempts",
        ["encoded_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        op.f("ix_video_encoding_attempts_expires_at"),
        "video_encoding_attempts",
        ["expires_at"],
        unique=False,
        postgresql_include=[],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_video_encoding_attempts_expires_at"),
        table_name="video_encoding_attempts",
        postgresql_include=[],
    )
    op.drop_index(
        op.f("ix_video_encoding_attempts_encoded_id"),
        table_name="video_encoding_attempts",
        postgresql_include=[],
    )
    op.drop_table("video_encoding_attempts")
