"""V4.8 read-only condition inspection for the Live Testing market panel (§7).

The dashboard has to show, for each enrolled node, which of its entry conditions
are currently met ("3 of 4 conditions met", ✓/✕ per clause) on the last *closed*
bar. That answer must come from the engine that actually trades, not from a
second implementation in the browser, so this module reuses the exact same
machinery the live loop uses:

  * ``LiveTestEngine._live_frame``   - the bridge's own bars;
  * ``LiveTestEngine._live_features``- the same feature cache;
  * ``Backtester._eval``             - the same vectorised condition evaluator.

Nothing here starts the engine, changes ``last_signal_bar``, writes the database
or touches an order: it is a pure read that returns the per-clause truth values
for the last closed bar, plus the session/day/regime gates that can veto a
signal. If the frames or features are unavailable the answer says so instead of
guessing.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import numpy as np

from ..backtest.engine import _Ctx
from ..genome.schema import referenced_features
from ..strategies.authoritative import parse_ast_conditions


def _clauses(expr: Any) -> List[Any]:
    """Top-level clauses of an entry expression (``and``/``or`` are flattened)."""
    if not isinstance(expr, dict):
        return []
    if expr.get("op") in ("and", "or"):
        return [c for c in (expr.get("clauses") or []) if isinstance(c, dict)]
    return [expr]


def _label(clause: Any) -> str:
    """Human label using the project's own renderer, never a new one."""
    lines = parse_ast_conditions(clause)
    return " AND ".join(lines) if lines else "unrenderable clause"


def _side_rows(engine: Any, ctx: Any, side: str, expr: Any, index: int,
               gate_ok: bool) -> Dict[str, Any]:
    items = _clauses(expr)
    rows: List[Dict[str, Any]] = []
    for clause in items:
        try:
            met = bool(engine._backtester._eval(clause, ctx)[index])
        except Exception as e:                     # a broken clause is reported, not guessed
            rows.append({"condition": _label(clause), "met": None,
                         "error": f"{type(e).__name__}: {e}"})
            continue
        rows.append({"condition": _label(clause), "met": met})
    known = [r["met"] for r in rows if r["met"] is not None]
    return {
        "side": side,
        "conditions": rows,
        "met_count": sum(1 for m in known if m),
        "total_count": len(known),
        "gate_ok": gate_ok,
        "signal": bool(known) and all(known) and gate_ok,
    }


def node_condition_states(engine: Any, symbol: Optional[str] = None) -> Dict[str, Any]:
    """Per-node condition truth table for the last closed bar (read-only)."""
    try:
        nodes = engine.eligible_nodes()
    except Exception as e:
        return {"available": False, "reason": f"eligible nodes unavailable: {e}", "nodes": []}

    frames: Dict[Any, Any] = {}
    cache_features: Dict[Any, Any] = {}
    out: List[Dict[str, Any]] = []
    for node in nodes:
        g = node.get("genome") or {}
        sym = str(g.get("symbol") or "").upper()
        tf = g.get("timeframe", "M15")
        sid = node.get("id")
        if symbol and sym != str(symbol).upper():
            continue
        row: Dict[str, Any] = {"node_id": sid, "symbol": sym, "timeframe": tf,
                               "direction": g.get("direction", "both"), "available": False,
                               "reason": None, "bar_time": None, "sides": []}
        key = (sym, tf)
        try:
            if key not in frames:
                frames[key] = engine._live_frame(sym, tf)
            df = frames.get(key)
            if df is None or df.empty or len(df) < 2:
                row["reason"] = "no closed bars available from the active market bridge"
                out.append(row)
                continue
            needed = list(referenced_features(g)) + ["close", "open", "high", "low"]
            ex = g.get("exit") or {}
            needed.append(ex.get("atr_spec", "atr:14"))
            needed += [f"regime:{r}" for r in g.get("regime_filters") or []]
            fkey = (key, tuple(sorted(set(needed))))
            if fkey not in cache_features:
                cache_features[fkey] = engine._live_features(df, sorted(set(needed)))
            cache = dict(cache_features[fkey])
            n = len(df)
            for k, v in list(cache.items()):
                if len(v) != n:
                    cache[k] = v[-n:]
            ctx = _Ctx("live-inspect", cache, n)
            i = n - 2                                          # last CLOSED bar
            allow = True
            if g.get("sessions"):
                allow = bool(df["session"].iloc[i] in g["sessions"]) or (
                    "london_ny_overlap" in g["sessions"] and 12 <= int(df["hour"].iloc[i]) < 16)
            if g.get("days") is not None:
                allow = allow and bool(int(df["dow"].iloc[i]) in list(g["days"]))
            for r in g.get("regime_filters") or []:
                arr = cache.get(f"regime:{r}")
                if arr is not None:
                    allow = allow and bool(np.nan_to_num(arr[i], nan=0.0) > 0.5)
            row["bar_time"] = float(df["ts"].iloc[i])
            direction = g.get("direction", "both")
            if direction in ("both", "long") and g.get("entry_long"):
                row["sides"].append(_side_rows(engine, ctx, "LONG", g["entry_long"], i, allow))
            if direction in ("both", "short") and g.get("entry_short"):
                row["sides"].append(_side_rows(engine, ctx, "SHORT", g["entry_short"], i, allow))
            row["available"] = bool(row["sides"])
            if not row["available"]:
                row["reason"] = "node has no entry expression to evaluate"
        except Exception as e:                                  # never take the panel down
            row["reason"] = f"{type(e).__name__}: {e}"
        out.append(row)

    try:
        excluded = engine.excluded_nodes()
    except Exception:
        excluded = []
    return {
        "available": True,
        "enrolled_count": len(getattr(engine, "live_test_configs", lambda: [])() or []),
        "excluded": [{"node_id": e.get("id"), "reason": e.get("reason")} for e in excluded if e.get("id")],
        "source": None,          # the market panel already reports the active bridge source
        "engine_active": bool(getattr(engine, "active", False)),
        "bar_note": ("Condition states are evaluated on the last CLOSED bar with the engine's own "
                     "features and evaluator. Evaluating them never opens a trade."),
        "nodes": out,
    }




def node_indicator_values(engine: Any, strategy_id: int,
                          symbol: Optional[str] = None) -> Dict[str, Any]:
    """V5 §8 — the selected node's own indicator values on the last closed bar.

    Reads the node's genome, resolves exactly the features its rules reference
    (plus its ATR spec and regime filters) through the engine's own feature code
    on the live frame, and reports the numeric value of each at the last CLOSED
    bar. Values are whatever the engine computed — nothing is smoothed, filled or
    defaulted; a feature the engine cannot produce is reported with its error.
    """
    out: Dict[str, Any] = {"strategy_id": strategy_id, "available": False, "reason": None,
                           "timeframe": None, "bar_time": None, "indicators": [],
                           "read_only": True}
    db = None
    try:
        from ..db.database import get_db
        db = get_db()
    except Exception:
        pass

    genome: Dict[str, Any] = {}
    node = None
    try:
        for n in engine.eligible_nodes():
            if n.get("id") == strategy_id:
                node = n
                genome = n.get("genome") or {}
                break
    except Exception as e:
        out["reason"] = f"eligible nodes unavailable: {e}"
        return out
    if node is None and db is not None:
        row = db.get_strategy(strategy_id)
        if row is None:
            out["reason"] = f"node {strategy_id} not found"
            return out
        try:
            genome = json.loads(row.get("genome") or "{}")
        except Exception:
            genome = {}

    sym = str(genome.get("symbol") or symbol or "").upper()
    tf = genome.get("timeframe") or "M15"
    out["symbol"] = sym
    out["timeframe"] = tf
    if not sym:
        out["reason"] = "the node's genome does not name a symbol"
        return out

    try:
        df = engine._live_frame(sym, tf)
    except Exception as e:
        out["reason"] = f"live frame unavailable: {type(e).__name__}: {e}"
        return out
    if df is None or df.empty or len(df) < 2:
        out["reason"] = "no closed bars available from the active market bridge"
        return out

    ex = genome.get("exit") or {}
    needed = set(referenced_features(genome)) | {"close", "open", "high", "low", "atr:14"}
    needed.add(ex.get("atr_spec", "atr:14"))
    needed |= {f"regime:{r}" for r in genome.get("regime_filters") or []}
    try:
        cache = engine._live_features(df, sorted(needed))
    except Exception as e:
        out["reason"] = f"feature computation failed: {type(e).__name__}: {e}"
        return out

    n = len(df)
    i = n - 2                                    # last CLOSED bar (never the forming one)
    out["bar_time"] = float(df["ts"].iloc[i])
    out["bar_index"] = i
    rows: List[Dict[str, Any]] = []
    for spec in sorted(needed):
        arr = cache.get(spec)
        if arr is None:
            rows.append({"spec": spec, "value": None, "error": "not produced by the engine"})
            continue
        try:
            v = float(np.asarray(arr)[i])
        except Exception as e:
            rows.append({"spec": spec, "value": None, "error": f"{type(e).__name__}: {e}"})
            continue
        rows.append({"spec": spec, "value": None if not np.isfinite(v) else round(v, 6),
                     "error": None if np.isfinite(v) else "value is not finite on this bar"})
    out["indicators"] = rows
    out["available"] = True
    out["note"] = ("Values are the engine's own feature output at the last CLOSED bar of the "
                   "node's timeframe — the same values the live rules are evaluated against.")
    return out
