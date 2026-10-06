#!/usr/bin/env python
"""
V4.2 — on-host MT5 DEMO execution verification (spec §17 tests 1-4).

Run this ON THE WINDOWS HOST where the MetaTrader 5 terminal is installed and
logged into the DEMO account (the Linux/CI sandbox cannot reach a terminal).

    set EVOLUTIONARY_LAB_DATA_ROOT=C:\\path\\to\\DATA
    python -X utf8 scripts\\verify_mt5_demo_execution.py --place

What it does, in order (nothing is sent unless --place is given):

  1. connection      : bridge status, terminal identity, account identity
  2. demo safety     : positive DEMO verification (blocks on anything else)
  3. BUY             : small market BUY with real SL/TP through the V4.2 path
  4. SELL            : small market SELL with real SL/TP
  5. verification    : position + SL/TP read back from the terminal
  6. cleanup --close : closes the verification positions again

Every order goes through app.mt5.execution.place_demo_order(), i.e. the exact
code path the dashboard button uses (validation -> demo gate -> single send ->
retcode interpretation -> position verification -> audit row).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BACKEND = os.path.join(REPO, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main() -> int:
    ap = argparse.ArgumentParser(description="V4.2 MT5 demo execution verification")
    ap.add_argument("--symbol", default=None, help="defaults to data.symbol from config")
    ap.add_argument("--volume", type=float, default=None, help="defaults to the symbol minimum")
    ap.add_argument("--sl-points", type=float, default=300.0, help="SL distance in points")
    ap.add_argument("--tp-points", type=float, default=400.0, help="TP distance in points")
    ap.add_argument("--deviation", type=int, default=20)
    ap.add_argument("--place", action="store_true", help="actually send the BUY and SELL test orders")
    ap.add_argument("--close", action="store_true", help="close the positions created by this script")
    ap.add_argument("--keep", action="store_true", help="do not close positions (default keeps only with --keep)")
    args = ap.parse_args()

    from app.config import get_config
    from app.mt5.execution import (PLACE_CONFIRMATION, close_demo_position,
                                   demo_account_guard, execution_state,
                                   place_demo_order, validate_order_request)

    cfg = get_config()
    symbol = (args.symbol or cfg.data.symbol).upper()
    results = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    # ---------------- 1. connection ----------------
    hr("TEST 1 — MT5 connection")
    st = execution_state()
    print(f"  bridge        : {st['bridge']}")
    print(f"  default symbol: {st['default_symbol']}")
    check("MT5 bridge is real (not simulator)", st["bridge"]["source"] == "MT5",
          f"source={st['bridge']['source']}")
    check("MT5 terminal connected", st["bridge"]["connected"])

    # ---------------- 2. demo safety ----------------
    hr("TEST 2 — DEMO account safety guard")
    guard = demo_account_guard()
    acct = guard.get("account") or {}
    print(f"  account       : {acct.get('login')} @ {acct.get('server')} "
          f"({acct.get('trade_mode_name')}) balance={acct.get('balance')} "
          f"free_margin={acct.get('margin_free')}")
    print(f"  verdict       : demo_verified={guard.get('demo_verified')} "
          f"blocked_code={guard.get('blocked_code')}")
    check("Account positively verified as DEMO", guard.get("demo_verified"),
          guard.get("blocked_reason") or "trade_mode=DEMO")
    if not guard.get("demo_verified"):
        print("\nOrder execution is correctly BLOCKED — nothing was sent.")
        return _summary(results)

    sinfo = st.get("symbol") or {}
    quote = st.get("quote") or {}
    point = float(sinfo.get("point") or 0.01)
    volume = args.volume if args.volume else (sinfo.get("volume_min") or 0.01)
    print(f"  symbol        : {symbol} point={point} digits={sinfo.get('digits')} "
          f"vol min/max/step={sinfo.get('volume_min')}/{sinfo.get('volume_max')}/{sinfo.get('volume_step')} "
          f"stops_level={sinfo.get('stops_level')}")
    print(f"  quote         : bid={quote.get('bid')} ask={quote.get('ask')}")
    print(f"  test volume   : {volume} lots, SL {args.sl_points} pts, TP {args.tp_points} pts")

    if not args.place:
        hr("DRY RUN — no orders sent")
        print("  re-run with --place to send the BUY/SELL test orders")
        return _summary(results)

    # ---------------- 3/4. BUY + SELL ----------------
    placed = []
    for side in ("buy", "sell"):
        hr(f"TEST {'3' if side == 'buy' else '4'} — {side.upper()} with real SL/TP")
        tick = st["quote"] or {}
        ref = float(tick["ask"] if side == "buy" else tick["bid"])
        sl = ref - args.sl_points * point if side == "buy" else ref + args.sl_points * point
        tp = ref + args.tp_points * point if side == "buy" else ref - args.tp_points * point
        sl, tp = round(sl, int(sinfo.get("digits") or 2)), round(tp, int(sinfo.get("digits") or 2))

        rep = validate_order_request(symbol=symbol, side=side, volume=volume, sl=sl, tp=tp)
        if not rep["placement_allowed"]:
            check(f"{side.upper()} passes local validation", False,
                  "; ".join(e["message"] for e in rep["errors"][:3]))
            continue
        check(f"{side.upper()} passes local validation", True, f"SL {sl} / TP {tp}")

        try:
            res = place_demo_order({
                "symbol": symbol, "side": side, "volume": volume, "sl": sl, "tp": tp,
                "deviation": args.deviation, "confirm": PLACE_CONFIRMATION,
            })
        except Exception as e:                      # structured failure, never swallowed
            check(f"{side.upper()} order submission", False, str(e))
            continue

        ex_ = res["execution"]
        print(f"  status        : {res['status']} ({res['label']})")
        print(f"  retcode       : {res['broker']['retcode']} — {res['broker']['message']}")
        print(f"  ticket/deal   : {res['order']['ticket']} / {res['order']['deal_ticket']}"
              f"  position {res['order']['position_ticket']}")
        print(f"  executed      : {ex_['executed_volume']} lots @ {ex_['exec_price']} "
              f"(requested {ex_['requested_price']})")
        print(f"  SL/TP broker  : {ex_['sl_broker']} / {ex_['tp_broker']} "
              f"(verified={ex_['sl_tp_verified']})")
        print(f"  audit row     : client_order_id={res['client_order_id']}")
        check(f"{side.upper()} executed on the demo account",
              res["status"] in ("POSITION_OPEN", "PENDING_ORDER"), res["label"])
        check(f"{side.upper()} SL/TP present on the broker object",
              ex_["sl_tp_verified"] is True,
              f"broker SL {ex_['sl_broker']} / TP {ex_['tp_broker']}")
        if res["order"]["ticket"]:
            placed.append(res["order"]["ticket"])
        time.sleep(1.0)

    # ---------------- cleanup ----------------
    if args.close and placed:
        hr("CLEANUP — closing the verification positions")
        for t in placed:
            try:
                out = close_demo_position(t)
                check(f"position {t} closed", bool(out.get("ok")), str(out.get("error") or ""))
            except Exception as e:
                check(f"position {t} closed", False, str(e))

    return _summary(results)


def _summary(results) -> int:
    hr("SUMMARY")
    failed = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
