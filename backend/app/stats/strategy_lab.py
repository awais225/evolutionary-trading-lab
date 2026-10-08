"""V4.5 - Strategy Lab / Backtest Matrix (research selection & comparison).

This module is the research interface layer on top of the V4.4 statistics
primitives. It deliberately contains **no new metric definitions**:

* scope, population counters, per-node statistics and node economics come from
  ``app.stats.research_stats`` (V4.4);
* the genome-derived economics (risk %, SL/TP ATR multiples, reward:risk) come
  from ``research_stats.reward_risk_from_genome`` - one formula, used by both
  the V4.4 node economics and the V4.5 matrix / comparison views;
* filtering, sorting and pagination are performed **in SQL** over the
  USER_RESEARCH scope, so a normally sized page never reads the whole
  population and never issues one query per strategy.

Values that the stored research data cannot support are returned as ``None``
and listed in an ``unavailable`` array with a reason; the UI renders them as
``N/A``. Execution / audit rows are reported in their own labelled block and
are never mixed into a research number.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .research_stats import (
    LEGACY_POPULATION,
    SCOPE_PREDICATE,
    USER_POPULATION,
    _LABELS,
    _f,
    _one,
    _pred,
    _round,
    _rows,
    node_stats,
    population,
    reward_risk_from_genome,
    scope_info,
)

#: Selecting more than this many strategies at once is an explicit user error
#: (the comparison panel is meant for a handful of nodes).
MAX_COMPARE = 8
MAX_MATRIX_ROWS = 200
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

#: Deterministic sort keys. Every key is resolved in SQL and always ends with
#: ``s.id ASC`` as the tie-breaker, so equal values can never reorder between
#: requests. ``nulls last`` keeps unknown values out of the "best" end.
SORTS: Dict[str, str] = {
    "id": "s.id",
    "fitness": "s.fitness",
    "generation": "s.generation",
    "status": "s.status",
    "created": "s.created_at",
    "updated": "s.updated_at",
    "symbol": "s.symbol",
    "timeframe": "s.timeframe",
    "complexity": "s.complexity",
    "return": "m_return",
    "net_profit": "m_net_profit",
    "profit_factor": "m_profit_factor",
    "win_rate": "m_win_rate",
    "trades": "m_trades",
    "drawdown": "m_max_drawdown_pct",
    "sharpe": "m_sharpe",
    "sortino": "m_sortino",
    "expectancy": "m_expectancy",
    "robustness": "v_robustness",
    "oos_return": "oos_return",
}

_SORT_OPTIONS = [
    {"key": "return", "label": "Backtest return", "layer": _LABELS["backtest"]},
    {"key": "net_profit", "label": "Net P&L", "layer": _LABELS["backtest"]},
    {"key": "profit_factor", "label": "Profit factor", "layer": _LABELS["backtest"]},
    {"key": "win_rate", "label": "Win rate", "layer": _LABELS["backtest"]},
    {"key": "trades", "label": "Trades", "layer": _LABELS["backtest"]},
    {"key": "drawdown", "label": "Max drawdown (asc = lowest first)", "layer": _LABELS["backtest"]},
    {"key": "expectancy", "label": "Expectancy", "layer": _LABELS["backtest"]},
    {"key": "sharpe", "label": "Sharpe", "layer": _LABELS["backtest"]},
    {"key": "robustness", "label": "Robustness (validation)", "layer": _LABELS["validation"]},
    {"key": "oos_return", "label": "OOS return (validation)", "layer": _LABELS["validation"]},
    {"key": "fitness", "label": "Engine fitness", "layer": "ENGINE"},
    {"key": "generation", "label": "Generation", "layer": "ENGINE"},
    {"key": "id", "label": "Node id", "layer": "ENGINE"},
]

#: The comparison matrix columns, published by the API so the UI renders the
#: table from the server's own definition instead of re-implementing it.
MATRIX_COLUMNS = [
    {"key": "id", "label": "Node", "layer": "IDENTITY"},
    {"key": "generation", "label": "Gen", "layer": "IDENTITY"},
    {"key": "symbol", "label": "Symbol", "layer": "IDENTITY"},
    {"key": "timeframe", "label": "TF", "layer": "IDENTITY"},
    {"key": "status", "label": "Status", "layer": "IDENTITY"},
    {"key": "stage", "label": "Backtest stage", "layer": _LABELS["backtest"]},
    {"key": "trades", "label": "Trades", "layer": _LABELS["backtest"]},
    {"key": "wins", "label": "Wins", "layer": _LABELS["backtest"], "derived": True},
    {"key": "losses", "label": "Losses", "layer": _LABELS["backtest"], "derived": True},
    {"key": "win_rate", "label": "Win rate", "layer": _LABELS["backtest"]},
    {"key": "net_profit", "label": "Net P&L", "layer": _LABELS["backtest"]},
    {"key": "return_pct", "label": "Return", "layer": _LABELS["backtest"]},
    {"key": "max_drawdown_pct", "label": "Max DD", "layer": _LABELS["backtest"]},
    {"key": "profit_factor", "label": "PF", "layer": _LABELS["backtest"]},
    {"key": "expectancy", "label": "Expectancy", "layer": _LABELS["backtest"]},
    {"key": "reward_risk_ratio", "label": "R:R", "layer": _LABELS["backtest"],
     "derived": True, "derived_from": "genome tp/sl ATR multiples (V4.4 node economics)"},
    {"key": "validation_passed", "label": "Validation", "layer": _LABELS["validation"]},
    {"key": "robustness_score", "label": "Robustness", "layer": _LABELS["validation"]},
    {"key": "qualified", "label": "Qualified", "layer": "ENGINE"},
    {"key": "execution_records", "label": "Execution records", "layer": "EXECUTION"},
]

# --------------------------------------------------------------------------- #
# filter plumbing
# --------------------------------------------------------------------------- #
def _as_bool(v: Any) -> Optional[bool]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return None


def _as_float(v: Any) -> Optional[float]:
    return _f(v)


def _as_int(v: Any) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _as_list(v: Any) -> List[str]:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple, set)):
        items = [str(x).strip() for x in v]
    else:
        items = [p.strip() for p in str(v).split(",")]
    return [x for x in items if x]


def _build_where(include_legacy: bool = False, *, search: Any = None,
                 generation: Any = None, status: Any = None, symbol: Any = None,
                 timeframe: Any = None, direction: Any = None, qualified: Any = None,
                 has_backtest: Any = None, has_validation: Any = None,
                 validated_passed: Any = None, stage: Any = None,
                 shortlist_only: Any = None, min_return: Any = None,
                 min_net_profit: Any = None, min_profit_factor: Any = None,
                 min_win_rate: Any = None, min_trades: Any = None,
                 max_drawdown: Any = None, min_expectancy: Any = None,
                 min_robustness: Any = None, min_oos_return: Any = None
                 ) -> Tuple[str, List[Any], Dict[str, Any]]:
    """Translate the request filters into SQL over the scoped population.

    Returns ``(where_sql, args, applied)`` where ``applied`` echoes exactly what
    the server used, so a client can show the effective filter set. Unknown /
    empty filters are ignored rather than silently coerced.
    """
    where: List[str] = []
    args: List[Any] = []
    applied: Dict[str, Any] = {"scope": USER_POPULATION if not include_legacy else "ALL"}

    if not include_legacy:
        where.append(_pred("s"))
    if search:
        term = f"%{str(search).strip()}%"
        where.append("(CAST(s.id AS TEXT) LIKE ? OR CAST(s.research_node_num AS TEXT) LIKE ?"
                     " OR s.symbol LIKE ? OR s.timeframe LIKE ? OR s.status LIKE ?"
                     " OR COALESCE(s.mutation_type,'') LIKE ?)")
        args.extend([term] * 6)
        applied["search"] = str(search).strip()

    gens = [_as_int(g) for g in _as_list(generation)]
    gens = [g for g in gens if g is not None]
    if gens:
        where.append(f"s.generation IN ({','.join('?' * len(gens))})")
        args.extend(gens)
        applied["generation"] = gens

    statuses = [s.upper() for s in _as_list(status)]
    if statuses:
        where.append(f"UPPER(s.status) IN ({','.join('?' * len(statuses))})")
        args.extend(statuses)
        applied["status"] = statuses

    for key, col, val in (("symbol", "s.symbol", symbol), ("timeframe", "s.timeframe", timeframe),
                          ("direction", "s.direction", direction)):
        if val:
            where.append(f"UPPER({col}) = ?")
            args.append(str(val).strip().upper())
            applied[key] = str(val).strip().upper()

    q = _as_bool(qualified)
    if q is not None:
        where.append("UPPER(s.status) = 'QUALIFIED'" if q else "UPPER(s.status) <> 'QUALIFIED'")
        applied["qualified"] = q

    hb = _as_bool(has_backtest)
    if hb is not None:
        where.append(("EXISTS (SELECT 1 FROM backtests b WHERE b.strategy_id=s.id)" if hb else
                      "NOT EXISTS (SELECT 1 FROM backtests b WHERE b.strategy_id=s.id)"))
        applied["has_backtest"] = hb

    hv = _as_bool(has_validation)
    if hv is not None:
        where.append(("EXISTS (SELECT 1 FROM validations v WHERE v.strategy_id=s.id)" if hv else
                      "NOT EXISTS (SELECT 1 FROM validations v WHERE v.strategy_id=s.id)"))
        applied["has_validation"] = hv

    vp = _as_bool(validated_passed)
    if vp is not None:
        where.append(("EXISTS (SELECT 1 FROM validations v WHERE v.strategy_id=s.id AND v.passed)"
                      if vp else
                      "NOT EXISTS (SELECT 1 FROM validations v WHERE v.strategy_id=s.id AND v.passed)"))
        applied["validated_passed"] = vp

    if stage:
        st = str(stage).strip().lower()
        if st == "none":
            where.append("bt.bid IS NULL")
        elif st in ("screen", "detail"):
            where.append("bt.stage = ?")
            args.append(st)
        applied["stage"] = st

    sb = _as_bool(shortlist_only)
    if sb:
        where.append("EXISTS (SELECT 1 FROM research_shortlist r WHERE r.strategy_id=s.id)")
        applied["shortlist_only"] = True

    for key, expr, val, cmp_ in (
        ("min_return", _METRIC_EXPR["m_return"], min_return, ">="),
        ("min_net_profit", _METRIC_EXPR["m_net_profit"], min_net_profit, ">="),
        ("min_profit_factor", _METRIC_EXPR["m_profit_factor"], min_profit_factor, ">="),
        ("min_win_rate", _METRIC_EXPR["m_win_rate"], min_win_rate, ">="),
        ("min_trades", _METRIC_EXPR["m_trades"], min_trades, ">="),
        ("max_drawdown", _METRIC_EXPR["m_max_drawdown_pct"], max_drawdown, "<="),
        ("min_expectancy", _METRIC_EXPR["m_expectancy"], min_expectancy, ">="),
        ("min_robustness", _METRIC_EXPR["v_robustness"], min_robustness, ">="),
        ("min_oos_return", _METRIC_EXPR["oos_return"], min_oos_return, ">="),
    ):
        f = _as_float(val)
        if f is not None:
            where.append(f"{expr} {cmp_} ?")
            args.append(f)
            applied[key] = f

    return (" AND ".join(where) if where else "1=1"), args, applied


#: SQL expressions for the metric columns. ``SORTS`` uses the SELECT aliases
#: (legal in ORDER BY); WHERE clauses must avoid aliases, because SQLite only
#: allows output-column names in ORDER BY / GROUP BY / HAVING - not in WHERE.
_METRIC_EXPR: Dict[str, str] = {
    "m_return": "json_extract(bt.metrics,'$.total_return_pct')",
    "m_net_profit": "json_extract(bt.metrics,'$.net_profit')",
    "m_profit_factor": "json_extract(bt.metrics,'$.profit_factor')",
    "m_win_rate": "json_extract(bt.metrics,'$.win_rate')",
    "m_trades": "json_extract(bt.metrics,'$.trades')",
    "m_max_drawdown_pct": "json_extract(bt.metrics,'$.max_drawdown_pct')",
    "m_expectancy": "json_extract(bt.metrics,'$.expectancy')",
    "v_robustness": "v.robustness",
    "oos_return": "json_extract(v.oos,'$.metrics.total_return_pct')",
}

#: The page query reads the *preferred* stored backtest per strategy - the same
#: preference rule the V4.4 node statistics and the node-detail view use
#: (``detail`` beats ``screen``; newest id wins inside a stage).
_CTE = """
WITH bt_ranked AS (
    SELECT b.strategy_id AS sid, b.id AS bid, b.stage AS stage, b.metrics AS metrics,
           b.created_at AS at,
           ROW_NUMBER() OVER (PARTITION BY b.strategy_id
                              ORDER BY CASE b.stage WHEN 'detail' THEN 0 ELSE 1 END,
                                       b.id DESC) AS rn
    FROM backtests b
),
v_ranked AS (
    SELECT v.strategy_id AS sid, v.passed AS passed, v.robustness_score AS robustness,
           v.oos AS oos, v.created_at AS at,
           ROW_NUMBER() OVER (PARTITION BY v.strategy_id ORDER BY v.id DESC) AS rn
    FROM validations v
)
"""

_SELECT_COLS = """
SELECT s.id, s.parent_id, s.generation, s.status, s.symbol, s.timeframe, s.direction,
       s.fitness, s.complexity, s.run_id, s.data_source, s.research_node_num,
       s.created_at, s.updated_at, s.mutation_type, s.genome,
       bt.bid AS bt_id, bt.stage AS bt_stage, bt.metrics AS bt_metrics, bt.at AS bt_at,
       json_extract(bt.metrics,'$.total_return_pct')   AS m_return,
       json_extract(bt.metrics,'$.net_profit')         AS m_net_profit,
       json_extract(bt.metrics,'$.profit_factor')      AS m_profit_factor,
       json_extract(bt.metrics,'$.win_rate')           AS m_win_rate,
       json_extract(bt.metrics,'$.trades')             AS m_trades,
       json_extract(bt.metrics,'$.max_drawdown_pct')   AS m_max_drawdown_pct,
       json_extract(bt.metrics,'$.sharpe')             AS m_sharpe,
       json_extract(bt.metrics,'$.sortino')            AS m_sortino,
       json_extract(bt.metrics,'$.expectancy')         AS m_expectancy,
       json_extract(bt.metrics,'$.avg_trade')          AS m_avg_trade,
       json_extract(bt.metrics,'$.avg_hold_bars')      AS m_avg_hold_bars,
       json_extract(bt.metrics,'$.consistency')        AS m_consistency,
       json_extract(bt.metrics,'$.gross_profit')       AS m_gross_profit,
       json_extract(bt.metrics,'$.gross_loss')         AS m_gross_loss,
       json_extract(bt.metrics,'$.final_equity')       AS m_final_equity,
       v.passed AS v_passed, v.robustness AS v_robustness, v.oos AS v_oos, v.at AS v_at,
       json_extract(v.oos,'$.metrics.total_return_pct') AS oos_return,
       json_extract(v.oos,'$.metrics.profit_factor')    AS oos_profit_factor,
       (SELECT COUNT(*) FROM backtests b2 WHERE b2.strategy_id=s.id)      AS bt_count,
       (SELECT COUNT(*) FROM validations v2 WHERE v2.strategy_id=s.id)    AS val_count,
       (SELECT 1 FROM research_shortlist r WHERE r.strategy_id=s.id)      AS shortlisted,
       (SELECT COUNT(*) FROM paper_trades p WHERE p.strategy_id=s.id)     AS ex_paper,
       (SELECT COUNT(*) FROM live_test_trades lt WHERE lt.strategy_id=s.id) AS ex_live,
       (SELECT COUNT(*) FROM mt5_demo_trades md WHERE md.strategy_id=s.id)  AS ex_demo
FROM strategies s
LEFT JOIN bt_ranked bt ON bt.sid = s.id AND bt.rn = 1
LEFT JOIN v_ranked  v  ON v.sid  = s.id AND v.rn  = 1
"""


# --------------------------------------------------------------------------- #
# row shaping
# --------------------------------------------------------------------------- #
def _json_maybe(v: Any) -> Optional[Dict[str, Any]]:
    if v is None:
        return None
    if isinstance(v, dict):
        return v
    try:
        out = json.loads(v)
        return out if isinstance(out, dict) else None
    except Exception:
        return None


def _derive_wins_losses(trades: Any, win_rate: Any) -> Tuple[Optional[int], Optional[int]]:
    """Wins / losses derived from two *stored* values (no default, no guessing).

    Returns ``(None, None)`` when either input is missing, so the caller can
    report the column as unavailable.
    """
    t = _as_int(trades)
    wr = _f(win_rate)
    if t is None or wr is None or t <= 0 or wr < 0 or wr > 1:
        return None, None
    wins = int(round(t * wr))
    return wins, max(0, t - wins)


def _research_metrics(row: Dict[str, Any]) -> Dict[str, Any]:
    """The stored research results of one node, with explicit gaps."""
    unavailable: List[Dict[str, str]] = []
    trades = row.get("m_trades")
    win_rate = row.get("m_win_rate")
    wins, losses = _derive_wins_losses(trades, win_rate)
    if wins is None:
        unavailable.append({"metric": "wins/losses",
                            "reason": "derived as trades x win_rate; this node has no stored "
                                      "trade count and/or win rate"})
    metrics = {
        "trades": _as_int(trades),
        "wins": wins,
        "losses": losses,
        "wins_derived": True,
        "wins_source": "round(trades x win_rate) from the stored backtest metrics",
        "win_rate": _round(win_rate),
        "net_profit": _round(row.get("m_net_profit"), 2),
        "return_pct": _round(row.get("m_return")),
        "profit_factor": _round(row.get("m_profit_factor")),
        "max_drawdown_pct": _round(row.get("m_max_drawdown_pct")),
        "expectancy": _round(row.get("m_expectancy")),
        "sharpe": _round(row.get("m_sharpe")),
        "sortino": _round(row.get("m_sortino")),
        "avg_trade": _round(row.get("m_avg_trade"), 2),
        "avg_hold_bars": _round(row.get("m_avg_hold_bars"), 2),
        "consistency": _round(row.get("m_consistency")),
        "gross_profit": _round(row.get("m_gross_profit"), 2),
        "gross_loss": _round(row.get("m_gross_loss"), 2),
        "final_equity": _round(row.get("m_final_equity"), 2),
        "available": bool(row.get("bt_id")),
        "stage": row.get("bt_stage"),
        "recorded_at": row.get("bt_at"),
        "record_count": _as_int(row.get("bt_count")) or 0,
        "layer": _LABELS["backtest"],
        "source": "backtests.metrics (stored)",
    }
    if not metrics["available"]:
        unavailable.append({"metric": "backtest",
                            "reason": "this node has no stored backtest record yet"})
    metrics["unavailable"] = unavailable
    return metrics


def _validation_block(row: Dict[str, Any]) -> Dict[str, Any]:
    available = bool(row.get("v_at") is not None or row.get("v_robustness") is not None
                     or row.get("v_passed") is not None)
    return {
        "available": available,
        "passed": bool(row.get("v_passed")) if available else None,
        "robustness_score": _round(row.get("v_robustness")),
        "oos_return_pct": _round(row.get("oos_return")),
        "oos_profit_factor": _round(row.get("oos_profit_factor")),
        "recorded_at": row.get("v_at"),
        "record_count": _as_int(row.get("val_count")) or 0,
        "layer": _LABELS["validation"],
        "source": "validations (stored)",
        "unavailable": [] if available else [
            {"metric": "validation", "reason": "this node has no stored validation record yet"}],
    }


def _execution_block(row: Dict[str, Any]) -> Dict[str, Any]:
    """Execution / audit records - informational only, never a research metric."""
    return {
        "layer": "EXECUTION",
        "informational_only": True,
        "note": _LABELS["execution_note"],
        "paper_trades": _as_int(row.get("ex_paper")) or 0,
        "live_test_trades": _as_int(row.get("ex_live")) or 0,
        "mt5_demo_trades": _as_int(row.get("ex_demo")) or 0,
        "records_total": (_as_int(row.get("ex_paper")) or 0) + (_as_int(row.get("ex_live")) or 0)
                         + (_as_int(row.get("ex_demo")) or 0),
        "source": "paper_trades / live_test_trades / mt5_demo_trades (audit records)",
    }


def _row_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    research = _research_metrics(row)
    validation = _validation_block(row)
    economics = reward_risk_from_genome(row.get("genome"))
    data_source = str(row.get("data_source") or USER_POPULATION).upper()
    return {
        "id": row.get("id"),
        "node_id": f"Node_{row.get('id')}",
        "research_node_num": row.get("research_node_num"),
        "run_id": row.get("run_id"),
        "data_source": data_source,
        "research_eligible": data_source != LEGACY_POPULATION,
        "generation": row.get("generation"),
        "status": row.get("status"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "direction": row.get("direction"),
        "fitness": _round(row.get("fitness"), 4),
        "complexity": row.get("complexity"),
        "parent_id": row.get("parent_id"),
        "mutation_type": row.get("mutation_type"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "shortlisted": bool(row.get("shortlisted")),
        "qualified": str(row.get("status") or "").upper() == "QUALIFIED",
        "research": research,
        "validation": validation,
        "economics": {
            "risk_per_trade_pct": economics["risk_per_trade_pct"],
            "sl_atr_multiple": economics["sl_atr_multiple"],
            "tp_atr_multiple": economics["tp_atr_multiple"],
            "atr_reference": economics["atr_reference"],
            "reward_risk_ratio": economics["reward_risk_ratio"],
            "reward_risk_source": economics["reward_risk_source"],
            "layer": "NODE ECONOMICS",
            "source": economics["source"],
        },
        "execution": _execution_block(row),
        "stage": row.get("bt_stage"),
        "labels": _LABELS,
    }


# --------------------------------------------------------------------------- #
# strategy list (Strategy Lab table)
# --------------------------------------------------------------------------- #
def strategy_list(db: Any = None, limit: int = DEFAULT_LIMIT, offset: int = 0,
                  sort: str = "return", dir: str = "desc", include_legacy: bool = False,
                  **filters: Any) -> Dict[str, Any]:
    """Paged, filtered, sorted USER_RESEARCH strategy list.

    Exactly two SQL statements are executed regardless of page size or match
    count: one scoped ``COUNT(*)`` and one page query (with the fixed-size bulk
    sub-selects for that page's rows). No per-row query is issued.
    """
    d = db or _default_db()
    offset = max(0, _as_int(offset) or 0)
    # V5.2.3 §10 — the node tables render ONE scrollable table, so ``limit=0`` (or
    # negative) means "every matching row". A positive limit keeps the historical
    # cap, and ``truncated`` always tells the caller whether rows were withheld.
    all_rows = limit is not None and _as_int(limit) is not None and _as_int(limit) <= 0
    limit = max(1, min(_as_int(limit) or DEFAULT_LIMIT, MAX_LIMIT)) if not all_rows else 0
    sort_key = (sort or "return").strip().lower()
    if sort_key not in SORTS:
        sort_key = "return"
    direction = "ASC" if str(dir or "desc").strip().lower() == "asc" else "DESC"
    order_expr = SORTS[sort_key]

    where, args, applied = _build_where(include_legacy=bool(include_legacy), **filters)

    total_row = _one(d, f"{_CTE} SELECT COUNT(*) AS c {_FROM_WHERE(where)}", args) or {}
    total = int(total_row.get("c") or 0)

    if all_rows:
        rows = _rows(d, f"{_CTE}{_SELECT_COLS} WHERE {where} "
                        f"ORDER BY {order_expr} {direction} NULLS LAST, s.id ASC", args)
    else:
        rows = _rows(d, f"{_CTE}{_SELECT_COLS} WHERE {where} "
                        f"ORDER BY {order_expr} {direction} NULLS LAST, s.id ASC LIMIT ? OFFSET ?",
                     list(args) + [limit, offset])

    pop_total = _one(d, f"SELECT COUNT(*) AS c FROM strategies s WHERE {_pred('s')}") or {}
    legacy_total = _one(d, "SELECT COUNT(*) AS c FROM strategies s WHERE NOT "
                           f"({_pred('s')})") or {}

    return {
        "scope": USER_POPULATION if not include_legacy else "ALL",
        "predicate": SCOPE_PREDICATE,
        "include_legacy": bool(include_legacy),
        "population_total": int(pop_total.get("c") or 0),
        "legacy_excluded_total": int(legacy_total.get("c") or 0),
        "total": total,
        "offset": offset,
        "limit": limit,
        "returned": len(rows),
        "all_rows": all_rows,
        "truncated": (not all_rows) and (offset + len(rows) < total),
        "pages": 1 if all_rows else ((total + limit - 1) // limit if limit else 0),
        "sort": sort_key,
        "dir": direction.lower(),
        "filters": applied,
        "nodes": [_row_payload(r) for r in rows],
        "labels": _LABELS,
        "generated_at": time.time(),
    }


def _FROM_WHERE(where: str) -> str:
    """The FROM/JOIN body shared by the count query (no SELECT list needed)."""
    return (" FROM strategies s "
            "LEFT JOIN bt_ranked bt ON bt.sid = s.id AND bt.rn = 1 "
            "LEFT JOIN v_ranked  v  ON v.sid  = s.id AND v.rn  = 1 "
            f"WHERE {where}")


def _default_db() -> Any:
    from ..db.database import get_db
    return get_db()


# --------------------------------------------------------------------------- #
# facets (filter options + research population summary)
# --------------------------------------------------------------------------- #
def facets(db: Any = None, include_legacy: bool = False) -> Dict[str, Any]:
    """Everything the Strategy Lab filter bar needs, in one request.

    Option lists are data-driven (GROUP BY over the scoped population) so the
    UI never hard-codes a symbol/generation list that the data does not have.
    """
    d = db or _default_db()
    scope = _pred("s") if not include_legacy else "1=1"

    def options(sql: str, args: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        return [{"value": r.get("v"), "count": int(r.get("c") or 0)}
                for r in _rows(d, sql, args) if r.get("v") is not None]

    generations = options(f"SELECT s.generation AS v, COUNT(*) AS c FROM strategies s "
                          f"WHERE {scope} GROUP BY s.generation ORDER BY s.generation DESC")
    symbols = options(f"SELECT s.symbol AS v, COUNT(*) AS c FROM strategies s "
                      f"WHERE {scope} GROUP BY s.symbol ORDER BY c DESC, v ASC")
    timeframes = options(f"SELECT s.timeframe AS v, COUNT(*) AS c FROM strategies s "
                         f"WHERE {scope} GROUP BY s.timeframe ORDER BY c DESC, v ASC")
    statuses = options(f"SELECT s.status AS v, COUNT(*) AS c FROM strategies s "
                       f"WHERE {scope} GROUP BY s.status ORDER BY c DESC, v ASC")
    directions = options(f"SELECT s.direction AS v, COUNT(*) AS c FROM strategies s "
                         f"WHERE {scope} GROUP BY s.direction ORDER BY c DESC, v ASC")

    def scoped_count(extra_from: str, extra_where: str = "") -> int:
        row = _one(d, f"""SELECT COUNT(DISTINCT x.strategy_id) AS c
                          FROM {extra_from} x JOIN strategies s ON s.id = x.strategy_id
                          WHERE {scope} {extra_where}""") or {}
        return int(row.get("c") or 0)

    backtested = scoped_count("backtests")
    validated = scoped_count("validations")
    validation_passed = scoped_count("validations", "AND x.passed")
    qualified_row = _one(d, f"SELECT COUNT(*) AS c FROM strategies s WHERE {scope} "
                            "AND UPPER(s.status) = 'QUALIFIED'") or {}
    shortlisted_row = _one(d, f"SELECT COUNT(*) AS c FROM research_shortlist r "
                              f"JOIN strategies s ON s.id = r.strategy_id WHERE {scope}") or {}

    return {
        "scope": USER_POPULATION if not include_legacy else "ALL",
        "predicate": SCOPE_PREDICATE,
        "include_legacy": bool(include_legacy),
        "scope_info": scope_info(d),
        "population": population(d),
        "options": {
            "generation": generations,
            "symbol": symbols,
            "timeframe": timeframes,
            "status": statuses,
            "direction": directions,
            "result_state": [
                {"value": "backtested", "count": backtested},
                {"value": "validated", "count": validated},
                {"value": "validation_passed", "count": validation_passed},
                {"value": "qualified", "count": int(qualified_row.get("c") or 0)},
                {"value": "shortlisted", "count": int(shortlisted_row.get("c") or 0)},
            ],
            "stage": [{"value": "detail"}, {"value": "screen"}, {"value": "none"}],
        },
        "sorts": _SORT_OPTIONS,
        "limits": {"default": DEFAULT_LIMIT, "max": MAX_LIMIT,
                   "max_compare": MAX_COMPARE, "max_matrix_rows": MAX_MATRIX_ROWS},
        "labels": _LABELS,
        "generated_at": time.time(),
    }


# --------------------------------------------------------------------------- #
# backtest matrix (comparison table over existing results)
# --------------------------------------------------------------------------- #
def matrix(db: Any = None, ids: Any = None, limit: int = DEFAULT_LIMIT, offset: int = 0,
           sort: str = "return", dir: str = "desc", include_legacy: bool = False,
           **filters: Any) -> Dict[str, Any]:
    """The comparison matrix: one row per node with the stored research results.

    ``ids`` selects explicit nodes (the Strategy Lab selection); otherwise the
    same filters/sort/pagination as the strategy list are applied. Research
    columns and the separate, clearly labelled execution-record column are
    never combined into one number.
    """
    d = db or _default_db()
    id_list = [i for i in (_as_int(x) for x in _as_list(ids)) if i is not None]
    id_list = list(dict.fromkeys(id_list))

    if id_list:
        marks = ",".join("?" * len(id_list))
        rows = _rows(d, f"{_CTE}{_SELECT_COLS} WHERE s.id IN ({marks})", id_list)
        order = {sid: idx for idx, sid in enumerate(id_list)}
        rows.sort(key=lambda r: order.get(int(r.get("id") or 0), 10 ** 9))
        missing = [sid for sid in id_list if sid not in {int(r.get("id") or 0) for r in rows}]
        mode, total_matching, offset, limit = "ids", len(rows), 0, len(rows)
    else:
        limit = max(1, min(_as_int(limit) or DEFAULT_LIMIT, MAX_MATRIX_ROWS))
        offset = max(0, _as_int(offset) or 0)
        sort_key = (sort or "return").strip().lower()
        if sort_key not in SORTS:
            sort_key = "return"
        direction = "ASC" if str(dir or "desc").strip().lower() == "asc" else "DESC"
        where, args, _applied = _build_where(include_legacy=bool(include_legacy), **filters)
        total_row = _one(d, f"{_CTE} SELECT COUNT(*) AS c {_FROM_WHERE(where)}", args) or {}
        total_matching = int(total_row.get("c") or 0)
        rows = _rows(d, f"{_CTE}{_SELECT_COLS} WHERE {where} ORDER BY "
                        f"{SORTS[sort_key]} {direction} NULLS LAST, s.id ASC LIMIT ? OFFSET ?",
                     list(args) + [limit, offset])
        missing = []
        mode = "filter"

    payload_rows = []
    for r in rows:
        row = _row_payload(r)
        # The matrix flattens the scalar research columns for table rendering but
        # keeps the nested blocks as the authoritative shape.
        flat = {c["key"]: None for c in MATRIX_COLUMNS}
        flat.update({
            "id": row["id"],
            "generation": row["generation"],
            "symbol": row["symbol"],
            "timeframe": row["timeframe"],
            "status": row["status"],
            "stage": row["stage"],
            "trades": row["research"]["trades"],
            "wins": row["research"]["wins"],
            "losses": row["research"]["losses"],
            "win_rate": row["research"]["win_rate"],
            "net_profit": row["research"]["net_profit"],
            "return_pct": row["research"]["return_pct"],
            "max_drawdown_pct": row["research"]["max_drawdown_pct"],
            "profit_factor": row["research"]["profit_factor"],
            "expectancy": row["research"]["expectancy"],
            "reward_risk_ratio": row["economics"]["reward_risk_ratio"],
            "validation_passed": row["validation"]["passed"],
            "robustness_score": row["validation"]["robustness_score"],
            "qualified": row["qualified"],
            "execution_records": row["execution"],
        })
        payload_rows.append({
            "row": flat,
            "research": row["research"],
            "validation": row["validation"],
            "economics": row["economics"],
            "execution": row["execution"],
            "node": {"id": row["id"], "node_id": row["node_id"], "status": row["status"],
                     "generation": row["generation"], "symbol": row["symbol"],
                     "timeframe": row["timeframe"], "research_eligible": row["research_eligible"],
                     "data_source": row["data_source"], "qualified": row["qualified"],
                     "shortlisted": row["shortlisted"],
                     "research_node_num": row["research_node_num"]},
        })

    return {
        "scope": USER_POPULATION if not include_legacy else "ALL",
        "predicate": SCOPE_PREDICATE,
        "include_legacy": bool(include_legacy),
        "mode": mode,
        "requested_ids": id_list,
        "missing_ids": missing,
        "count": len(payload_rows),
        "total_matching": total_matching,
        "offset": offset,
        "limit": limit,
        "columns": MATRIX_COLUMNS,
        "rows": payload_rows,
        "labels": _LABELS,
        "notes": [
            "Research columns come from the stored backtest / validation records of "
            "USER_RESEARCH nodes; nothing is re-simulated here.",
            "Wins / losses are derived from two stored values (trades x win rate) and are "
            "flagged as derived; a node without both stays N/A.",
            "Execution records are shown in their own column and are informational only: "
            "they never become research statistics.",
        ],
        "generated_at": time.time(),
    }


# --------------------------------------------------------------------------- #
# comparison (side-by-side, reuses the V4.4 node statistics)
# --------------------------------------------------------------------------- #
def compare(db: Any = None, ids: Any = None) -> Dict[str, Any]:
    """Side-by-side comparison of a handful of strategies.

    Reuses the V4.4 per-node statistics builder for every selected node
    (identity, strategy definition, research results, node economics and the
    separate execution records) - no second formula system, no second
    node-detail implementation.
    """
    d = db or _default_db()
    id_list = [i for i in (_as_int(x) for x in _as_list(ids)) if i is not None]
    id_list = list(dict.fromkeys(id_list))
    if not id_list:
        return {"scope": USER_POPULATION, "error": "no strategy ids given",
                "count": 0, "max_compare": MAX_COMPARE, "rows": [], "not_found": [],
                "generated_at": time.time()}
    if len(id_list) > MAX_COMPARE:
        return {"scope": USER_POPULATION,
                "error": f"at most {MAX_COMPARE} strategies can be compared at once "
                         f"({len(id_list)} requested)",
                "count": 0, "max_compare": MAX_COMPARE, "rows": [],
                "not_found": id_list[MAX_COMPARE:], "generated_at": time.time()}

    rows: List[Dict[str, Any]] = []
    not_found: List[int] = []
    for sid in id_list:
        node = node_stats(sid, d)
        if not node:
            not_found.append(sid)
            continue
        r = node.get("research") or {}
        bt = r.get("backtest") or {}
        val = r.get("validation") or {}
        node_meta = node.get("node") or {}
        rows.append({
            "id": sid,
            "identity": {
                "node_id": node_meta.get("node_id"),
                "research_node_num": node_meta.get("research_node_num"),
                "generation": node_meta.get("generation"),
                "status": node_meta.get("status"),
                "symbol": node_meta.get("symbol"),
                "timeframe": node_meta.get("timeframe"),
                "direction": node_meta.get("direction"),
                "fitness": node_meta.get("fitness"),
                "run_id": node_meta.get("run_id"),
                "data_source": node_meta.get("data_source"),
                "research_eligible": node_meta.get("research_eligible"),
                "scope_note": node_meta.get("scope_note"),
                "shortlisted": node_meta.get("shortlisted"),
                "pipeline_stage": node_meta.get("pipeline_stage"),
                "parent_id": node_meta.get("parent_id"),
            },
            "definition": {
                "layer": "STRATEGY DEFINITION",
                "indicators": r.get("parameters", {}).get("indicators"),
                "entry_conditions": r.get("parameters", {}).get("entry_conditions"),
                "exit_conditions": r.get("parameters", {}).get("exit_conditions"),
                "position_management": r.get("parameters", {}).get("position_management"),
                "source": "strategies.genome (stored research parameters)",
            },
            "research_results": {
                "layer": r.get("layer"),
                "qualification": {
                    "status": node_meta.get("status"),
                    "qualified": str(node_meta.get("status") or "").upper() == "QUALIFIED",
                    "pipeline_stage": node_meta.get("pipeline_stage"),
                    "validation_passed": val.get("passed"),
                },
                "backtest": bt,
                "validation": val,
                "returns": r.get("returns"),
                "profit_factors": r.get("profit_factors"),
                "robustness_score": r.get("robustness_score"),
                "trades": bt.get("trades"),
                "win_rate": bt.get("win_rate"),
                "net_profit": bt.get("net_profit"),
                "max_drawdown_pct": bt.get("max_drawdown_pct"),
                "profit_factor": bt.get("profit_factor"),
                "expectancy": bt.get("expectancy"),
            },
            "node_economics": node.get("economics"),
            "execution": node.get("execution"),
        })

    return {
        "scope": USER_POPULATION,
        "count": len(rows),
        "requested_ids": id_list,
        "max_compare": MAX_COMPARE,
        "not_found": not_found,
        "layers": {
            "research": _LABELS["research"],
            "execution": _LABELS["execution_note"],
            "economics": "Reused from the V4.4 node economics (single formula system); "
                         "unsupported values stay N/A.",
        },
        "rows": rows,
        "generated_at": time.time(),
    }
