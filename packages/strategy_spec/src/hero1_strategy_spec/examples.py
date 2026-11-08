"""Reference example specifications used as fixtures and in the UI.

Bullish Engulfing (LONG-only):
  1. Previous completed candle is bearish.
  2. Current completed candle is bullish.
  3. Current candle body engulfs the previous candle body.
  4. Current high > previous high.
  5. Current low < previous low.

Execution: evaluate only after the signal candle closes; entry at next bar open.
Stop-loss: 10 pips below the signal candle low (pip_size resolved from broker
metadata — NEVER hardcoded). Take-profit: risk:reward 1:2.

Same strategy logic on M1, M5, M15 — the instrument and timeframe context is
explicit in each spec and is never mixed.
"""
from __future__ import annotations

from typing import Dict, List

from .spec import SCHEMA_VERSION


def ema_sma_stepped_trailing(instrument: str, timeframe: str = "H1",
                             activation_pips: float = 30.0) -> dict:
    """EMA(9) cross above SMA(20) entry with a stepped trailing stop.

    The ladder figures (activation 30 pips, then +30 pips per step) are a
    CONFIGURABLE EXAMPLE matching the owner's request - they live in the
    spec data below and are never baked into engine code. Stop-loss is a
    fixed 25-pip initial stop; the stepped rule locks profit as favourable
    excursion grows. Pip sizes resolve ONLY from broker metadata.
    """
    instrument = instrument.upper()

    def ind(name: str, period: int, offset: int = 0) -> dict:
        return {"kind": "indicator", "name": name, "params": {"period": period},
                "field": "value", "offset": offset}

    def pips(value: float) -> dict:
        return {"value": value, "unit": "pips"}

    return {
        "schema_version": SCHEMA_VERSION,
        "identity": {
            "name": f"EMA9/SMA20 Cross + Stepped Trail · {instrument} {timeframe}",
            "description": (
                "Long when EMA(9) crosses above SMA(20) on the last completed candle. "
                "Initial stop 25 pips; stepped trailing arms at +30 pips locking 0 "
                "(break-even), then advances 30 pips for every further 30 pips of "
                "favourable excursion. Mirrors mechanically for shorts."
            ),
            "instrument": instrument,
            "timeframe": timeframe,
            "direction": "LONG",
            "origin": "CUSTOM",
            "tags": ["trend", "crossover", "stepped_trailing", "reference"],
        },
        "entry": {"type": "compare", "op": "cross_above",
                  "left": ind("EMA", 9), "right": ind("SMA", 20)},
        "exits": {
            "stop_loss": {"anchor": "entry_price", "distance": pips(25.0)},
            "take_profit": None,
        },
        "management": {
            "stepped_trailing": {
                "start": "after_activation",
                "activation": pips(activation_pips),
                "initial_locked_profit": pips(0.0),
                "step": pips(30.0),
                "advance_per_step": pips(30.0),
                "first_step_to_breakeven": True,
                "applies_to": "BOTH",
            }
        },
        "risk": {"sizing": {"mode": "fixed_risk", "risk_pct": 1.0}},
        "execution": {"entry_timing": "next_bar_open", "candle": "completed"},
    }


def bullish_engulfing(instrument: str, timeframe: str) -> dict:
    if timeframe not in ("M1", "M5", "M15", "M30", "H1", "H4", "D1"):
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    instrument = instrument.upper()

    def candle(field: str, offset: int) -> dict:
        return {"kind": "candle", "field": field, "offset": offset}

    def compare(op: str, left: dict, right: dict) -> dict:
        return {"type": "compare", "op": op, "left": left, "right": right}

    entry = {
        "type": "and",
        "children": [
            compare("lt", candle("close", 1), candle("open", 1)),   # prev candle bearish
            compare("gt", candle("close", 0), candle("open", 0)),   # current candle bullish
            compare("lte", candle("open", 0), candle("close", 1)),  # engulf: open <= prev close
            compare("gte", candle("close", 0), candle("open", 1)),  # engulf: close >= prev open
            compare("gt", candle("high", 0), candle("high", 1)),    # current high > prev high
            compare("lt", candle("low", 0), candle("low", 1)),      # current low < prev low
        ],
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "identity": {
            "name": f"Bullish Engulfing · {instrument} {timeframe}",
            "description": (
                "Reference price-action pattern: bearish candle followed by an engulfing bullish "
                "candle closing above the previous high. Entry next bar open after signal close. "
                "Stop-loss 10 pips below signal low; take-profit 2R."
            ),
            "instrument": instrument,
            "timeframe": timeframe,
            "direction": "LONG",
            "origin": "CUSTOM",
            "tags": ["pattern", "price_action", "reference"],
            "metadata": {"reference": True, "pattern": "bullish_engulfing"},
        },
        "entry": entry,
        "exits": {
            "stop_loss": {
                "anchor": "signal_low",
                "distance": {"unit": "pips", "value": 10},   # pip_size comes from broker metadata
            },
            "take_profit": {"ratio": 2.0},                    # 1:2 risk:reward
            "max_holding_candles": 100,
        },
        "risk": {
            "sizing": {"mode": "fixed_risk", "risk_pct": 1.0},
        },
        "execution": {
            "entry_timing": "next_bar_open",
            "candle": "completed",
            "permit_forming_candle": False,
            "order_type": "market",
            "position_handling": "single",
        },
    }


EXAMPLE_TIMEFRAMES = ("M1", "M5", "M15")


def example_specs() -> List[dict]:
    return [bullish_engulfing("XAUUSD", tf) for tf in EXAMPLE_TIMEFRAMES]


def example_spec(timeframe: str) -> Dict:
    return bullish_engulfing("XAUUSD", timeframe)


def prev_day_high_low_momentum_reversal(instrument: str = "XAUUSD",
                                        timeframe: str = "H1") -> dict:
    """NODE 1 — Previous Day High/Low Momentum Reversal (official seed).

    Monitors the Previous Day High and Previous Day Low. When price touches
    either level, the trade side is the OPPOSITE of the immediate M1
    momentum (completed M1 closes, independent of the node timeframe):
    momentum UP -> SHORT, momentum DOWN -> LONG, equal -> no trade. The
    touched level does NOT choose the side. Fixed SL 300 pips; TP distance
    = SL distance x reward_risk_ratio (exits.take_profit.ratio = 2.0,
    editable without touching the engine). No trailing, no break-even, no
    indicator/time exits. Execution model: signals evaluate on completed
    candles and fill at the next bar open (existing engine semantics);
    with OHLC data a touch is bar-range contact with the level - exact
    intrabar/tick sequencing is NOT claimed."""
    touch_high = {"type": "compare", "op": "touch",
                  "left": {"kind": "candle", "field": "close", "offset": 0},
                  "right": {"kind": "reference", "level": "PREV_DAY_HIGH"}}
    touch_low = {"type": "compare", "op": "touch",
                 "left": {"kind": "candle", "field": "close", "offset": 0},
                 "right": {"kind": "reference", "level": "PREV_DAY_LOW"}}
    momentum_live = {"type": "compare", "op": "neq",
                     "left": {"kind": "momentum", "source": "M1"},
                     "right": {"kind": "constant", "value": 0}}
    return {
        "schema_version": SCHEMA_VERSION,
        "identity": {
            "name": "NODE 1 — Previous Day High Low Momentum Reversal",
            "description": ("Previous Day High/Low touch + M1 momentum reversal. "
                            "Touch either level, then trade opposite to the immediate "
                            "M1 momentum (up -> SHORT, down -> LONG, equal -> no trade). "
                            "SL 300 pips; TP = SL distance x reward_risk_ratio (2.0). "
                            "OHLC touch = bar-range contact; tick precision is not claimed."),
            "instrument": instrument.upper(),
            "timeframe": timeframe,
            "direction": "BOTH",
            "origin": "CUSTOM",
            "tags": ["node-1", "price-reference", "momentum-reversal"],
            "metadata": {"reward_risk_ratio": 2.0, "official_seed": True},
        },
        "entry": {"type": "and", "children": [
            {"type": "or", "children": [touch_high, touch_low]},
            momentum_live,
        ]},
        "exits": {
            "stop_loss": {"anchor": "entry_price",
                          "distance": {"unit": "pips", "value": 300}},
            "take_profit": {"ratio": 2.0},
        },
        "risk": {"sizing": {"mode": "fixed_risk", "risk_pct": 1.0}},
        "execution": {"entry_side_rule": "m1_momentum_reversal",
                      "entry_timing": "next_bar_open", "candle": "completed",
                      "permit_forming_candle": False, "order_type": "market",
                      "position_handling": "single"},
    }
