"""Initial schema: queue."""

import sqlalchemy as sa
from alembic import op
from procrastinate.schema import SchemaManager

revision = "initial_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT to_regclass('procrastinate_jobs')")) is None:
        op.get_bind().exec_driver_sql(SchemaManager.get_schema().replace("%", "%%"))


def downgrade() -> None:
    # Preserve library-owned operational history, matching the previous migrations.
    pass
