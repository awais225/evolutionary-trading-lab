"""Add durable DeepTesting decision ledger storage."""
from alembic import op
import sqlalchemy as sa
revision = "0005_decision_ledger"
down_revision = "0004_deeptesting"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    if "decision_ledger_json" not in {c["name"] for c in sa.inspect(bind).get_columns("deeptest_runs")}:
        op.add_column("deeptest_runs", sa.Column("decision_ledger_json", sa.Text(), nullable=True))

def downgrade():
    bind = op.get_bind()
    if "decision_ledger_json" in {c["name"] for c in sa.inspect(bind).get_columns("deeptest_runs")}:
        op.drop_column("deeptest_runs", "decision_ledger_json")
