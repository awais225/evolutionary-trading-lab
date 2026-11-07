"""Broker-specific instrument metadata: normalization, snapshots, fingerprints.

Every field comes from the broker's own `symbol_info()` / `symbol_info_tick()`.
Nothing is defaulted: what the terminal does not report stays None and is listed
in `validation.missing`. Snapshots carry broker/server + terminal identity +
retrieved_at + a content fingerprint; one broker's XAUUSD can never silently
overwrite another broker's XAUUSD (storage keys are server-scoped, see
services/api/app/metadata_service.py).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .connection import as_dict
from .sizing import pip_size_from_digits, point_from_digits

METADATA_SCHEMA_VERSION = 1

# Normalization map: canonical key -> accepted MT5 symbol_info keys (first hit wins).
_SYMBOL_FIELD_MAP = {
    "digits": ("digits",),
    "point": ("point",),
    "trade_tick_size": ("trade_tick_size", "tick_size"),
    "trade_tick_value": ("trade_tick_value", "tick_value"),
    "trade_tick_value_profit": ("trade_tick_value_profit",),
    "trade_tick_value_loss": ("trade_tick_value_loss",),
    "trade_contract_size": ("trade_contract_size", "contract_size"),
    "volume_min": ("volume_min",),
    "volume_max": ("volume_max",),
    "volume_step": ("volume_step",),
    "currency_base": ("currency_base",),
    "currency_profit": ("currency_profit",),
    "currency_margin": ("currency_margin",),
    "trade_calc_mode": ("trade_calc_mode",),
    "trade_mode": ("trade_mode",),
    "stops_level": ("stops_level", "trade_stops_level"),
    "freeze_level": ("freeze_level", "trade_freeze_level"),
    "filling_mode": ("filling_mode",),
    "spread": ("spread",),
    "spread_float": ("spread_float",),
    "swap_long": ("swap_long",),
    "swap_short": ("swap_short",),
    "session_open": ("session_open",),
    "session_deals": ("session_deals",),
    "select": ("select",),
    "visible": ("visible",),
}

_TICK_FIELD_MAP = {
    "bid": ("bid",),
    "ask": ("ask",),
    "last": ("last",),
    "time": ("time",),
    "time_msc": ("time_msc",),
    "volume": ("volume", "volume_real"),
    "flags": ("flags",),
}

REQUIRED_FOR_VALIDATION = ("point_size", "tick_size", "pip_size")


def _first(raw: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    for k in keys:
        if k in raw and raw[k] is not None:
            return raw[k]
    return None


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return f


def normalize_symbol_info(raw_symbol: Any) -> Dict[str, Any]:
    d = as_dict(raw_symbol)
    out: Dict[str, Any] = {}
    for key, candidates in _SYMBOL_FIELD_MAP.items():
        out[key] = _first(d, candidates)

    missing: List[str] = []

    digits = out.get("digits")
    try:
        digits = int(digits) if digits is not None else None
    except (TypeError, ValueError):
        missing.append("digits (not an integer)")
        digits = None
    out["digits"] = digits

    point = _num(out.get("point"))
    if point is None or point <= 0:
        missing.append("point")
    else:
        out["point"] = point
    if point is None:
        derived_point = point_from_digits(digits)
        if derived_point:
            out["point"] = derived_point
            missing.append("point (derived from digits - broker did not report it; prefer symbol_info())")

    tick_size = _num(out.get("trade_tick_size"))
    if tick_size is None or tick_size <= 0:
        missing.append("trade_tick_size")
    else:
        out["trade_tick_size"] = tick_size

    contract = _num(out.get("trade_contract_size"))
    if contract is None or contract <= 0:
        missing.append("trade_contract_size")
    else:
        out["trade_contract_size"] = contract

    for key in ("trade_tick_value", "volume_min", "volume_max", "volume_step",
                "swap_long", "swap_short", "spread"):
        f = _num(out.get(key))
        out[key] = f if f is not None else out.get(key)

    if out.get("volume_min") is not None and out.get("volume_max") is not None \
            and _num(out["volume_min"]) is not None and _num(out["volume_max"]) is not None:
        if float(out["volume_min"]) > float(out["volume_max"]):
            missing.append("volume_min > volume_max (inconsistent broker values)")

    # derived convenience block (formulas documented; broker values are authoritative)
    point_v = _num(out.get("point"))
    pip = pip_size_from_digits(digits, point_v)
    out["_derived"] = {
        "point_size": point_v,
        "tick_size": _num(out.get("trade_tick_size")),
        "pip_size": pip,
        "contract_size": _num(out.get("trade_contract_size")),
        "digits_rule": "pip = 10*point on 3/5-digit feeds else point (XAUUSD 2-digit -> pip == point)",
    }
    if pip is None:
        missing.append("pip_size (undeterminable from digits/point)")

    out["_missing"] = missing
    out["_raw"] = d
    return out


def normalize_tick(raw_tick: Any) -> Dict[str, Any]:
    d = as_dict(raw_tick)
    out: Dict[str, Any] = {}
    for key, candidates in _TICK_FIELD_MAP.items():
        out[key] = _first(d, candidates)
    for key in ("bid", "ask", "last", "volume"):
        f = _num(out.get(key))
        if f is not None:
            out[key] = f
    out["_raw"] = d
    return out


def _semantic_fingerprint(payload: Dict[str, Any]) -> str:
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def build_snapshot(*, broker_server: str, symbol: str, raw_symbol: Any,
                   raw_tick: Any = None, terminal_identity: Optional[Dict[str, Any]] = None,
                   account_login: Optional[int] = None, source: str = "mt5-terminal",
                   retrieved_at: Optional[str] = None) -> Dict[str, Any]:
    if not str(broker_server or "").strip():
        raise ValueError("broker_server is required - a snapshot without broker identity is refused")
    sym = str(symbol or "").strip().upper()
    if not sym:
        raise ValueError("symbol is required")

    fields = normalize_symbol_info(raw_symbol)
    tick = normalize_tick(raw_tick) if raw_tick is not None else {}
    missing = list(fields.get("_missing", [])) + list(tick.get("_missing", []))

    derived = dict(fields.get("_derived", {}))
    # live spread from the tick itself (arithmetic on broker values only)
    bid, ask, point = _num(tick.get("bid")), _num(tick.get("ask")), _num(fields.get("point"))
    if bid is not None and ask is not None and ask >= bid:
        derived["spread_price"] = round(ask - bid, 10)
        if point:
            derived["spread_points"] = round((ask - bid) / point, 6)
        derived["spread_source"] = "symbol_info_tick (ask-bid) at snapshot time"
    semantic = {
        "instrument": sym,
        "broker_server": str(broker_server).strip(),
        "digits": fields.get("digits"),
        "point": _num(fields.get("point")),
        "trade_tick_size": _num(fields.get("trade_tick_size")),
        "trade_tick_value": _num(fields.get("trade_tick_value")),
        "trade_contract_size": _num(fields.get("trade_contract_size")),
        "volume_min": _num(fields.get("volume_min")),
        "volume_max": _num(fields.get("volume_max")),
        "volume_step": _num(fields.get("volume_step")),
        "stops_level": fields.get("stops_level"),
        "freeze_level": fields.get("freeze_level"),
        "currency_base": fields.get("currency_base"),
        "currency_profit": fields.get("currency_profit"),
        "currency_margin": fields.get("currency_margin"),
        "trade_mode": fields.get("trade_mode"),
    }
    snapshot = {
        "schema_version": METADATA_SCHEMA_VERSION,
        "instrument": sym,
        "broker_server": str(broker_server).strip(),
        "terminal": terminal_identity or {},
        "account_login": int(account_login) if account_login is not None else None,
        "source": source,
        "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(),
        "fields": {k: v for k, v in fields.items() if not k.startswith("_")},
        "raw": {"symbol_info": fields.get("_raw", {}), "tick": tick.get("_raw", {})},
        "tick": {k: v for k, v in tick.items() if not k.startswith("_")},
        "derived": derived,
        "fingerprint": _semantic_fingerprint(semantic),
        "validation": {
            "ok": not missing,
            "missing": missing,
            "required_for_validation": list(REQUIRED_FOR_VALIDATION),
        },
    }
    return snapshot


def broker_meta_from_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """The validation contract: {SYMBOL: {point_size, tick_size, pip_size, ...}}.

    Fields absent from the snapshot stay absent - validation will name them."""
    d = snapshot.get("derived") or {}
    meta: Dict[str, float] = {}
    for src_key, dst_key in (("point_size", "point_size"), ("tick_size", "tick_size"),
                             ("pip_size", "pip_size"), ("contract_size", "contract_size")):
        v = _num(d.get(src_key))
        if v is not None and v > 0:
            meta[dst_key] = v
    return {str(snapshot.get("instrument", "")).upper(): meta} if meta else {}


__all__ = [
    "METADATA_SCHEMA_VERSION", "REQUIRED_FOR_VALIDATION",
    "normalize_symbol_info", "normalize_tick", "build_snapshot",
    "broker_meta_from_snapshot",
]
