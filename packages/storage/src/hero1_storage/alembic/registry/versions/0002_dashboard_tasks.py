"""Dashboard task progress table (global bars / Live Feed / DeepTesting)."""
from alembic import op
import sqlalchemy as sa

revision = "0002_dashboard_tasks"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dashboard_tasks",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("instrument_id", sa.String(16), nullable=True),
        sa.Column("label", sa.String(160), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("total_units", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completed_units", sa.Integer, nullable=False, server_default="0"),
        sa.Column("current_unit", sa.String(160), nullable=True),
        sa.Column("current_unit_progress", sa.Float, nullable=True),
        sa.Column("summary", sa.String(500), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("acknowledged", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("payload_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("started_at", sa.Float, nullable=True),
        sa.Column("updated_at", sa.Float, nullable=False),
        sa.Column("finished_at", sa.Float, nullable=True),
    )
    op.create_index("ix_dt_category_status", "dashboard_tasks", ["category", "status"])
    op.create_index("ix_dashboard_tasks_category", "dashboard_tasks", ["category"])
    op.create_index("ix_dashboard_tasks_instrument_id", "dashboard_tasks", ["instrument_id"])


def downgrade():
    op.drop_table("dashboard_tasks")
