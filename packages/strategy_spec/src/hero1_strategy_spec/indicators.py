"""Indicator reference contract — metadata and parameter validation ONLY.

Phase 2 deliberately does NOT implement indicator mathematics (that is Phase 3).
This module declares which identifiers are supported, their parameter
constraints, their permitted multi-output selectors and DECLARED warm-up
approximations used for metadata purposes only.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

PRICE_SOURCES = {"close", "open", "high", "low", "hl2", "hlc3", "ohlc4"}


class IndicatorName(str, Enum):
    SMA = "SMA"
    EMA = "EMA"
    RSI = "RSI"
    MACD = "MACD"
    BBANDS = "BBANDS"
    ATR = "ATR"
    VWAP = "VWAP"
    ROC = "ROC"
    STOCH = "STOCH"
    ADX = "ADX"


# params: name -> {param: (type, lo, hi, required, default)}
_INDICATOR_SPECS: dict[IndicatorName, dict[str, tuple]] = {
    IndicatorName.SMA:    {"period": ("int", 2, 500, True, None), "source": ("source", None, None, False, "close")},
    IndicatorName.EMA:    {"period": ("int", 2, 500, True, None), "source": ("source", None, None, False, "close")},
    IndicatorName.RSI:    {"period": ("int", 1, 100, False, 14),  "source": ("source", None, None, False, "close")},
    IndicatorName.MACD:   {"fast": ("int", 1, 200, False, 12), "slow": ("int", 2, 300, False, 26),
                           "signal": ("int", 1, 100, False, 9), "source": ("source", None, None, False, "close")},
    IndicatorName.BBANDS: {"period": ("int", 2, 500, False, 20), "stddev": ("float", 0.1, 10.0, False, 2.0),
                           "source": ("source", None, None, False, "close")},
    IndicatorName.ATR:    {"period": ("int", 1, 100, False, 14)},
    IndicatorName.VWAP:   {"anchor": ("anchor", None, None, False, "daily")},
    IndicatorName.ROC:    {"period": ("int", 1, 200, False, 12), "source": ("source", None, None, False, "close")},
    IndicatorName.STOCH:  {"k_period": ("int", 1, 100, False, 14), "d_period": ("int", 1, 50, False, 3),
                           "slowing": ("int", 1, 50, False, 3)},
    IndicatorName.ADX:    {"period": ("int", 1, 100, False, 14)},
}

# Multi-output selectors per indicator (None = single output named "value")
_INDICATOR_FIELDS: dict[IndicatorName, Optional[set[str]]] = {
    IndicatorName.SMA: None, IndicatorName.EMA: None, IndicatorName.RSI: None,
    IndicatorName.MACD: {"macd", "signal", "histogram"},
    IndicatorName.BBANDS: {"upper", "basis", "lower"},
    IndicatorName.ATR: None, IndicatorName.VWAP: None, IndicatorName.ROC: None,
    IndicatorName.STOCH: {"k", "d"},
    IndicatorName.ADX: {"adx"},
}

_ANCHORS = {"daily"}


def validate_indicator_params(name: IndicatorName, params: dict, field: Optional[str]) -> list[str]:
    """Return human-readable parameter problems ('' keyed as strings)."""
    problems: list[str] = []
    spec = _INDICATOR_SPECS[name]

    unknown = sorted(set(params) - set(spec))
    for key in unknown:
        problems.append(f"unknown parameter {key!r} for {name.value} (known: {sorted(spec)})")

    for key, (kind, lo, hi, required, default) in spec.items():
        value = params.get(key, default)
        if value is None:
            if required:
                problems.append(f"missing required parameter {key!r} for {name.value}")
            continue
        if kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                problems.append(f"parameter {key!r} for {name.value} must be an integer, got {value!r}")
                continue
            if not (lo <= value <= hi):
                problems.append(f"parameter {key!r}={value} out of range [{lo}, {hi}] for {name.value}")
        elif kind == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problems.append(f"parameter {key!r} for {name.value} must be a number, got {value!r}")
                continue
            if not (lo <= float(value) <= hi):
                problems.append(f"parameter {key!r}={value} out of range [{lo}, {hi}] for {name.value}")
        elif kind == "source":
            if str(value).lower() not in PRICE_SOURCES:
                problems.append(f"parameter source={value!r} must be one of {sorted(PRICE_SOURCES)} for {name.value}")
        elif kind == "anchor":
            if str(value).lower() not in _ANCHORS:
                problems.append(f"parameter anchor={value!r} must be one of {sorted(_ANCHORS)} for {name.value}")

    # cross-parameter constraints
    if name is IndicatorName.MACD:
        fast = params.get("fast", 12); slow = params.get("slow", 26)
        if isinstance(fast, int) and isinstance(slow, int) and fast >= slow:
            problems.append(f"MACD requires fast < slow, got fast={fast}, slow={slow}")

    allowed = _INDICATOR_FIELDS[name]
    if allowed is None:
        if field not in (None, "value"):
            problems.append(f"{name.value} has a single output; field must be omitted or 'value', got {field!r}")
    elif field is None:
        problems.append(f"{name.value} requires a field selector, one of {sorted(allowed)}")
    elif field not in allowed:
        problems.append(f"{name.value} field={field!r} is not supported; expected one of {sorted(allowed)}")

    return problems


def indicator_warmup(name: IndicatorName, params: dict) -> int:
    """Declared warm-up approximation (completed candles) for metadata only.

    This is NOT an implementation of indicator math — it is the minimum history
    Phase 3 must guarantee before values become meaningful.
    """
    get = lambda k, d: params.get(k, d)  # noqa: E731
    if name in (IndicatorName.SMA, IndicatorName.EMA):
        return int(get("period", 20)) - 1
    if name is IndicatorName.RSI:
        return int(get("period", 14))
    if name is IndicatorName.MACD:
        return int(get("slow", 26)) + int(get("signal", 9)) - 2
    if name is IndicatorName.BBANDS:
        return int(get("period", 20)) - 1
    if name is IndicatorName.ATR:
        return int(get("period", 14))
    if name is IndicatorName.VWAP:
        return 0
    if name is IndicatorName.ROC:
        return int(get("period", 12))
    if name is IndicatorName.STOCH:
        return int(get("k_period", 14)) + int(get("slowing", 3)) - 1
    if name is IndicatorName.ADX:
        return 2 * int(get("period", 14)) - 1
    return 0
