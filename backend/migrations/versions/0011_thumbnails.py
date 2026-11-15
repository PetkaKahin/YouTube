"""Owner thumbnail choices, durable image preparation, reusable encoded outputs."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "video_thumbnail_uploads",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "video_id", sa.BigInteger(), sa.ForeignKey("video_publications.id"), nullable=False
        ),
        sa.Column("object_key", sa.Text(), unique=True, nullable=False),
        sa.Column("multipart_id", sa.Text()),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), server_default="creating", nullable=False),
        sa.Column(
            "activity_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("job_id", sa.BigInteger(), unique=True),
        sa.Column("token", sa.Uuid()),
        sa.Column("queue_attempt", sa.BigInteger()),
        sa.Column("error_code", sa.Text()),
        sa.Column("normalized_key", sa.Text()),
        sa.Column("normalized_size", sa.BigInteger()),
        sa.Column("encoded_id", sa.BigInteger(), sa.ForeignKey("video_encoded_tracks.id")),
        sa.Column("source_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint("size BETWEEN 1 AND 10485760", name="valid_size"),
        sa.CheckConstraint(
            "status IN ('creating','uploading','uploaded','preparing',"
            "'prepared','failed','expired')",
            name="valid_status",
        ),
        sa.CheckConstraint("(status='preparing') = (token IS NOT NULL)", name="active_token"),
    )
    op.create_index("ix_video_thumbnail_uploads_video_id", "video_thumbnail_uploads", ["video_id"])
    op.create_index(
        "uq_video_thumbnail_uploads_active",
        "video_thumbnail_uploads",
        ["video_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('creating','uploading')"),
    )
    op.add_column("video_encoded_tracks", sa.Column("source_thumbnail_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_encoded_thumbnail_source",
        "video_encoded_tracks",
        "video_thumbnail_uploads",
        ["source_thumbnail_id"],
        ["id"],
    )
    op.alter_column("video_encoded_tracks", "source_upload_id", nullable=True)
    op.create_check_constraint(
        "one_source",
        "video_encoded_tracks",
        "(source_upload_id IS NULL) <> (source_thumbnail_id IS NULL)",
    )
    op.create_table(
        "video_thumbnail_choices",
        sa.Column(
            "video_id", sa.BigInteger(), sa.ForeignKey("video_publications.id"), primary_key=True
        ),
        sa.Column("slot", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("custom_encoded_id", sa.BigInteger(), sa.ForeignKey("video_encoded_tracks.id")),
        sa.Column(
            "requested_upload_id", sa.BigInteger(), sa.ForeignKey("video_thumbnail_uploads.id")
        ),
        sa.CheckConstraint("slot BETWEEN 0 AND 2", name="valid_slot"),
    )
    op.create_table(
        "video_thumbnail_preparations",
        sa.Column("token", sa.Uuid(), primary_key=True),
        sa.Column(
            "upload_id",
            sa.BigInteger(),
            sa.ForeignKey("video_thumbnail_uploads.id"),
            nullable=False,
        ),
        sa.Column("prefix", sa.Text(), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_video_thumbnail_preparations_upload_id", "video_thumbnail_preparations", ["upload_id"]
    )
    op.create_index(
        "ix_video_thumbnail_preparations_expires_at", "video_thumbnail_preparations", ["expires_at"]
    )


def downgrade():
    op.drop_table("video_thumbnail_preparations")
    op.drop_table("video_thumbnail_choices")
    op.drop_constraint("ck_video_encoded_tracks_one_source", "video_encoded_tracks", type_="check")
    # A populated custom-image pipeline cannot be downgraded by silently losing media.
    op.alter_column("video_encoded_tracks", "source_upload_id", nullable=False)
    op.drop_constraint(
        "fk_encoded_thumbnail_source",
        "video_encoded_tracks",
        type_="foreignkey",
    )
    op.drop_column("video_encoded_tracks", "source_thumbnail_id")
    op.drop_table("video_thumbnail_uploads")
