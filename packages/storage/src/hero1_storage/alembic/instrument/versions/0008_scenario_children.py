"""Persist scenario-to-node/run relationships and MC scenario identity."""
import sqlalchemy as sa
from alembic import op

revision = "0008_scenario_children"
down_revision = "0007_monte_carlo_runs"
branch_labels = None
depends_on = None


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "deeptest_scenario_defs" in sa.inspect(op.get_bind()).get_table_names():
        existing = _columns("deeptest_scenario_defs")
        for name, typ in (("node_id", sa.Integer()), ("node_version", sa.Integer()),
                          ("source_run_id", sa.Integer()), ("filters_json", sa.Text()),
                          ("stats_json", sa.Text()), ("trades_json", sa.Text())):
            if name not in existing:
                op.add_column("deeptest_scenario_defs", sa.Column(name, typ, nullable=True))
    if "monte_carlo_runs" in sa.inspect(op.get_bind()).get_table_names():
        if "scenario_id" not in _columns("monte_carlo_runs"):
            op.add_column("monte_carlo_runs", sa.Column("scenario_id", sa.Integer(), nullable=True))


def downgrade():
    if "monte_carlo_runs" in sa.inspect(op.get_bind()).get_table_names() and "scenario_id" in _columns("monte_carlo_runs"):
        op.drop_column("monte_carlo_runs", "scenario_id")
    if "deeptest_scenario_defs" in sa.inspect(op.get_bind()).get_table_names():
        for name in ("trades_json", "stats_json", "filters_json", "source_run_id", "node_version", "node_id"):
            if name in _columns("deeptest_scenario_defs"):
                op.drop_column("deeptest_scenario_defs", name)
