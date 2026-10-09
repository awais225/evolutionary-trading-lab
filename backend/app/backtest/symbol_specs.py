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


# ---------------------------------------------------------------------------
# V6.4 — THE one explicit pip-size conversion (MT5_BRIDGE_DETAILS.txt, "PIP SIZE
# (the digits rule)"):
#
#     pip_size = 10.0 * point if digits in (3, 5) else 1.0 * point
#
#     XAUUSD on a 2-digit feed: digits=2, point=0.01, pip_size=0.01
#     EURUSD on a 5-digit feed: digits=5, point=0.00001, pip_size=0.0001
#
# Every pip -> price conversion in the product (SL/TP/R:R/validation/request
# prices, previews, risk sizing, panels) MUST go through these helpers so one
# symbol means one pip everywhere. The convention is derived from the symbol's
# own digits/point pair (the broker's specification) — never from the symbol
# name, never a per-product constant. When digits/point cannot be established,
# the helpers return None and the caller must REFUSE the order with a clear
# diagnostic instead of guessing (getting it wrong puts a stop ten times too
# far or ten times too close).
# ---------------------------------------------------------------------------

def pip_size_from_digits(digits: Optional[int], point: Optional[float]) -> Optional[float]:
    """One pip in price terms, from the symbol's own digits/point (never guessed).

    Returns ``None`` when the convention cannot be established (missing/zero
    ``point``, or ``digits`` outside the 2/3/4/5 quoting families). A ``None``
    result is a REFUSAL signal, not a default.
    """
    try:
        if point is None or digits is None:
            return None
        point = float(point)
        digits = int(digits)
    except (TypeError, ValueError):
        return None
    if point <= 0 or digits not in (2, 3, 4, 5):
        return None
    return round(10.0 * point, 12) if digits in (3, 5) else round(point, 12)


def points_per_pip_from_digits(digits: Optional[int]) -> Optional[float]:
    """How many of the symbol's own points make one pip (10 on 3/5-digit, 1 on 2/4)."""
    try:
        d = int(digits)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if d not in (2, 3, 4, 5):
        return None
    return 10.0 if d in (3, 5) else 1.0


def pips_to_price(pips: Optional[float], pip_size: Optional[float]) -> Optional[float]:
    """Convert a pip distance to a price distance (``None`` when either side is unestablished)."""
    if pips is None or pip_size is None:
        return None
    try:
        return float(pips) * float(pip_size)
    except (TypeError, ValueError):
        return None


def price_to_pips(distance: Optional[float], pip_size: Optional[float]) -> Optional[float]:
    """Convert a price distance to pips (``None`` when the conversion is unestablished)."""
    if distance is None or pip_size in (None, 0):
        return None
    try:
        return float(distance) / float(pip_size)
    except (TypeError, ValueError):
        return None


def round_to_tick(price: Optional[float], tick_size: Optional[float],
                  digits: Optional[int]) -> Optional[float]:
    """Round a price to the broker's tick size (falls back to ``digits`` rounding)."""
    if price is None:
        return None
    try:
        p = float(price)
        if tick_size:
            t = float(tick_size)
            if t > 0:
                return round(round(p / t) * t, int(digits) if digits is not None else 10)
        return round(p, int(digits)) if digits is not None else p
    except (TypeError, ValueError):
        return None


def stops_distance_check(price_distance: Optional[float], point: Optional[float],
                         stops_level_points: Optional[int],
                         freeze_level_points: Optional[int]) -> Dict[str, Any]:
    """MT5_BRIDGE_DETAILS.txt 'STOPS-LEVEL VALIDATION', client-side and read-only.

    ``min_stop_dist = max(trade_stops_level, trade_freeze_level) * point``; a
    SL/TP distance below the broker's minimum is refused with a readable message
    instead of a cryptic retcode 10016 after the send.
    """
    out: Dict[str, Any] = {"checked": False, "ok": True, "min_stop_dist": None,
                           "reason": None}
    try:
        if price_distance is None or price_distance <= 0:
            out["reason"] = "no stop distance set - nothing to validate"
            return out
        if not point or not (stops_level_points or freeze_level_points):
            out["reason"] = ("broker reported no stops/freeze level (or no point) - "
                             "nothing to validate client-side")
            return out
        min_dist = max(int(stops_level_points or 0), int(freeze_level_points or 0)) * float(point)
        out["checked"] = True
        out["min_stop_dist"] = min_dist
        if float(price_distance) < min_dist:
            out["ok"] = False
            out["reason"] = (f"stop distance {float(price_distance):.8f} is below the broker "
                             f"minimum {min_dist:.8f} "
                             f"(max(stops_level={int(stops_level_points or 0)}, "
                             f"freeze_level={int(freeze_level_points or 0)}) x point={float(point)})")
        else:
            out["reason"] = "stop distance satisfies the broker's minimum"
    except Exception as e:                                   # pragma: no cover - defensive
        out["reason"] = f"stops-level validation failed to run: {type(e).__name__}: {e}"
    return out


def pip_conversion_report(*, symbol: str, digits: Optional[int], point: Optional[float],
                          tick_size: Optional[float] = None,
                          stops_level_points: Optional[int] = None,
                          freeze_level_points: Optional[int] = None,
                          sl_pips: Optional[float] = None,
                          tp_pips: Optional[float] = None,
                          side: Optional[str] = None,
                          entry: Optional[float] = None) -> Dict[str, Any]:
    """The diagnostics every order preview carries: how the pip converts to price.

    States the resolved ``digits``/``point``/``pip_size``/``points_per_pip``, the
    price distance each pip input resolves to (SL/TP), the rounded request prices
    (to tick size) when an entry is known, and whether the convention could be
    established at all. The UI renders this verbatim so the pip label and the
    submitted distance can never disagree again.
    """
    pip_size = pip_size_from_digits(digits, point)
    sl_dist = pips_to_price(sl_pips, pip_size)
    tp_dist = pips_to_price(tp_pips, pip_size)
    rep: Dict[str, Any] = {
        "symbol": symbol,
        "digits": digits,
        "point": point,
        "tick_size": tick_size,
        "pip_size": pip_size,
        "points_per_pip": points_per_pip_from_digits(digits),
        "convention_established": pip_size is not None,
        "convention_rule": ("pip_size = 10 x point for 3/5-digit symbols, "
                            "1 x point for 2/4-digit symbols (MT5_BRIDGE_DETAILS.txt)"),
        "sl_pips": sl_pips,
        "sl_price_distance": sl_dist,
        "tp_pips": tp_pips,
        "tp_price_distance": tp_dist,
    }
    if pip_size is None:
        rep["refusal_reason"] = (
            f"the pip convention for {symbol} cannot be established from "
            f"digits={digits!r}, point={point!r} - the order must be refused rather "
            f"than sized with a guessed conversion")
    if entry is not None and side:
        up = str(side).upper() == "BUY"
        if sl_dist is not None:
            rep["sl_price"] = round_to_tick(entry - sl_dist if up else entry + sl_dist,
                                            tick_size, digits)
        if tp_dist is not None:
            rep["tp_price"] = round_to_tick(entry + tp_dist if up else entry - tp_dist,
                                            tick_size, digits)
    rep["stops_check"] = {
        "sl": stops_distance_check(sl_dist, point, stops_level_points, freeze_level_points),
        "tp": stops_distance_check(tp_dist, point, stops_level_points, freeze_level_points),
    }
    return rep


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
        """One pip in price terms — the V6.4 digits rule (see module docstring).

        MT5's *point* is the last quoted digit; a pip is 10 points on 3/5-digit
        symbols and 1 point on 2/4-digit symbols. For this product's XAUUSD
        (digits=2, point=0.01) that makes a pip **0.01**, so "100 pips" is a
        $1.00 price distance — the conversion the MT5 handoff specifies. The
        pre-V6.4 rule here (unconditionally 10 x point) made gold stops ten
        times too far; it is not used any more. Never guess: if digits/point do
        not establish a convention the property raises so the caller refuses.
        """
        pip = pip_size_from_digits(self.digits, self.point)
        if pip is None:
            raise ValueError(
                f"pip convention for {self.symbol} cannot be established from "
                f"digits={self.digits!r}, point={self.point!r} — refuse the order "
                f"instead of guessing")
        return pip

    @property
    def points_per_pip(self) -> float:
        """How many of this symbol's points make one pip (10 on 3/5-digit, 1 on 2/4)."""
        ppp = points_per_pip_from_digits(self.digits)
        if ppp is None:
            raise ValueError(
                f"points-per-pip for {self.symbol} cannot be established from "
                f"digits={self.digits!r}")
        return ppp

    def pip_conversion(self, sl_pips: Optional[float] = None,
                       tp_pips: Optional[float] = None) -> Dict[str, Any]:
        """Diagnostics payload: resolved digits/point/pip and the price distances."""
        return pip_conversion_report(
            symbol=self.symbol, digits=self.digits, point=self.point,
            tick_size=self.tick_size or self.point,
            stops_level_points=self.stops_level_points,
            freeze_level_points=self.freeze_level_points,
            sl_pips=sl_pips, tp_pips=tp_pips)

    def to_payload(self) -> Dict[str, Any]:
        d = asdict(self)
        try:
            d["pip_size"] = self.pip_size
            d["points_per_pip"] = self.points_per_pip
        except ValueError:
            d["pip_size"] = None
            d["points_per_pip"] = None
            d["pip_convention_established"] = False
        else:
            d["pip_convention_established"] = True
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
