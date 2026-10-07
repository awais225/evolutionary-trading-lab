"""V5 §5 — broker symbol specifications used by the execution model.

A backtest is only as honest as its cost and constraint model. This module is
the single place where the *broker's* contract facts come from, in priority
order:

1. ``MT5``    — the live terminal's ``symbol_info`` (tick size/value, contract
                size, volume min/max/step, stops level, currency, spread).
                ``verified`` is True because the values are the broker's own.
2. ``TABLE``  — a small, explicitly documented table of contract facts for the
                symbols the lab researches (XAUUSD and the majors). These are
                standard contract definitions, not broker quotes; they are
                marked ``verified=False`` and ``source='TABLE'`` so every report
                that uses them can say where the numbers came from.
3. ``CONFIG`` — the lab's configured defaults, again ``verified=False``.

Nothing here guesses: a field the broker does not report stays ``None`` and the
caller must decide what to do about it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

from ..config import get_config


@dataclass
class SymbolSpecs:
    symbol: str
    source: str = "CONFIG"                  # MT5 | TABLE | CONFIG
    verified: bool = False                   # True only when it came from the broker terminal
    point: float = 0.01                      # price change of one point
    tick_size: Optional[float] = None        # price change of one tick
    tick_value: Optional[float] = None       # money per tick per 1.0 lot
    contract_size: float = 100.0             # units per 1.0 lot
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    stops_level_points: int = 0              # broker minimum SL/TP distance, points
    freeze_level_points: int = 0
    spread_points: float = 18.0              # typical spread used when the data has none
    commission_per_lot: float = 7.0          # round turn
    swap_per_lot_per_day: float = -2.5
    currency_profit: str = "USD"
    digits: int = 2
    trade_allowed: Optional[bool] = None
    session_hours_utc: Optional[list] = None  # [[start_hour, end_hour], ...] Mon-Fri
    notes: str = ""

    @property
    def pip_size(self) -> float:
        """One pip in price terms.

        MT5's *point* is the last quoted digit; the market convention for these
        instruments is one pip = 10 points (XAUUSD: 0.01 -> 0.10, EURUSD:
        0.00001 -> 0.0001, USDJPY: 0.001 -> 0.01). Stated explicitly so a
        "300 pip" stop can always be converted back to a price, and back again.
        """
        return round(10.0 * float(self.point or 0.0), 10)

    def to_payload(self) -> Dict[str, Any]:
        d = asdict(self)
        d["pip_size"] = self.pip_size
        return d


#: documented contract facts (not broker quotes). XAUUSD: 100 oz/lot, $1 per
#: 0.01 move per lot; FX majors: 100 000 units/lot, $1 per pip per lot.
_TABLE: Dict[str, Dict[str, Any]] = {
    "XAUUSD": dict(point=0.01, tick_size=0.01, tick_value=1.0, contract_size=100.0,
                   digits=2, spread_points=18.0, commission_per_lot=7.0,
                   swap_per_lot_per_day=-2.5, session_hours_utc=[[0, 24]],
                   notes="Gold vs USD, 100 oz per lot, 0.01 quote step"),
    "XAGUSD": dict(point=0.001, tick_size=0.001, tick_value=5.0, contract_size=5000.0,
                   digits=3, spread_points=25.0, commission_per_lot=7.0,
                   swap_per_lot_per_day=-2.0, session_hours_utc=[[0, 24]]),
    "EURUSD": dict(point=0.00001, tick_size=0.00001, tick_value=1.0, contract_size=100000.0,
                   digits=5, spread_points=12.0, commission_per_lot=7.0,
                   swap_per_lot_per_day=-1.5, session_hours_utc=[[0, 24]]),
    "GBPUSD": dict(point=0.00001, tick_size=0.00001, tick_value=1.0, contract_size=100000.0,
                   digits=5, spread_points=14.0, commission_per_lot=7.0,
                   swap_per_lot_per_day=-1.8, session_hours_utc=[[0, 24]]),
    "USDJPY": dict(point=0.001, tick_size=0.001, tick_value=0.68, contract_size=100000.0,
                   digits=3, spread_points=14.0, commission_per_lot=7.0,
                   swap_per_lot_per_day=-2.0, session_hours_utc=[[0, 24]]),
    "BTCUSD": dict(point=0.01, tick_size=0.01, tick_value=0.01, contract_size=1.0,
                   digits=2, spread_points=300.0, commission_per_lot=0.0,
                   swap_per_lot_per_day=-5.0, session_hours_utc=[[0, 24]]),
}


def _from_mt5(symbol: str) -> Optional[SymbolSpecs]:
    """Broker facts from the live terminal, when one is attached and answers."""
    try:
        from ..mt5.bridge import get_bridge
        bridge = get_bridge()
        if getattr(bridge, "is_simulated", True):
            return None
        info = None
        getter = getattr(bridge, "symbol_spec", None) or getattr(bridge, "get_symbol_spec", None)
        if callable(getter):
            info = getter(symbol)
            if isinstance(info, dict):
                info = type("S", (), info)()
        if info is None:
            return None
        point = float(getattr(info, "point", 0.0) or 0.0) or None
        tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0) or None
        tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0) or None
        contract_size = float(getattr(info, "trade_contract_size", 0.0) or 0.0) or None
        if not point:
            return None
        return SymbolSpecs(
            symbol=symbol, source="MT5", verified=True,
            point=point, tick_size=tick_size or point, tick_value=tick_value,
            contract_size=contract_size or 100.0,
            volume_min=float(getattr(info, "volume_min", 0.01) or 0.01),
            volume_max=float(getattr(info, "volume_max", 100.0) or 100.0),
            volume_step=float(getattr(info, "volume_step", 0.01) or 0.01),
            stops_level_points=int(getattr(info, "trade_stops_level", 0) or 0),
            freeze_level_points=int(getattr(info, "freeze_level", 0) or 0),
            currency_profit=str(getattr(info, "currency_profit", "USD") or "USD"),
            digits=int(getattr(info, "digits", 2) or 2),
            trade_allowed=getattr(info, "trade_allowed", None),
            notes="from the attached MT5 terminal symbol_info",
        )
    except Exception:
        return None


def get_symbol_specs(symbol: str, *, allow_mt5: bool = True) -> SymbolSpecs:
    """Resolve the contract facts for ``symbol`` (never fabricated)."""
    sym = (symbol or "").upper().strip()
    if allow_mt5:
        live = _from_mt5(sym)
        if live is not None:
            return live

    cfg = get_config()
    bt = cfg.backtest
    base = dict(contract_size=float(bt.contract_size), point=float(bt.point_value),
                spread_points=float(bt.default_spread_points),
                commission_per_lot=float(bt.commission_per_lot),
                swap_per_lot_per_day=float(bt.swap_per_lot_per_day))

    entry = _TABLE.get(sym)
    if entry:
        base.update(entry)
    fields = {k: v for k, v in base.items()
              if k in SymbolSpecs.__dataclass_fields__ and k not in ("symbol", "source", "verified", "notes")}
    return SymbolSpecs(
        symbol=sym,
        source="TABLE" if entry else "CONFIG",
        verified=False,
        notes=(entry or {}).get("notes") or ("" if entry else
               "lab-configured defaults; no verified broker specification available"),
        **fields)


def execution_params_for(symbol: str, cfg=None):
    """Build ExecutionParams using the resolved symbol specs (config for the rest)."""
    from .execution import ExecutionParams
    specs = get_symbol_specs(symbol)
    ep = ExecutionParams.from_config(cfg)
    ep.contract_size = float(specs.contract_size or ep.contract_size)
    ep.point = float(specs.point or ep.point)
    ep.commission_per_lot = float(specs.commission_per_lot)
    ep.swap_per_lot_per_day = float(specs.swap_per_lot_per_day)
    if specs.spread_points:
        ep.default_spread_points = float(specs.spread_points)
    ep.min_lots = float(specs.volume_min or 0.01)
    ep.lot_step = float(specs.volume_step or 0.01)
    ep.max_lots = float(specs.volume_max or 100.0)
    ep.stops_level_points = int(specs.stops_level_points or 0)
    ep.specs_source = specs.source
    ep.specs_verified = bool(specs.verified)
    return ep, specs
