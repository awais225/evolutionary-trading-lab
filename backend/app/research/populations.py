"""V5.1a-next §D-§F — one authoritative population counter for the dashboard.

The operator asked for a single place that answers *"how many nodes are there,
really?"* and for the Deep Backtest page to select from a **union**, not from a
narrower accidental subset.

This module is that place.  It reads the strategy rows once and derives every
number with :func:`app.status.node_bucket` (the same authority the node filters
and the live engine use), so:

    total      every stored strategy row, legacy infrastructure included
    alive      not a dead end (status.is_alive) — never inflated by demoting
               infrastructure failures into "failed" or into "alive"
    qualified  cleared the research gates (bucket "qualified")
    final      qualified *and* at the furthest research stage (final candidate /
               live-completed / MT5-demo)
    deep       the deep-backtest universe: the UNION of everything the operator
               can legitimately deep-test (qualified + live-eligible + already
               deep-tested) — see :func:`deep_universe`
    live       nodes actually wired into the live layer (LIVE_TESTING status,
               an active live-test config, or an MT5-demo config)

Nothing here writes to the database and nothing here invents rows: a value that
cannot be computed is reported as 0 with a reason in ``notes``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Set

from ..status import (ALIVE_STATUSES, STATUS_LABELS, is_alive, node_bucket, v5_status)

log = logging.getLogger("research.populations")

#: word-for-word definitions, shipped with the numbers so the UI can show them
POPULATION_DEFINITIONS: Dict[str, str] = {
    "total": "Every strategy row in the authoritative database (legacy infrastructure rows included).",
    "alive": "Nodes that are not a dead end: they are still in research, valid, or already "
             "promoted to the live layers. Infrastructure failures are NOT counted as alive.",
    "qualified": "Nodes that cleared the research gates against real data "
                 "(status.node_bucket == 'qualified').",
    "final": "Qualified nodes at the furthest research stage: final candidates, live-completed "
             "or MT5-demo nodes.",
    "deep": "The deep-backtest universe: the union of qualified nodes, live-eligible nodes and "
            "nodes that already have a deep (mt5-historical) run.",
    "live": "Nodes actually wired into a live layer: LIVE_TESTING status, an active live-test "
            "config, or an MT5-demo configuration.",
}

#: research stages that count as "final"
_FINAL_STAGES = {"FINAL_CANDIDATE", "LIVE_TESTED", "MT5_DEMO", "LIVE_COMPLETED", "FINAL", "APPROVED"}
_FINAL_STATUSES = {"LIVE_COMPLETED", "MT5_DEMO"}

#: the select list; one query, reused by every counter. ``pipeline_stage`` and
#: ``shortlisted`` exist only in databases that were migrated to the later
#: schema, so the query degrades to the columns this DATA actually has instead
#: of reporting 0 nodes (which would look like an empty lab).
_ROW_SQLS = (
    """SELECT id, status, data_source, pipeline_stage, symbol, timeframe, genome,
              shortlisted, failure_reason, creation_reason, survival_reason
       FROM strategies""",
    """SELECT id, status, data_source, symbol, timeframe, genome,
              failure_reason, creation_reason, survival_reason
       FROM strategies""",
    """SELECT id, status, data_source, symbol, timeframe, genome FROM strategies""",
)


def load_rows(db: Any = None) -> List[Dict[str, Any]]:
    """All strategy rows (read-only). A DB error yields an empty list + a note."""
    if db is None:
        from ..db.database import get_db
        db = get_db()
    last_err = None
    for sql in _ROW_SQLS:
        try:
            rows = db.q(sql) or []
            return [dict(r) for r in rows]
        except Exception as e:
            last_err = e
    log.warning("population read failed: %s", last_err)
    return []


def _eligible_ids(db: Any) -> Set[int]:
    """The live engine's own eligibility verdict, when it can be computed."""
    try:
        from ..live_testing.engine import get_engine
        nodes = get_engine().eligible_nodes() or []
        return {int(n.get("id")) for n in nodes if n.get("id") is not None}
    except Exception as e:                       # pragma: no cover - optional
        log.debug("eligible-node computation unavailable: %s", e)
        return set()


def _deep_tested_ids(db: Any) -> Set[int]:
    try:
        rows = db.q("SELECT DISTINCT strategy_id FROM mt5_historical_runs") or []
    except Exception:
        return set()
    return {int(r["strategy_id"]) for r in rows if r.get("strategy_id") is not None}


def _live_ids(db: Any) -> Set[int]:
    out: Set[int] = set()
    for table, col in (("live_test_configs", "is_active"), ("mt5_demo_configs", "enabled")):
        try:
            rows = db.q(f"SELECT strategy_id FROM {table} WHERE COALESCE({col},0)=1") or []
            out |= {int(r["strategy_id"]) for r in rows if r.get("strategy_id") is not None}
        except Exception:
            continue
    return out


def _is_final(row: Dict[str, Any]) -> bool:
    stage = str(row.get("pipeline_stage") or "").strip().upper()
    if stage in _FINAL_STAGES:
        return True
    return v5_status(row) in _FINAL_STATUSES


def is_alive_user_node(row: Dict[str, Any]) -> bool:
    """§D — "alive" for a USER_RESEARCH node, without ever inflating the count.

    ``status.is_alive()`` translates an *unrecognised* stored status to NOT_TESTED
    (deliberately: an unknown value must not be called "failed") — which would
    count it as alive. For a population number that is the wrong direction: an
    unknown status must count as neither alive nor failed. The bucket decides
    that (``unknown``/``excluded`` are excluded here), and the count is therefore
    never inflated by rows the vocabulary cannot explain.
    """
    bucket = node_bucket(row)["bucket"]
    if bucket in ("unknown", "excluded"):
        return False
    return is_alive(row)


def has_genome_and_entry(row: Dict[str, Any]) -> bool:
    """Does this row carry a usable genome with at least one entry condition?"""
    import json
    g = row.get("genome")
    if not g:
        return False
    if isinstance(g, str):
        try:
            g = json.loads(g)
        except Exception:
            return False
    if not isinstance(g, dict):
        return False
    if not (g.get("symbol") and g.get("timeframe")):
        return False
    return bool(g.get("entry_long") or g.get("entry_short"))


def deep_universe(db: Any = None, rows: Optional[Iterable[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """§E — the union the Deep Backtest picker must offer.

    A node belongs to the union when ANY of these hold:

      * it is qualified by research (bucket ``qualified``);
      * the live engine currently accepts it as a candidate (``eligible_nodes``);
      * it already has a deep (mt5-historical) run, so re-running it is legitimate.

    LEGACY_TEST rows are never included (they are infrastructure records), and the
    ``why`` field records which rule(s) put the node in the union, so the UI can
    explain the selection instead of implying one single source.
    """
    rows = [dict(r) for r in (rows if rows is not None else load_rows(db))]
    if db is None:
        from ..db.database import get_db
        db = get_db()
    eligible = _eligible_ids(db)
    deep_done = _deep_tested_ids(db)

    union: List[Dict[str, Any]] = []
    for row in rows:
        src = str(row.get("data_source") or "").strip().upper()
        if src == "LEGACY_TEST":
            continue
        why: List[str] = []
        if node_bucket(row)["bucket"] == "qualified":
            why.append("qualified")
        if int(row.get("id") or -1) in eligible:
            why.append("live-eligible")
        if int(row.get("id") or -1) in deep_done:
            why.append("already deep-tested")
        if not why:
            continue
        union.append({
            "id": int(row["id"]),
            "symbol": row.get("symbol"),
            "timeframe": row.get("timeframe"),
            "status": v5_status(row),
            "status_label": STATUS_LABELS.get(v5_status(row), v5_status(row)),
            "bucket": node_bucket(row)["bucket"],
            "why": why,
            "has_genome": has_genome_and_entry(row),
        })
    union.sort(key=lambda r: r["id"])
    return union


def populations(db: Any = None) -> Dict[str, Any]:
    """§D — the authoritative counts, with their definitions attached."""
    rows = load_rows(db)
    if db is None:
        from ..db.database import get_db
        db = get_db()
    notes: List[str] = []
    if not rows:
        notes.append("no strategy rows could be read — counts are reported as 0, not estimated")

    user_rows = [r for r in rows if str(r.get("data_source") or "").upper() != "LEGACY_TEST"]
    legacy = len(rows) - len(user_rows)

    buckets: Dict[str, int] = {}
    for r in rows:
        b = node_bucket(r)["bucket"]
        buckets[b] = buckets.get(b, 0) + 1

    alive = sum(1 for r in user_rows if is_alive_user_node(r))
    qualified = buckets.get("qualified", 0)
    final = sum(1 for r in user_rows if node_bucket(r)["bucket"] == "qualified" and _is_final(r))

    try:
        deep = len(deep_universe(db, rows=rows))
    except Exception as e:                       # pragma: no cover - defensive
        notes.append(f"deep universe could not be computed: {e}")
        deep = 0

    # ``live`` is only ever counted over nodes that were actually read: a stray
    # config row for a node that is not in this database must not inflate it.
    present = {int(r["id"]) for r in user_rows}
    live_configured = _live_ids(db) & present
    live_direct = {int(r["id"]) for r in user_rows if v5_status(r) == "LIVE_TESTING"}
    live = len(live_configured | live_direct)

    by_source: Dict[str, int] = {}
    for r in rows:
        key = str(r.get("data_source") or "UNKNOWN")
        by_source[key] = by_source.get(key, 0) + 1

    return {
        "ok": True,
        "counts": {"total": len(rows), "alive": alive, "qualified": qualified,
                   "final": final, "deep": deep, "live": live},
        "definitions": POPULATION_DEFINITIONS,
        "detail": {
            "user_research_total": len(user_rows),
            "legacy_excluded": legacy,
            "buckets": buckets,
            "alive_statuses": list(ALIVE_STATUSES),
            "live_inputs": {"configured": len(live_configured), "live_testing_status": len(live_direct)},
            "by_data_source": by_source,
        },
        "authority": "app.status.node_bucket (single classifier used by the node filters)",
        "notes": notes,
    }


# --------------------------------------------------------------------------- #
# §F — deep-run progress: how many bars a run must cover, and what it has done
# --------------------------------------------------------------------------- #
#: seconds per bar for the timeframes the lab stores
_TF_SECONDS: Dict[str, int] = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400,
    "D1": 86400, "W1": 604800, "MN1": 2592000,
}


def timeframe_seconds(timeframe: str) -> Optional[int]:
    return _TF_SECONDS.get(str(timeframe or "").strip().upper())


def estimate_bars(timeframe: str, start_ts: float, end_ts: float) -> Dict[str, Any]:
    """Bars a deep run should cover — the honest progress denominator.

    Returns ``{"bars": int|None, "reason": str}``: ``None`` means the estimate
    cannot be made (unknown timeframe or an inverted window) and the caller must
    show "unknown", never a fabricated number.
    """
    sec = timeframe_seconds(timeframe)
    if sec is None:
        return {"bars": None, "reason": f"unknown timeframe {timeframe!r} — no bar estimate"}
    try:
        start, end = float(start_ts), float(end_ts)
    except (TypeError, ValueError):
        return {"bars": None, "reason": "window is not numeric — no bar estimate"}
    if end <= start:
        return {"bars": None, "reason": "window is empty (end <= start) — no bar estimate"}
    return {"bars": int((end - start) // sec), "reason": ""}


def run_progress(run: Dict[str, Any], *, processed_bars: Optional[int] = None,
                 tf_seconds: Optional[int] = None) -> Dict[str, Any]:
    """§F — one run's progress shape, shared by API and UI.

    ``pct`` is ``None`` (and the UI must show "unknown") whenever the expected bar
    count is unknown; it is never invented from the elapsed wall clock.
    """
    run = run or {}
    bars = run.get("bars")
    tf = run.get("timeframe") or ""
    expected = None
    reason = ""
    if isinstance(bars, int) and bars > 0:
        expected = bars
    else:
        est = estimate_bars(tf, run.get("start_ts") or 0, run.get("end_ts") or 0)
        expected = est["bars"]
        reason = est["reason"]
    done = processed_bars if processed_bars is not None else 0
    pct = None
    if expected:
        pct = max(0.0, min(100.0, round(100.0 * float(done) / float(expected), 2)))
    return {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "timeframe": tf,
        "bars_expected": expected,
        "bars_processed": done,
        "pct": pct,
        "reason": reason,
        "source": "bars column" if (isinstance(bars, int) and bars > 0) else "estimate_bars(timeframe, window)",
    }
