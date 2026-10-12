"""V6.8 — the single node-data authority for every node-related tab.

Freeze contract (V6.8 master prompt §1/§3): this module NEVER recalculates,
reinterprets or reclassifies research results. It only projects the EXISTING
stored records through the EXISTING frozen primitives:

* the stored ``strategies`` rows and their stored statuses (unchanged);
* the qualified-tier definition ``status IN ('QUALIFIED','PAPER')`` — the
  exact predicate of ``db.get_qualified_strategies()``, ``reconstruct_state``
  and the per-run counters (``database.py`` ``SUM(CASE WHEN status IN
  ('QUALIFIED','PAPER')...)``) — never a new interpretation of QUALIFIED;
* ``reconstruct_state`` semantics for alive/dead/backtesting/validating;
* ``node_bucket`` (V5.1.1 classifier) for the operator-facing bucket LABEL of
  each row — labels only, never a count authority;
* the established best-generation-record query (detail > screen, newest wins)
  for generation return/PF/DD/win rate/trades — displayed == stored.

Every tab (Overview, DeepTesting, node tables, inspection) reads THIS
projection, so counts, identities, rankings and generation metrics cannot
diverge between tabs.  Each response carries one ``revision`` (the existing
``data_revision`` meta + the V6.7 content fingerprints) so mixed snapshots are
detectable; the revision is for consistency verification only and never a
reason to reclassify nodes.

Scopes are explicit and labelled everywhere:

* ``counts``           — complete stored research history (ALL runs incl. the
                         preserved historical runs).  ``qualified`` = the
                         established QUALIFIED+PAPER tier = the number in the
                         V3.2 manifest ledger, the run-history sums and
                         ``get_qualified_strategies()`` (99 on the operator's
                         live data).
* ``counts_by_status`` — the raw stored-status histogram (same rows).
* ``counts_current_experiment`` — the V4.0 legacy-hiding scope
  (``reconstruct_state(exclude_legacy=True)``), kept as a labelled secondary,
  never silently mixed into the headline.
* ``tops``             — rankings over the SAME complete record set.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .populations import (INVENTORY_ROW_COLS, _best_generation_record,
                          _gen_metrics_from, _top_list, db_state_token,
                          node_label, research_label)

# The frozen qualified-tier predicate — copied verbatim from
# db.get_qualified_strategies / reconstruct_state / the per-run counter.
QUALIFIED_TIER_SQL = "status IN ('QUALIFIED','PAPER')"

ALIVE_TIER_STATUSES = ("BORN", "BACKTESTING", "SURVIVED", "VALIDATING",
                       "PAPER", "QUALIFIED")
DEAD_TIER_STATUSES = ("FAILED", "KILLED", "RETIRED")


def _own(db):
    """(handle, owns) — a path string gets a private handle; None shares the
    process singleton which is NEVER closed by this module."""
    if isinstance(db, str):
        from app.db.database import Database
        return Database(db), True
    if db is None:
        from app.db.database import get_db
        return get_db(), False
    return db, False


# V6.9 §6 — serve-time cache: the snapshot is recomputed ONLY when the cheap
# state token changes (any insert/update of the strategies universe). This is
# what makes the authority safe to include in every status response — first
# paint and refresh then read the SAME number with no delayed-hydration window.
_SNAPSHOT_CACHE: Dict[str, Dict[str, Any]] = {}
_SNAPSHOT_LOCK = __import__("threading").Lock()


def node_data_snapshot(db=None, limit_top: int = 10, use_cache: bool = True) -> Dict[str, Any]:
    """One authoritative snapshot of the stored node records (see module doc)."""
    db, owns = _own(db)
    try:
        # the cache key includes the DATABASE identity as well as the state
        # token — two different databases with identical shapes can never share
        # a snapshot (mixed-snapshot guard).
        try:
            db_key = str(getattr(db, "path", None) or getattr(db, "_path", None) or id(db))
        except Exception:
            db_key = str(id(db))
        if use_cache and not isinstance(db, str):
            try:
                token = db_state_token(db)
            except Exception:
                token = None
            if token:
                with _SNAPSHOT_LOCK:
                    hit = _SNAPSHOT_CACHE.get((db_key, token))
                if hit is not None:
                    out = dict(hit)
                    out["cached"] = True
                    return out
        out = _node_data_snapshot_uncached(db, limit_top=limit_top)
        if use_cache and out.get("state_token"):
            with _SNAPSHOT_LOCK:
                _SNAPSHOT_CACHE.clear()
                _SNAPSHOT_CACHE[(db_key, out["state_token"])] = dict(out)
        return out
    finally:
        if owns:
            try:
                db.close()
            except Exception:
                pass


def _node_data_snapshot_uncached(db, limit_top: int = 10) -> Dict[str, Any]:
    # §2.1 — mixed-snapshot detection: the cheap state token is read before and
    # after the scan; if the stored data changed mid-computation the result is
    # explicitly flagged instead of presenting a misleading mixture.
    try:
        token_before = db_state_token(db)
    except Exception:
        token_before = None
    try:
        rows = [dict(r) for r in (db.q(f"SELECT {INVENTORY_ROW_COLS} FROM strategies")
                                  or [])]
        gen = _best_generation_record(db)

        by_status: Dict[str, int] = {}
        identity: Dict[str, Any] = {}
        scored = []
        fit_scored = []
        errors: list = []
        for row in rows:
            st = str(row.get("status") or "UNKNOWN")
            by_status[st] = by_status.get(st, 0) + 1
            rid, rnum = row.get("id"), row.get("research_node_num")
            try:
                bucket = None
                from ..status import node_bucket
                bucket = node_bucket(row)["bucket"]
            except Exception as exc:            # one bad row never poisons the snapshot
                errors.append(f"node {rid}: classifier error {exc}")
                bucket = "unknown"
            if rid is not None:
                identity[str(int(rid))] = {
                    "canonical_node_id": int(rid),
                    "node_label": node_label(row),
                    "research_node_num": int(rnum) if rnum is not None else None,
                    "research_label": research_label(row),
                    "run_id": row.get("run_id"),
                    "stored_status": st,
                    "bucket": bucket,
                    "symbol": row.get("symbol"),
                    "timeframe": row.get("timeframe"),
                }
            met = gen.get(rid) or gen.get(int(rid) if rid is not None else -1) or {}
            met = _gen_metrics_from(met if isinstance(met, dict) else {})
            # rankings over the SAME complete record set the counts describe
            scored.append((row, met))
            fv = row.get("fitness")
            if isinstance(fv, (int, float)) and not isinstance(fv, bool):
                fit_scored.append((float(fv), row, met))

        total = len(rows)
        q = db.one(f"SELECT COUNT(*) c FROM strategies WHERE {QUALIFIED_TIER_SQL}") or {}
        alive = sum(by_status.get(s, 0) for s in ALIVE_TIER_STATUSES)
        dead = sum(by_status.get(s, 0) for s in DEAD_TIER_STATUSES)
        counts = {
            "qualified": int(q.get("c") or 0),
            "alive": int(alive),
            "dead": int(dead),
            "backtesting": int(by_status.get("BACKTESTING", 0)),
            "validating": int(by_status.get("VALIDATING", 0)),
            "total": int(total),
            "scope": "complete stored research history (all runs incl. preserved)",
            "formula": "qualified = stored status QUALIFIED + PAPER "
                       "(db.get_qualified_strategies / reconstruct_state semantics)",
        }

        # labelled secondary scope (V4.0 legacy-hiding display scope) — shown
        # beside the headline, never mixed into it.
        try:
            state2 = db.reconstruct_state(db.one(
                "SELECT COALESCE(MAX(id),0) m FROM strategies")["m"], exclude_legacy=True)
            counts_current = {
                "qualified": int(state2.get("qualified") or 0),
                "alive": int(state2.get("alive") or 0),
                "dead": int(state2.get("dead") or 0),
                "total": int(state2.get("total_nodes") or 0),
                "scope": "current experiment scope (LEGACY_TEST-classified rows hidden)",
                "formula": "reconstruct_state(exclude_legacy=True)",
            }
        except Exception as exc:
            counts_current = {"error": f"{type(exc).__name__}: {exc}"[:160]}

        tops = {
            "return_pct": _top_list(
                [(m["return_pct"], r, m) for r, m in scored], "return_pct",
                desc=True, limit=limit_top),
            "profit_factor": _top_list(
                [(m["profit_factor"], r, m) for r, m in scored], "profit_factor",
                desc=True, limit=limit_top),
            "max_drawdown_pct": _top_list(
                [(m["max_drawdown_pct"], r, m) for r, m in scored], "max_drawdown_pct",
                desc=False, limit=limit_top),
            "trades": _top_list(
                [(m["trades"], r, m) for r, m in scored], "trades",
                desc=True, limit=limit_top),
            "fitness": _top_list(fit_scored, "fitness", desc=True, limit=limit_top),
        }

        content_fp = hashlib.sha256(json.dumps(
            {"counts": counts, "by_status": by_status,
             "top_ids": [[t.get("canonical_node_id") for t in v] for v in tops.values()],
             "n_identity": len(identity)},
            sort_keys=True, default=str).encode()).hexdigest()
        try:
            from .populations import db_state_token
            token = db_state_token(db)
        except Exception:
            token = "unknown"
        try:
            rev_base = str(db.get_meta("data_revision") or "0")
        except Exception:
            rev_base = "0"
        try:
            token_after = db_state_token(db)
        except Exception:
            token_after = token_before
        return {
            "schema_version": 1,
            "revision": f"{rev_base}:{content_fp[:12]}",
            "state_token": token,
            "mixed_snapshot": bool(token_before and token_after and token_before != token_after),
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "counts": counts,
            "counts_by_status": by_status,
            "counts_current_experiment": counts_current,
            "identity": identity,
            "tops": tops,
            "errors": errors,
        }
    finally:
        pass
