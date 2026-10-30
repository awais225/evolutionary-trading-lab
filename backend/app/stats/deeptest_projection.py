"""V6.6 §12 — the ONE shared statistics-priority projection.

Precedence for a node's PERFORMANCE statistics (never for live operational
metrics like status, open P&L, positions, account state — those stay live):

1. the explicitly preferred DeepTest scenario, when one is set and still valid;
2. otherwise the latest valid DeepTest scenario;
3. otherwise the node's generation backtest metrics;
4. otherwise N/A (None) — never 0, never a fabricated value.

Both DeepTesting and the LIVE TESTING performance-statistics display read this
same projection, so the numbers can never disagree. Generation metrics are kept
separately (``generation_metrics``) with provenance and never disappear.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

#: the field contract of ``nodes_index`` rows' ``metrics`` block
STAT_FIELDS = ("return_pct", "profit_factor", "max_drawdown_pct", "win_rate",
               "trades", "net_profit", "expectancy", "avg_trade", "sharpe")

SOURCE_DEEPTEST_PREFERRED = "deeptest_preferred"
SOURCE_DEEPTEST_LATEST = "deeptest_latest_valid"
SOURCE_GENERATION = "generation"

SOURCE_LABELS = {
    SOURCE_DEEPTEST_PREFERRED: "DeepTest (preferred scenario)",
    SOURCE_DEEPTEST_LATEST: "DeepTest (latest valid scenario)",
    SOURCE_GENERATION: "generation backtest",
    "none": "N/A — no valid DeepTest or generation metrics",
}


def _deeptest_metrics(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Map a DeepTest analytics summary onto the STAT_FIELDS contract."""
    s = summary or {}
    return {
        "return_pct": s.get("net_return_pct"),
        "profit_factor": s.get("profit_factor"),
        "max_drawdown_pct": s.get("max_drawdown_pct"),
        "win_rate": s.get("win_rate"),
        "trades": s.get("trades"),
        "net_profit": s.get("net_profit"),
        "expectancy": s.get("expectancy_per_trade"),
        "avg_trade": s.get("expectancy_per_trade"),
        "sharpe": s.get("sharpe"),
    }


def _generation_metrics(m: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "return_pct": m.get("return_pct"),
        "profit_factor": m.get("profit_factor"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "win_rate": m.get("win_rate"),
        "trades": m.get("trades"),
        "net_profit": m.get("net_profit"),
        "expectancy": m.get("expectancy"),
        "avg_trade": m.get("avg_trade"),
        "sharpe": m.get("sharpe"),
    }


def empty_metrics() -> Dict[str, Any]:
    return {k: None for k in STAT_FIELDS}


def effective_metrics(deeptest: Optional[Dict[str, Any]],
                      generation: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], str, str]:
    """Apply §12's precedence. Returns (metrics, source_key, source_label)."""
    if deeptest and deeptest.get("analytics"):
        summary = (deeptest.get("analytics") or {}).get("summary") or {}
        metrics = _deeptest_metrics(summary)
        src = (SOURCE_DEEPTEST_PREFERRED
               if deeptest.get("preference_source") == "explicit" else SOURCE_DEEPTEST_LATEST)
        return metrics, src, SOURCE_LABELS[src]
    if generation and any(generation.get(k) is not None for k in STAT_FIELDS):
        return _generation_metrics(generation), SOURCE_GENERATION, SOURCE_LABELS[SOURCE_GENERATION]
    return empty_metrics(), "none", SOURCE_LABELS["none"]


def project_node(db: Any, node_id: int,
                 generation_metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The projection for ONE node (the single source of truth for §12 stats)."""
    from ..deeptest import scenarios as SC
    try:
        pref = SC.preferred_for(db, int(node_id))
    except Exception:
        pref = None
    deeptest_block = None
    if pref and pref.get("analytics"):
        deeptest_block = {
            "run_id": pref.get("run_id"),
            "status": pref.get("status"),
            "coverage_status": pref.get("coverage_status"),
            "duration_key": pref.get("duration_key"),
            "finished_at": pref.get("finished_at"),
            "preference_source": pref.get("preference_source"),
            **{k:pref.get(k) for k in ("dataset_id","dataset_fingerprint","actual_start","actual_end")},
            "analytics": pref.get("analytics"),
            "metrics": pref.get("metrics"),
        }
    metrics, src, label = effective_metrics(deeptest_block, generation_metrics)
    return {
        "metrics": metrics,
        "metrics_source": src,
        "metrics_source_label": label,
        "generation_metrics": _generation_metrics(generation_metrics) if generation_metrics else None,
        "deeptest": {
            "preferred_run_id": deeptest_block["run_id"] if deeptest_block else None,
            "preferred_status": deeptest_block["status"] if deeptest_block else None,
            "coverage_status": deeptest_block["coverage_status"] if deeptest_block else None,
            "preference_source": deeptest_block["preference_source"] if deeptest_block else None,
            **({k:deeptest_block.get(k) for k in ("dataset_id","dataset_fingerprint","actual_start","actual_end","finished_at")} if deeptest_block else {}),
            "last_run": ({"run_id": deeptest_block["run_id"],
                          "finished_at": deeptest_block["finished_at"]}
                         if deeptest_block else None),
        } if deeptest_block else None,
    }


def build_index(db: Any) -> Dict[int, Dict[str, Any]]:
    """ONE-pass index over the DeepTest tables, for endpoints that serve many
    nodes (``nodes_index``). Maps node_id -> {preferred, latest_valid} light
    blocks (no full analytics payload — full detail stays on /api/deeptest/*).
    """
    index: Dict[int, Dict[str, Any]] = {}
    try:
        from ..deeptest import store as S
        S.ensure_schema(db)
        prefs = {int(r["node_id"]): dict(r)
                 for r in (db.q("SELECT * FROM deeptest_prefs") or [])}
        rows = db.q("""SELECT run_id, node_id, status, coverage_status, duration_key,
                              finished_at, metrics, analytics, dataset_id, dataset_fingerprint, actual_start, actual_end, created_at
                       FROM deeptest_scenarios
                       WHERE status IN ('COMPLETED','PARTIAL')
                       ORDER BY node_id, created_at DESC, run_id DESC""") or []
        from ..deeptest.metric_service import enrich_many
        rows=enrich_many(db,[dict(r) for r in rows])
        by_node: Dict[int, List[Dict[str, Any]]] = {}
        for r in rows:
            d = dict(r)
            try:
                d["analytics"] = (json.loads(d["analytics"]) if isinstance(d.get("analytics"),str) else d.get("analytics"))
            except Exception:
                d["analytics"] = None
            if not (d.get('analytics') or {}).get('summary'):continue
            by_node.setdefault(int(d["node_id"]), []).append(d)
        for nid, lst in by_node.items():
            pref = prefs.get(nid) or {}
            chosen = None
            source = None
            if pref.get("preferred_run_id") and int(pref.get("explicitly_selected") or 0):
                for d in lst:
                    if d["run_id"] == pref["preferred_run_id"]:
                        chosen, source = d, "explicit"
                        break
            if chosen is None and lst:
                chosen, source = lst[0], "latest_valid"
            index[nid] = {
                "preferred": chosen,
                "preference_source": source,
                "valid_count": len(lst),
            }
    except Exception:
        return {}
    return index


def effective_for(index: Dict[int, Dict[str, Any]], node_id: int,
                  generation_metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The §12 projection for one node from a prebuilt index."""
    entry = index.get(int(node_id))
    deeptest_block = None
    if entry and entry.get("preferred"):
        p = entry["preferred"]
        deeptest_block = {"run_id": p.get("run_id"), "status": p.get("status"),
                          "coverage_status": p.get("coverage_status"),
                          "duration_key": p.get("duration_key"),
                          "finished_at": p.get("finished_at"),
                          "preference_source": entry.get("preference_source"),
                          "analytics": p.get("analytics"),**{k:p.get(k) for k in ("dataset_id","dataset_fingerprint","actual_start","actual_end","finished_at")}}
    metrics, src, label = effective_metrics(deeptest_block, generation_metrics)
    return {
        "metrics": metrics,
        "metrics_source": src,
        "metrics_source_label": label,
        "generation_metrics": _generation_metrics(generation_metrics) if generation_metrics else None,
        "deeptest": {
            "preferred_run_id": deeptest_block["run_id"] if deeptest_block else None,
            "preferred_status": deeptest_block["status"] if deeptest_block else None,
            "coverage_status": deeptest_block["coverage_status"] if deeptest_block else None,
            "preference_source": deeptest_block["preference_source"] if deeptest_block else None,
            **({k:deeptest_block.get(k) for k in ("dataset_id","dataset_fingerprint","actual_start","actual_end","finished_at")} if deeptest_block else {}),
        } if deeptest_block else None,
    }


def project_many(db: Any, rows: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Project a nodes_index row list. One table scan, then per-row mapping."""
    index = build_index(db)
    out: Dict[int, Dict[str, Any]] = {}
    for r in rows or []:
        nid = int(r.get("node_id") or r.get("strategy_id") or 0)
        if not nid:
            continue
        out[nid] = effective_for(index, nid, generation_metrics=r.get("generation_metrics_parsed")
                                 or r.get("_gen_metrics"))
    return out
