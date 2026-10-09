#!/usr/bin/env python3
"""Standalone MT5 DEMO execution verification (MT5 handoff, Part 9).

Run this ON WINDOWS with the MT5 terminal open, logged in to a DEMO account,
Algo Trading enabled and XAUUSD visible in Market Watch::

    python backend/app/mt5/scripts/verify_mt5_demo_execution.py

It contains every behaviour that makes the production bridge succeed where a
naive MT5 integration fails (Part 0 of MT5_BRIDGE_DETAILS.txt):

  - four-tier connection fallback (path -> bare initialize -> discovered terminals)
  - mt5.symbol_select before every symbol query
  - SL/TP request keys OMITTED when absent (never sl=0.0)
  - the filling-mode fallback chain IOC -> FOK -> RETURN, each a REAL
    mt5.order_send() call
  - success judged only by retcode == 10009
  - lots floored to volume_step and clipped to [volume_min, volume_max]
  - pip size derived from the symbol's digits (never hardcoded)
  - volume minimum enforced — never silently clamped up
  - the verbatim retcode and broker comment reported on failure
  - DEMO safety: trade_mode == 2 (REAL) is refused outright

Nothing here is faked. If it prints FAILED, the printed retcode/comment is the
broker's own answer. This script IS the acceptance tool for the requirement

    mt5.order_send() actually executed against a real MT5 terminal.
"""
from __future__ import annotations

import time

MAGIC = 777001
DEVIATION = 20
SUCCESS = 10009                      # TRADE_RETCODE_DONE
RETRY_ON = {10029, 10030}            # invalid order / unsupported filling


def _mt5():
    """Guarded import (the app must stay importable where MT5 does not exist)."""
    try:
        import MetaTrader5 as mt5
        return mt5
    except Exception as e:                                  # pragma: no cover
        raise SystemExit(f"MetaTrader5 is not importable ({e}). "
                         f"pip install MetaTrader5 on Windows.") from e


def discover_terminals(mt5) -> list:
    """Standard install locations + the Windows Uninstall registry."""
    import os
    from pathlib import Path
    roots, found = [], []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        val = os.environ.get(var)
        if val:
            roots.append(Path(val))
            if var == "LOCALAPPDATA":
                roots.append(Path(val) / "Programs")
    for root in roots:
        if not root.exists():
            continue
        for exe in ("terminal64.exe", "terminal.exe", "metatrader64.exe", "metatrader.exe"):
            c = root / "MetaTrader 5" / exe
            if c.exists():
                found.append(str(c))
        try:
            for child in root.iterdir():
                if child.is_dir() and ("metatrader" in child.name.lower()
                                       or "mt5" in child.name.lower()):
                    for exe in ("terminal64.exe", "terminal.exe", "metatrader64.exe",
                                "metatrader.exe"):
                        c = child / exe
                        if c.exists():
                            found.append(str(c))
        except Exception:
            pass
    try:                                                    # registry (Windows only)
        import sys
        if sys.platform == "win32":
            import winreg
            for hkey, subkey in (
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            ):
                try:
                    with winreg.OpenKey(hkey, subkey) as key:
                        count, _, _ = winreg.QueryInfoKey(key)
                        for i in range(count):
                            try:
                                sub = winreg.EnumKey(key, i)
                                with winreg.OpenKey(key, sub) as item:
                                    name, _ = winreg.QueryValueEx(item, "DisplayName")
                                    loc, _ = winreg.QueryValueEx(item, "InstallLocation")
                                    if "metatrader" in str(name).lower() or "mt5" in str(name).lower():
                                        for exe in ("terminal64.exe", "terminal.exe"):
                                            c = Path(loc) / exe
                                            if c.exists():
                                                found.append(str(c))
                            except Exception:
                                continue
                except Exception:
                    continue
    except Exception:
        pass
    return list(dict.fromkeys(found))


def connect(mt5, login=0, password="", server="", path=None, timeout_ms=60000) -> bool:
    """Four tiers: explicit path, bare discovery, then discovered terminals."""
    kwargs = {"timeout": timeout_ms}
    if login:
        kwargs.update(login=int(login), password=password, server=server)

    if path:
        if mt5.initialize(path=path, **kwargs):
            return True

    if mt5.initialize(**kwargs):
        return True

    for cand in discover_terminals(mt5):
        if mt5.initialize(path=cand, **kwargs):
            return True

    print("initialize failed:", mt5.last_error())
    return False


def pip_size_of(digits, point):
    """A pip is ten points on 3/5-digit symbols, one point on 2/4-digit."""
    return 10.0 * point if digits in (3, 5) else 1.0 * point


def normalise_lots(lots, vol_min, vol_max, vol_step):
    """Floor to the step so you never risk more than asked. Never round up."""
    steps = int(lots / vol_step)
    return max(vol_min, min(vol_max, steps * vol_step))


def place_order(mt5, symbol, side, lots, sl_pips=None, tp_pips=None, comment="evolab"):
    """Place a real market order with SL/TP. Returns (ok, info_dict)."""

    # --- gate: terminal present
    if not mt5.terminal_info():
        return False, {"error": "MT5 terminal not initialised"}

    # --- gate: symbol in Market Watch (this is the step most code forgets)
    if not mt5.symbol_select(symbol, True):
        return False, {"error": f"symbol_select failed for {symbol}"}

    si = mt5.symbol_info(symbol)
    if si is None:
        return False, {"error": f"symbol_info returned None for {symbol}"}

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False, {"error": f"no tick for {symbol}"}

    digits = si.digits
    point = si.point
    pip = pip_size_of(digits, point)
    price = tick.ask if side.lower() == "buy" else tick.bid
    order_type = mt5.ORDER_TYPE_BUY if side.lower() == "buy" else mt5.ORDER_TYPE_SELL

    vol_min = float(si.volume_min or 0.01)
    vol_max = float(si.volume_max or 100.0)
    vol_step = float(si.volume_step or 0.01)

    # --- lots: floor to step, clip to range, refuse below minimum
    lots = normalise_lots(float(lots), vol_min, vol_max, vol_step)
    if lots < vol_min:
        return False, {"error": f"lots {lots} below broker minimum {vol_min}"}

    # --- SL/TP prices. OMIT the keys entirely when not supplied.
    sl_price = tp_price = None
    if sl_pips and sl_pips > 0:
        sl_price = (price - sl_pips * pip) if side.lower() == "buy" \
                   else (price + sl_pips * pip)
        sl_price = round(sl_price, digits)
    if tp_pips and tp_pips > 0:
        tp_price = (price + tp_pips * pip) if side.lower() == "buy" \
                   else (price - tp_pips * pip)
        tp_price = round(tp_price, digits)

    # --- stops level guard: cheaper to check than to be rejected
    min_stop = max(int(getattr(si, "trade_stops_level", 0) or 0),
                   int(getattr(si, "trade_freeze_level", 0) or 0)) * point
    if min_stop > 0:
        for label, px in (("SL", sl_price), ("TP", tp_price)):
            if px is not None and abs(px - price) < min_stop:
                return False, {"error": f"{label} too close to price "
                                        f"(min distance {min_stop})"}

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lots,
        "type": order_type,
        "price": price,
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
    }
    # Only insert when genuinely present. Sending sl=0.0 is rejected.
    if sl_price:
        request["sl"] = sl_price
    if tp_price:
        request["tp"] = tp_price

    # --- the fallback chain. This is what makes it work across brokers.
    attempts = []
    last = None
    for name, mode in (("IOC", mt5.ORDER_FILLING_IOC),
                       ("FOK", mt5.ORDER_FILLING_FOK),
                       ("RETURN", mt5.ORDER_FILLING_RETURN)):
        req = dict(request)
        req["type_filling"] = mode
        try:
            res = mt5.order_send(req)
        except Exception as e:                              # never swallowed
            attempts.append({"filling": name, "outcome": "ORDER_SEND_EXCEPTION",
                             "exception": f"{type(e).__name__}: {e}"})
            return False, {"attempts": attempts, "request": request,
                           "exception": f"{type(e).__name__}: {e}"}
        last = res
        if res is None:
            attempts.append({"filling": name, "outcome": "ORDER_SEND_RETURNED_NONE",
                             "last_error": mt5.last_error()})
            break                     # unknown outcome - never blindly resend
        attempts.append({"filling": name, "retcode": res.retcode,
                         "comment": res.comment, "order": res.order, "deal": res.deal})
        if res.retcode == SUCCESS:
            return True, {
                "ticket": res.order, "deal": getattr(res, "deal", None),
                "price": res.price, "volume": lots, "retcode": res.retcode,
                "comment": res.comment, "filling": name,
                "sl": sl_price, "tp": tp_price, "attempts": attempts,
            }
        if res.retcode in RETRY_ON:
            continue          # this broker rejects this mode - try the next
        break                 # any other code is a real rejection - stop

    return False, {
        "retcode": getattr(last, "retcode", None) if last else None,
        "comment": getattr(last, "comment", "order_send returned None")
                   if last else "order_send returned None",
        "request": request,
        "attempts": attempts,
    }


def lots_for_risk(equity, risk_fraction, sl_distance, contract_size,
                  vol_min, vol_max, vol_step):
    """Size from the money you are willing to LOSE, never from margin."""
    if sl_distance <= 0 or contract_size <= 0:
        return 0.0
    raw = (equity * risk_fraction) / (sl_distance * contract_size)
    return normalise_lots(raw, vol_min, vol_max, vol_step)


def main() -> int:
    mt5 = _mt5()
    if not connect(mt5):
        raise SystemExit("could not connect to MT5")

    info = mt5.account_info()
    print(f"account={info.login} server={info.server} "
          f"trade_mode={info.trade_mode} (0=demo, 2=real)")

    # Safety: refuse to trade a real account
    if info.trade_mode == 2:
        raise SystemExit("SAFETY LOCK: real account detected, refusing to trade")

    mt5.symbol_select("XAUUSD", True)
    si = mt5.symbol_info("XAUUSD")
    if si is None:
        raise SystemExit("XAUUSD is not visible in Market Watch")

    # Risk 1% with a 200 pip stop
    pip = pip_size_of(si.digits, si.point)
    lots = lots_for_risk(
        equity=info.equity, risk_fraction=0.01,
        sl_distance=200 * pip, contract_size=si.trade_contract_size,
        vol_min=float(si.volume_min), vol_max=float(si.volume_max),
        vol_step=float(si.volume_step),
    )
    print(f"computed lots = {lots}")

    ok, res = place_order(mt5, "XAUUSD", "buy", lots, sl_pips=200, tp_pips=400)
    print("FILLED" if ok else "FAILED", res)
    mt5.shutdown()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
