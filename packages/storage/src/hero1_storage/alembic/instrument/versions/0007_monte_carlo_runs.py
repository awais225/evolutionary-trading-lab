"""Persist immutable Monte Carlo research results."""
import sqlalchemy as sa
from alembic import op
revision = "0007_monte_carlo_runs"
down_revision = "0006_decision_events"
branch_labels = None
depends_on = None

def upgrade():
    from hero1_storage.instrument_models import MonteCarloRun
    if "monte_carlo_runs" not in sa.inspect(op.get_bind()).get_table_names():
        MonteCarloRun.__table__.create(bind=op.get_bind())

def downgrade():
    if "monte_carlo_runs" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("monte_carlo_runs")
