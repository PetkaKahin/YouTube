"""Optional allowlisted diagnosis, compatible with old journal/spool events.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("background_job_errors", sa.Column("error_code", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("background_job_errors", "error_code")
