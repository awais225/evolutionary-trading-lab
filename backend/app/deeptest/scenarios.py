"""V6.6 §8/§12 — DeepTest run orchestration and persistent scenario snapshots.

A DeepTest run executes the node's ACTUAL strategy through the lab's historical
Backtester (the same engine that produced the generation metrics) on validated,
persisted candles — never mock or generic logic, never any broker order, never
touching the live execution worker. Every run is persisted as an immutable
scenario snapshot (requested vs actual dates, bars, dataset fingerprint,
coverage status + gaps, settings, assumptions, metrics, trades, timestamps,
engine versions). Later edits never alter a stored scenario.

Statuses: QUEUED | FETCHING | VALIDATING | RUNNING | COMPLETED | PARTIAL |
CANCELLED | FAILED. PARTIAL = COMPLETED results on data the operator accepted
under §9 with incomplete coverage (always marked; never labelled complete).

Statistics precedence (§12): explicit preferred DeepTest -> latest valid
DeepTest -> generation stats -> N/A. A failed/cancelled/invalid run never
displaces a valid one; an explicit preference is never silently replaced.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from . import analytics as A
from . import store as S
from .coverage import check_period, pair_coverage, tf_seconds
from .periods import explicit_period, resolve_period

VALID_STATUSES = ("QUEUED", "FETCHING", "VALIDATING", "RUNNING",
                  "COMPLETED", "PARTIAL", "CANCELLED", "FAILED")
TERMINAL = ("COMPLETED", "PARTIAL", "CANCELLED", "FAILED")
VALID_FOR_PREFERENCE = ("COMPLETED", "PARTIAL")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="deeptest")
_CANCELS: set = set()
_LOCK = threading.Lock()


def settings_fingerprint(settings: Dict[str, Any]) -> str:
    payload = json.dumps({k: settings.get(k) for k in sorted(settings)}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


NODE_SETTING_FIELDS = ("risk_mode", "risk_pct", "risk_amount", "max_trades",
                       "sl_offset_pips", "tp_offset_pips")


def _validate_node_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the per-node DeepTest settings (never touching live config)."""
    errors = []
    values: Dict[str, Any] = {}
    rm = str((payload or {}).get("risk_mode") or "").upper()
    if rm:
        if rm not in ("PERCENT", "AMOUNT"):
            errors.append({"field": "risk_mode", "error": "risk_mode must be PERCENT or AMOUNT"})
        else:
            values["risk_mode"] = rm
    for key, lo, hi in (("risk_pct", 0.01, 100.0), ("risk_amount", 0.01, 10_000_000.0),
                        ("sl_offset_pips", -10_000.0, 10_000.0),
                        ("tp_offset_pips", -10_000.0, 10_000.0)):
        if key in (payload or {}) and payload.get(key) is not None:
            try:
                v = float(payload.get(key))
            except (TypeError, ValueError):
                errors.append({"field": key, "error": f"{key} must be a number"})
                continue
            if key in ("risk_pct", "risk_amount") and not (lo <= v <= hi):
                errors.append({"field": key, "error": f"{key} must be between {lo:g} and {hi:g}"})
                continue
            values[key] = v
    if "max_trades" in (payload or {}) and payload.get("max_trades") is not None:
        try:
            mt = int(payload.get("max_trades"))
            if mt < 0:
                raise ValueError
            values["max_trades"] = mt
        except (TypeError, ValueError):
            errors.append({"field": "max_trades", "error": "max_trades must be a non-negative "
                                                            "whole number (0 = unlimited)"})
    return {"ok": not errors, "values": values, "errors": errors}


# --------------------------------------------------------------------------- #
# §9 preflight
# --------------------------------------------------------------------------- #
def preflight(db: Any, *, node_id: int, duration_key: str = "",
              requested_start: Optional[float] = None,
              requested_end: Optional[float] = None,
              settings: Optional[Dict[str, Any]] = None,
              node_settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve the period, inspect coverage, and decide whether the §9
    incomplete-history confirmation is required. Never runs anything.

    The confirmation fingerprint binds EXACTLY these settings (global + the
    per-node experimental ones) — so both ``preflight`` and ``start_run`` must
    see the same dict for a remembered YES to carry over.
    """
    settings = {**S.get_settings(db), **(settings or {})}
    vns = _validate_node_settings(node_settings or {})
    if not vns["ok"]:
        return {"ok": False, "error": "invalid node settings", "details": vns["errors"]}
    settings["node_settings"] = vns["values"]
    node = _load_node(db, node_id)
    symbol = node["symbol"]
    timeframe = node["timeframe"]
    if duration_key:
        period = resolve_period(duration_key, end_ts=requested_end)
    else:
        if requested_start is None or requested_end is None:
            return {"ok": False, "error": "duration_key or explicit start/end is required"}
        period = explicit_period(requested_start, requested_end)

    chk = check_period(symbol, timeframe,
                       period["start_ts"], period["end_ts"],
                       node_id=node_id, duration_key=period["duration_key"],
                       settings_fingerprint=settings_fingerprint(settings))
    needs_confirmation = chk["coverage_status"] != "COMPLETE"
    if chk["coverage_status"] == "NONE":
        return {"ok": False, "error": ("no historical candles exist for "
                                       f"{symbol}/{timeframe} — use GET MT5 DATA to fetch "
                                       "history or MANUALLY LOAD DATA to import a CSV"),
                "coverage": chk, "period": period}

    saved = S.get_confirmation(db, chk["confirmation_fingerprint"])
    decided_yes = bool(saved and saved.get("decision") == "YES_PROCEED")
    return {
        "ok": True,
        "period": period,
        "coverage": chk,
        "settings": settings,
        "settings_fingerprint": settings_fingerprint(settings),
        "needs_confirmation": needs_confirmation and not decided_yes,
        "confirmation_valid_for_this_fingerprint": decided_yes,
        "may_proceed": (not needs_confirmation) or decided_yes,
    }


def record_decision(db: Any, *, confirmation_fingerprint: str, decision: str,
                    node_id: Any = None) -> Dict[str, Any]:
    """§9's three buttons. ``decision`` ∈ YES_PROCEED | NO_CANCEL | LOAD_CSV."""
    if decision not in ("YES_PROCEED", "NO_CANCEL", "LOAD_CSV"):
        return {"ok": False, "error": "decision must be YES_PROCEED, NO_CANCEL or LOAD_CSV"}
    rec = S.save_confirmation(db, confirmation_fingerprint, decision)
    return {"ok": True, "decision": rec.get("decision"), "fingerprint": confirmation_fingerprint}


# --------------------------------------------------------------------------- #
# run lifecycle
# --------------------------------------------------------------------------- #
def start_run(db: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + queue a DeepTest run. Returns the QUEUED scenario row.

    Idempotency: an existing scenario with the same idempotency_key is returned
    unchanged instead of queueing a duplicate.
    """
    try:
        node_id = int(payload.get("node_id"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "node_id is required"}

    idem = payload.get("idempotency_key")
    if idem:
        for row in db.q("SELECT run_id FROM deeptest_scenarios WHERE idempotency_key=?", (str(idem),)) or []:
            return {"ok": True, "idempotent_replay": True,
                    "scenario": S.get_scenario(db, row["run_id"])}

    node = _load_node(db, node_id)
    settings = payload.get("settings") or S.get_settings(db)
    v = S.validate_settings(settings)
    if payload.get("settings") and not v["ok"]:
        return {"ok": False, "error": "invalid settings", "details": v["errors"]}
    settings = {**S.get_settings(db), **(payload.get("settings") or {})}
    # per-node experimental settings (Deep Testing only — the LIVE TESTING
    # config is never mutated). Missing fields inherit the node's own semantics
    # as read-only defaults inside the engine call.
    node_settings = payload.get("node_settings") or {}
    vns = _validate_node_settings(node_settings)
    if not vns["ok"]:
        return {"ok": False, "error": "invalid node settings", "details": vns["errors"]}
    settings["node_settings"] = vns["values"]

    pf = preflight(db, node_id=node_id,
                   duration_key=payload.get("duration_key") or "",
                   requested_start=payload.get("requested_start"),
                   requested_end=payload.get("requested_end"),
                   settings=settings,
                   node_settings=None)     # already merged above — same dict
    if not pf.get("ok"):
        return pf
    if pf["needs_confirmation"]:
        return {"ok": False, "needs_confirmation": True,
                "error": "the requested history is incomplete — confirm the coverage "
                         "warning before this run may start",
                "confirmation": pf["coverage"]}
    # a remembered YES binds to EXACTLY this fingerprint
    if pf["coverage"]["coverage_status"] != "COMPLETE":
        saved = S.get_confirmation(db, pf["coverage"]["confirmation_fingerprint"])
        if not (saved and saved.get("decision") == "YES_PROCEED"):
            return {"ok": False, "needs_confirmation": True,
                    "error": "the stored confirmation does not match this request "
                             "(data/period/settings changed) — confirm again",
                    "confirmation": pf["coverage"]}

    period = pf["period"]
    ghash = _genome_hash(node.get("genome"))
    cfg_hash = _genome_hash(node.get("config"))
    run_id = S.new_run_id()
    S.insert_scenario(db, {
        "run_id": run_id,
        "node_id": node_id,
        "status": "QUEUED",
        "idempotency_key": str(idem) if idem else None,
        "duration_key": period["duration_key"],
        "requested_start": period["start_ts"],
        "requested_end": period["end_ts"],
        "symbol": node["symbol"],
        "timeframe": node["timeframe"],
        "coverage_status": "PENDING",
        "settings": settings,
        "genome_hash": ghash,
        "config_hash": cfg_hash,
        "confirmed_fingerprint": pf["coverage"]["confirmation_fingerprint"]
        if pf["coverage"]["coverage_status"] != "COMPLETE" else None,
        "notes": ("fetched by engine" if False else None),
        "provenance": {"requested_period": period,
                       "requested_by": "deeptest-api",
                       "run_policy": "historical simulation only — no broker orders"},
    })
    _executor.submit(_execute, run_id)
    return {"ok": True, "run_id": run_id, "scenario": S.get_scenario(db, run_id)}


def cancel_run(db: Any, run_id: str) -> Dict[str, Any]:
    sc = S.get_scenario(db, run_id)
    if not sc:
        return {"ok": False, "error": f"unknown run {run_id}"}
    if sc["status"] in TERMINAL:
        return {"ok": False, "error": f"run already {sc['status']}"}
    with _LOCK:
        _CANCELS.add(run_id)
    S.update_scenario(db, run_id, status="CANCELLED", finished_at=time.time(),
                      notes="cancelled by operator")
    return {"ok": True, "run_id": run_id, "status": "CANCELLED"}


def _execute(run_id: str) -> None:
    from ..db.database import get_db
    db = get_db()
    t0 = time.time()
    try:
        sc = S.get_scenario(db, run_id)
        if not sc or sc["status"] in TERMINAL:
            return
        if run_id in _CANCELS:
            S.update_scenario(db, run_id, status="CANCELLED", finished_at=time.time())
            return
        node = _load_node(db, int(sc["node_id"]))
        settings = sc.get("settings") or S.get_settings(db)
        symbol, timeframe = sc["symbol"], sc["timeframe"]

        # ---- VALIDATING: materialise the dataset view over the persisted store
        S.update_scenario(db, run_id, status="VALIDATING", started_at=t0)
        from ..data.engine import get_data_engine
        from ..data.master import get_master
        from ..mt5.factory import get_bridge
        engine = get_data_engine()
        mstore = get_master(symbol, timeframe, get_bridge().profile())
        if not mstore.rows:
            S.update_scenario(db, run_id, status="FAILED", finished_at=time.time(),
                              error=f"no validated candles are persisted for {symbol}/{timeframe} — "
                                    "use GET MT5 DATA or IMPORT CSV first (nothing was fabricated)")
            return
        dataset_id = engine._materialize_dataset_view(mstore)
        frame = engine.get_frame(dataset_id)
        req_start, req_end = float(sc["requested_start"]), float(sc["requested_end"])
        ts = frame["ts"].to_numpy()
        lo = int((ts >= req_start).argmax()) if len(ts) else 0
        hi = len(ts)
        mask = ts < req_end
        if mask.any():
            hi = int(mask.sum())
        if hi - lo < 2:
            S.update_scenario(db, run_id, status="FAILED", finished_at=time.time(),
                              error="fewer than 2 validated bars in the requested range — "
                                    "refusing to backtest a near-empty window")
            return
        if run_id in _CANCELS:
            S.update_scenario(db, run_id, status="CANCELLED", finished_at=time.time())
            return

        # ---- RUNNING: the node's ACTUAL strategy through the historical engine
        S.update_scenario(db, run_id, status="RUNNING")
        result = _run_strategy(db, node, dataset_id, (lo, hi), settings, symbol, timeframe)
        if run_id in _CANCELS:
            S.update_scenario(db, run_id, status="CANCELLED", finished_at=time.time())
            return
        if not result.get("ok"):
            S.update_scenario(db, run_id, status="FAILED", finished_at=time.time(),
                              error=str(result.get("error"))[:500])
            return

        metrics = result["metrics"]
        trades = result["trades"]
        summary = A.summarize(trades, initial_capital=float(settings["total_capital"]))
        reconciliation = A.reconcile(trades, summary["summary"])

        cov = pair_coverage(symbol, timeframe)
        gaps = (cov.get("missing_periods") or [])[:25]
        coverage_status = sc.get("provenance", {}) or {}
        coverage_state = "COMPLETE"
        if sc.get("confirmed_fingerprint"):
            coverage_state = "PARTIAL"

        ts_lo = float(ts[lo]); ts_hi = float(ts[hi - 1])
        assumptions = result["assumptions"]
        ns = (settings or {}).get("node_settings") or {}
        assumptions["capital_and_risk"] = {
            "total_capital": settings["total_capital"],
            "sl_risk_per_trade_pct": ns.get("risk_pct", settings["risk_pct"]),
            "risk_mode": ns.get("risk_mode", "PERCENT"),
            "risk_amount": ns.get("risk_amount") if ns.get("risk_mode") == "AMOUNT" else None,
            "risk_basis": ("percent of the scenario's simulated equity at trade entry "
                           "(equity starts at Total Capital; each trade is sized when it "
                           "opens, using the equity realised at that moment)"
                           if ns.get("risk_mode", "PERCENT") != "AMOUNT" else
                           "constant monetary risk per trade (money at risk if the SL is hit)"),
            "max_active_trades_per_node": settings["max_active_trades_per_node"],
            "max_active_trades_semantics": ("0 = unlimited concurrent simulated trades; "
                                            "N = at most N open at once (entries gated only — "
                                            "an exit is never forced early); 1 = one open "
                                            "simulated trade at a time"),
        }
        S.update_scenario(db, run_id,
                          status="PARTIAL" if coverage_state == "PARTIAL" else "COMPLETED",
                          finished_at=time.time(),
                          actual_start=ts_lo, actual_end=ts_hi,
                          bars_used=int(hi - lo),
                          dataset_id=dataset_id,
                          dataset_fingerprint=cov.get("fingerprint"),
                          coverage_status=coverage_state,
                          gaps=gaps,
                          metrics=metrics,
                          analytics={**summary, "reconciliation": reconciliation},
                          provenance={
                              "requested_period": {"start_ts": req_start, "end_ts": req_end},
                              "actual_tested_range": {"start_ts": ts_lo, "end_ts": ts_hi,
                                                      "bars": int(hi - lo)},
                              "dataset": {"id": dataset_id,
                                          "fingerprint": cov.get("fingerprint"),
                                          "source": cov.get("source"),
                                          "broker": cov.get("broker"),
                                          "server": cov.get("server")},
                              "assumptions": assumptions,
                              "provisional": coverage_state == "PARTIAL",
                              "run_policy": "historical simulation only — no broker orders",
                          },
                          engine_versions=result["engine_versions"])
        S.insert_trades(db, run_id, trades)
    except Exception as e:  # noqa: BLE001 — a failed run must persist its failure
        try:
            S.update_scenario(db, run_id, status="FAILED", finished_at=time.time(),
                              error=f"{type(e).__name__}: {e}"[:500],
                              notes=traceback.format_exc()[-2000:])
        except Exception:
            pass


def _run_strategy(db: Any, node: Dict[str, Any], dataset_id: str,
                  window: Tuple[int, int], settings: Dict[str, Any],
                  symbol: str, timeframe: str) -> Dict[str, Any]:
    """Build the BacktestRequest from the node's own genome + DeepTest settings."""
    from ..backtest.engine import BacktestRequest, run_backtest
    from ..versions import manifest as engine_manifest

    g = node.get("genome") or {}
    ns = (settings or {}).get("node_settings") or {}
    max_trades = int(ns.get("max_trades") if ns.get("max_trades") is not None else 3000)
    # offsets default to the node's own config values (read-only); an explicit
    # DeepTest setting overrides them FOR THIS SCENARIO ONLY
    gen_settings = node.get("config") or {}
    sl_off = float(ns.get("sl_offset_pips")
                   if ns.get("sl_offset_pips") is not None
                   else gen_settings.get("sl_offset_pips") or 0.0)
    tp_off = float(ns.get("tp_offset_pips")
                   if ns.get("tp_offset_pips") is not None
                   else gen_settings.get("tp_offset_pips") or 0.0)
    risk_mode = str(ns.get("risk_mode") or "PERCENT").upper()
    risk_amount = float(ns.get("risk_amount") or 0.0)
    risk_pct = float(ns.get("risk_pct") if ns.get("risk_pct") is not None
                     else settings["risk_pct"])

    req = BacktestRequest(
        genome=g,
        dataset_id=dataset_id,
        stage="oos",
        window=window,
        # the historical research defaults exactly: the engine applies its own
        # base spread/slippage/commission; mults stay 1.0 (no cost fudging)
        spread_mult=1.0,
        slippage_mult=1.0,
        commission_mult=1.0,
        max_trades=max_trades,
        schedule=node.get("schedule"),
        conditions=node.get("conditions"),
        initial_balance=float(settings["total_capital"]),
        risk_per_trade_pct=(risk_pct if risk_mode == "PERCENT" else None),
        risk_mode=risk_mode,
        risk_amount=(risk_amount if risk_mode == "AMOUNT" else None),
        max_active_trades=int(settings["max_active_trades_per_node"]),
        sl_offset_pips=sl_off,
        tp_offset_pips=tp_off,
    )
    res = run_backtest(req)
    if not res.ok:
        return {"ok": False, "error": getattr(res, "error", "backtest failed")}

    assumptions = {
        "execution_model": (res.metrics or {}).get("execution_model"),
        "spread_mult": req.spread_mult,
        "slippage_mult": req.slippage_mult,
        "commission_mult": req.commission_mult,
        "commission": ((g.get("costs") or {}).get("commission_per_lot"),
                       "per-lot commission from the node's generation cost model"),
        "swap": "not modelled (positions are intraday-window only in this engine)",
        "sl_tp_same_bar_policy": ("SL-first: if a bar touches both stop and target, the stop "
                                  "is assumed hit (conservative, never profit-favouring); "
                                  "signals are evaluated on bar close"),
        "timing": "signals on bar close; fills at the next bar open",
        "session_timezone": "stored bar timestamps (UTC) — session labels from the lab's "
                            "DST-aware session bands",
        "gaps": "gaps are reported, never filled; no bars are interpolated or extrapolated",
        "sizing": "volume floored to the broker lot step; a trade whose size falls below the "
                  "broker minimum is skipped rather than enlarged",
        "look_ahead": "none — features and signals are computed on closed bars only; the "
                      "window bounds the dataset slice",
        "sl_offsets_pips": sl_off,
        "tp_offsets_pips": tp_off,
    }
    engine_versions = {
        **engine_manifest(),
        "mt5_historical_adapter": "MT5->pandas historical adapter v1 (no broker orders)",
        "deeptest": "V6.6",
        "product": _product_version(),
    }

    trades = _normalize_trades(res.trades, settings)
    return {"ok": True, "metrics": res.metrics, "trades": trades,
            "assumptions": assumptions, "engine_versions": engine_versions}


def _normalize_trades(raw_trades: List[Dict[str, Any]], settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map engine trades onto the §11 trade-level schema (costs broken out)."""
    out = []
    for t in raw_trades or []:
        hold = None
        try:
            hold = float(t.get("exit_ts")) - float(t.get("entry_ts"))
        except (TypeError, ValueError):
            hold = None
        spread_cost = float(t.get("spread_paid") or 0.0)
        slippage_cost = float(t.get("slippage_points") or 0.0)
        pnl = float(t.get("pnl") or 0.0)
        # engine pnl already subtracts commission+swap; costs recorded as modelled
        out.append({
            "side": t.get("side"), "symbol": None,
            "entry_ts": t.get("entry_ts"), "entry_price": t.get("entry_price"),
            "exit_ts": t.get("exit_ts"), "exit_price": t.get("exit_price"),
            "volume": t.get("lots"), "sl": t.get("sl"), "tp": t.get("tp"),
            "exit_reason": t.get("exit_reason"),
            "hold_seconds": hold,
            "gross_pnl": None,
            "commission": None,
            "spread_cost": spread_cost,
            "slippage_cost": slippage_cost,
            "swap_cost": None,
            "net_pnl": pnl,
            "session": t.get("session"), "dow": t.get("dow"),
        })
    return out


# --------------------------------------------------------------------------- #
# §12 preference + statistics precedence
# --------------------------------------------------------------------------- #
def preferred_for(db: Any, node_id: int) -> Optional[Dict[str, Any]]:
    """The node's preferred scenario row (explicit choice first, else latest valid)."""
    pref = S.get_pref(db, node_id)
    if pref and pref.get("preferred_run_id"):
        sc = S.get_scenario(db, pref["preferred_run_id"])
        if sc and sc["status"] in VALID_FOR_PREFERENCE:
            sc["preference_source"] = "explicit"
            return sc
        if sc:
            # a stored explicit preference pointing at an invalid run is kept
            # as a record but never used silently
            pass
    valid = [r for r in S.list_scenarios(db, int(node_id)) if r["status"] in VALID_FOR_PREFERENCE]
    if valid:
        valid[0]["preference_source"] = "latest_valid"
        return valid[0]
    return None


def set_preferred(db: Any, node_id: int, run_id: str, explicit: bool = True) -> Dict[str, Any]:
    sc = S.get_scenario(db, run_id)
    if not sc:
        return {"ok": False, "error": f"unknown run {run_id}"}
    if int(sc["node_id"]) != int(node_id):
        return {"ok": False, "error": "that scenario belongs to a different node"}
    if sc["status"] not in VALID_FOR_PREFERENCE:
        return {"ok": False,
                "error": f"a {sc['status']} run cannot be preferred — only COMPLETED or "
                         "PARTIAL scenarios hold valid results"}
    S.set_pref(db, node_id, run_id, explicit=explicit)
    return {"ok": True, "preferred_run_id": run_id, "explicit": bool(explicit),
            "scenario": S.get_scenario(db, run_id)}


def node_deeptest_summary(db: Any, node_id: int) -> Dict[str, Any]:
    """Everything the DEEPTESTED-NODES / stats projection needs for one node."""
    rows = S.list_scenarios(db, int(node_id))
    valid = [r for r in rows if r["status"] in VALID_FOR_PREFERENCE]
    pref = preferred_for(db, node_id)
    return {
        "node_id": int(node_id),
        "scenario_count": len(rows),
        "valid_scenario_count": len(valid),
        "statuses": {s: sum(1 for r in rows if r["status"] == s) for s in VALID_STATUSES},
        "preferred": ({"run_id": pref["run_id"], "status": pref["status"],
                       "coverage_status": pref.get("coverage_status"),
                       "metrics": pref.get("metrics"), "analytics": pref.get("analytics"),
                       "duration_key": pref.get("duration_key"),
                       "finished_at": pref.get("finished_at"),
                       "preference_source": pref.get("preference_source")}
                      if pref else None),
        "last_run": ({"run_id": rows[0]["run_id"], "status": rows[0]["status"],
                      "finished_at": rows[0].get("finished_at")}
                     if rows else None),
    }


def compare(db: Any, run_ids: List[str]) -> Dict[str, Any]:
    """§13 — side-by-side comparison that EXPLICITLY shows config/coverage diffs."""
    if len(run_ids or []) < 2:
        return {"ok": False, "error": "at least two scenario run_ids are required"}
    rows = []
    for rid in run_ids:
        sc = S.get_scenario(db, rid)
        if not sc:
            return {"ok": False, "error": f"unknown run {rid}"}
        rows.append(sc)
    node_ids = {int(r["node_id"]) for r in rows}
    if len(node_ids) > 1:
        return {"ok": False, "error": "scenarios of different nodes cannot be compared"}
    a, b = rows[0], rows[1]

    def _set(r):
        s = r.get("settings") or {}
        prov = r.get("provenance") or {}
        return {
            "node_id": r["node_id"], "run_id": r["run_id"], "status": r["status"],
            "duration_key": r.get("duration_key"),
            "requested": (prov.get("requested_period") or
                          {"start_ts": r.get("requested_start"), "end_ts": r.get("requested_end")}),
            "actual_tested_range": prov.get("actual_tested_range"),
            "bars_used": r.get("bars_used"),
            "dataset_fingerprint": r.get("dataset_fingerprint"),
            "coverage_status": r.get("coverage_status"),
            "gaps_count": len(r.get("gaps") or []),
            "total_capital": s.get("total_capital"),
            "risk_pct": s.get("risk_pct"),
            "max_active_trades": s.get("max_active_trades_per_node"),
            "sl_offset_pips": s.get("sl_offset_pips"),
            "tp_offset_pips": s.get("tp_offset_pips"),
            "genome_hash": r.get("genome_hash"),
            "config_hash": r.get("config_hash"),
            "engine_versions": r.get("engine_versions"),
        }

    left, right = _set(a), _set(b)
    differences = []
    for k in left:
        if k in ("run_id",):
            continue
        if left.get(k) != right.get(k):
            differences.append({"field": k, "left": left.get(k), "right": right.get(k)})
    metrics_rows = []
    keys = ["net_profit", "net_return_pct", "max_drawdown_pct", "sharpe", "profit_factor",
            "trades", "win_rate", "expectancy_per_trade"]
    for k in keys:
        metrics_rows.append({
            "metric": k,
            "left": ((a.get("analytics") or {}).get("summary") or {}).get(k),
            "right": ((b.get("analytics") or {}).get("summary") or {}).get(k),
        })
    return {
        "ok": True,
        "scenarios": [left, right],
        "config_and_coverage_differences": differences,
        "metrics": metrics_rows,
        "warning": ("these scenarios differ in the fields listed above — do not treat "
                    "their metrics as directly equivalent") if differences else
                   "same configuration and coverage binding for the compared fields",
    }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _load_node(db: Any, node_id: int) -> Dict[str, Any]:
    """The node's own generation data — the authoritative strategies row
    (same universe as the nodes index; never a second qualification rule)."""
    row = db.one("SELECT * FROM strategies WHERE id=?", (int(node_id),))
    if not row:
        raise ValueError(f"node {node_id} is not in the authoritative inventory")
    r = dict(row)
    g = r.get("genome") or {}
    if isinstance(g, str):
        try:
            g = json.loads(g)
        except Exception:
            g = {}
    # READ-ONLY view of the node's live config (for default display semantics);
    # Deep Testing never writes it.
    cfg = {}
    try:
        cfg_row = db.one("SELECT * FROM live_test_configs WHERE strategy_id=?", (int(node_id),))
        cfg = dict(cfg_row) if cfg_row else {}
    except Exception:
        cfg = {}
    symbol = str(g.get("symbol") or r.get("symbol") or "").upper()
    timeframe = str(g.get("timeframe") or r.get("timeframe") or "").upper()
    if not symbol or not timeframe:
        raise ValueError(f"node {node_id} has no symbol/timeframe in its generation data")
    return {"node_id": int(node_id), "symbol": symbol, "timeframe": timeframe,
            "genome": g, "config": cfg,
            "research_node_num": r.get("research_node_num")}


def _genome_hash(g: Any) -> str:
    try:
        return hashlib.sha256(json.dumps(g, sort_keys=True, default=str).encode()).hexdigest()[:16]
    except Exception:
        return ""


def _product_version() -> str:
    try:
        from ..versions import PRODUCT_RELEASE
        return PRODUCT_RELEASE
    except Exception:
        return "unknown"
