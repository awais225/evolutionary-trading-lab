"""
Genome operations: random generation, mutation (all types from the spec),
crossover, and directed mutations used by specialization / AI hypotheses.

Every operator returns (child_genome, mutation_type, description).
Children are always re-validated; invalid children are rejected by callers.
"""
from __future__ import annotations

import copy
import random
from typing import Any, Dict, List, Optional, Tuple

from .schema import (VALID_TIMEFRAMES, VALID_SESSIONS, VALID_REGIMES, DIRECTIONS,
                     validate_genome, GenomeError)
from ..features.library import feature_family, PRIMARY_FAMILIES

TF_LIST = ["M1", "M5", "M15", "M30", "H1"]

# plausible parameter grids per family
PARAM_GRID = {
    "sma": [10, 20, 50, 100, 200],
    "ema": [9, 14, 20, 34, 50, 100, 200],
    "rsi": [7, 10, 14, 21],
    "macd": [(12, 26, 9), (8, 21, 5)],
    "roc": [6, 12, 24],
    "momentum": [5, 10, 20],
    "stoch": [(14, 3), (9, 3)],
    "cci": [14, 20, 34],
    "adx": [10, 14, 21],
    "atr": [7, 10, 14, 21],
    "bb": [(20, 2.0), (20, 2.5), (50, 2.0)],
    "volatility": [20, 50],
    "volume": [20, 50],
    "vwap": [()],
    "prev_day": [()],
    "regime": [()],
}


def spec_with_params(family: str, params) -> str:
    if family in ("vwap", "prev_day", "regime"):
        return family
    if isinstance(params, tuple):
        return family + ":" + ":".join(str(p) for p in params)
    return f"{family}:{params}"


def rand_spec(family: str, rng: random.Random) -> str:
    return spec_with_params(family, rng.choice(PARAM_GRID[family]))


# ---------------- condition templates ----------------

def rand_condition_for(family: str, spec: str, rng: random.Random,
                       other_specs: List[str]) -> Optional[Dict]:
    """A sensible random leaf condition using `spec` (and possibly others)."""
    if family in ("ema", "sma"):
        others = [s for s in other_specs
                  if feature_family(s) in ("ema", "sma") and s != spec]
        if others and rng.random() < 0.6:
            b = rng.choice(others)
            return {"type": "crossover", "a": spec, "b": b,
                    "dir": rng.choice(["up", "down"])}
        return {"type": "compare", "left": spec,
                "cmp": rng.choice([">", "<"]), "right": "close"}
    if family == "rsi":
        thr = rng.choice([25, 30, 35, 45, 50, 55, 60, 65, 70, 75])
        cmp = ">" if thr >= 50 else "<"
        return {"type": "compare", "left": spec, "cmp": cmp, "right": thr}
    if family == "macd":
        return {"type": "compare", "left": f"macd_hist:{spec.split(':', 1)[1]}",
                "cmp": rng.choice([">", "<"]), "right": 0.0}
    if family == "adx":
        return {"type": "compare", "left": spec, "cmp": ">",
                "right": rng.choice([18, 20, 22, 25, 28, 30, 35])}
    if family in ("roc", "momentum"):
        return {"type": "compare", "left": spec, "cmp": rng.choice([">", "<"]),
                "right": round(rng.uniform(-0.5, 0.5), 3) if family == "roc"
                else round(rng.uniform(-2.0, 2.0), 2)}
    if family == "stoch":
        p = spec.split(":")
        k = f"stoch_k:{p[1]}:{p[2]}"
        d = f"stoch_d:{p[1]}:{p[2]}"
        if rng.random() < 0.5:
            return {"type": "compare", "left": k, "cmp": rng.choice([">", "<"]),
                    "right": rng.choice([20, 25, 50, 75, 80])}
        return {"type": "crossover", "a": k, "b": d, "dir": rng.choice(["up", "down"])}
    if family == "cci":
        return {"type": "compare", "left": spec, "cmp": rng.choice([">", "<"]),
                "right": rng.choice([-200, -100, -50, 0, 50, 100, 200])}
    if family == "atr":
        return {"type": "compare", "left": f"atr_pct:{spec.split(':', 1)[1]}",
                "cmp": rng.choice([">", "<"]),
                "right": round(rng.uniform(0.03, 0.35), 3)}
    if family == "bb":
        p = spec.split(":")
        pctb = f"bb_pctb:{p[1]}:{p[2]}"
        return {"type": "compare", "left": pctb, "cmp": rng.choice([">", "<"]),
                "right": round(rng.choice([0.05, 0.15, 0.5, 0.85, 0.95]), 2)}
    if family == "volatility":
        p = spec.split(":", 1)[1]
        return {"type": "compare", "left": f"range_expansion:{p}",
                "cmp": rng.choice([">", "<"]), "right": round(rng.uniform(0.7, 1.4), 2)}
    if family == "volume":
        p = spec.split(":", 1)[1]
        return {"type": "compare", "left": f"relvol:{p}", "cmp": ">",
                "right": round(rng.uniform(1.0, 2.5), 2)}
    if family == "vwap":
        return {"type": "compare", "left": "vwap_dist", "cmp": rng.choice([">", "<"]),
                "right": round(rng.uniform(-0.4, 0.4), 3)}
    if family == "prev_day":
        return {"type": "compare", "left": rng.choice(
            ["break_prev_day_high", "break_prev_day_low"]), "cmp": ">", "right": 0.5}
    if family == "regime":
        return {"type": "compare",
                "left": f"regime:{rng.choice(sorted(VALID_REGIMES))}",
                "cmp": ">", "right": 0.5}
    return None


def build_entry_ast(specs: List[str], rng: random.Random,
                    n_conditions: int) -> Optional[Dict]:
    leaves = []
    for _ in range(n_conditions):
        spec = rng.choice(specs)
        fam = feature_family(spec)
        leaf = rand_condition_for(fam, spec, rng, specs)
        if leaf is not None:
            leaves.append(leaf)
    if not leaves:
        return None
    # dedupe identical leaves
    uniq = []
    seen = set()
    for l in leaves:
        key = str(sorted(l.items()))
        if key not in seen:
            seen.add(key)
            uniq.append(l)
    if len(uniq) == 1:
        return uniq[0]
    node: Dict[str, Any] = {"op": "and", "clauses": uniq[:3]}
    if rng.random() < 0.15 and len(uniq) > 2:
        node = {"op": "or", "clauses": [uniq[0], {"op": "and", "clauses": uniq[1:3]}]}
    return node


# ---------------- random genome ----------------

def random_genome(symbol: str, rng: random.Random, max_indicators: int = 3,
                  timeframe: Optional[str] = None,
                  direction: Optional[str] = None) -> Dict[str, Any]:
    tf = timeframe or rng.choice(TF_LIST)
    n_ind = rng.randint(1, max(1, min(max_indicators, 3)))
    families = rng.sample(PRIMARY_FAMILIES, n_ind)
    specs = []
    for fam in families:
        specs.append(rand_spec(fam, rng))
    if rng.random() < 0.45 and n_ind >= 2:
        # often include a trend MA pair for crossovers
        pass
    n_cond = rng.randint(1, min(3, max(1, n_ind)))
    direction = direction or rng.choice(["both", "both", "long", "short"])
    entry_long = build_entry_ast(specs, rng, n_cond) if direction in ("both", "long") else None
    entry_short = build_entry_ast(specs, rng, n_cond) if direction in ("both", "short") else None
    if direction == "both" and (entry_long is None or entry_short is None):
        direction = "long" if entry_long else "short"
        entry_long, entry_short = (entry_long, None) if direction == "long" else (None, entry_short)

    ex = {
        "atr_spec": rand_spec("atr", rng) if "atr" not in {feature_family(s) for s in specs}
                      else next(s for s in specs if feature_family(s) == "atr"),
        "sl_atr_mult": round(rng.uniform(0.7, 3.0), 2),
        "tp_atr_mult": round(rng.uniform(0.8, 5.0), 2),
        "trailing": ({"atr_mult": round(rng.uniform(1.0, 3.0), 2),
                      "activation_mult": round(rng.uniform(0.5, 2.0), 2)}
                     if rng.random() < 0.25 else None),
        "min_hold_bars": rng.choice([0, 1, 1, 2, 3]),
        "max_hold_bars": rng.choice([12, 24, 48, 72, 96, 144]),
        "exit_condition": None,
    }
    g = {
        "symbol": symbol,
        "timeframe": tf,
        "direction": direction,
        "features": specs,
        "entry_long": entry_long,
        "entry_short": entry_short,
        "exit": ex,
        "sessions": None,
        "days": None,
        "regime_filters": None,
        "risk": {"risk_per_trade": 0.005, "max_concurrent": 1},
    }
    if rng.random() < 0.25:
        g["sessions"] = rng.sample(["asia", "london", "newyork"], rng.randint(1, 2))
    if rng.random() < 0.15:
        g["days"] = sorted(rng.sample(range(5), rng.randint(3, 4)))
    if rng.random() < 0.15:
        g["regime_filters"] = rng.sample(sorted(VALID_REGIMES), 1)
    return g


# ---------------- AST helpers ----------------

def iter_leaves(node: Any):
    if not isinstance(node, dict):
        return
    if "op" in node:
        for c in node.get("clauses", []):
            yield from iter_leaves(c)
        if node.get("clause") is not None:
            yield from iter_leaves(node["clause"])
    else:
        yield node


def top_clauses(node: Any) -> List[Dict]:
    if isinstance(node, dict) and node.get("op") in ("and", "or"):
        return list(node["clauses"])
    return [node] if isinstance(node, dict) else []


def rebuild(clauses: List[Dict], op: str = "and") -> Optional[Dict]:
    clauses = [c for c in clauses if c]
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"op": op, "clauses": clauses[:6]}


def leaves_referencing(node: Any, families: set) -> List[Dict]:
    return [l for l in iter_leaves(node)
            if any(feature_family(str(l.get(k, ""))) in families
                   for k in ("left", "right", "a", "b"))]


# ---------------- mutation ----------------

MUTATION_TYPES = [
    "param_mutation", "indicator_replacement", "indicator_addition",
    "indicator_removal", "entry_condition_mutation", "exit_condition_mutation",
    "sl_mutation", "tp_mutation", "timeframe_mutation", "session_mutation",
    "day_mutation", "regime_mutation", "min_hold_mutation", "max_hold_mutation",
    "crossover", "random_exploration",
]


def _perturb_num(v: float, rng: random.Random, scale: float = 0.15) -> float:
    return v * (1.0 + rng.uniform(-scale, scale))


def _mutate_ast_params(node: Any, rng: random.Random) -> Tuple[Any, int]:
    """Jitter numeric thresholds inside an AST."""
    n_changed = 0
    node = copy.deepcopy(node)

    def walk(x):
        nonlocal n_changed
        if not isinstance(x, dict):
            return x
        if "op" in x:
            if x["op"] in ("and", "or"):
                x["clauses"] = [walk(c) for c in x["clauses"]]
            else:
                x["clause"] = walk(x["clause"])
            return x
        if x.get("type") == "compare" and isinstance(x.get("right"), (int, float)):
            if rng.random() < 0.55:
                r = x["right"]
                if abs(r) >= 1:
                    x["right"] = round(_perturb_num(float(r), rng), 2)
                else:
                    x["right"] = round(r + rng.uniform(-0.05, 0.05), 3)
                n_changed += 1
            elif rng.random() < 0.2:
                x["cmp"] = rng.choice([">", ">=", "<", "<="])
                n_changed += 1
        if x.get("type") == "crossover" and rng.random() < 0.2:
            x["dir"] = "down" if x["dir"] == "up" else "up"
            n_changed += 1
        return x

    return walk(node), n_changed


def mutate(genome: Dict[str, Any], rng: random.Random,
           mutation_type: Optional[str] = None,
           max_indicators: int = 8) -> Tuple[Dict, str, str]:
    g = copy.deepcopy(genome)
    mt = mutation_type or rng.choice([
        "param_mutation", "param_mutation", "param_mutation",
        "entry_condition_mutation", "entry_condition_mutation",
        "indicator_addition", "indicator_removal", "indicator_replacement",
        "sl_mutation", "tp_mutation", "timeframe_mutation", "session_mutation",
        "day_mutation", "regime_mutation", "min_hold_mutation", "max_hold_mutation",
    ])
    desc = mt
    ex = g.setdefault("exit", {})

    if mt == "param_mutation":
        # jitter indicator periods, rewriting every reference in the ASTs
        DERIVED = {"macd_hist": "macd", "macd_signal": "macd", "stoch_k": "stoch",
                   "stoch_d": "stoch", "bb_upper": "bb", "bb_lower": "bb",
                   "bb_mid": "bb", "bb_width": "bb", "bb_pctb": "bb",
                   "atr_pct": "atr", "range_expansion": "volatility",
                   "rolling_vol": "volatility", "relvol": "volume",
                   "volume_ma": "volume"}

        def make_fix(old: str, new: str):
            po, pn = old.split(":"), new.split(":")

            def fix(v):
                if not isinstance(v, str):
                    return v
                if v == old:
                    return new
                parts = v.split(":")
                if len(po) > 1 and parts[1:] == po[1:]:
                    fam = DERIVED.get(parts[0], parts[0])
                    if fam == po[0]:
                        return ":".join([parts[0]] + pn[1:])
                return v
            return fix

        def walk_fix(node, fix):
            if isinstance(node, dict):
                return {k: walk_fix(v, fix) for k, v in node.items()}
            if isinstance(node, list):
                return [walk_fix(v, fix) for v in node]
            return fix(node) if isinstance(node, str) else node

        changed = []
        for s in list(g["features"]):
            fam = feature_family(s)
            if fam in PARAM_GRID and PARAM_GRID[fam] != [()] and rng.random() < 0.4:
                ns = rand_spec(fam, rng)
                if ns != s:
                    changed.append((s, ns))
        for old, new in changed:
            fix = make_fix(old, new)
            g["features"] = [fix(s) for s in g["features"]]
            g["entry_long"] = walk_fix(g.get("entry_long"), fix)
            g["entry_short"] = walk_fix(g.get("entry_short"), fix)
            if ex.get("atr_spec"):
                ex["atr_spec"] = fix(ex["atr_spec"])
            if ex.get("exit_condition"):
                ex["exit_condition"] = walk_fix(ex["exit_condition"], fix)
        if changed:
            desc = "param mutation: " + ", ".join(f"{o}->{n}" for o, n in changed)
        else:
            n1 = n2 = 0
            if g.get("entry_long"):
                g["entry_long"], n1 = _mutate_ast_params(g["entry_long"], rng)
            if g.get("entry_short"):
                g["entry_short"], n2 = _mutate_ast_params(g["entry_short"], rng)
            desc = f"threshold jitter ({n1 + n2} values)"

    elif mt == "entry_condition_mutation":
        key = rng.choice([k for k in ("entry_long", "entry_short") if g.get(k)] or ["entry_long"])
        node = g.get(key)
        if node is not None:
            r = rng.random()
            clauses = top_clauses(node)
            if r < 0.4 and len(clauses) >= 1:
                clauses, n = _mutate_ast_params({"op": "and", "clauses": clauses}, rng)
                clauses = clauses["clauses"]
                desc = f"{key}: jittered thresholds"
            elif r < 0.6 and len(clauses) > 1:
                clauses.pop(rng.randrange(len(clauses)))
                desc = f"{key}: dropped a condition"
            elif r < 0.85:
                spec = rng.choice(g["features"])
                leaf = rand_condition_for(feature_family(spec), spec, rng, g["features"])
                if leaf:
                    clauses.append(leaf)
                    desc = f"{key}: added condition"
            else:
                if len(clauses) > 1:
                    clauses = [{"op": "not", "clause": clauses.pop(rng.randrange(len(clauses)))}] + clauses
                    desc = f"{key}: negated a condition"
            g[key] = rebuild(clauses, "and")

    elif mt == "indicator_addition":
        if len(g["features"]) < max_indicators:
            avail = [f for f in PRIMARY_FAMILIES
                     if f not in {feature_family(s) for s in g["features"]}]
            fam = rng.choice(avail)
            spec = rand_spec(fam, rng)
            g["features"].append(spec)
            leaf = rand_condition_for(fam, spec, rng, g["features"])
            key = rng.choice([k for k in ("entry_long", "entry_short") if g.get(k)])
            if leaf and key:
                clauses = top_clauses(g[key]) + [leaf]
                g[key] = rebuild(clauses, "and")
            desc = f"added indicator {spec}"

    elif mt == "indicator_removal":
        if len(g["features"]) > 1:
            spec = rng.choice(g["features"])
            fam = feature_family(spec)
            g["features"] = [s for s in g["features"] if s != spec]
            for key in ("entry_long", "entry_short"):
                if g.get(key):
                    remaining = [l for l in top_clauses(g[key])
                                 if not leaves_referencing(l, {fam})]
                    g[key] = rebuild(remaining, "and")
            if g.get("entry_long") is None and g.get("entry_short") is None:
                raise GenomeError("removal emptied all entries")
            if g["direction"] == "both" and (g["entry_long"] is None or g["entry_short"] is None):
                g["direction"] = "long" if g["entry_long"] else "short"
            desc = f"removed indicator {spec} (simplification)"

    elif mt == "indicator_replacement":
        idx = rng.randrange(len(g["features"]))
        old = g["features"][idx]
        fam_old = feature_family(old)
        avail = [f for f in PRIMARY_FAMILIES
                 if f not in {feature_family(s) for s in g["features"]}] or PRIMARY_FAMILIES
        fam_new = rng.choice(avail)
        new = rand_spec(fam_new, rng)
        leaf = rand_condition_for(fam_new, new, rng, g["features"])
        for key in ("entry_long", "entry_short"):
            if g.get(key):
                clauses = [l for l in top_clauses(g[key])
                           if not leaves_referencing(l, {fam_old})]
                if leaf and rng.random() < 0.8:
                    clauses.append(copy.deepcopy(leaf))
                g[key] = rebuild(clauses, "and")
        g["features"][idx] = new
        desc = f"replaced {old} with {new}"

    elif mt == "sl_mutation":
        ex["sl_atr_mult"] = round(max(0.3, _perturb_num(float(ex.get("sl_atr_mult", 1.5)), rng, 0.3)), 2)
        desc = f"SL -> {ex['sl_atr_mult']}xATR"
    elif mt == "tp_mutation":
        ex["tp_atr_mult"] = round(max(0.3, _perturb_num(float(ex.get("tp_atr_mult", 2.0)), rng, 0.3)), 2)
        desc = f"TP -> {ex['tp_atr_mult']}xATR"
    elif mt == "max_hold_mutation":
        ex["max_hold_bars"] = rng.choice([12, 24, 48, 72, 96, 144, 288])
        desc = f"max_hold -> {ex['max_hold_bars']} bars"
    elif mt == "min_hold_mutation":
        ex["min_hold_bars"] = rng.choice([0, 1, 2, 3, 5, 8])
        desc = f"min_hold -> {ex['min_hold_bars']} bars"
    elif mt == "timeframe_mutation":
        cur = g["timeframe"]
        i = TF_LIST.index(cur) if cur in TF_LIST else 2
        j = min(max(i + rng.choice([-1, 1]), 0), len(TF_LIST) - 1)
        g["timeframe"] = TF_LIST[j]
        desc = f"timeframe {cur} -> {g['timeframe']}"
    elif mt == "session_mutation":
        opts: List[Optional[List[str]]] = [None, ["london"], ["newyork"], ["asia"],
                                           ["london", "newyork"], ["london_ny_overlap"]]
        g["sessions"] = rng.choice([o for o in opts if o != g.get("sessions")])
        desc = f"sessions -> {g['sessions'] or 'all'}"
    elif mt == "day_mutation":
        if g.get("days") is None:
            g["days"] = sorted(set(range(5)) - {rng.randrange(5)})
            desc = f"excluded day {[d for d in range(5) if d not in g['days']]}"
        else:
            cand = sorted(set(range(5)) - set(g["days"]))
            if cand and rng.random() < 0.5:
                g["days"] = sorted(set(g["days"]) | {rng.choice(cand)})
            elif len(g["days"]) > 1:
                g["days"] = sorted(set(g["days"]) - {rng.choice(g["days"])})
            else:
                g["days"] = None
            desc = f"days -> {g['days'] if g['days'] is not None else 'all'}"
    elif mt == "regime_mutation":
        cur = set(g.get("regime_filters") or [])
        pool = sorted(VALID_REGIMES - cur)
        if cur and rng.random() < 0.4:
            cur = set()
            desc = "regime filter removed"
        elif pool:
            cur = {rng.choice(pool)}
            desc = f"regime filter -> {cur}"
        g["regime_filters"] = sorted(cur) or None
    elif mt == "exit_condition_mutation":
        r = rng.random()
        if r < 0.5 or ex.get("trailing"):
            ex["trailing"] = None
            desc = "trailing stop removed"
        else:
            ex["trailing"] = {"atr_mult": round(rng.uniform(1.0, 3.0), 2),
                              "activation_mult": round(rng.uniform(0.5, 2.0), 2)}
            desc = f"trailing stop added ({ex['trailing']['atr_mult']}xATR)"
    else:
        raise GenomeError(f"unknown mutation {mt}")

    return g, mt, desc


# ---------------- crossover ----------------

def crossover(g1: Dict[str, Any], g2: Dict[str, Any], rng: random.Random,
              max_indicators: int = 8) -> Tuple[Dict, str, str]:
    """Combine features/conditions/filters of two parents."""
    child = copy.deepcopy(g1)
    feats1 = list(g1["features"])
    feats2 = [f for f in g2["features"] if f not in feats1]
    keep2 = rng.sample(feats2, min(len(feats2), max(0, max_indicators - len(feats1))))
    # random subset of parent1 features too, to make room
    if len(feats1) + len(keep2) > max_indicators:
        feats1 = rng.sample(feats1, max(1, max_indicators - len(keep2)))
    child["features"] = feats1 + keep2
    fams = {feature_family(f) for f in child["features"]}

    def filt(node):
        if node is None:
            return None
        clauses = [l for l in top_clauses(node)
                   if not leaves_referencing(l, {"__none__"})]
        # keep leaves whose referenced families ⊂ child families (plus price/time/sessions)
        ok = []
        for c in clauses:
            refs = {feature_family(str(l.get(k, ""))) for l in iter_leaves(c)
                    for k in ("left", "right", "a", "b") if isinstance(l.get(k), str)}
            if refs <= fams | {"price", "time", "sessions"}:
                ok.append(c)
        return rebuild(ok, "and")

    if rng.random() < 0.5:
        child["entry_long"] = filt(g2.get("entry_long")) or filt(g1.get("entry_long"))
    else:
        child["entry_long"] = filt(g1.get("entry_long"))
    if rng.random() < 0.5:
        child["entry_short"] = filt(g2.get("entry_short")) or filt(g1.get("entry_short"))
    else:
        child["entry_short"] = filt(g1.get("entry_short"))

    # direction consistency
    if child["entry_long"] is None and child["entry_short"] is None:
        child = copy.deepcopy(g1)  # degenerate; return parent-1-like child
    if child["entry_short"] is None:
        child["direction"] = "long"
    elif child["entry_long"] is None:
        child["direction"] = "short"
    else:
        child["direction"] = rng.choice(["both", g1.get("direction", "both"),
                                         g2.get("direction", "both")])
        if child["direction"] == "long":
            child["entry_short"] = None
        elif child["direction"] == "short":
            child["entry_long"] = None

    # exit mix
    ex1, ex2 = g1.get("exit", {}), g2.get("exit", {})
    ex = child.setdefault("exit", {})
    for k in ("sl_atr_mult", "tp_atr_mult", "max_hold_bars", "min_hold_bars", "trailing"):
        if rng.random() < 0.5 and k in ex2:
            ex[k] = copy.deepcopy(ex2[k])
    if feature_family(ex.get("atr_spec", "atr:14")) != "atr":
        ex["atr_spec"] = "atr:14"
    if "atr" in fams:
        atr_specs = [f for f in child["features"] if feature_family(f) == "atr"]
        ex["atr_spec"] = atr_specs[0]

    # filters mix
    for k in ("sessions", "days", "regime_filters", "timeframe"):
        if rng.random() < 0.5 and k in g2:
            child[k] = copy.deepcopy(g2[k])
    desc = f"crossover of features/filters/exit ({len(child['features'])} indicators)"
    return child, "crossover", desc


# ---------------- directed mutations (specialization / AI hypotheses) ----------------

def directed(genome: Dict[str, Any], action: str, params: Dict[str, Any],
             rng: Optional[random.Random] = None) -> Tuple[Dict, str, str]:
    """Apply a hypothesis/specialization directive.

    Supported actions:
      add_regime_filter {regime}
      restrict_sessions {sessions:[...]}
      exclude_day {day:int}
      restrict_days {days:[...]}
      change_timeframe {timeframe}
      add_adx_filter {threshold}
      add_feature_condition {spec, cmp, value}
      restrict_direction {direction}
      tighten_sl / widen_tp {factor}
    """
    g = copy.deepcopy(genome)
    rng = rng or random.Random()
    if action == "add_regime_filter":
        reg = params["regime"]
        cur = set(g.get("regime_filters") or [])
        g["regime_filters"] = sorted(cur | {reg})
        return g, "regime_mutation", f"added regime filter {reg}"
    if action == "restrict_sessions":
        g["sessions"] = list(params["sessions"])
        return g, "session_mutation", f"restricted to sessions {g['sessions']}"
    if action == "exclude_day":
        d = int(params["day"])
        cur = set(g.get("days") if g.get("days") is not None else range(5))
        cur.discard(d)
        g["days"] = sorted(cur) if len(cur) < 5 else None
        names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        return g, "day_mutation", f"excluded {names[d]}"
    if action == "restrict_days":
        g["days"] = sorted(params["days"])
        return g, "day_mutation", f"restricted days to {g['days']}"
    if action == "change_timeframe":
        old = g["timeframe"]
        g["timeframe"] = params["timeframe"]
        return g, "timeframe_mutation", f"timeframe {old} -> {g['timeframe']}"
    if action == "add_adx_filter":
        thr = float(params.get("threshold", 25))
        spec = next((s for s in g["features"] if feature_family(s) == "adx"), None)
        if spec is None:
            spec = rand_spec("adx", rng)
            g["features"].append(spec)
        leaf = {"type": "compare", "left": spec, "cmp": ">", "right": thr}
        for key in ("entry_long", "entry_short"):
            if g.get(key):
                g[key] = rebuild(top_clauses(g[key]) + [leaf], "and")
        return g, "indicator_addition", f"added ADX > {thr} filter"
    if action == "add_feature_condition":
        spec = params["spec"]
        leaf = {"type": "compare", "left": spec, "cmp": params.get("cmp", ">"),
                "right": params.get("value", 0)}
        fam = feature_family(spec)
        if fam not in {feature_family(s) for s in g["features"]} and fam not in ("price", "time", "sessions"):
            g["features"].append(spec)
        for key in ("entry_long", "entry_short"):
            if g.get(key):
                g[key] = rebuild(top_clauses(g[key]) + [leaf], "and")
        return g, "indicator_addition", f"added condition {leaf['left']} {leaf['cmp']} {leaf['right']}"
    if action == "restrict_direction":
        d = params["direction"]
        if d == "long":
            g["entry_short"] = None
        elif d == "short":
            g["entry_long"] = None
        g["direction"] = d
        return g, "param_mutation", f"direction restricted to {d}"
    if action == "tighten_sl":
        f = float(params.get("factor", 0.8))
        g["exit"]["sl_atr_mult"] = round(max(0.3, g["exit"].get("sl_atr_mult", 1.5) * f), 2)
        return g, "sl_mutation", f"SL tightened x{f}"
    if action == "widen_tp":
        f = float(params.get("factor", 1.25))
        g["exit"]["tp_atr_mult"] = round(max(0.3, g["exit"].get("tp_atr_mult", 2.0) * f), 2)
        return g, "tp_mutation", f"TP widened x{f}"
    raise GenomeError(f"unknown directed action {action}")
