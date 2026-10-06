#!/usr/bin/env python
"""
V4.2/V4.3 — on-host MT5 DEMO execution verification.

V4.2 (spec §17 tests 1-4): connection, demo safety, BUY, SELL, SL/TP read-back,
cleanup.
V4.3 (added below): Live Testing mode safety (starts INACTIVE), risk-based sizing
against the real broker symbol specification, the MT5-authoritative active-trade
counter, explicit activation/deactivation semantics and the stage log.

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
    ap.add_argument("--node", type=int, default=None,
                    help="V4.3: research node to use for the risk/override checks")
    ap.add_argument("--risk-pct", type=float, default=None,
                    help="V4.3: risk %% per trade for the sizing preview (default: configured)")
    ap.add_argument("--activate-probe", action="store_true",
                    help="V4.3: activate Live Testing for a few seconds, then stop it again "
                         "(sends orders ONLY if a real node signal fires during that window)")
    ap.add_argument("--wait-minutes", type=float, default=0.0,
                    help="V4.3: keep Live Testing ACTIVE for this long before printing the log")
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

    # ================= V4.3 — controlled live testing =================
    def check_live_testing(with_mt5: bool) -> None:
        """V4.3 checks shared by the blocked and the fully connected path."""
        from app.live_testing import get_live_testing_engine
        from app.live_testing.risk import RiskBlock, compute_risk, risk_limits
        from app.db.database import get_db as _gdb

        eng = get_live_testing_engine()
        limits = risk_limits()

        hr("TEST 5 — Live Testing starts INACTIVE and never auto-resumes")
        mode = eng.get_mode()
        print(f"  mode          : {mode['mode']}  (previously active: {mode.get('previously_active')})")
        print(f"  engine thread : running={eng.running}")
        check("Live Testing is INACTIVE before any explicit activation", mode["active"] is False)
        check("no persisted 'active' flag exists in the configuration",
              not hasattr(cfg.live_testing, "enabled") and not hasattr(cfg.live_testing, "active"))
        refused = eng.activate(confirmed=False)
        check("activation without confirmation is refused",
              refused.get("code") == "CONFIRMATION_REQUIRED", str(refused.get("code") or ""))

        hr("TEST 6 — live market panel (MT5 authoritative)")
        panel = eng.market_panel(symbol)
        print(f"  symbol        : {panel['symbol']}  connected={panel['connected']} source={panel['source']}")
        print(f"  bid/ask       : {panel['bid']} / {panel['ask']}  spread={panel['spread']}")
        print(f"  tick age      : {panel['tick_age_s']}s (fresh={panel['data_fresh']}) "
              f"tradable={panel['symbol_tradable']} session={panel['session_status']}")
        print(f"  tick size/val : {panel.get('tick_size')} / {panel.get('tick_value')} "
              f"vol min/max/step={panel.get('volume_min')}/{panel.get('volume_max')}/{panel.get('volume_step')}")
        print(f"  reasons       : {panel.get('reasons')}")
        check("market panel follows the active bridge and blocks trading without real MT5",
              panel["source"] == st["bridge"]["source"]
              and (st["bridge"]["source"] == "MT5" or panel["trading_available"] is False),
              f"source={panel['source']} trading_available={panel['trading_available']} "
              f"reasons={panel.get('reasons')}")

        hr("TEST 7 — risk-based sizing on the real broker specification")
        if not with_mt5:
            print("  (skipped: no real MT5 symbol specification available on this host)")
        else:
            acct_equity = (guard.get("account") or {}).get("equity")
            node_id = args.node
            override = None
            if node_id is not None:
                row = _gdb().one("SELECT risk_pct FROM live_test_configs WHERE strategy_id=?", (node_id,))
                override = row["risk_pct"] if row else None
                print(f"  node {node_id} override: {override}%")
            atr_proxy = args.sl_points * point
            ref = float((quote or {}).get("ask") or 0.0)
            sl_preview = ref - atr_proxy
            try:
                trace = compute_risk(node_id=node_id or 0, strategy_id=node_id, symbol=symbol,
                                     side="BUY", entry=ref, sl=sl_preview, equity=acct_equity,
                                     global_pct=limits["risk_pct_default"], override_pct=override,
                                     spec=sinfo)
                sz = trace["sizing"]
                print(f"  equity        : {trace['equity']}  risk {trace['risk_pct']}% "
                      f"({trace['risk_pct_source']}) = {trace['risk_amount']}")
                print(f"  stop distance : {sz['stop_distance']}  risk/lot={sz['risk_per_lot']}")
                print(f"  volume        : {sz['volume']} lots (raw {sz['raw_volume']}) "
                      f"actual risk {sz['actual_risk']}")
                check("risk sizing produced a broker-valid volume",
                      sz["volume_min"] <= sz["volume"] <= sz["volume_max"], f"{sz['volume']} lots")
                check("effective risk never exceeds the configured maximum",
                      trace["risk_pct"] <= limits["risk_pct_max"], f"max {limits['risk_pct_max']}%")
            except RiskBlock as e:
                check("risk sizing produced a broker-valid volume", False, f"{e.code}: {e.message}")
            try:
                compute_risk(node_id=0, strategy_id=None, symbol=symbol, side="BUY", entry=ref,
                             sl=sl_preview, equity=acct_equity,
                             global_pct=limits["risk_pct_max"] * 5, override_pct=None, spec=sinfo)
                check("risk above the configured maximum is blocked", False, "not blocked")
            except RiskBlock as e:
                check("risk above the configured maximum is blocked (no clamping)",
                      e.code == "RISK_PCT_ABOVE_MAXIMUM", e.code)

        hr("TEST 8 — active live-test trade counter (MT5 positions/orders)")
        act = eng.count_activity()
        lim = eng.order_limit_reached(act)
        print(f"  counted from  : {act.get('source')} (counted={act.get('counted')})")
        print(f"  positions/orders: {act.get('positions')} / {act.get('orders')} "
              f"-> active_total={act.get('active_total')}")
        print(f"  limit         : {lim['limit']}  reached={lim['reached']}")
        if act.get("error"):
            print(f"  note          : {act['error']}")
        check("counter never invents a broker position (unknown state is reported)",
              act.get("counted") is True or bool(act.get("error")))
        print(f"  live-test magic range: 778000..778999 (used to isolate live-test trades)")

        hr("TEST 9 — activation / deactivation semantics")
        if not with_mt5:
            print("  (skipped: activation probe needs a verified DEMO account)")
        elif args.activate_probe or args.wait_minutes > 0:
            conf = eng.activation_payload()
            print(f"  confirmation  : nodes={conf['node_count']} symbols={conf['symbols']} "
                  f"risk={conf['risk_pct_default']}% max_active={conf['max_active_trades']} "
                  f"market_ok={conf['market']['trading_available']}")
            hl = len(act.get("position_tickets") or []) + len(act.get("order_tickets") or [])
            out = eng.activate(confirmed=True, armed_by="verify_mt5_demo_execution")
            check("explicit confirmed activation switches to ACTIVE", out.get("ok") is True,
                  str(out.get("mode") or out.get("error") or ""))
            print(f"  mode          : {eng.get_mode()['mode']}")
            window = args.wait_minutes if args.wait_minutes > 0 else (20.0 / 60.0)
            print(f"  running the live-testing loop for {window:g} minute(s) ...")
            end_ts = time.time() + window * 60.0
            while time.time() < end_ts:
                time.sleep(min(10.0, max(1.0, end_ts - time.time())))
            de = eng.deactivate(reason="verification script completed")
            check("STOP LIVE TESTING returns to INACTIVE", de.get("mode") == "INACTIVE")
            check("STOP LIVE TESTING closed no positions", de.get("positions_closed", 0) == 0)
            act2 = eng.count_activity()
            hl2 = len(act2.get("position_tickets") or []) + len(act2.get("order_tickets") or [])
            check("existing positions were left untouched by STOP", hl2 >= hl,
                  f"before={hl} after={hl2}")
        else:
            print("  (skipped: pass --activate-probe to exercise activation on the demo account)")

        hr("TEST 10 — stage-by-stage live-test log (last events)")
        rows = _gdb().get_live_test_events(limit=15)
        if not rows:
            print("  no live-test events recorded yet (no signal has fired)")
        for r in reversed(rows):
            print(f"  {time.strftime('%H:%M:%S', time.localtime(r['ts']))} "
                  f"{r['stage']:<24} [{r['status']:<16}] {r['message']}")
        check("every logged stage carries a human-readable message (never [object Object])",
              all(r.get("message") and "object Object" not in r["message"] for r in rows))

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
        check_live_testing(with_mt5=False)      # V4.3: still proves the safe state
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

    check_live_testing(with_mt5=guard.get("demo_verified"))
    if not args.place:
        hr("DRY RUN — no orders sent")
        print("  re-run with --place to send the BUY/SELL test orders")
        print("  (live-testing orders are only placed by the engine when an eligible node "
              "signal fires while Live Testing is ACTIVE)")
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
