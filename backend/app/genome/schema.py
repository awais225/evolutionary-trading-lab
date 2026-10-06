"""
Strategy Genome — controlled JSON DSL/AST.

A genome is DATA, never code. No arbitrary Python is allowed anywhere in a
strategy definition. Structure:

{
  "symbol": "XAUUSD",
  "timeframe": "M15",
  "direction": "both" | "long" | "short",
  "features": ["ema:20", "ema:50", "rsi:14"],       # primary indicators (bounded)
  "entry_long":  <condition AST>,
  "entry_short": <condition AST>,
  "exit": {
      "sl_atr_mult": 1.5,          # stop loss = entry -/+ mult * ATR
      "tp_atr_mult": 2.5,          # take profit
      "atr_spec": "atr:14",
      "trailing": null | {"atr_mult": 2.0, "activation_mult": 1.0},
      "min_hold_bars": 1,
      "max_hold_bars": 48,
      "exit_condition": <AST> | null
  },
  "sessions": null | ["london","newyork", ...],
  "days": null | [0..4],            # 0=Mon
  "regime_filters": null | ["trending", "high_volatility", ...],
  "risk": {"risk_per_trade": 0.005, "max_concurrent": 1}
}

Condition AST nodes:
  {"op":"and"|"or", "clauses":[node,...]}
  {"op":"not", "clause":node}
  {"type":"compare", "left":"rsi:14", "cmp":">"|"<'|">="|"<="|"==", "right":55 or "ema:20"}
  {"type":"crossover", "a":"ema:20", "b":"ema:50", "dir":"up"|"down"}
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Set, Tuple

from ..features.library import feature_family

VALID_TIMEFRAMES = {"M1", "M5", "M15", "M30", "H1"}
VALID_SESSIONS = {"asia", "london", "newyork", "london_ny_overlap", "all"}
VALID_REGIMES = {"trending", "ranging", "breakout", "high_volatility",
                 "low_volatility", "expansion", "compression", "momentum"}
VALID_CMPS = {">", "<", ">=", "<=", "=="}
DIRECTIONS = {"both", "long", "short"}


class GenomeError(ValueError):
    pass


def canonical(genome: Dict[str, Any]) -> str:
    """Canonical JSON (sorted keys, stable floats) used for hashing/dupes."""
    return json.dumps(genome, sort_keys=True, separators=(",", ":"), default=str)


def genome_hash(genome: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical(genome).encode()).hexdigest()[:32]


# ---------------- validation ----------------

def _validate_condition(node: Any, known: Set[str], depth: int, max_depth: int,
                        counters: Dict[str, int]) -> None:
    if depth > max_depth:
        raise GenomeError(f"condition depth exceeds {max_depth}")
    if not isinstance(node, dict):
        raise GenomeError("condition node must be an object")
    if "op" in node:
        op = node["op"]
        counters["conditions"] += 1
        if op in ("and", "or"):
            clauses = node.get("clauses")
            if not isinstance(clauses, list) or not (1 <= len(clauses) <= 6):
                raise GenomeError("and/or needs 1..6 clauses")
            for c in clauses:
                _validate_condition(c, known, depth + 1, max_depth, counters)
        elif op == "not":
            _validate_condition(node.get("clause"), known, depth + 1, max_depth, counters)
        else:
            raise GenomeError(f"unknown op {op}")
        return
    t = node.get("type")
    if t == "compare":
        counters["conditions"] += 1
        left = node.get("left")
        if not isinstance(left, str) or feature_family(left) not in known:
            raise GenomeError(f"compare.left '{left}' not in genome features")
        if node.get("cmp") not in VALID_CMPS:
            raise GenomeError(f"bad cmp {node.get('cmp')}")
        right = node.get("right")
        if isinstance(right, str):
            if feature_family(right) not in known:
                raise GenomeError(f"compare.right '{right}' not in genome features")
        elif not isinstance(right, (int, float)):
            raise GenomeError("compare.right must be number or feature spec")
    elif t == "crossover":
        counters["conditions"] += 1
        for k in ("a", "b"):
            v = node.get(k)
            if not isinstance(v, str) or feature_family(v) not in known:
                raise GenomeError(f"crossover.{k} '{v}' not in genome features")
        if node.get("dir") not in ("up", "down"):
            raise GenomeError("crossover.dir must be up|down")
    else:
        raise GenomeError(f"unknown condition node: {node}")


def validate_genome(g: Dict[str, Any], max_indicators: int = 8,
                    max_conditions: int = 8, max_depth: int = 3) -> None:
    """Raise GenomeError if the genome violates the DSL rules."""
    if not isinstance(g, dict):
        raise GenomeError("genome must be an object")
    if g.get("symbol") is None:
        raise GenomeError("missing symbol")
    if g.get("timeframe") not in VALID_TIMEFRAMES:
        raise GenomeError(f"bad timeframe {g.get('timeframe')}")
    if g.get("direction", "both") not in DIRECTIONS:
        raise GenomeError("bad direction")
    feats = g.get("features")
    if not isinstance(feats, list) or not feats:
        raise GenomeError("features must be a non-empty list")
    if len(feats) > max_indicators:
        raise GenomeError(f"too many indicators ({len(feats)} > {max_indicators})")
    if len(set(feature_family(f) for f in feats)) != len(set(feats)) and len(set(feats)) != len(feats):
        raise GenomeError("duplicate feature specs")
    known = {feature_family(f) for f in feats} | {"price", "time", "sessions"}

    counters = {"conditions": 0}
    has_long = g.get("entry_long") is not None
    has_short = g.get("entry_short") is not None
    direction = g.get("direction", "both")
    if direction in ("both", "long") and not has_long:
        raise GenomeError("direction requires entry_long")
    if direction in ("both", "short") and not has_short:
        raise GenomeError("direction requires entry_short")
    for key in ("entry_long", "entry_short"):
        if g.get(key) is not None:
            _validate_condition(g[key], known, 0, max_depth, counters)
    if counters["conditions"] == 0:
        raise GenomeError("genome has no entry conditions")
    if counters["conditions"] > max_conditions:
        raise GenomeError(f"too many conditions ({counters['conditions']} > {max_conditions})")

    ex = g.get("exit") or {}
    sl = ex.get("sl_atr_mult")
    tp = ex.get("tp_atr_mult")
    if sl is not None and not (0.2 <= float(sl) <= 10.0):
        raise GenomeError("sl_atr_mult out of range")
    if tp is not None and not (0.2 <= float(tp) <= 20.0):
        raise GenomeError("tp_atr_mult out of range")
    tr = ex.get("trailing")
    if tr is not None:
        if not (0.3 <= float(tr.get("atr_mult", 0)) <= 10.0):
            raise GenomeError("trailing atr_mult out of range")
    mh = int(ex.get("max_hold_bars", 48))
    if not (1 <= mh <= 2000):
        raise GenomeError("max_hold_bars out of range")
    if int(ex.get("min_hold_bars", 0)) > mh:
        raise GenomeError("min_hold > max_hold")

    ss = g.get("sessions")
    if ss is not None:
        if not isinstance(ss, list) or any(s not in VALID_SESSIONS for s in ss):
            raise GenomeError("bad sessions")
    days = g.get("days")
    if days is not None:
        if not isinstance(days, list) or any(not (0 <= int(d) <= 4) for d in days):
            raise GenomeError("bad days")
    rf = g.get("regime_filters")
    if rf is not None:
        if not isinstance(rf, list) or any(r not in VALID_REGIMES for r in rf):
            raise GenomeError("bad regime_filters")
    risk = g.get("risk") or {}
    rpt = float(risk.get("risk_per_trade", 0.005))
    if not (0.0005 <= rpt <= 0.02):
        raise GenomeError("risk_per_trade out of allowed band [0.05%, 2%]")


def complexity(genome: Dict[str, Any]) -> Tuple[int, int, int]:
    """(n_indicators, n_conditions, complexity_score)."""
    feats = genome.get("features") or []
    counters = {"conditions": 0}
    try:
        for key in ("entry_long", "entry_short"):
            if genome.get(key) is not None:
                _validate_condition(genome[key], set(), 0, 99, counters)
    except GenomeError:
        pass
    ncond = counters["conditions"]
    ex = genome.get("exit") or {}
    extras = sum(1 for k in ("trailing", "exit_condition") if ex.get(k))
    extras += sum(1 for k in ("sessions", "days", "regime_filters") if genome.get(k))
    score = len(feats) + ncond + extras
    return len(feats), ncond, score


def referenced_features(genome: Dict[str, Any]) -> Set[str]:
    """All feature specs referenced anywhere in the genome."""
    out: Set[str] = set()

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if "op" in node:
            for c in node.get("clauses", []):
                walk(c)
            walk(node.get("clause"))
            return
        if node.get("type") == "compare":
            out.add(node["left"])
            if isinstance(node.get("right"), str):
                out.add(node["right"])
        elif node.get("type") == "crossover":
            out.add(node["a"]); out.add(node["b"])

    walk(genome.get("entry_long"))
    walk(genome.get("entry_short"))
    ex = genome.get("exit") or {}
    walk(ex.get("exit_condition"))
    if ex.get("atr_spec"):
        out.add(ex["atr_spec"])
    return out


def species_key(genome: Dict[str, Any]) -> str:
    """Diversity grouping: timeframe + direction + indicator families."""
    fams = sorted({feature_family(f) for f in genome.get("features", [])})
    return f"{genome.get('timeframe')}|{genome.get('direction','both')}|{','.join(fams)}"


def describe(genome: Dict[str, Any]) -> str:
    """Human readable one-liner for the research history."""
    parts = []
    def walk(node, depth=0):
        if not isinstance(node, dict):
            return "?"
        if node.get("op") in ("and", "or"):
            inner = " {} ".format(node["op"].upper()).join(walk(c, depth+1) for c in node["clauses"])
            return f"({inner})" if depth else inner
        if node.get("op") == "not":
            return f"NOT {walk(node['clause'], depth+1)}"
        if node.get("type") == "compare":
            return f"{node['left']} {node['cmp']} {node['right']}"
        if node.get("type") == "crossover":
            return f"{node['a']} x-{node['dir']} {node['b']}"
        return "?"
    if genome.get("entry_long"):
        parts.append("L: " + walk(genome["entry_long"]))
    if genome.get("entry_short"):
        parts.append("S: " + walk(genome["entry_short"]))
    ex = genome.get("exit") or {}
    parts.append(f"SL={ex.get('sl_atr_mult')}xATR TP={ex.get('tp_atr_mult')}xATR "
                 f"maxHold={ex.get('max_hold_bars')}")
    if genome.get("sessions"):
        parts.append("sessions=" + ",".join(genome["sessions"]))
    if genome.get("days") is not None and len(genome["days"]) < 5:
        names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        parts.append("days=" + ",".join(names[d] for d in genome["days"]))
    if genome.get("regime_filters"):
        parts.append("regime=" + ",".join(genome["regime_filters"]))
    return " | ".join(parts)
