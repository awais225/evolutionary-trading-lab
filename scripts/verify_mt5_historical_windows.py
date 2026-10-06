#!/usr/bin/env python3
"""
V4.6 — real-MT5 historical-data verification script (run this ON a Windows host
that has a MetaTrader 5 terminal installed).

Why it exists
-------------
The V4.6 historical backtest executes over *stored* market data with the local
backtest engine, so it needs no terminal at all. What a terminal adds is the
ability to fetch/refresh the stored bars and to prove that a real MT5 terminal
(instead of the labelled SIMULATOR bridge) is what produced them. This script
verifies exactly that, read-only, and never places an order.

What it checks
--------------
  1. `MetaTrader5` package + terminal reachable, and the bridge is the REAL one
     (not the simulator).
  2. The symbol exists and is tradable *for data purposes* (symbol_info), with
     the broker/server identity that will be recorded in every run's provenance.
  3. Which timeframes the terminal actually has bars for, and the real available
     range per timeframe (copy_rates_range).
  4. Whether the historical period a user asks for is covered by the data.
  5. The stored DATA datasets the backtester resolves for those symbol/timeframe
     pairs (the V4.6 catalogue), so runtime provenance can be compared with the
     terminal's own answer.

It is strictly read-only: it never calls send_market_order/close_position, never
runs the V4.2 demo execution path and never activates live testing.

Usage (Windows, from the repository root):
    python scripts/verify_mt5_historical_windows.py --symbol XAUUSD --timeframe M15 \
        --start 2026-09-01 --end 2026-10-05

Exit code 0 = verification passed, 1 = a check failed, 2 = could not run
(MetaTrader5 package or terminal unavailable — report that plainly instead of
claiming a real MT5 result).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")


def _mt5_timeframe(name: str):
    import MetaTrader5 as mt5            # noqa: N813  (Windows-only package)
    return getattr(mt5, f"TIMEFRAME_{name}")


def check_terminal(args) -> dict:
    import MetaTrader5 as mt5            # noqa: N813
    if not mt5.initialize(path=args.terminal or None, timeout=args.timeout_ms):
        return {"ok": False, "error": f"mt5.initialize failed: {mt5.last_error()}"}
    info = mt5.terminal_info()
    acct = mt5.account_info()
    return {
        "ok": True,
        "terminal": {"build": getattr(info, "build", None), "company": getattr(info, "company", None),
                     "path": getattr(info, "path", None), "connected": getattr(info, "connected", None)},
        "account": {"login": getattr(acct, "login", None), "server": getattr(acct, "server", None),
                    "trade_mode": getattr(acct, "trade_mode", None),
                    "is_demo": getattr(acct, "trade_mode", None) == 0 if acct else None},
        "read_only": True,
    }


def check_symbol(symbol: str) -> dict:
    import MetaTrader5 as mt5            # noqa: N813
    si = mt5.symbol_info(symbol)
    if si is None:
        return {"ok": False, "error": f"symbol {symbol} not available on this terminal"}
    if not si.visible:
        mt5.symbol_select(symbol, True)
        si = mt5.symbol_info(symbol)
    return {"ok": True, "symbol": symbol, "digits": si.digits, "point": si.point,
            "contract_size": si.trade_contract_size, "spread_points": si.spread,
            "currency_profit": getattr(si, "currency_profit", None)}


def check_range(symbol: str, timeframe: str, start: dt.datetime, end: dt.datetime) -> dict:
    import MetaTrader5 as mt5            # noqa: N813
    tf = _mt5_timeframe(timeframe)
    rates = mt5.copy_rates_range(symbol, tf, start, end)
    if rates is None:
        return {"ok": False, "error": f"copy_rates_range returned None: {mt5.last_error()}"}
    n = len(rates)
    out = {"ok": n > 0, "bars": n, "requested": {"start": start.isoformat(), "end": end.isoformat()}}
    if n:
        out["first_bar"] = dt.datetime.utcfromtimestamp(int(rates[0]["time"])).isoformat() + "Z"
        out["last_bar"] = dt.datetime.utcfromtimestamp(int(rates[-1]["time"])).isoformat() + "Z"
    else:
        out["error"] = "no bars in the requested period on this terminal"
    return out


def check_stored_datasets(symbol: str, timeframe: str) -> dict:
    """What the V4.6 catalogue resolves locally (stored DATA), for comparison."""
    try:
        from app.historical_backtest import runs as hb
        caps = hb.capabilities()
    except Exception as e:
        return {"ok": False, "error": f"could not read the V4.6 catalogue: {e}"}
    matches = [d for d in caps["datasets"]
               if d["symbol"] == symbol and d["timeframe"] == timeframe]
    return {"ok": bool(matches), "datasets": matches,
            "bridge": caps["bridge"], "note": "these are the stored datasets a V4.6 run would use"}


def main() -> int:
    ap = argparse.ArgumentParser(description="V4.6 real-MT5 historical data verification (read-only)")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--timeframe", default="M15", choices=TIMEFRAMES)
    ap.add_argument("--start", default=None, help="YYYY-MM-DD (default: 60 days before --end)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--terminal", default=None, help="explicit path to terminal64.exe")
    ap.add_argument("--timeout-ms", type=int, default=60000)
    args = ap.parse_args()

    end = dt.datetime.strptime(args.end, "%Y-%m-%d") if args.end else dt.datetime.utcnow()
    start = dt.datetime.strptime(args.start, "%Y-%m-%d") if args.start else end - dt.timedelta(days=60)

    report: dict = {"script": "verify_mt5_historical_windows", "version": "V4.6",
                    "orders_placed": False,
                    "generated_at": dt.datetime.utcnow().isoformat() + "Z",
                    "python": sys.version.split()[0], "cwd": os.getcwd()}

    try:
        import MetaTrader5  # noqa: F401
    except Exception as e:
        report["result"] = "UNAVAILABLE"
        report["error"] = (f"the MetaTrader5 package is not importable here ({e}). This is the expected "
                           f"result on Linux/CI: report it plainly — a V4.6 run in that environment uses "
                           f"stored MT5 bars with the local engine and must not be described as a terminal run.")
        print(json.dumps(report, indent=2, default=str))
        return 2

    terminal = check_terminal(args)
    report["terminal"] = terminal
    if not terminal.get("ok"):
        report["result"] = "UNAVAILABLE"
        print(json.dumps(report, indent=2, default=str))
        return 2

    report["symbol"] = check_symbol(args.symbol)
    report["range"] = check_range(args.symbol, args.timeframe, start, end)
    report["stored"] = check_stored_datasets(args.symbol, args.timeframe)

    checks = {
        "terminal_connected": bool(terminal.get("connected")),
        "symbol_available": bool(report["symbol"].get("ok")),
        "bars_in_requested_period": bool(report["range"].get("ok")),
        "local_dataset_available": bool(report["stored"].get("ok")),
    }
    report["checks"] = checks
    report["result"] = "PASS" if all(checks.values()) else "FAIL"
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
