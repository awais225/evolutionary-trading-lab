"""Atomic active task deduplication key."""
from alembic import op
import sqlalchemy as sa

revision = "0003_task_active_dedupe"
down_revision = "0002_dashboard_tasks"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("dashboard_tasks")}
    if "dedupe_key" not in columns:
        op.add_column("dashboard_tasks", sa.Column("dedupe_key", sa.String(64), nullable=True))
    op.create_index("uq_dt_active_dedupe", "dashboard_tasks", ["dedupe_key"], unique=True,
                    sqlite_where=sa.text("dedupe_key IS NOT NULL AND status IN ('QUEUED','RUNNING')"))


def downgrade():
    op.drop_index("uq_dt_active_dedupe", table_name="dashboard_tasks")
    op.drop_column("dashboard_tasks", "dedupe_key")
