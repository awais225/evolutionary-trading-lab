#!/usr/bin/env python3
"""V5.3 DEMO EXECUTION FORENSICS — one report that says exactly where an MT5
DEMO order stops.

Why this exists
---------------
Ten releases went into "the demo order does not go through" without an answer,
because each failure was summarised *after the fact* ("retcode null",
"unknown"). This tool does not summarise: it walks the real chain with the real
interpreter, the real MetaTrader5 binding, the real terminal and the real
account, and prints every value the binding produced, including the ones that
are normally thrown away:

    initialize -> terminal_info -> account_info -> symbol_select -> symbol_info
    -> symbol_info_tick -> filling resolution -> order_check -> order_send
    -> mt5.last_error() BEFORE and AFTER -> MqlTradeResult (verbatim)
    -> positions_get()/orders_get()  (the ACTUAL terminal state)

It uses the application's own code path (`app.mt5.factory` -> `MT5RealBridge` ->
`app.mt5.execution.place_demo_order`), so what it proves is what the dashboard
does — not a re-implementation. Nothing is faked and no value is invented: a
field that MT5 does not report prints as `not reported`.

Usage (Windows, from the repository root)
-----------------------------------------
    .venv\\Scripts\\python.exe backend\\tools\\mt5_demo_forensics.py --root . ^
        --symbol XAUUSD --symbol-side buy --volume 0.03

Read-only by default: it runs order_check and STOPS (never order_send).
To actually send ONE demo order the operator must add

    --send --confirm PLACE_DEMO_ORDER

One click -> one `order_send` attempt. There is no retry, ever.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional


class _Tee:
    """Print to the console AND to the report file (one run = one report)."""

    def __init__(self, path: Optional[Path]):
        self._fh = None
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = path.open("w", encoding="utf-8")
            except Exception as e:                                    # pragma: no cover
                print(f"WARNING: cannot write the report file {path}: {e}")

    def write(self, text: str) -> int:
        sys.__stdout__.write(text)
        sys.__stdout__.flush()
        if self._fh is not None:
            self._fh.write(text)
            self._fh.flush()
        return len(text)

    def flush(self) -> None:
        sys.__stdout__.flush()
        if self._fh is not None:
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()


def _hr(title: str = "", char: str = "=") -> None:
    print(char * 78)
    if title:
        print(title)


def _field(label: str, value: Any, note: str = "") -> None:
    shown = "not reported" if value is None or value == "" else str(value)
    if note:
        print(f"  {label:<30}: {shown}    ({note})")
    else:
        print(f"  {label:<30}: {shown}")


def _client_order_id() -> str:
    """ONE identity per run.

    §2.5 — the id names the attempt, so it has to be unique per attempt: the
    executor refuses a `client_order_id` it already recorded (the duplicate gate
    that stops a double click, a refresh or a restart from sending twice). A
    second, deliberate forensic run is a NEW attempt and must not collide with
    the previous one — hence seconds AND a random suffix, both logged.
    """
    import uuid
    return f"forensic-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _bool_txt(v: Any) -> Any:
    if v is None:
        return "not reported"
    return "YES" if v else "NO"


def _default_root() -> Path:
    """The checkout this script belongs to, whatever the current directory is.

    A ZIP checkout is started from the explorer (double click) as often as from a
    terminal, and the working directory then differs per machine — resolving the
    root from this file's own location keeps "run it however you like" true.
    """
    here = Path(__file__).resolve()
    for cand in (Path.cwd(), *(here.parents[i] for i in range(len(here.parents)))):
        if (cand / "backend" / "app").is_dir():
            return cand
    return Path.cwd()


def _bootstrap(root: Path) -> None:
    backend = root / "backend"
    if not (backend / "app").is_dir():
        raise SystemExit(
            f"[FAIL] --root {root} has no backend/app — point --root at the "
            f"repository root (the folder that contains backend\\ and frontend\\)")
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    os.environ.setdefault("EVOLUTIONARY_LAB_DATA_ROOT", str(root / "DATA"))
    os.chdir(backend)


#: the magics this tool's exposure check looks for: the manual-order magic the
#: dashboard uses and the forensic magic this tool itself sends with.
MANUAL_MAGIC = 777000
FORENSIC_MAGIC = 777900
_EXPOSURE_MAGICS = (MANUAL_MAGIC, FORENSIC_MAGIC)


def _existing_exposure(bridge, symbol: str) -> Dict[str, Any]:
    """V5.4 §2.4 — what is ALREADY open for this symbol before anything is sent.

    The operator's brief is explicit: before testing the repaired execution path,
    inspect the current MT5 state and do NOT blindly send the same request again
    (the previous attempt may have reached the broker). This reads the terminal
    only — positions_get / orders_get for the symbol, filtered by the magics this
    tool and the dashboard use — and never changes anything.
    """
    out: Dict[str, Any] = {"symbol": symbol, "magics": list(_EXPOSURE_MAGICS),
                           "positions": [], "orders": [], "queried": False,
                           "errors": []}
    try:
        rows = bridge.positions_get(symbol=symbol)
    except Exception as e:                                    # pragma: no cover - defensive
        out["errors"].append(f"positions_get: {type(e).__name__}: {e}")
        rows = []
    out["queried"] = True
    positions = [p for p in (rows or [])
                 if int(p.get("magic") or 0) in _EXPOSURE_MAGICS]
    out["positions"] = [{"ticket": p.get("ticket"), "symbol": p.get("symbol"),
                         "volume": p.get("volume"), "open": p.get("price_open"),
                         "sl": p.get("sl"), "tp": p.get("tp"),
                         "magic": p.get("magic"), "type": p.get("type"),
                         "time": p.get("time"), "comment": p.get("comment")}
                        for p in positions]
    try:
        orders = bridge.orders_get(symbol=symbol)
    except Exception as e:                                    # pragma: no cover - defensive
        out["errors"].append(f"orders_get: {type(e).__name__}: {e}")
        orders = []
    pending = [o for o in (orders or [])
               if int(o.get("magic") or 0) in _EXPOSURE_MAGICS]
    out["orders"] = [{"ticket": o.get("ticket"), "symbol": o.get("symbol"),
                      "volume": o.get("volume_initial"), "price": o.get("price_open"),
                      "sl": o.get("sl"), "tp": o.get("tp"), "magic": o.get("magic"),
                      "comment": o.get("comment")} for o in pending]
    out["count"] = len(out["positions"]) + len(out["orders"])
    out["verdict"] = ("EXISTING EXPOSURE FOUND — do NOT resend; inspect it in the "
                      "terminal first" if out["count"] else
                      "no open position/order for this symbol with the manual or "
                      "forensic magic — the path is clear to send ONE order")
    return out


def _print_exposure(exposure: Dict[str, Any]) -> None:
    _hr("EXISTING EXPOSURE (checked BEFORE anything is sent)", "-")
    _field("symbol", exposure.get("symbol"))
    _field("magics checked", ", ".join(str(m) for m in exposure.get("magics") or []))
    _field("positions found", len(exposure.get("positions") or []))
    for p in exposure.get("positions") or []:
        print(f"      ticket={p.get('ticket')} {p.get('symbol')} vol={p.get('volume')} "
              f"open={p.get('open')} sl={p.get('sl')} tp={p.get('tp')} magic={p.get('magic')}")
    _field("pending orders found", len(exposure.get("orders") or []))
    for o in exposure.get("orders") or []:
        print(f"      ticket={o.get('ticket')} {o.get('symbol')} vol={o.get('volume')} "
              f"price={o.get('price')} magic={o.get('magic')}")
    for e in exposure.get("errors") or []:
        _field("query error", e)
    _field("verdict", exposure.get("verdict"))


def _run_read_only(bridge, symbol: str, side: str) -> Dict[str, Any]:
    """Everything that can be learned without sending anything."""
    from app.mt5 import mt5_real
    from app.mt5 import execution as ex

    mod = mt5_real.mt5
    out: Dict[str, Any] = {"ok": True}
    out["python"] = {"executable": sys.executable, "version": sys.version.split()[0],
                     "platform": platform.platform(),
                     "is_64bit": sys.maxsize > 2 ** 32}
    out["package"] = {"importable": bool(mt5_real.MT5_PACKAGE_AVAILABLE),
                      "version": getattr(mod, "__version__", None) if mod else None,
                      "import_error": mt5_real.MT5_IMPORT_ERROR or None}
    out["session"] = bridge.session_state() if hasattr(bridge, "session_state") else None
    if not mt5_real.MT5_PACKAGE_AVAILABLE:
        out["ok"] = False
        out["blocker"] = "MT5_PACKAGE_MISSING"
        return out

    # terminal / account
    ti = mod.terminal_info() if mod else None
    ai = mod.account_info() if mod else None
    out["terminal"] = ({
        "name": getattr(ti, "name", None), "company": getattr(ti, "company", None),
        "path": getattr(ti, "path", None), "build": getattr(ti, "build", None),
        "connected": getattr(ti, "connected", None),
        "trade_allowed": getattr(ti, "trade_allowed", None),
        "tradeapi_disabled": getattr(ti, "tradeapi_disabled", None),
        "dlls_allowed": getattr(ti, "dlls_allowed", None),
        "community_account": getattr(ti, "community_account", None),
    } if ti is not None else None)
    out["account"] = ({
        "login": getattr(ai, "login", None), "server": getattr(ai, "server", None),
        "trade_mode": getattr(ai, "trade_mode", None),
        "trade_mode_name": {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(
            getattr(ai, "trade_mode", None), "unknown"),
        "currency": getattr(ai, "currency", None), "leverage": getattr(ai, "leverage", None),
        "balance": getattr(ai, "balance", None), "equity": getattr(ai, "equity", None),
        "margin_free": getattr(ai, "margin_free", None),
        "trade_allowed": getattr(ai, "trade_allowed", None),
        "trade_expert": getattr(ai, "trade_expert", None),
    } if ai is not None else None)

    # symbol
    try:
        mod.symbol_select(symbol, True)
    except Exception as e:                                            # pragma: no cover
        out["symbol_select_error"] = f"{type(e).__name__}: {e}"
    si = mod.symbol_info(symbol) if mod else None
    out["symbol"] = ({
        "symbol": symbol, "visible": getattr(si, "visible", None),
        "digits": getattr(si, "digits", None), "point": getattr(si, "point", None),
        "tick_size": getattr(si, "trade_tick_size", None),
        "tick_value": getattr(si, "trade_tick_value", None),
        "contract_size": getattr(si, "trade_contract_size", None),
        "volume_min": getattr(si, "volume_min", None),
        "volume_max": getattr(si, "volume_max", None),
        "volume_step": getattr(si, "volume_step", None),
        "stops_level": getattr(si, "trade_stops_level", None),
        "freeze_level": getattr(si, "freeze_level", None),
        "trade_mode": getattr(si, "trade_mode", None),
        "trade_mode_name": {0: "DISABLED", 1: "LONG_ONLY", 2: "SHORT_ONLY",
                            3: "CLOSE_ONLY", 4: "FULL"}.get(
            getattr(si, "trade_mode", None), "unknown"),
        "trade_allowed": getattr(si, "trade_allowed", None),
        "filling_mode_raw": getattr(si, "filling_mode", None),
        "bid": getattr(si, "bid", None), "ask": getattr(si, "ask", None),
        "spread": getattr(si, "spread", None),
    } if si is not None else None)
    tick = mod.symbol_info_tick(symbol) if mod else None
    out["tick"] = ({"bid": getattr(tick, "bid", None), "ask": getattr(tick, "ask", None),
                    "last": getattr(tick, "last", None), "spread": getattr(tick, "spread", None),
                    "time": getattr(tick, "time", None)} if tick is not None else None)

    # the read-only pre-flight the dashboard shows
    out["trade_capability"] = ex.trade_capability(bridge, symbol=symbol, side=side)
    # the authoritative filling resolver (same helper the order path uses)
    out["filling_probe"] = _filling_probe(bridge, symbol, side, out)
    return out


def _filling_probe(bridge, symbol: str, side: str, facts: Dict[str, Any]) -> Dict[str, Any]:
    """What this broker permits for this symbol, probed with order_check only.

    V5.3 §2.3 — the numeric ``symbol_info().filling_mode`` is a SYMBOL_FILLING_MODE
    bitmask and is *not* the same scale as ENUM_ORDER_TYPE_FILLING, so each
    candidate is confirmed by the terminal's own read-only order_check.
    """
    from app.mt5 import mt5_real
    from app.mt5 import execution as ex
    from app.mt5.order_semantics import (filling_candidates, filling_name,
                                         symbol_filling_bitmask, ORDER_FILLING_FOK,
                                         ORDER_FILLING_IOC, ORDER_FILLING_RETURN)
    mod = mt5_real.mt5
    si = mod.symbol_info(symbol)
    rep: Dict[str, Any] = {"symbol_filling_mode_raw": getattr(si, "filling_mode", None)
                           if si is not None else None,
                           "bitmask": symbol_filling_bitmask(si) if si is not None else None,
                           "candidates": [c["name"] for c in filling_candidates(si, mod)]
                           if si is not None else [],
                           "probes": []}
    tick = facts.get("tick") or {}
    price = (tick.get("ask") if side.lower() == "buy" else tick.get("bid"))
    if not price:                     # market closed / no tick: probe with the symbol's own quote
        si2 = mod.symbol_info(symbol)
        price = (getattr(si2, "ask", 0.0) or getattr(si2, "bid", 0.0) or 0.0) if si2 else 0.0
    if not price:
        rep["note"] = ("no live quote from the terminal, so the filling probe could not run "
                       "(order_check needs a price); top up the quote and re-run")
        return rep
    rep["probe_price"] = float(price)
    for fill, name in ((ORDER_FILLING_RETURN, filling_name(ORDER_FILLING_RETURN)),
                       (ORDER_FILLING_IOC, filling_name(ORDER_FILLING_IOC)),
                       (ORDER_FILLING_FOK, filling_name(ORDER_FILLING_FOK))):
        try:
            req = ex.build_market_order_request(
                {"symbol": symbol, "side": side, "volume": facts.get("volume") or 0.01,
                 "price": price, "magic": 777900, "comment": "evolab-forensic"},
                filling=fill)
            check = bridge.check_market_order(req)
            raw = check.get("raw") or {}
            rep["probes"].append({"type_filling": fill, "name": name,
                                  "retcode": check.get("retcode"),
                                  "comment": raw.get("comment"),
                                  "ok": check.get("ok"),
                                  "unsupported": check.get("unsupported"),
                                  "error": check.get("error")})
        except Exception as e:                                        # pragma: no cover
            rep["probes"].append({"type_filling": fill, "name": name,
                                  "error": f"{type(e).__name__}: {e}"})
    usable = [p for p in rep["probes"] if p.get("ok")]
    rep["accepted_by_order_check"] = [p["name"] for p in usable]
    rep["selected"] = usable[0]["name"] if usable else None
    rep["selected_type_filling"] = usable[0]["type_filling"] if usable else None
    rep["rule"] = ("the selected mode is the first one the terminal itself ACCEPTS in "
                   "order_check; the raw bitmask alone is never used as type_filling")
    return rep


def _print_report(facts: Dict[str, Any], send: Optional[Dict[str, Any]]) -> None:
    py = facts.get("python") or {}
    pkg = facts.get("package") or {}
    term = facts.get("terminal") or {}
    acct = facts.get("account") or {}
    sym = facts.get("symbol") or {}
    tick = facts.get("tick") or {}
    cap = facts.get("trade_capability") or {}
    fill = facts.get("filling_probe") or {}
    ses = facts.get("session") or {}

    _hr("V5.3 DEMO EXECUTION FORENSICS")
    print(f"  timestamp                     : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    _hr("PYTHON / BINDING", "-")
    _field("python executable", py.get("executable"))
    _field("python version", py.get("version"))
    _field("platform", py.get("platform"))
    _field("MetaTrader5 package", pkg.get("version") or "NOT IMPORTABLE")
    if pkg.get("import_error"):
        _field("package import error", pkg.get("import_error"))
    _field("IPC session initialized", _bool_txt(ses.get("terminal_info_available")))
    _field("session generation", ses.get("generation"))
    _field("last session shutdown by", ses.get("last_shutdown_by"))
    _field("last session shutdown at", ses.get("last_shutdown_ts"))
    _field("mt5.last_error() now", ses.get("last_error"))

    _hr("TERMINAL", "-")
    _field("name", term.get("name"))
    _field("company", term.get("company"))
    _field("path", term.get("path"))
    _field("build", term.get("build"))
    _field("connected to trade server", _bool_txt(term.get("connected")))
    _field("trade allowed (Algo Trading)", _bool_txt(term.get("trade_allowed")))
    _field("API trading disabled", _bool_txt(term.get("tradeapi_disabled")))

    _hr("ACCOUNT", "-")
    _field("login", acct.get("login"))
    _field("server", acct.get("server"))
    _field("type", acct.get("trade_mode_name"))
    _field("currency / leverage", f"{acct.get('currency')} / {acct.get('leverage')}")
    _field("balance / equity", f"{acct.get('balance')} / {acct.get('equity')}")
    _field("margin free", acct.get("margin_free"))
    _field("account trade allowed", _bool_txt(acct.get("trade_allowed")))
    _field("expert trading allowed", _bool_txt(acct.get("trade_expert")))

    _hr("SYMBOL", "-")
    _field("symbol", sym.get("symbol"))
    _field("visible", _bool_txt(sym.get("visible")))
    _field("trade mode", sym.get("trade_mode_name"))
    _field("digits / point", f"{sym.get('digits')} / {sym.get('point')}")
    _field("tick size / value", f"{sym.get('tick_size')} / {sym.get('tick_value')}")
    _field("volume min / step / max", f"{sym.get('volume_min')} / {sym.get('volume_step')} / "
                                      f"{sym.get('volume_max')}")
    _field("stops level / freeze", f"{sym.get('stops_level')} / {sym.get('freeze_level')}")
    _field("bid / ask (tick)", f"{tick.get('bid')} / {tick.get('ask')}")

    _hr("FILLING", "-")
    _field("symbol filling_mode raw", fill.get("symbol_filling_mode_raw"),
           "SYMBOL_FILLING_MODE bitmask - NOT type_filling")
    _field("supported modes (bitmask)", fill.get("candidates"))
    for p in fill.get("probes") or []:
        _field(f"order_check with {p.get('name')}",
               f"retcode={p.get('retcode')} comment={p.get('comment')!r}",
               "ACCEPTED" if p.get("ok") else (p.get("error") or "refused"))
    _field("selected type_filling", fill.get("selected"))
    _field("selection rule", fill.get("rule"))

    _hr("READ-ONLY PRE-FLIGHT (what the dashboard shows)", "-")
    _field("applicable", _bool_txt(cap.get("applicable")))
    _field("headline", cap.get("headline"))
    for w in cap.get("warnings") or []:
        _field("unknown fact", f"{w.get('fact')} -> {w.get('value')}")
    for b in cap.get("blockers") or []:
        _field("BLOCKER", f"{b.get('code')} ({b.get('fact')}={b.get('value')})", b.get("action"))

    if send is None:
        if facts.get("existing_exposure") is not None:
            _print_exposure(facts["existing_exposure"])
        _hr("ORDER CHECK / SEND", "-")
        print("  not run in this mode (read-only). Re-run with --send --confirm "
              "PLACE_DEMO_ORDER")
        return

    check = send.get("order_check") or {}
    osend = send.get("order_send") or {}
    broker = send.get("broker") or {}
    req = send.get("request") or {}
    _print_exposure(send.get("existing_exposure") or {})

    _hr("REQUEST BUILT", "-")
    for k in ("action", "symbol", "volume", "type", "price", "sl", "tp", "deviation",
              "magic", "comment", "type_time", "type_filling"):
        if k in req:
            _field(k, req.get(k))
    _field("client_order_id", send.get("client_order_id"))

    _hr("ORDER CHECK", "-")
    _field("called", _bool_txt(check.get("called", True)))
    _field("retcode", check.get("retcode"))
    _field("comment", check.get("comment"))
    _field("margin", check.get("margin"))
    _field("verdict", "PASSED" if check.get("ok") else "REFUSED")

    _hr("ORDER SEND", "-")
    _field("outcome", osend.get("outcome"))
    _field("called", _bool_txt(osend.get("called")))
    _field("returned", _bool_txt(osend.get("returned")))
    _field("result type", osend.get("result_type"))
    _field("result repr", osend.get("result_repr"))
    _field("mt5.last_error() BEFORE", osend.get("last_error_before"))
    _field("mt5.last_error() AFTER", osend.get("last_error_after") or osend.get("last_error"))
    _field("exception type", osend.get("exception_type"))
    _field("exception message", osend.get("exception_message"))

    _hr("BROKER RESULT", "-")
    _field("retcode", broker.get("retcode"))
    _field("comment", broker.get("comment"))
    _field("category", broker.get("category"))
    _field("order ticket", (send.get("order") or {}).get("ticket"))
    _field("deal ticket", (send.get("order") or {}).get("deal_ticket"))

    _hr("POST-SEND MT5 STATE (positions_get / orders_get)", "-")
    ts = send.get("terminal_state") or ((send.get("verification") or {}).get("terminal_state")
                                        if isinstance(send.get("verification"), dict) else None)
    if not ts:
        print("  no terminal state was queried")
    else:
        _field("queried", _bool_txt(ts.get("queried")))
        _field("filtered by", f"symbol={ts.get('symbol')} magic={ts.get('magic')} "
                              f"ticket={ts.get('by_ticket')}")
        pos = ts.get("positions") or []
        orders = ts.get("orders") or []
        _field("positions found", len(pos))
        for p in pos[:5]:
            print(f"      ticket={p.get('ticket')} {p.get('symbol')} vol={p.get('volume')} "
                  f"open={p.get('price_open')} sl={p.get('sl')} tp={p.get('tp')} "
                  f"magic={p.get('magic')}")
        _field("pending orders found", len(orders))
        for o in orders[:5]:
            print(f"      ticket={o.get('ticket')} {o.get('symbol')} "
                  f"vol={o.get('volume_initial')} price={o.get('price_open')} "
                  f"magic={o.get('magic')}")
        _field("verdict", ts.get("verdict"))
        for e in ts.get("errors") or []:
            _field("terminal query error", e)

    _hr("FINAL")
    print(f"  {send.get('demo_trade_acceptance') or send.get('status')}")
    if send.get("demo_trade_acceptance_rule"):
        print(f"  rule: {send['demo_trade_acceptance_rule']}")
    if send.get("diagnostic", {}).get("phase"):
        print(f"  diagnostic phase: {send['diagnostic']['phase']}")
    for a in send.get("next_actions") or []:
        print(f"  next action {a.get('step')}: {a.get('action')}")
    _hr()


def _write_json(args, facts: Dict[str, Any], payload: Optional[Dict[str, Any]]) -> None:
    """The machine-readable twin of the printed report (same facts, no invention)."""
    if not args.json:
        return
    try:
        Path(args.json).write_text(json.dumps({"facts": facts, "send": payload},
                                              indent=2, default=str), encoding="utf-8")
        print(f"JSON written to {args.json}")
    except Exception as e:                                        # pragma: no cover - io
        print(f"could not write JSON: {e}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V5.3 MT5 DEMO execution forensics (real binding only)")
    ap.add_argument("--root", default="", help="repository root (default: this script's checkout)")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--symbol-side", default="buy", choices=["buy", "sell"])
    ap.add_argument("--volume", type=float, default=0.03)
    ap.add_argument("--sl-points", type=float, default=300.0,
                    help="SL distance in POINTS (XAUUSD point=0.01 -> 300 = 3.00)")
    ap.add_argument("--tp-points", type=float, default=600.0)
    ap.add_argument("--terminal-path", default="", help="override the terminal executable")
    ap.add_argument("--send", action="store_true",
                    help="actually send ONE demo order (order_send); default is read-only")
    ap.add_argument("--confirm", default="", help="must be PLACE_DEMO_ORDER when --send is used")
    ap.add_argument("--force", action="store_true",
                    help="send even though an open position/order exists for this magic "
                         "(only after inspecting the terminal; never a retry)")
    ap.add_argument("--json", default="", help="also write the collected facts as JSON here")
    ap.add_argument("--log", default="", help="write the whole report to this file as well")
    args = ap.parse_args(argv)

    # every line printed below goes to the console and to --log (one run = one report)
    tee = _Tee(Path(args.log) if args.log else None)
    sys.stdout = tee

    root = Path(args.root).resolve() if args.root else _default_root()
    _bootstrap(root)
    print(f"checkout root: {root}")

    from app.mt5.factory import build_bridge
    from app.mt5 import execution as ex
    from app.config import get_config

    cfg = get_config().mt5
    bridge = build_bridge(cfg, explicit_path=(args.terminal_path or None))
    print(f"bridge: {bridge.name} (source={getattr(bridge, 'source', '?')})")
    if getattr(bridge, "source", "") != "MT5":
        print("BLOCKER: the active bridge is NOT a real MT5 terminal — no order can be sent.")
        print("         install the MetaTrader5 package in this interpreter and run a terminal.")
        return 3

    facts = _run_read_only(bridge, args.symbol, args.symbol_side)
    facts["volume"] = args.volume
    facts["side"] = args.symbol_side
    # V5.4 §2.4 — never send blindly: the terminal is asked what is already open
    facts["existing_exposure"] = _existing_exposure(bridge, args.symbol)

    send_payload: Optional[Dict[str, Any]] = None
    if args.send:
        if (args.confirm or "").strip() != ex.PLACE_CONFIRMATION:
            print(f"refusing to send: --confirm must be {ex.PLACE_CONFIRMATION}")
            _print_report(facts, None)
            return 5
        exposure = facts.get("existing_exposure") or {}
        if exposure.get("count") and not args.force:
            print()
            print("REFUSING TO SEND: an open position/order already exists for "
                  f"{args.symbol} with magic {exposure.get('magics')}.")
            print("                   The previous attempt may already have executed — "
                  "inspect it in the terminal (Trade / History) first.")
            print("                   Nothing was transmitted: the order itself was never "
                  "checked and never sent (the three order_check calls above belong to the "
                  "read-only filling probe). --force sends anyway and is NOT a retry.")
            refusal = {
                "ok": False, "status": "REFUSED_BEFORE_SEND",
                "result_class": "EXISTING_EXPOSURE", "result_class_detail": "NOT_SENT",
                "demo_trade_acceptance": "REFUSED_BEFORE_SEND",
                "demo_trade_acceptance_rule": ("an existing open position/order for this "
                                               "symbol and magic means the earlier attempt "
                                               "may already have executed: inspect it in "
                                               "the terminal before sending anything"),
                "broker": {"message": "existing exposure found before send",
                           "retcode": None, "comment": None,
                           "category": "EXISTING_EXPOSURE", "safe_to_retry": False},
                "order_send": {"outcome": "NOT_CALLED", "called": False,
                               "phase": "NOT_CALLED"},
                "order_check": {"called": False},
                "existing_exposure": exposure,
                "next_actions": [
                    {"step": 1, "action": ("open the MT5 terminal: Toolbox -> Trade for the "
                                           "live position, History for a closed one")},
                    {"step": 2, "action": ("decide by hand whether that position is the "
                                           "earlier attempt; close it there if it is not wanted")},
                    {"step": 3, "action": ("then re-run this tool WITHOUT --force to prove a "
                                           "clean execution, or WITH --force knowingly")},
                ],
            }
            _print_report(facts, refusal)
            _write_json(args, facts, refusal)
            if args.log:
                print(f"report written to {args.log}")
            sys.stdout = sys.__stdout__
            tee.close()
            return 6
        si = facts.get("symbol") or {}
        tick = facts.get("tick") or {}
        point = float(si.get("point") or 0.01)
        entry = tick.get("ask") if args.symbol_side == "buy" else tick.get("bid")
        if not entry:
            print("refusing to send: no live quote from the terminal (symbol_info_tick).")
            _print_report(facts, None)
            return 4
        sl = round(float(entry) - args.sl_points * point, 2) if args.symbol_side == "buy" \
            else round(float(entry) + args.sl_points * point, 2)
        tp = round(float(entry) + args.tp_points * point, 2) if args.symbol_side == "buy" \
            else round(float(entry) - args.tp_points * point, 2)
        payload = {"symbol": args.symbol, "side": args.symbol_side, "volume": args.volume,
                   "price": float(entry), "sl": sl, "tp": tp,
                   "magic": 777900, "comment": "evolab-forensic",
                   "client_order_id": _client_order_id(),
                   "confirm": ex.PLACE_CONFIRMATION}
        print(f"sending ONE demo {args.symbol_side.upper()} {args.volume} {args.symbol} "
              f"(entry={entry} sl={sl} tp={tp}) — exactly one order_send attempt ...")
        try:
            send_payload = ex.place_demo_order(payload, bridge=bridge)
        except ex.MT5ExecutionError as e:
            det = e.details or {}
            fx = det.get("forensics") if isinstance(det.get("forensics"), dict) else {}
            send_payload = {"ok": False, "status": "BLOCKED", "result_class": e.code,
                            "demo_trade_acceptance": "REFUSED_BEFORE_SEND",
                            "broker": {"message": e.message, "retcode": None, "comment": None,
                                       "category": e.code, "safe_to_retry": False},
                            "order_send": {"outcome": fx.get("outcome"),
                                           "called": fx.get("order_send_called", False),
                                           "returned": fx.get("order_send_returned"),
                                           "result_type": fx.get("order_send_result_type"),
                                           "result_repr": fx.get("order_send_result_repr"),
                                           "last_error_before": fx.get("mt5_last_error_before"),
                                           "last_error_after": fx.get("mt5_last_error_after"),
                                           "exception_type": fx.get("exception_type"),
                                           "exception_message": fx.get("exception_message")},
                            "order_check": {}, "diagnostic": det.get("diagnostic"),
                            "session": det.get("session"),
                            "next_actions": [{"step": 1, "action": e.message}]}
        except Exception as e:                                        # pragma: no cover
            send_payload = {"ok": False, "status": "EXCEPTION", "result_class": "UNEXPECTED",
                            "broker": {"message": f"{type(e).__name__}: {e}"},
                            "order_send": {}, "order_check": {}}

    if isinstance(send_payload, dict):
        send_payload["existing_exposure"] = facts.get("existing_exposure")

    _print_report(facts, send_payload)

    _write_json(args, facts, send_payload)

    if args.log:
        print(f"report written to {args.log}")
    sys.stdout = sys.__stdout__
    tee.close()
    if send_payload is None:
        return 0
    return 0 if send_payload.get("demo_trade_acceptance") == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
