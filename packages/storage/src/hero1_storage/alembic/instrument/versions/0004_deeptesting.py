"""Phase 3 DeepTesting persistence: deeptest_runs, deeptest_run_trades,
deeptest_scenario_defs. Additive only; the legacy backtest_runs /
deeptest_scenarios / deeptest_trades shells are left untouched.

0001_initial materialises CURRENT model metadata on fresh databases, so
this migration is idempotent: create only what is missing.
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_deeptesting"
down_revision = "0003_timeframe_unique_name"
branch_labels = None
depends_on = None

_TABLES = ("deeptest_runs", "deeptest_run_trades", "deeptest_scenario_defs")


def _create_missing(bind):
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())
    from hero1_storage.instrument_models import DeepTestRun, DeepTestRunTrade, DeepTestScenarioDef
    for model in (DeepTestRun, DeepTestRunTrade, DeepTestScenarioDef):
        if model.__tablename__ not in existing:
            model.__table__.create(bind=bind)


def upgrade():
    _create_missing(op.get_bind())


def downgrade():
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for name in reversed(_TABLES):
        if name in existing:
            op.drop_table(name)
