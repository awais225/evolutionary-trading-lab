"""Indexed append-only DeepTesting decision events."""
from alembic import op
import sqlalchemy as sa
revision = "0006_decision_events"
down_revision = "0005_decision_ledger"
branch_labels = None
depends_on = None

def upgrade():
    from hero1_storage.instrument_models import DeepTestDecisionEvent
    if "deeptest_decision_events" not in sa.inspect(op.get_bind()).get_table_names():
        DeepTestDecisionEvent.__table__.create(bind=op.get_bind())

def downgrade():
    if "deeptest_decision_events" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("deeptest_decision_events")
