"""nodes.starred — favorite flag, research metadata only.

Additive, reversible. Defaults to 0/False for every existing row; no data is
read, rewritten or backfilled. Cannot affect strategy semantics: the column is
never consulted by spec compilation, hashing, validation or execution.

Registry DB is untouched (this migration lives in the instrument head only).

0001_initial materialises the CURRENT model metadata on fresh databases, so a
fresh install already owns this column; this migration must therefore be
idempotent: add/create only what is missing. Existing databases at 0001 lack
the column and receive it here.
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_nodes_starred"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("nodes")}
    if "starred" not in cols:
        op.add_column("nodes", sa.Column("starred", sa.Boolean(), nullable=False, server_default=sa.false()))
    idx = {i["name"] for i in sa.inspect(bind).get_indexes("nodes")}
    if "ix_nodes_instr_starred" not in idx:
        op.create_index("ix_nodes_instr_starred", "nodes", ["instrument_id", "starred"])


def downgrade():
    bind = op.get_bind()
    idx = {i["name"] for i in sa.inspect(bind).get_indexes("nodes")}
    if "ix_nodes_instr_starred" in idx:
        op.drop_index("ix_nodes_instr_starred", table_name="nodes")
    cols = {c["name"] for c in sa.inspect(bind).get_columns("nodes")}
    if "starred" in cols:
        with op.batch_alter_table("nodes") as batch:
            batch.drop_column("starred")
