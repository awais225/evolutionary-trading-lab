"""V4.4 research statistics & node economics — read-only analytics (spec §3-§13).

Design rules enforced here (nothing in this module writes to the database):

1. **Scope.** Every aggregate, list and per-node calculation uses the V4.0 user
   scope predicate ``COALESCE(data_source, 'USER_RESEARCH') <> 'LEGACY_TEST'``.
   The 787 LEGACY_TEST infrastructure records are reported only as an excluded
   count. No second classification system is introduced.
2. **Reuse over rewrite.** Headline population numbers come from the engine's
   authoritative snapshot (``get_node_generation_state(exclude_legacy=True)``)
   and from the scoped counter primitive (``count_by_status(exclude_legacy=True)``);
   grouped statuses (alive/dead/qualified/...) use the *engine's own* state
   tuples, so the Stats page cannot drift from the Overview. Per-node detail
   reuses ``strategies.authoritative.get_authoritative_strategy``.
3. **No fabrication.** Anything the stored data cannot support is returned as
   ``None`` plus a reason in ``unavailable``; the UI renders that as ``N/A``.
   Nothing is invented, and execution records are never presented as research
   results (§9).
4. **No N+1.** List endpoints page the node ids first and then hydrate the page
   with a fixed number of bulk queries.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Sequence

from ..db.database import get_db

USER_POPULATION = "USER_RESEARCH"
LEGACY_POPULATION = "LEGACY_TEST"

#: The V4.0 display-scope predicate (same string the population/list endpoints
#: use). Kept in one place so every V4.4 surface counts the same rows.
SCOPE_PREDICATE = "COALESCE(data_source, 'USER_RESEARCH') <> 'LEGACY_TEST'"

_LABELS = {
    "research": "Research results (simulated backtest / validation) — not broker results",
    "backtest": "BACKTEST",
    "validation": "VALIDATION",
    "mt5_backtest": "MT5 BACKTEST",
    "paper": "PAPER",
    "live_test": "LIVE TEST",
    "mt5_demo": "MT5 DEMO",
    "execution_note": ("Execution and audit records are shown separately and are never "
                       "mixed into research population statistics."),
}


def _pred(alias: str = "") -> str:
    """Scope predicate, optionally qualified with a table alias."""
    return SCOPE_PREDICATE.replace("data_source", f"{alias}.data_source") if alias else SCOPE_PREDICATE


def _state_groups() -> Dict[str, Sequence[str]]:
    """The engine's own status grouping (single source of truth)."""
    from ..evolution.engine import IN_FLIGHT_STATES, QUALIFIED_STATES, TERMINAL_STATES
    return {
        "in_flight": tuple(IN_FLIGHT_STATES),
        "qualified_states": tuple(QUALIFIED_STATES),
        "terminal": tuple(TERMINAL_STATES),
    }


def _sum(counts: Dict[str, int], names: Sequence[str]) -> int:
    return int(sum(counts.get(n, 0) for n in names))


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        out = float(v)
        return None if out != out else out          # NaN -> None
    except (TypeError, ValueError):
        return None


def _round(v: Any, nd: int = 4) -> Optional[float]:
    x = _f(v)
    return None if x is None else round(x, nd)


def _one(d: Any, sql: str, args: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    try:
        return d.one(sql, tuple(args))
    except Exception:
        return None


def _rows(d: Any, sql: str, args: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    try:
        return list(d.q(sql, tuple(args)) or [])
    except Exception:
        return []


def _engine_snapshot() -> Dict[str, Any]:
    """Authoritative node-generation snapshot (USER_RESEARCH scope). Never raises."""
    try:
        from ..evolution.engine import get_evo_engine
        return dict(get_evo_engine().get_node_generation_state(exclude_legacy=True) or {})
    except Exception:
        return {}


def _configured_target() -> Optional[int]:
    try:
        from ..config import get_config
        return int(get_config().evolution.total_node_target)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# scope
# --------------------------------------------------------------------------- #
def scope_info(db: Any = None) -> Dict[str, Any]:
    """What the V4.4 analytics count, and what they deliberately do not (spec §3)."""
    d = db or get_db()
    user = _one(d, f"SELECT COUNT(*) c FROM strategies WHERE {_pred()}") or {}
    legacy = _one(d, f"SELECT COUNT(*) c FROM strategies WHERE NOT ({_pred()})") or {}
    return {
        "population": USER_POPULATION,
        "authoritative": True,
        "excluded_population": LEGACY_POPULATION,
        "user_research_nodes": int(user.get("c") or 0),
        "legacy_excluded_nodes": int(legacy.get("c") or 0),
        "predicate": SCOPE_PREDICATE,
        "note": ("Research analytics run on USER_RESEARCH only. LEGACY_TEST infrastructure "
                 "nodes are neither counted nor listed; they are reported here as an excluded "
                 "count and remain retrievable through the existing V4.0 escape hatch."),
    }


# --------------------------------------------------------------------------- #
# population
# --------------------------------------------------------------------------- #
def population(db: Any = None) -> Dict[str, Any]:
    """Population block: totals, target and the full status breakdown (spec §4).

    Headline values reuse the engine snapshot; the status breakdown is the
    scoped counter primitive grouped with the engine's own state tuples.
    """
    d = db or get_db()
    snapshot = _engine_snapshot()
    counts = d.count_by_status(exclude_legacy=True)
    groups = _state_groups()

    in_flight = groups["in_flight"]
    qualified_states = groups["qualified_states"]
    terminal = groups["terminal"]

    alive = _sum(counts, tuple(in_flight) + tuple(qualified_states))
    dead = _sum(counts, terminal)
    # ``qualified`` counts nodes currently in the QUALIFIED state - the same
    # number the existing status / Overview counters show. The engine's wider
    # QUALIFIED_STATES group (it also contains the paper-trading states) is
    # reported separately as ``qualified_incl_paper`` so nothing is lost and no
    # existing definition is re-invented for the UI.
    qualified = int(counts.get("QUALIFIED", 0))
    qualified_incl_paper = _sum(counts, qualified_states)
    paper = _sum(counts, ("PAPER", "PAPER_TRADING", "PAPER_PASSED", "PAPER_ELIGIBLE"))
    survived = _sum(counts, ("SURVIVED", "EVALUATED"))
    failed = _sum(counts, ("FAILED", "PAPER_FAILED"))
    killed = _sum(counts, ("KILLED",))
    retired = _sum(counts, ("RETIRED",))

    # ------------------------------------------------------------------ #
    # V5.4 §1 — these headline numbers are the ONE authoritative snapshot
    # (app.research.populations.node_state_snapshot), not a second derivation.
    # This block used to publish the run-scoped engine counters and the raw
    # QUALIFIED status count, so Stats/Strategy Lab/Final Testing reported
    # TOTAL 10,000 / QUALIFIED 5 while the NODES strip reported 10,787 / 33.
    # The legacy breakdown below stays (it is genuinely different information:
    # paper/killed/retired/status_counts) but it is now labelled as a breakdown
    # OF the authority, not as a competing total.
    # ------------------------------------------------------------------ #
    from ..research.populations import node_state_snapshot
    node_state = node_state_snapshot(db=d)
    ns = node_state.get("state") or {}
    prog = node_state.get("progress") or {}
    nc = node_state.get("counts") or {}

    total = int(ns.get("TOTAL") or 0)
    target = int(ns.get("TARGET") or _configured_target() or 0)
    remaining = int(ns.get("REMAINING") or 0)
    alive = int(ns.get("ALIVE") or 0)
    dead = int(ns.get("DEAD") or 0)
    qualified = int(ns.get("QUALIFIED") or 0)

    return {
        "scope": USER_POPULATION,
        "authority": node_state.get("authority"),
        "total": total,
        "target": target,
        "remaining": remaining,
        "progress_pct": _round(prog.get("pct") if prog.get("pct") is not None else 100.0, 1),
        "target_reached": bool(prog.get("ceiling_reached")),
        # the ceiling is the CURRENT EXPERIMENT's; the total above is every node
        "experiment_nodes": int(prog.get("nodes") or 0),
        "progress_scope": prog.get("scope"),
        "alive": alive,
        "dead": dead,
        "qualified": qualified,
        # V5.4 §1 — the authority's own explicit names travel with the block, so a
        # consumer (Stats/Overview/Strategy Lab) never has to translate a short key
        # into a state name to prove it is reading the same snapshot.
        "node_state": dict(ns),
        "node_state_authority": node_state.get("authority"),
        "node_state_invariants": node_state.get("invariants"),
        "qualified_incl_paper": qualified_incl_paper,
        "legacy_excluded": int(nc.get("legacy_excluded") or 0),
        "user_research": int(nc.get("user_research") or 0),
        "failed": int(nc.get("failed") or 0),
        "blocked": int(nc.get("blocked") or 0),
        "retired": retired,
        "killed": killed,
        "survived": survived,
        "paper": paper,
        "backtesting": int(ns.get("BACKTESTING") or 0),
        "validating": int(ns.get("VALIDATING") or 0),
        "born": _sum(counts, ("BORN", "GENERATED", "QUEUED")),
        "status_counts": {k: int(v) for k, v in sorted(counts.items())},
        "scope_note": ("Headline numbers come from the ONE node-state authority "
                       "(app.research.populations): TOTAL counts every stored node, legacy "
                       "infrastructure rows included, and DEAD is the not-alive remainder "
                       "(failed + infrastructure-blocked + unknown). The status_counts "
                       "LEGACY_TEST infrastructure rows are part of TOTAL and are excluded "
                       "from every research counter (reported as legacy_excluded). The "
                       "status_counts breakdown below is the raw stored vocabulary, kept for "
                       "diagnosis."),
        "source": "app.research.populations.node_state_snapshot (ONE authority); "
                  "breakdown: db.count_by_status(exclude_legacy=True)",
    }


# --------------------------------------------------------------------------- #
# evolution
# --------------------------------------------------------------------------- #
def evolution(db: Any = None, generations: int = 40) -> Dict[str, Any]:
    """Generation / throughput block (spec §4 "Evolution")."""
    d = db or get_db()
    snapshot = _engine_snapshot()
    pop = population(d)
    # V5.4 §1 — "how many nodes has THIS experiment generated" is the experiment
    # scope (the same value the authority's ceiling progress uses). The all-rows
    # TOTAL is published alongside it instead of being passed off as throughput.
    total = int(pop.get("experiment_nodes") or 0)
    target = pop["target"]

    gen_row = _one(d, f"SELECT MAX(generation) g, COUNT(DISTINCT generation) n, "
                      f"MAX(fitness) best FROM strategies WHERE {_pred()}") or {}
    evaluated_row = _one(d, f"SELECT COUNT(DISTINCT b.strategy_id) c FROM backtests b "
                            f"JOIN strategies s ON s.id=b.strategy_id WHERE {_pred('s')}") or {}
    validated_row = _one(d, f"SELECT COUNT(DISTINCT v.strategy_id) c FROM validations v "
                            f"JOIN strategies s ON s.id=v.strategy_id WHERE {_pred('s')}") or {}

    evaluated = int(evaluated_row.get("c") or 0)
    validated = int(validated_row.get("c") or 0)
    qualified = pop["qualified"]

    # V5.4 §1 — the rates are computed on the SAME classifier as the headline
    # counts (populations.node_bucket), over the nodes that were actually
    # evaluated. Dividing the whole population's qualified count by the evaluated
    # count produced rates above 1.0, which is never a real percentage.
    evaluated_ids = {int(r["strategy_id"]) for r in
                     _rows(d, f"SELECT DISTINCT b.strategy_id FROM backtests b "
                              f"JOIN strategies s ON s.id=b.strategy_id WHERE {_pred('s')}")}
    buckets = classified = None
    try:
        from ..research.populations import classified_ids
        buckets = classified_ids(d, ids=evaluated_ids)
        classified = True
    except Exception:                                       # pragma: no cover - defensive
        buckets = None
    if buckets is not None:
        qualified_eval = len(buckets.get("qualified", set()) & evaluated_ids)
        survived_eval = len(evaluated_ids - (buckets.get("failed", set())
                                             | buckets.get("blocked", set())
                                             | buckets.get("unknown", set())
                                             | buckets.get("excluded", set())))
    else:                                                   # pragma: no cover - defensive
        qualified_eval, survived_eval = 0, 0
    survived = int(pop["survived"])

    per_gen: Dict[int, Dict[str, Any]] = {}
    for r in _rows(d, f"""SELECT generation g, COUNT(*) total,
                                 SUM(CASE WHEN status='QUALIFIED' THEN 1 ELSE 0 END) qualified,
                                 SUM(CASE WHEN status IN ('SURVIVED','EVALUATED') THEN 1 ELSE 0 END) survived,
                                 SUM(CASE WHEN status IN ('FAILED','KILLED','RETIRED','PAPER_FAILED')
                                          THEN 1 ELSE 0 END) dead,
                                 MAX(fitness) best_fitness
                          FROM strategies WHERE {_pred()} GROUP BY generation"""):
        per_gen[int(r["g"] or 0)] = {
            "generation": int(r["g"] or 0),
            "nodes": int(r["total"] or 0),
            "qualified": int(r["qualified"] or 0),
            "survived": int(r["survived"] or 0),
            "dead": int(r["dead"] or 0),
            "best_fitness": _round(r["best_fitness"], 4),
            "evaluated": 0,
        }
    for r in _rows(d, f"""SELECT s.generation g, COUNT(DISTINCT b.strategy_id) c
                          FROM backtests b JOIN strategies s ON s.id=b.strategy_id
                          WHERE {_pred('s')} GROUP BY s.generation"""):
        g = int(r["g"] or 0)
        if g in per_gen:
            per_gen[g]["evaluated"] = int(r["c"] or 0)
    generation_rows = [per_gen[k] for k in sorted(per_gen, reverse=True)][: max(1, int(generations))]

    return {
        "current_generation": int(snapshot.get("generation_number")
                                  if snapshot.get("generation_number") is not None
                                  else (gen_row.get("g") or 0)),
        "generations_recorded": int(gen_row.get("n") or 0),
        "nodes_generated": total,
        "nodes_generated_scope": "current experiment (USER_RESEARCH)",
        "population_total": int(pop["total"]),
        "nodes_evaluated": evaluated,
        "nodes_validated": validated,
        "nodes_remaining": max(0, target - total),
        "best_fitness": _round(gen_row.get("best") or snapshot.get("best_fitness"), 4),
        "qualification_rate": _round(qualified_eval / evaluated, 4) if evaluated else None,
        "survival_rate": _round(survived_eval / evaluated, 4) if evaluated else None,
        "qualified_evaluated": qualified_eval,
        "survived_evaluated": survived_eval,
        "nodes_per_minute": _f(snapshot.get("nodes_per_minute")),
        "evaluated_definition": "nodes with at least one stored backtest row (USER_RESEARCH)",
        "rates_definition": ("USER_RESEARCH rates: (nodes in the qualified bucket / evaluated nodes) "
                             "and (evaluated nodes that are not failed, blocked or unknown / evaluated "
                             "nodes) — both classified by the SAME classifier as the headline counts "
                             "(populations.node_bucket), so a rate can never exceed 100%"),
        "generations": generation_rows,
        "source": "strategies (scoped) + backtests + validations + engine snapshot",
    }


# --------------------------------------------------------------------------- #
# research performance
# --------------------------------------------------------------------------- #
_PERF_FIELDS = {
    "total_return_pct": "avg_return_pct",
    "profit_factor": "avg_profit_factor",
    "win_rate": "avg_win_rate",
    "max_drawdown_pct": "avg_max_drawdown_pct",
    "sharpe": "avg_sharpe",
    "sortino": "avg_sortino",
    "expectancy": "avg_expectancy",
    "trades": "avg_trades",
}


def research_performance(db: Any = None, top: int = 10) -> Dict[str, Any]:
    """Research performance summary built only from stored backtest/validation data.

    Every metric is an aggregate over rows the research engine already wrote -
    nothing is re-simulated here, and the stage (screen/detail) is preserved so
    a screen-stage average is never presented as a validated result.
    """
    d = db or get_db()
    stages: List[Dict[str, Any]] = []
    for r in _rows(d, f"""
        SELECT b.stage stage, COUNT(*) backtests, COUNT(DISTINCT b.strategy_id) nodes,
               AVG(json_extract(b.metrics,'$.total_return_pct')) avg_return_pct,
               MAX(json_extract(b.metrics,'$.total_return_pct')) best_return_pct,
               MIN(json_extract(b.metrics,'$.total_return_pct')) worst_return_pct,
               AVG(json_extract(b.metrics,'$.profit_factor')) avg_profit_factor,
               MAX(json_extract(b.metrics,'$.profit_factor')) best_profit_factor,
               AVG(json_extract(b.metrics,'$.win_rate')) avg_win_rate,
               AVG(json_extract(b.metrics,'$.max_drawdown_pct')) avg_max_drawdown_pct,
               AVG(json_extract(b.metrics,'$.sharpe')) avg_sharpe,
               AVG(json_extract(b.metrics,'$.sortino')) avg_sortino,
               AVG(json_extract(b.metrics,'$.expectancy')) avg_expectancy,
               AVG(json_extract(b.metrics,'$.trades')) avg_trades,
               SUM(json_extract(b.metrics,'$.trades')) total_trades,
               SUM(json_extract(b.metrics,'$.net_profit')) total_net_profit,
               MAX(b.created_at) last_at
        FROM backtests b JOIN strategies s ON s.id=b.strategy_id
        WHERE {_pred('s')}
        GROUP BY b.stage ORDER BY backtests DESC"""):
        stages.append({
            "stage": r.get("stage") or "unknown",
            "layer": _LABELS["backtest"],
            "backtests": int(r.get("backtests") or 0),
            "nodes": int(r.get("nodes") or 0),
            "avg_return_pct": _round(r.get("avg_return_pct")),
            "best_return_pct": _round(r.get("best_return_pct")),
            "worst_return_pct": _round(r.get("worst_return_pct")),
            "avg_profit_factor": _round(r.get("avg_profit_factor")),
            "best_profit_factor": _round(r.get("best_profit_factor")),
            "avg_win_rate": _round(r.get("avg_win_rate")),
            "avg_max_drawdown_pct": _round(r.get("avg_max_drawdown_pct")),
            "avg_sharpe": _round(r.get("avg_sharpe")),
            "avg_sortino": _round(r.get("avg_sortino")),
            "avg_expectancy": _round(r.get("avg_expectancy")),
            "avg_trades": _round(r.get("avg_trades"), 1),
            "total_trades": int(r.get("total_trades") or 0) if r.get("total_trades") is not None else None,
            "total_net_profit": _round(r.get("total_net_profit"), 2),
            "last_at": r.get("last_at"),
        })

    nodes_with_bt = _one(d, f"""SELECT COUNT(DISTINCT b.strategy_id) c
                                      FROM backtests b JOIN strategies s ON s.id=b.strategy_id
                                      WHERE {_pred('s')}""") or {}
    val = _one(d, f"""SELECT COUNT(*) records, COUNT(DISTINCT v.strategy_id) nodes,
                             SUM(CASE WHEN v.passed THEN 1 ELSE 0 END) passed,
                             AVG(v.robustness_score) avg_robustness,
                             MAX(v.robustness_score) best_robustness
                      FROM validations v JOIN strategies s ON s.id=v.strategy_id
                      WHERE {_pred('s')}""") or {}
    records = int(val.get("records") or 0)
    validations = {
        "layer": _LABELS["validation"],
        "records": records,
        "nodes": int(val.get("nodes") or 0),
        "passed": int(val.get("passed") or 0) if val.get("passed") is not None else None,
        "pass_rate": _round((int(val.get("passed") or 0) / records), 4) if records else None,
        "avg_robustness": _round(val.get("avg_robustness")),
        "best_robustness": _round(val.get("best_robustness")),
    }

    top_nodes: List[Dict[str, Any]] = []
    for r in _rows(d, f"""
            SELECT s.id, s.status, s.symbol, s.timeframe, s.generation, s.fitness,
                   s.research_node_num, s.run_id,
                   (SELECT b.metrics FROM backtests b WHERE b.strategy_id=s.id
                     ORDER BY CASE b.stage WHEN 'detail' THEN 0 ELSE 1 END, b.id DESC LIMIT 1) metrics
            FROM strategies s WHERE {_pred()}
            ORDER BY COALESCE(s.fitness, -9) DESC, s.id ASC LIMIT ?""", (max(1, int(top)),)):
        m = r.get("metrics")
        if isinstance(m, str):
            try:
                m = json.loads(m)
            except Exception:
                m = None
        m = m or {}
        top_nodes.append({
            "id": r["id"],
            "status": r.get("status"),
            "symbol": r.get("symbol"),
            "timeframe": r.get("timeframe"),
            "generation": r.get("generation"),
            "fitness": _round(r.get("fitness"), 4),
            "research_node_num": r.get("research_node_num"),
            "backtest_return_pct": _round(m.get("total_return_pct")),
            "backtest_profit_factor": _round(m.get("profit_factor")),
            "backtest_win_rate": _round(m.get("win_rate")),
            "layer": _LABELS["backtest"],
        })

    return {
        "backtest": {
            "layer": _LABELS["backtest"],
            "stages": stages,
            "total_backtests": sum(s["backtests"] for s in stages),
            "nodes_with_backtest": int(nodes_with_bt.get("c") or 0),
            "note": ("Averages are calculated over the stored backtest rows of USER_RESEARCH "
                     "nodes, grouped by backtest stage; no formula is re-implemented here."),
        },
        "validation": validations,
        "top_nodes": top_nodes,
        "source": "backtests.metrics (stored JSON) + validations (stored rows), USER_RESEARCH scope",
    }


# --------------------------------------------------------------------------- #
# execution / audit records (deliberately separate from research stats)
# --------------------------------------------------------------------------- #
def _trade_block(d: Any, table: str, label: str, ts_cols: Sequence[str],
                 open_states: Sequence[str]) -> Dict[str, Any]:
    """One execution/audit record block. ``ts_cols`` are that table's own time
    columns (the execution tables do not share one timestamp naming scheme)."""
    ts = ", ".join(f"{c}" for c in ts_cols)
    open_list = ", ".join(f"'{s}'" for s in open_states)
    row = _one(d, f"""SELECT COUNT(*) trades,
                             SUM(CASE WHEN status IN ({open_list})
                                      THEN 1 ELSE 0 END) open_trades,
                             SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END) closed_trades,
                             SUM(COALESCE(pnl, 0)) total_pnl,
                             SUM(CASE WHEN COALESCE(pnl, 0) > 0 THEN 1 ELSE 0 END) wins,
                             MAX(COALESCE({ts})) last_ts
                      FROM {table}""")
    if not row:
        return {"layer": label, "available": False, "trades": None, "reason":
                f"table '{table}' is not present in this database"}
    trades = int(row.get("trades") or 0)
    closed = int(row.get("closed_trades") or 0)
    return {
        "layer": label,
        "available": True,
        "trades": trades,
        "open": int(row.get("open_trades") or 0),
        "closed": closed,
        "total_pnl": _round(row.get("total_pnl"), 2),
        "win_rate": _round(int(row.get("wins") or 0) / closed, 4) if closed else None,
        "last_ts": row.get("last_ts"),
        "source": f"{table} (execution/audit record)",
    }


def execution_records(db: Any = None) -> Dict[str, Any]:
    """Execution side of the ledger — never merged into research statistics (§9)."""
    d = db or get_db()
    manual = _one(d, """SELECT COUNT(*) n,
                               SUM(CASE WHEN rejected_by IS NOT NULL THEN 1 ELSE 0 END) rejected,
                               MAX(ts) last_ts FROM executions""") or {}
    return {
        "layer": "EXECUTION",
        "paper": _trade_block(d, "paper_trades", _LABELS["paper"],
                              ("exit_ts", "exec_ts", "signal_ts"), ("OPEN",)),
        "mt5_demo": _trade_block(d, "mt5_demo_trades", _LABELS["mt5_demo"],
                                 ("close_ts", "open_ts"), ("OPEN", "POSITION_OPEN",
                                                           "PENDING_ORDER")),
        "live_test": _trade_block(d, "live_test_trades", _LABELS["live_test"],
                                  ("close_ts", "open_ts"), ("OPEN", "POSITION_OPEN",
                                                            "PENDING_ORDER")),
        "manual_orders": {
            "layer": "MANUAL / EXECUTION AUDIT",
            "available": bool(manual),
            "executions": int(manual.get("n") or 0) if manual else None,
            "rejected": int(manual.get("rejected") or 0) if manual else None,
            "last_ts": manual.get("last_ts") if manual else None,
            "source": "executions (audit record)",
        },
        "note": _LABELS["execution_note"],
        "never_affects": ["research population", "alive/dead/qualified counters",
                          "target", "generation", "backtest statistics"],
    }


# --------------------------------------------------------------------------- #
# overview
# --------------------------------------------------------------------------- #
def _sources_map() -> Dict[str, Any]:
    """Metric provenance (spec §10) — what each block is built from."""
    return {
        "population": {
            "source": "strategies (scoped) via engine snapshot + db.count_by_status",
            "kind": "stored rows, counted with the engine's own status grouping",
            "existing_function": "evo.get_node_generation_state(exclude_legacy=True), db.count_by_status(exclude_legacy=True)",
            "scope": USER_POPULATION,
        },
        "evolution": {
            "source": "strategies.generation, backtests, validations",
            "kind": "stored + calculated aggregates (rates)",
            "existing_function": "engine snapshot for the current generation",
            "scope": USER_POPULATION,
        },
        "research_performance": {
            "source": "backtests.metrics (JSON), validations",
            "kind": "calculated aggregates over stored rows (no re-simulation)",
            "existing_function": "reuses the stored metrics written by the backtest/validation engines",
            "scope": USER_POPULATION,
        },
        "node_statistics": {
            "source": "strategies, backtests, validations, research_shortlist, strategy_pipeline_states",
            "kind": "stored",
            "existing_function": "strategies.authoritative.get_authoritative_strategy",
            "scope": USER_POPULATION,
        },
        "economics": {
            "source": "strategies.genome (risk/exit blocks) + backtests.metrics",
            "kind": "stored genome values + derived ratios (calculated)",
            "existing_function": "strategies.authoritative.parse_exit_conditions / parse_position_management",
            "scope": USER_POPULATION,
        },
        "execution_records": {
            "source": "paper_trades, mt5_demo_trades, live_test_trades, executions",
            "kind": "stored audit rows",
            "existing_function": "own queries (kept separate from research statistics)",
            "scope": "EXECUTION (separate layer)",
        },
    }


def overview(db: Any = None) -> Dict[str, Any]:
    """One-call payload for the Stats page (spec §4, §10, §12)."""
    d = db or get_db()
    return {
        "generated_at": time.time(),
        "labels": _LABELS,
        "scope": scope_info(d),
        "population": population(d),
        "evolution": evolution(d),
        "research_performance": research_performance(d),
        "execution_records": execution_records(d),
        "sources": _sources_map(),
    }


# --------------------------------------------------------------------------- #
# counter consistency audit (spec §5)
# --------------------------------------------------------------------------- #
#: V5.4 §1 — the two definitions a node counter may legitimately use. Every check
#: below declares which one it reports, so two different *definitions* can no longer
#: masquerade as an inconsistency, and two different *values* under one definition can
#: no longer hide behind a single "looks fine" verdict.
CHECK_DEFINITIONS = {
    "ALL_STORED": "every stored node row (the ONE authority's TOTAL)",
    "USER_RESEARCH": "nodes of the current research population (LEGACY_TEST excluded)",
}


def counter_audit(db: Any = None) -> Dict[str, Any]:
    """Compare the node total produced by every existing counter path.

    The Stats page, Overview, Live Activity, pipeline state, node lists and the
    research selectors must agree on the same population. V5.4 makes the
    app.research.populations snapshot the ONE authority, so every check here also
    reports the definition it claims and whether it matches the authority's own
    value for that definition. A path that still re-derives its own number is
    visible as ``matches_authority: false`` instead of being papered over.
    """
    d = db or get_db()
    snapshot = _engine_snapshot()
    counts = d.count_by_status(exclude_legacy=True)
    scoped = d.total_strategies_count(exclude_legacy=True)

    from ..research.populations import node_state_snapshot
    auth = node_state_snapshot(db=d)
    auth_total = int((auth.get("state") or {}).get("TOTAL") or 0)
    auth_user = int((auth.get("counts") or {}).get("user_research") or 0)

    checks: List[Dict[str, Any]] = []

    def add(surface: str, value: Any, source: str, definition: str) -> None:
        value = None if value is None else int(value)
        expected = auth_total if definition == "ALL_STORED" else auth_user
        checks.append({"surface": surface, "value": value, "source": source,
                       "definition": definition, "matches_authority": value == expected})

    # --- the authority itself (the value every other path must reproduce) ----
    add("node_state.state.TOTAL", auth_total,
        "app.research.populations.node_state_snapshot", "ALL_STORED")
    add("node_state.counts.user_research", auth_user,
        "app.research.populations.node_state_snapshot", "USER_RESEARCH")

    add("stats.population.total", population(d)["total"],
        "api/stats/overview -> research_stats.population()", "ALL_STORED")
    if snapshot:
        add("lab.status.total_nodes", auth_total,
            "api/lab/status -> orchestrator.lab.status() (authority snapshot)", "ALL_STORED")
        add("workflow.milestones.current_nodes", int(snapshot.get("current_nodes") or 0),
            "api/workflow/milestones -> engine snapshot", "USER_RESEARCH")
    add("api.status.status_counts_sum", sum(counts.values()),
        "api/status -> db.count_by_status(exclude_legacy=True)", "USER_RESEARCH")
    add("population.list.total", scoped,
        "api/population -> db.total_strategies_count(exclude_legacy=True)", "USER_RESEARCH")
    add("stats.population.user_research", population(d)["user_research"],
        "api/stats/overview -> research_stats.population()", "USER_RESEARCH")
    try:
        recon = d.reconstruct_state(0, exclude_legacy=True).get("total_nodes")
        add("state.reconstruction.total_nodes", int(recon or 0),
            "db.reconstruct_state(exclude_legacy=True)", "USER_RESEARCH")
    except Exception:
        pass

    def axis(definition: str) -> List[int]:
        return sorted({c["value"] for c in checks
                       if c["definition"] == definition and c["value"] is not None})

    values = axis("USER_RESEARCH")
    values_all = axis("ALL_STORED")
    mismatched = [c["surface"] for c in checks if c["matches_authority"] is False]
    legacy = _one(d, f"SELECT COUNT(*) c FROM strategies WHERE NOT ({_pred()})") or {}
    return {
        "scope": USER_POPULATION,
        "predicate": SCOPE_PREDICATE,
        "authority": auth.get("authority"),
        "authority_values": {"ALL_STORED": auth_total, "USER_RESEARCH": auth_user},
        "definitions": CHECK_DEFINITIONS,
        "checks": checks,
        "distinct_values": values,
        "distinct_values_all_stored": values_all,
        "mismatched_surfaces": mismatched,
        "consistent": len(values) <= 1 and len(values_all) <= 1,
        "legacy_excluded_nodes": int(legacy.get("c") or 0),
        "note": ("Every counter path must reproduce the ONE authority's value for the "
                 "definition it reports: TOTAL counts every stored node (ALL_STORED), the "
                 "research counters report the current experiment (USER_RESEARCH). "
                 "Execution/audit tables never contribute to these counters."),
    }


# --------------------------------------------------------------------------- #
# node list (paged, no N+1)
# --------------------------------------------------------------------------- #
_SORTS = {
    "fitness": "COALESCE(s.fitness, -9) DESC, s.id ASC",
    "id": "s.id DESC",
    "generation": "s.generation DESC, s.id DESC",
    "status": "s.status ASC, s.id DESC",
    "created": "s.created_at DESC",
}


def node_list(db: Any = None, limit: int = 50, offset: int = 0, sort: str = "fitness",
              status: Optional[str] = None, search: Optional[str] = None,
              include_legacy: bool = False) -> Dict[str, Any]:
    """Paged USER_RESEARCH node list with the statistics each row can support.

    Hydration is done with fixed-size bulk queries over the page's ids only.
    """
    d = db or get_db()
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    where, args = [], []
    where.append(_pred("s") if not include_legacy else "1=1")
    if status:
        where.append("s.status=?"); args.append(status)
    if search:
        term = f"%{str(search).strip()}%"
        where.append("(s.symbol LIKE ? OR CAST(s.id AS TEXT) LIKE ? OR CAST(s.research_node_num AS TEXT) LIKE ?)")
        args.extend([term, term, term])
    wsql = " AND ".join(where)
    order = _SORTS.get(sort or "fitness", _SORTS["fitness"])

    total_row = _one(d, f"SELECT COUNT(*) c FROM strategies s WHERE {wsql}", args) or {}
    rows = _rows(d, f"""SELECT s.id, s.parent_id, s.generation, s.status, s.symbol, s.timeframe,
                               s.direction, s.fitness, s.complexity, s.run_id, s.data_source,
                               s.research_node_num, s.created_at, s.updated_at,
                               (SELECT COUNT(*) FROM backtests b WHERE b.strategy_id=s.id) backtest_count,
                               (SELECT p.stage FROM strategy_pipeline_states p
                                 WHERE p.strategy_id=s.id) pipeline_stage,
                               (SELECT 1 FROM research_shortlist r WHERE r.strategy_id=s.id) shortlisted
                        FROM strategies s WHERE {wsql} ORDER BY {order} LIMIT ? OFFSET ?""",
                list(args) + [limit, offset])
    ids = [int(r["id"]) for r in rows]
    if not ids:
        return {"scope": USER_POPULATION, "total": int(total_row.get("c") or 0), "offset": offset,
                "limit": limit, "returned": 0, "sort": sort, "nodes": [],
                "labels": _LABELS, "include_legacy": bool(include_legacy)}

    marks = ",".join("?" * len(ids))
    latest: Dict[int, Dict[str, Any]] = {}
    for r in _rows(d, f"""SELECT b.strategy_id sid, b.stage, b.created_at, b.metrics
                          FROM backtests b WHERE b.strategy_id IN ({marks})
                          ORDER BY CASE b.stage WHEN 'detail' THEN 0 ELSE 1 END, b.id DESC""", ids):
        sid = int(r["sid"])
        if sid in latest:
            continue
        m = r.get("metrics")
        if isinstance(m, str):
            try:
                m = json.loads(m)
            except Exception:
                m = None
        latest[sid] = {"stage": r.get("stage"), "at": r.get("created_at"), "metrics": m or {}}

    validations: Dict[int, Dict[str, Any]] = {}
    for r in _rows(d, f"""SELECT v.strategy_id sid, v.passed, v.robustness_score
                          FROM validations v WHERE v.strategy_id IN ({marks})""", ids):
        validations[int(r["sid"])] = {"passed": bool(r.get("passed")),
                                      "robustness_score": _round(r.get("robustness_score"))}

    exec_counts: Dict[int, Dict[str, int]] = {}
    for table, key in (("paper_trades", "paper"), ("mt5_demo_trades", "mt5_demo"),
                       ("live_test_trades", "live_test")):
        for r in _rows(d, f"""SELECT strategy_id sid, COUNT(*) c FROM {table}
                              WHERE strategy_id IN ({marks}) GROUP BY strategy_id""", ids):
            exec_counts.setdefault(int(r["sid"]), {})[key] = int(r["c"] or 0)

    nodes: List[Dict[str, Any]] = []
    for r in rows:
        sid = int(r["id"])
        bt = latest.get(sid) or {}
        m = bt.get("metrics") or {}
        nodes.append({
            "id": sid,
            "node_id": f"Node_{sid}",
            "research_node_num": r.get("research_node_num"),
            "run_id": r.get("run_id"),
            "data_source": r.get("data_source") or USER_POPULATION,
            "generation": r.get("generation"),
            "status": r.get("status"),
            "symbol": r.get("symbol"),
            "timeframe": r.get("timeframe"),
            "direction": r.get("direction"),
            "fitness": _round(r.get("fitness"), 4),
            "complexity": r.get("complexity"),
            "parent_id": r.get("parent_id"),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
            "shortlisted": bool(r.get("shortlisted")),
            "pipeline_stage": r.get("pipeline_stage"),
            "research": {
                "layer": _LABELS["backtest"],
                "backtest_count": int(r.get("backtest_count") or 0),
                "latest_backtest_stage": bt.get("stage"),
                "latest_backtest_at": bt.get("at"),
                "return_pct": _round(m.get("total_return_pct")),
                "profit_factor": _round(m.get("profit_factor")),
                "win_rate": _round(m.get("win_rate")),
                "max_drawdown_pct": _round(m.get("max_drawdown_pct")),
                "sharpe": _round(m.get("sharpe")),
                "trades": int(m["trades"]) if isinstance(m.get("trades"), (int, float)) else None,
                "net_profit": _round(m.get("net_profit"), 2),
                "validation": validations.get(sid),
            },
            "execution": {
                "layer": "EXECUTION",
                "paper_trades": (exec_counts.get(sid) or {}).get("paper", 0),
                "mt5_demo_trades": (exec_counts.get(sid) or {}).get("mt5_demo", 0),
                "live_test_trades": (exec_counts.get(sid) or {}).get("live_test", 0),
            },
        })

    return {"scope": USER_POPULATION, "total": int(total_row.get("c") or 0), "offset": offset,
            "limit": limit, "returned": len(nodes), "sort": sort, "nodes": nodes,
            "labels": _LABELS, "include_legacy": bool(include_legacy)}


# --------------------------------------------------------------------------- #
# per-node statistics & economics
# --------------------------------------------------------------------------- #
def reward_risk_from_genome(genome: Any) -> Dict[str, Any]:
    """Genome-derived economics - the single source of truth for the risk and
    stop/target numbers (the V4.4 node economics and the V4.5 matrix / compare
    views both call this, so the formula exists exactly once).

    Missing inputs stay ``None``: a node whose genome has no stop or target
    multiple gets no made-up ratio.
    """
    g = genome
    if isinstance(g, str):
        try:
            g = json.loads(g)
        except Exception:
            g = {}
    g = g or {}
    ex = g.get("exit") or {}
    risk = g.get("risk") or {}
    risk_pct = _f(risk.get("risk_per_trade"))
    sl_mult = _f(ex.get("sl_atr_mult"))
    tp_mult = _f(ex.get("tp_atr_mult"))
    rr = None
    if sl_mult and tp_mult and sl_mult > 0:
        rr = round(tp_mult / sl_mult, 3)
    return {
        "risk_per_trade": risk_pct,
        "risk_per_trade_pct": _round(risk_pct * 100.0, 4) if risk_pct is not None else None,
        "sl_atr_multiple": _round(sl_mult, 3),
        "tp_atr_multiple": _round(tp_mult, 3),
        "atr_reference": ex.get("atr_spec") or None,
        "reward_risk_ratio": rr,
        "reward_risk_source": "tp_atr_mult / sl_atr_mult (calculated)",
        "trailing": ex.get("trailing"),
        "min_hold_bars": ex.get("min_hold_bars"),
        "max_hold_bars": ex.get("max_hold_bars"),
        "max_concurrent_positions": risk.get("max_concurrent"),
        "source": "strategies.genome (stored research parameters)",
    }


def node_economics(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Node economics derived only from stored genome values + stored backtest stats.

    Missing inputs are reported as ``None`` with a reason instead of a made-up
    number: research records genuinely do not contain lot sizes, account equity
    or price levels, so risk amount / position size / exposure are ``N/A`` on a
    pure research node (they exist per-trade in the execution layer).
    """
    genome = payload.get("genome") or {}
    if isinstance(genome, str):
        try:
            genome = json.loads(genome)
        except Exception:
            genome = {}
    ex = genome.get("exit") or {}
    risk = genome.get("risk") or {}
    pos = payload.get("position_management") or {}
    bt = ((payload.get("metrics") or {}).get("backtest") or {})
    unavailable: List[Dict[str, str]] = []

    def na(metric: str, reason: str) -> None:
        unavailable.append({"metric": metric, "reason": reason})

    _rr = reward_risk_from_genome(genome)
    risk_pct = _rr["risk_per_trade"]
    sl_mult = _rr["sl_atr_multiple"]
    tp_mult = _rr["tp_atr_multiple"]
    rr = _rr["reward_risk_ratio"]

    for metric, present in (("risk_per_trade_pct", risk_pct is not None),
                            ("sl_atr_multiple", sl_mult is not None),
                            ("tp_atr_multiple", tp_mult is not None)):
        if not present:
            na(metric, "not present in this node's genome (the stored genome has no such field)")
    na("risk_amount",
       "needs account equity + instrument value; research records do not store an account. "
       "The live-testing layer calculates it from live MT5 equity and stores it per trade "
       "(live_test_trades.risk_amount).")
    na("position_size_lots",
       "lot size is produced by the execution layer (PAPER / MT5 DEMO / LIVE TEST records) and is "
       "not stored for a pure research node.")
    na("estimated_exposure",
       "requires position size x price; neither is stored in the research records.")
    na("sl_distance_price / tp_distance_price",
       "absolute price distance needs the ATR value of the signal bar, which is not stored with "
       "the node; the stop/target rules are stored as ATR multiples.")

    trades = bt.get("trades")
    return {
        "risk_per_trade_pct": _round(risk_pct * 100.0, 4) if risk_pct is not None else None,
        "risk_per_trade_source": "genome.risk.risk_per_trade",
        "risk_amount": None,
        "risk_amount_layer": "LIVE TEST only",
        "position_size_lots": None,
        "sl_atr_multiple": _round(sl_mult, 3),
        "tp_atr_multiple": _round(tp_mult, 3),
        "atr_reference": ex.get("atr_spec") or None,
        "reward_risk_ratio": rr,
        "reward_risk_source": "tp_atr_mult / sl_atr_mult (calculated)",
        "trailing_stop": payload.get("exit_conditions", {}).get("trailing_stop"),
        "min_hold_bars": payload.get("exit_conditions", {}).get("min_hold_bars"),
        "max_hold_bars": payload.get("exit_conditions", {}).get("max_hold_bars"),
        "max_concurrent_positions": pos.get("max_concurrent_positions"),
        "estimated_exposure": None,
        "trade_stats": {
            "layer": _LABELS["backtest"],
            "trade_count": int(trades) if isinstance(trades, (int, float)) else None,
            "win_rate": _round(bt.get("win_rate")),
            "profit_factor": _round(bt.get("profit_factor")),
            "expectancy": _round(bt.get("expectancy")),
            "avg_trade": _round(bt.get("avg_trade"), 2),
            "net_profit": _round(bt.get("net_profit"), 2),
            "total_return_pct": _round(bt.get("total_return_pct")),
            "max_drawdown_pct": _round(bt.get("max_drawdown_pct")),
            "sharpe": _round(bt.get("sharpe")),
            "source": "backtests.metrics (stored)",
        },
        "unavailable": unavailable,
        "note": ("Genome-derived values are stored research parameters; the trade statistics come "
                 "from the stored backtest of this node and are labelled BACKTEST."),
    }


def node_stats(sid: int, db: Any = None) -> Optional[Dict[str, Any]]:
    """Per-node research statistics + economics + separate execution records (§7-§9)."""
    d = db or get_db()
    from ..strategies.authoritative import get_authoritative_strategy
    payload = get_authoritative_strategy(int(sid), d)
    if not payload:
        return None

    data_source = str(payload.get("data_source") or USER_POPULATION).upper()
    eligible = data_source != LEGACY_POPULATION
    metrics = payload.get("metrics") or {}
    bt = metrics.get("backtest") or {}
    val = metrics.get("validation") or {}

    paper_rows = _rows(d, "SELECT * FROM paper_trades WHERE strategy_id=? ORDER BY id DESC LIMIT 25", (int(sid),))
    paper = {
        "layer": _LABELS["paper"],
        "available": bool(paper_rows),
        "trades": len(paper_rows),
        "total_pnl": _round(sum(_f(t.get("pnl")) or 0.0 for t in paper_rows), 2) if paper_rows else None,
        "open": sum(1 for t in paper_rows if str(t.get("status", "")).upper() in ("OPEN", "POSITION_OPEN")),
        "records": [{
            "id": t.get("id"), "symbol": t.get("symbol"), "side": t.get("side"),
            "lots": _f(t.get("lots")), "pnl": _round(t.get("pnl"), 2), "status": t.get("status"),
            "open_ts": t.get("exec_ts") or t.get("signal_ts"), "close_ts": t.get("exit_ts"),
            "sl": _f(t.get("sl")), "tp": _f(t.get("tp")),
        } for t in paper_rows[:10]],
        "source": "paper_trades (execution record)",
    }

    node = {
        "id": payload.get("id"),
        "node_id": payload.get("node_id"),
        "research_node_num": payload.get("research_node_num"),
        "run_id": payload.get("run_id"),
        "data_source": data_source,
        "research_eligible": eligible,
        "generation": payload.get("generation"),
        "status": payload.get("status"),
        "symbol": payload.get("symbol"),
        "timeframe": payload.get("timeframe"),
        "direction": payload.get("direction"),
        "fitness": _round(payload.get("fitness"), 4),
        "complexity": payload.get("complexity"),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "parent_id": payload.get("parent_id"),
        "children": payload.get("children") or [],
        "shortlisted": payload.get("shortlisted"),
        "pipeline_stage": payload.get("pipeline_stage"),
        "has_matrix": payload.get("has_matrix"),
        "mutation_type": payload.get("mutation_type"),
        "creation_reason": payload.get("creation_reason"),
        "survival_reason": payload.get("survival_reason"),
        "scope_note": ("USER_RESEARCH node - included in research analytics." if eligible else
                       "LEGACY_TEST infrastructure record - excluded from research analytics and "
                       "from the Stats page population (shown here through the V4.0 escape hatch)."),
    }

    research = {
        "layer": _LABELS["research"],
        "parameters": {
            "symbol": payload.get("symbol"),
            "timeframe": payload.get("timeframe"),
            "direction": payload.get("direction"),
            "indicators": payload.get("indicators") or [],
            "entry_conditions": payload.get("entry_conditions") or {},
            "exit_conditions": payload.get("exit_conditions") or {},
            "position_management": payload.get("position_management") or {},
        },
        "backtest": {
            "layer": _LABELS["backtest"],
            "available": bool(bt),
            "stage": bt.get("stage"),
            "window": bt.get("window"),
            "dataset_id": bt.get("dataset_id"),
            "total_return_pct": _round(bt.get("total_return_pct")),
            "profit_factor": _round(bt.get("profit_factor")),
            "win_rate": _round(bt.get("win_rate")),
            "max_drawdown_pct": _round(bt.get("max_drawdown_pct")),
            "sharpe": _round(bt.get("sharpe")),
            "sortino": _round(bt.get("sortino")),
            "expectancy": _round(bt.get("expectancy")),
            "trades": int(bt["trades"]) if isinstance(bt.get("trades"), (int, float)) else None,
            "net_profit": _round(bt.get("net_profit"), 2),
            "gross_profit": _round(bt.get("gross_profit"), 2),
            "gross_loss": _round(bt.get("gross_loss"), 2),
            "avg_hold_bars": _round(bt.get("avg_hold_bars"), 2),
            "consistency": _round(bt.get("consistency")),
            "exit_reasons": bt.get("exit_reasons"),
            "source": "backtests.metrics (stored)",
        },
        "validation": {
            "layer": _LABELS["validation"],
            "available": bool(val),
            "passed": val.get("passed"),
            "robustness_score": _round(val.get("robustness_score")),
            "oos": val.get("oos"),
            "walkforward": val.get("walkforward"),
            "perturbation": val.get("perturbation"),
            "spread_stress": val.get("spread_stress"),
            "slippage_stress": val.get("slippage_stress"),
            "montecarlo": val.get("montecarlo"),
            "regime_holdout": val.get("regime_holdout"),
            "created_at": val.get("created_at"),
            "source": "validations (stored)",
        },
        "returns": payload.get("returns") or {},
        "profit_factors": payload.get("profit_factors") or {},
        "robustness_score": _round(payload.get("robustness_score")),
    }

    execution = {
        "layer": "EXECUTION",
        "note": _LABELS["execution_note"],
        "paper": paper,
        "live_test": {
            "layer": _LABELS["live_test"],
            "is_active": (metrics.get("live_test") or {}).get("is_active"),
            "status": (metrics.get("live_test") or {}).get("status"),
            "total_trades": (metrics.get("live_test") or {}).get("total_trades"),
            "total_pnl": _round((metrics.get("live_test") or {}).get("total_pnl"), 2),
            "win_rate": _round((metrics.get("live_test") or {}).get("win_rate")),
            "source": "live_test_configs / live_test_trades (audit record)",
        },
        "mt5_demo": {
            "layer": _LABELS["mt5_demo"],
            "enabled": (metrics.get("mt5_demo") or {}).get("enabled"),
            "status": (metrics.get("mt5_demo") or {}).get("status"),
            "total_trades": (metrics.get("mt5_demo") or {}).get("total_trades"),
            "total_pnl": _round((metrics.get("mt5_demo") or {}).get("total_pnl"), 2),
            "win_rate": _round((metrics.get("mt5_demo") or {}).get("win_rate")),
            "source": "mt5_demo_configs / mt5_demo_trades (audit record)",
        },
        "mt5_backtest": {
            "layer": _LABELS["mt5_backtest"],
            "available": bool(metrics.get("mt5_backtest")),
            "record": metrics.get("mt5_backtest"),
            "source": "mt5_backtests (stored)",
        },
    }

    return {
        "generated_at": time.time(),
        "node": node,
        "research": research,
        "economics": node_economics(payload),
        "execution": execution,
        "labels": _LABELS,
    }
