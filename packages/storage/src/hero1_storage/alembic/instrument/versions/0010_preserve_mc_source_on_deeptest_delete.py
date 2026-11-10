"""Keep Monte Carlo evidence when its DeepTesting source data is deleted."""
from alembic import op
import sqlalchemy as sa

revision = "0010_preserve_mc_source"
down_revision = "0009_mc_study_fingerprint"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "monte_carlo_runs" not in sa.inspect(bind).get_table_names():
        return
    cols = {c["name"] for c in sa.inspect(bind).get_columns("monte_carlo_runs")}
    if "study_fingerprint" not in cols:
        op.add_column("monte_carlo_runs", sa.Column("study_fingerprint", sa.String(64), nullable=True))
    # SQLite does not support changing this FK's ON DELETE action in place.
    # Rebuild transactionally; the old table has no inbound FKs.
    op.execute("""CREATE TABLE monte_carlo_runs_new (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        instrument_id VARCHAR(16) NOT NULL,
        node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        deeptest_run_id INTEGER NULL REFERENCES deeptest_runs(id) ON DELETE SET NULL,
        scenario_id INTEGER NULL,
        node_version INTEGER NULL,
        fingerprint VARCHAR(64) NULL,
        study_fingerprint VARCHAR(64) NULL,
        method VARCHAR(32) NOT NULL,
        seed INTEGER NOT NULL,
        status VARCHAR(16) NOT NULL,
        config_json TEXT NOT NULL,
        result_json TEXT NULL,
        warning_json TEXT NULL,
        error TEXT NULL,
        created_at FLOAT NOT NULL,
        finished_at FLOAT NULL
    )""")
    target = "id,instrument_id,node_id,deeptest_run_id,scenario_id,node_version,fingerprint,study_fingerprint,method,seed,status,config_json,result_json,warning_json,error,created_at,finished_at"
    source_cols = set(cols) | ({"study_fingerprint"} if "study_fingerprint" in cols else set())
    select = target if "study_fingerprint" in source_cols else target.replace("study_fingerprint,", "NULL,")
    op.execute(f"INSERT INTO monte_carlo_runs_new ({target}) SELECT {select} FROM monte_carlo_runs")
    op.drop_table("monte_carlo_runs")
    op.rename_table("monte_carlo_runs_new", "monte_carlo_runs")
    op.create_index("ix_mcr_node_created", "monte_carlo_runs", ["instrument_id", "node_id", "created_at"])
    op.create_index("ix_mcr_source_run", "monte_carlo_runs", ["instrument_id", "deeptest_run_id"])
    op.create_index("ix_monte_carlo_runs_instrument_id", "monte_carlo_runs", ["instrument_id"])
    op.create_index("ix_monte_carlo_runs_study_fingerprint", "monte_carlo_runs", ["study_fingerprint"])


def downgrade():
    # Downgrade would reintroduce ON DELETE CASCADE and risk losing studies;
    # deliberately refuse rather than silently weaken persistence guarantees.
    raise RuntimeError("downgrade would restore cascading Monte Carlo source deletion; restore from backup instead")
