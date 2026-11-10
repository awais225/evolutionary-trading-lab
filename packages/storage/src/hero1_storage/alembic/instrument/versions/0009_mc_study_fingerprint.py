"""Add canonical identity for exact Monte Carlo study configurations."""
import sqlalchemy as sa
from alembic import op

revision = "0009_mc_study_fingerprint"
down_revision = "0008_scenario_children"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "monte_carlo_runs" in tables:
        cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("monte_carlo_runs")}
        if "study_fingerprint" not in cols:
            op.add_column("monte_carlo_runs", sa.Column("study_fingerprint", sa.String(64), nullable=True))
        indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("monte_carlo_runs")}
        if "ix_monte_carlo_runs_study_fingerprint" not in indexes:
            op.create_index("ix_monte_carlo_runs_study_fingerprint", "monte_carlo_runs", ["study_fingerprint"])


def downgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "monte_carlo_runs" in tables:
        indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("monte_carlo_runs")}
        if "ix_monte_carlo_runs_study_fingerprint" in indexes:
            op.drop_index("ix_monte_carlo_runs_study_fingerprint", table_name="monte_carlo_runs")
        cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("monte_carlo_runs")}
        if "study_fingerprint" in cols:
            op.drop_column("monte_carlo_runs", "study_fingerprint")
