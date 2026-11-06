"""nodes.timeframe (node identity) + unique (instrument_id, name).

Timeframe is a property of the NODE, assigned at creation time from the spec's
identity.timeframe. NULL for drafts (no spec) and for legacy rows whose stored
spec cannot be parsed — NEVER backfilled with a guessed value.

The unique (instrument_id, name) index enforces name uniqueness at the database
boundary so concurrent node creation can never produce duplicate names. The
upgrade REFUSES to run if duplicate (instrument_id, name) pairs already exist:
renaming user data silently is not allowed — resolve the duplicates first
(the error lists them).

Additive + idempotent, mirroring 0002: columns/indexes/constraints are created
only if missing. Fresh databases (whose 0001 materialises current metadata)
already carry everything; existing 0002 databases receive it here.
"""
import json
import re

import sqlalchemy as sa
from alembic import op

revision = "0003_timeframe_unique_name"
down_revision = "0002_nodes_starred"
branch_labels = None
depends_on = None

# Mirror of hero1_strategy_spec.SUPPORTED_TIMEFRAMES — self-contained on purpose
# (an alembic revision must not depend on application code).
SUPPORTED = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
_TF_RE = re.compile(r"^(?:[A-Za-z0-9]{1,4})$")


def _backfill_timeframes(bind) -> None:
    rows = bind.execute(sa.text(
        "SELECT id, spec_json FROM nodes WHERE spec_json IS NOT NULL")).fetchall()
    updated = 0
    for row_id, spec_json in rows:
        tf = None
        try:
            spec = json.loads(spec_json)
            ident = spec.get("identity") if isinstance(spec, dict) else None
            cand = str(ident.get("timeframe")).upper() if isinstance(ident, dict) and ident.get("timeframe") else None
            if cand and cand in SUPPORTED and _TF_RE.match(cand):
                tf = cand
        except Exception:
            tf = None
        if tf:
            bind.execute(sa.text("UPDATE nodes SET timeframe = :tf WHERE id = :i"),
                         {"tf": tf, "i": row_id})
            updated += 1
    print(f"[0003] timeframe backfilled from spec_json for {updated} node(s)")


def upgrade():
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("nodes")}

    # Refuse destructive upgrade if names already collide (list them).
    dups = bind.execute(sa.text(
        "SELECT instrument_id, name, COUNT(*) AS c FROM nodes "
        "GROUP BY instrument_id, name HAVING c > 1 ORDER BY c DESC")).fetchall()
    if dups:
        details = ", ".join(f"{r.instrument_id}/{r.name!r}×{r.c}" for r in dups)
        raise RuntimeError(
            "uq_nodes_instr_name cannot be created — duplicate node names exist: "
            + details
            + ". Rename the duplicates (display names only) before upgrading. "
              "No data has been modified.")

    if "timeframe" not in cols:
        op.add_column("nodes", sa.Column("timeframe", sa.String(4), nullable=True))
        _backfill_timeframes(bind)

    idx = {i["name"] for i in sa.inspect(bind).get_indexes("nodes")}
    if "ix_nodes_instr_timeframe" not in idx:
        op.create_index("ix_nodes_instr_timeframe", "nodes", ["instrument_id", "timeframe"])

    # Unique constraint (SQLite: unique index) on (instrument_id, name)
    uqs = {uq["name"] for uq in sa.inspect(bind).get_unique_constraints("nodes")}
    uq_idx = {i["name"] for i in sa.inspect(bind).get_indexes("nodes") if i.get("unique")}
    if "uq_nodes_instr_name" not in uqs and "uq_nodes_instr_name" not in uq_idx:
        op.create_index("uq_nodes_instr_name", "nodes",
                        ["instrument_id", "name"], unique=True)


def downgrade():
    bind = op.get_bind()
    idx = {i["name"] for i in sa.inspect(bind).get_indexes("nodes")}
    for name in ("uq_nodes_instr_name", "ix_nodes_instr_timeframe"):
        if name in idx:
            op.drop_index(name, table_name="nodes")
    cols = {c["name"] for c in sa.inspect(bind).get_columns("nodes")}
    if "timeframe" in cols:
        with op.batch_alter_table("nodes") as batch:
            batch.drop_column("timeframe")
