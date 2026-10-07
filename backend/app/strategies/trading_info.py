"""V5 §15 — Trading Info: the node's genome, translated into trading rules.

Every statement this module produces is derived from the node's own stored
genome. There is no template sentence that would be true of any genome, no
placeholder value and no defaulted parameter: a field the genome does not define
is reported as ``None`` with the reason "not defined by this genome", so the tab
can never describe a rule the node does not actually have.

The output is structured (a list of sections, each a list of items) and also
carries a plain-text rendering, which keeps the API usable by both the UI and by
copy/paste.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: display names for the feature families the genome vocabulary uses
_FAMILY_LABELS = {
    "sma": "Simple moving average", "ema": "Exponential moving average",
    "rsi": "RSI", "macd": "MACD", "roc": "Rate of change", "momentum": "Momentum",
    "stoch": "Stochastic", "cci": "CCI", "adx": "ADX", "atr": "ATR",
    "bb": "Bollinger bands", "volatility": "Volatility", "volume": "Volume",
    "vwap": "VWAP", "prev_day": "Previous-day levels", "time": "Time filters",
    "regime": "Market regime", "sessions": "Sessions", "price": "Price",
}

_CMP_WORDS = {
    ">": "is greater than", ">=": "is greater than or equal to",
    "<": "is less than", "<=": "is less than or equal to",
    "==": "equals", "!=": "does not equal",
}

_SESSION_LABELS = {
    "asia": "Asia (00:00–08:00 UTC)",
    "london": "London (07:00–16:00 UTC)",
    "newyork": "New York (12:00–21:00 UTC)",
    "london_ny_overlap": "London–New York overlap (12:00–16:00 UTC)",
}

_DAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _spec_label(spec: str) -> str:
    """'ema:50' -> 'Exponential moving average (50)'."""
    if not isinstance(spec, str) or not spec:
        return "(unset)"
    if spec == "price" or spec == "close":
        return "Close price"
    parts = spec.split(":")
    family = parts[0]
    label = _FAMILY_LABELS.get(family, family)
    args = parts[1:]
    if not args:
        return label
    return f"{label} ({', '.join(args)})"


def _day_names(values: Any) -> str:
    """Render a day list however it was stored: numbers (0=Mon) or short names."""
    if not isinstance(values, (list, tuple)):
        return str(values)
    from ..live_testing.schedule import day_number

    out = []
    for d in values:
        n = day_number(d)
        out.append(_DAY_LABELS[n] if n is not None else str(d))
    return ", ".join(out)


def _fmt_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _describe_expr(node: Any) -> Optional[str]:
    """Render one genome expression node as an English clause (recursively)."""
    if not isinstance(node, dict):
        return None
    op = node.get("op")
    if op in ("and", "or"):
        parts = [_describe_expr(c) for c in node.get("clauses") or []]
        parts = [p for p in parts if p]
        if not parts:
            return None
        joiner = " AND " if op == "and" else " OR "
        return "(" + joiner.join(parts) + ")"
    if op == "not":
        inner = _describe_expr(node.get("clause"))
        return f"NOT ({inner})" if inner else None

    t = node.get("type")
    if t == "compare":
        left = node.get("left")
        right = node.get("right")
        cmp_word = _CMP_WORDS.get(node.get("cmp"), node.get("cmp") or "?")
        if isinstance(right, str):
            return f"{_spec_label(left)} {cmp_word} {_spec_label(right)}"
        return f"{_spec_label(left)} {cmp_word} {_fmt_value(right)}"
    if t == "crossover":
        direction = "crosses above" if node.get("dir") == "up" else "crosses below"
        return f"{_spec_label(node.get('a'))} {direction} {_spec_label(node.get('b'))}"
    return None


def _feature_parameters(genome: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every feature the genome references, with its family and parameters."""
    from ..genome.schema import referenced_features

    out: List[Dict[str, Any]] = []
    seen = set()
    for spec in sorted(referenced_features(genome)):
        if spec in seen:
            continue
        seen.add(spec)
        parts = spec.split(":")
        out.append({
            "spec": spec,
            "family": parts[0],
            "family_label": _FAMILY_LABELS.get(parts[0], parts[0]),
            "parameters": parts[1:],
            "label": _spec_label(spec),
        })
    return out


def _as_genome(raw: Any) -> Dict[str, Any]:
    """A stored genome, whatever the driver handed back.

    The database layer may return the column already decoded as a dict (the
    sqlite row factory does), as a JSON string, or as nothing at all. Passing a
    dict to ``json.loads`` raises, and silently falling back to ``{}`` would make
    this module describe *every* node as "no rule defined" - the one outcome the
    whole tab exists to prevent. So the three shapes are handled explicitly.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str) and raw.strip():
        try:
            import json
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def build_trading_info(strategy: Dict[str, Any], genome: Optional[Dict[str, Any]] = None,
                       config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Translate one node's genome (and live config, when present) into rules."""
    if genome is None:
        genome = _as_genome(strategy.get("genome"))
    genome = genome or {}
    cfg = config or {}

    symbol = genome.get("symbol") or strategy.get("symbol")
    timeframe = genome.get("timeframe") or strategy.get("timeframe")
    direction = genome.get("direction", "both")
    exit_rules = genome.get("exit") or {}
    risk = genome.get("risk") or {}

    def _nd(v: Any, reason: str = "not defined by this genome") -> Any:
        return v if v not in (None, "", []) else {"value": None, "reason": reason}

    sections: List[Dict[str, Any]] = []

    # ---- 1. identity ----------------------------------------------------- #
    sections.append({
        "id": "identity", "title": "Identity",
        "items": [
            {"label": "Node", "value": f"Node_{strategy.get('id')}"},
            {"label": "Symbol", "value": symbol or {"value": None, "reason": "not set on this node"}},
            {"label": "Timeframe", "value": timeframe or {"value": None, "reason": "not set on this node"}},
            {"label": "Direction", "value": {"both": "Long and short", "long": "Long only",
                                             "short": "Short only"}.get(str(direction).lower(), direction or "both")},
            {"label": "Research status", "value": strategy.get("status")},
            {"label": "Generation", "value": strategy.get("generation")},
        ],
    })

    # ---- 2. indicators --------------------------------------------------- #
    feats = _feature_parameters(genome)
    sections.append({
        "id": "indicators", "title": "Indicators and parameters",
        "items": [{"label": f["label"], "value": f["spec"],
                   "detail": (f"parameters: {', '.join(f['parameters'])}" if f["parameters"]
                              else "no parameters (family default)")}
                  for f in feats] or [
            {"label": "Indicators", "value": {"value": None,
                                              "reason": "this genome references no indicator"}}],
    })

    # ---- 3. entries ------------------------------------------------------- #
    entry_long = _describe_expr(genome.get("entry_long"))
    entry_short = _describe_expr(genome.get("entry_short"))
    entry_items = []
    if entry_long:
        entry_items.append({"label": "Long entry", "value": entry_long,
                            "rule": genome.get("entry_long")})
    if entry_short:
        entry_items.append({"label": "Short entry", "value": entry_short,
                            "rule": genome.get("entry_short")})
    if not entry_items:
        entry_items.append({"label": "Entry", "value": {
            "value": None, "reason": "this genome defines no entry rule"}})
    sections.append({"id": "entry", "title": "Entry rules", "items": entry_items})

    # ---- 4. exit / SL / TP ------------------------------------------------ #
    exit_items: List[Dict[str, Any]] = []
    exit_cond = _describe_expr(exit_rules.get("exit_condition"))
    if exit_cond:
        exit_items.append({"label": "Exit condition", "value": exit_cond,
                           "rule": exit_rules.get("exit_condition")})
    sl = exit_rules.get("sl_atr_mult")
    tp = exit_rules.get("tp_atr_mult")
    atr_spec = exit_rules.get("atr_spec", "atr:14")
    if sl is not None:
        exit_items.append({"label": "Stop loss",
                           "value": f"{_fmt_value(sl)} × ATR({atr_spec.split(':')[-1]}) below/above entry",
                           "detail": f"distance = {_fmt_value(sl)} × {atr_spec}"})
    else:
        exit_items.append({"label": "Stop loss", "value": {
            "value": None, "reason": "this genome defines no ATR stop loss"}})
    if tp is not None:
        exit_items.append({"label": "Take profit",
                           "value": f"{_fmt_value(tp)} × ATR({atr_spec.split(':')[-1]}) from entry",
                           "detail": f"distance = {_fmt_value(tp)} × {atr_spec}"})
    else:
        exit_items.append({"label": "Take profit", "value": {
            "value": None, "reason": "this genome defines no ATR take profit"}})
    trailing = exit_rules.get("trailing") or None
    if trailing:
        exit_items.append({"label": "Trailing stop",
                           "value": f"activate at {_fmt_value(trailing.get('activation_mult'))} × ATR, "
                                    f"trail {_fmt_value(trailing.get('atr_mult'))} × ATR",
                           "detail": "activation and trail distances are genome parameters"})
    if exit_rules.get("max_hold_bars") is not None:
        exit_items.append({"label": "Maximum holding",
                           "value": f"{exit_rules['max_hold_bars']} bars"})
    if exit_rules.get("min_hold_bars") is not None:
        exit_items.append({"label": "Minimum holding",
                           "value": f"{exit_rules['min_hold_bars']} bars"})
    sections.append({"id": "exit", "title": "Exit, stop loss and take profit", "items": exit_items})

    # ---- 5. risk ---------------------------------------------------------- #
    risk_items = []
    if risk:
        for k, v in risk.items():
            label = {"risk_per_trade": "Risk per trade", "max_positions": "Maximum positions",
                     "max_daily_trades": "Maximum trades per day",
                     "cooldown_minutes": "Cooldown between trades (minutes)"}.get(k, k)
            if k == "risk_per_trade" and isinstance(v, (int, float)):
                risk_items.append({"label": label, "value": f"{v * 100:.2f}% of equity"})
            else:
                risk_items.append({"label": label, "value": _fmt_value(v)})
    else:
        risk_items.append({"label": "Risk", "value": {
            "value": None, "reason": "this genome carries no explicit risk block"}})

    # live overrides (when the node is enrolled in live testing)
    if cfg.get("risk_pct") is not None:
        risk_items.append({"label": "Live risk override",
                           "value": f"{float(cfg['risk_pct']) * 100:.2f}% (per-node override)",
                           "detail": "the live engine sizes positions with this value"})
    elif cfg:
        risk_items.append({"label": "Live risk", "value": "global default",
                           "detail": "no per-node override is stored for this node"})
    if cfg.get("lot_size") is not None:
        risk_items.append({"label": "Fixed live lot", "value": _fmt_value(cfg["lot_size"]),
                           "detail": "used instead of risk sizing when set"})
    sections.append({"id": "risk", "title": "Risk and position sizing", "items": risk_items})

    # ---- 6. filters ------------------------------------------------------- #
    filter_items = []
    sessions = genome.get("sessions")
    if sessions:
        filter_items.append({"label": "Sessions",
                             "value": ", ".join(_SESSION_LABELS.get(s, s) for s in sessions)})
    else:
        filter_items.append({"label": "Sessions",
                             "value": {"value": None, "reason": "no session filter — trades any session"}})
    days = genome.get("days")
    if days:
        filter_items.append({"label": "Trading days", "value": _day_names(days)})
    else:
        filter_items.append({"label": "Trading days",
                             "value": {"value": None, "reason": "no day filter — all days allowed"}})
    regimes = genome.get("regime_filters")
    if regimes:
        filter_items.append({"label": "Regime filter", "value": ", ".join(regimes)})
    if cfg.get("days"):
        filter_items.append({"label": "Live schedule days", "value": _day_names(cfg["days"]),
                             "detail": "enforced by the live engine"})
    if cfg.get("sessions"):
        filter_items.append({"label": "Live schedule sessions",
                             "value": ", ".join(str(s) for s in cfg["sessions"]),
                             "detail": "enforced by the live engine"})
    if cfg.get("start_time") and cfg.get("end_time"):
        filter_items.append({"label": "Live schedule window",
                             "value": f"{cfg['start_time']} → {cfg['end_time']} "
                                      f"({cfg.get('timezone') or 'UTC'})",
                             "detail": "enforced by the live engine"})
    sections.append({"id": "filters", "title": "Sessions, days and constraints", "items": filter_items})

    # ---- plain text rendering -------------------------------------------- #
    lines = []
    for sec in sections:
        lines.append(f"{sec['title'].upper()}")
        for it in sec["items"]:
            v = it["value"]
            if isinstance(v, dict):
                text = f"— ({v.get('reason')})"
            else:
                text = str(v)
            lines.append(f"  {it['label']}: {text}")
            if it.get("detail"):
                lines.append(f"      {it['detail']}")
        lines.append("")

    return {
        "ok": True,
        "strategy_id": strategy.get("id"),
        "node_id": f"Node_{strategy.get('id')}",
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "sections": sections,
        "text": "\n".join(lines).strip(),
        "source": "generated from the node's stored genome",
        "generated_at": __import__("time").time(),
    }
