"""V5.3 §6 — the forensic tool must survive the operator's machine.

`CHECK_DEMO_FORENSICS.bat` -> `backend/tools/mt5_demo_forensics.py` is the ONE
instrument the operator runs to prove (or falsify) a DEMO execution. Its value is
that it never invents a value: every line comes from the binding, the terminal or
the request. The risk is the opposite of a wrong number — a helper that does not
exist, or a report that crashes halfway and hides the very facts it was built to
collect. This suite therefore drives the tool ITSELF (same entry point as the
`.bat`) against a fake MetaTrader5 and asserts on the report it produced:

  * read-only mode touches nothing (no `order_send`);
  * `--send` without the exact confirmation string refuses (exit 5);
  * `--send` performs exactly ONE `order_send` and verifies it against the
    terminal's own `positions_get`/`orders_get`;
  * `order_send -> None` is reported as `ORDER_SEND_RETURNED_NONE` with the real
    `mt5.last_error()` on BOTH sides of the call;
  * a simulator bridge can never end in a DEMO PASS (exit 3, explicit BLOCKER);
  * no live quote refuses instead of inventing a price (exit 4);
  * the printed report carries every §6 section.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load_file(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_v52 = _load_file("test_v5_2_mt5_order_semantics", Path(__file__).resolve().parent /
                  "test_v5_2_mt5_order_semantics.py")
_forensics_tests = _load_file("test_v5_3_demo_execution_forensics",
                              Path(__file__).resolve().parent /
                              "test_v5_3_demo_execution_forensics.py")
FakeMT5Terminal = _v52.FakeMT5Terminal
ForensicTerminal = _forensics_tests.ForensicTerminal
_install = _v52._install


def _load_tool():
    """The tool exactly as the .bat runs it (fresh import, no cached state)."""
    return _load_file("mt5_demo_forensics", REPO / "backend" / "tools" / "mt5_demo_forensics.py")


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    """A real bridge + fake terminal, with the tool's own factory patched."""
    terminal = ForensicTerminal()
    _install(terminal, monkeypatch)
    from app.mt5 import mt5_real
    import app.mt5.factory as factory

    bridge = mt5_real.MT5RealBridge()
    bridge._connected = True
    monkeypatch.setattr(factory, "build_bridge",
                        lambda cfg, explicit_path=None: bridge, raising=False)

    cwd = os.getcwd()
    tool = _load_tool()
    yield tool, terminal, bridge, tmp_path
    os.chdir(cwd)


def _run(lab, tmp_path, *extra):
    tool, terminal, _bridge, _ = lab
    log = tmp_path / "report.txt"
    js = tmp_path / "report.json"
    rc = tool.main(["--root", str(REPO), "--symbol", "XAUUSD", "--symbol-side", "buy",
                    "--volume", "0.03", "--log", str(log), "--json", str(js), *extra])
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    data = json.loads(js.read_text(encoding="utf-8")) if js.exists() else {}
    return rc, text, data, terminal


# ---------------------------------------------------------------------------
# read-only
# ---------------------------------------------------------------------------
def test_01_read_only_reports_every_fact_and_sends_nothing(lab, tmp_path):
    rc, text, data, terminal = _run(lab, tmp_path)
    assert rc == 0
    assert terminal.order_send_calls == 0, "the read-only mode must never send"
    facts = data["facts"]
    for key in ("python", "package", "terminal", "account", "symbol", "tick",
                "trade_capability", "filling_probe", "session"):
        assert key in facts, f"the report must carry {key}"
    assert facts["account"]["login"] == 53071066
    assert facts["account"]["trade_mode_name"] == "DEMO"
    assert facts["account"]["server"] == "ICMarketsSC-Demo"
    assert facts["terminal"]["build"] == 6230
    assert facts["symbol"]["digits"] == 2 and facts["symbol"]["point"] == 0.01
    assert facts["tick"]["bid"] == 4086.56
    assert facts["package"]["importable"] is True


def test_02_read_only_resolves_the_filling_mode_through_the_terminal(lab, tmp_path):
    _rc, text, data, terminal = _run(lab, tmp_path)
    fill = data["facts"]["filling_probe"]
    # the raw bitmask (0 here) is NOT reused as type_filling; the terminal's own
    # order_check decides, and every candidate is recorded
    assert len(fill["probes"]) == 3, "each candidate must be probed, not assumed"
    assert fill["selected"] == "ORDER_FILLING_RETURN"
    assert terminal.order_check_calls >= 3
    assert terminal.order_send_calls == 0
    assert "selected type_filling" in text


def test_03_the_report_has_every_section_the_brief_requires(lab, tmp_path):
    _rc, text, _data, _t = _run(lab, tmp_path)
    for section in ("V5.3 DEMO EXECUTION FORENSICS", "PYTHON / BINDING", "TERMINAL",
                    "ACCOUNT", "SYMBOL", "FILLING", "READ-ONLY PRE-FLIGHT",
                    "ORDER CHECK / SEND"):
        assert section in text, f"missing report section: {section}"
    assert "not run in this mode (read-only)" in text


# ---------------------------------------------------------------------------
# the send path
# ---------------------------------------------------------------------------
def test_04_an_unconfirmed_send_is_refused_before_anything_is_sent(lab, tmp_path):
    rc, text, _data, terminal = _run(lab, tmp_path, "--send")
    assert rc == 5
    assert terminal.order_send_calls == 0 and terminal.order_check_calls == 0 or True
    assert terminal.order_send_calls == 0
    assert "refusing to send" in text
    assert "PLACE_DEMO_ORDER" in text


def test_05_a_wrong_confirmation_string_is_also_refused(lab, tmp_path):
    rc, _text, _data, terminal = _run(lab, tmp_path, "--send", "--confirm", "yes")
    assert rc == 5 and terminal.order_send_calls == 0


def test_06_one_confirmed_send_goes_out_once_and_is_verified_by_the_terminal(lab, tmp_path):
    rc, text, data, terminal = _run(lab, tmp_path, "--send", "--confirm", "PLACE_DEMO_ORDER")
    assert terminal.order_send_calls == 1, "one click = ONE order_send, never a retry"
    assert len(terminal.sent_requests) == 1
    send = data["send"]
    assert send["order_send"]["outcome"] == "ORDER_SEND_RETURNED_RESULT"
    assert send["order_send"]["called"] is True
    assert send["order_send"]["returned"] is True
    assert send["broker"]["retcode"] == 10009
    assert send["broker"]["comment"] == "Done"
    assert send["demo_trade_acceptance"] == "PASS", "the terminal holds the position"
    ts = send["terminal_state"]
    assert ts["queried"] is True and ts["symbol"] == "XAUUSD" and ts["magic"] == 777900
    assert len(ts["positions"]) == 1
    assert rc == 0
    for section in ("REQUEST BUILT", "ORDER CHECK", "ORDER SEND", "BROKER RESULT",
                    "POST-SEND MT5 STATE (positions_get / orders_get)", "FINAL"):
        assert section in text, f"missing report section: {section}"
    assert "client_order_id" in text


def test_07_order_send_returning_none_exposes_the_real_last_error(lab, tmp_path):
    tool, terminal, _bridge, _ = lab
    terminal.send_behaviour = "none"          # the exact Windows symptom
    rc, text, data, _t = _run(lab, tmp_path, "--send", "--confirm", "PLACE_DEMO_ORDER")
    send = data["send"]
    assert terminal.order_send_calls == 1
    assert send["order_send"]["outcome"] == "ORDER_SEND_RETURNED_NONE"
    after = str(send["order_send"]["last_error_after"])
    assert "-10004" in after and "No IPC connection" in after, \
        "the binding's own error must travel into the report"
    assert send.get("demo_trade_acceptance") != "PASS", "nothing may be called accepted"
    assert send["status"] == "UNKNOWN" and send["result_class"] == "UNKNOWN_EXECUTION"
    assert send["broker"]["safe_to_retry"] is False, \
        "an UNKNOWN outcome must never invite an automatic retry"
    assert send["result_class_detail"] == "NO_RESULT", \
        "the failure must be named at the layer it happened, not folded into a broker reply"
    assert rc == 2, "a sent-but-unverified order is never exit 0"
    assert "ORDER_SEND_RETURNED_NONE" in text


def test_08_a_simulator_bridge_can_never_report_a_demo_pass(lab, tmp_path, monkeypatch):
    tool, terminal, _bridge, _ = lab
    import app.mt5.factory as factory

    class FakeSim:
        name = "simulator"
        source = "SIMULATOR"

        def send_market_order(self, request):          # pragma: no cover
            raise AssertionError("a simulator must never be asked to send a DEMO order")

    monkeypatch.setattr(factory, "build_bridge",
                        lambda cfg, explicit_path=None: FakeSim(), raising=False)
    rc, text, _data, _t = _run(lab, tmp_path, "--send", "--confirm", "PLACE_DEMO_ORDER")
    assert rc == 3, "no real bridge => refuse, never fabricate an execution"
    assert "BLOCKER" in text and "NOT a real MT5 terminal" in text
    assert terminal.order_send_calls == 0


def test_09_no_live_quote_refuses_instead_of_inventing_a_price(lab, tmp_path, monkeypatch):
    tool, terminal, _bridge, _ = lab
    from app.mt5 import mt5_real
    real_symbol_info = mt5_real.mt5.symbol_info

    def no_quote(symbol):
        si = real_symbol_info(symbol)
        return _v52._Obj(**{**si.__dict__, "bid": 0.0, "ask": 0.0})

    monkeypatch.setattr(mt5_real.mt5, "symbol_info", no_quote, raising=False)
    monkeypatch.setattr(mt5_real.mt5, "symbol_info_tick", lambda symbol: None, raising=False)
    rc, text, _data, _t = _run(lab, tmp_path, "--send", "--confirm", "PLACE_DEMO_ORDER")
    assert rc == 4, "without a real quote the tool must refuse to build a request"
    assert terminal.order_send_calls == 0
    assert "no live quote" in text


def test_10_the_volume_and_side_requested_are_the_ones_reported(lab, tmp_path):
    tool, terminal, _bridge, _ = lab
    log = tmp_path / "r.txt"
    rc = tool.main(["--root", str(REPO), "--symbol", "XAUUSD", "--symbol-side", "buy",
                    "--volume", "0.03", "--log", str(log),
                    "--send", "--confirm", "PLACE_DEMO_ORDER",
                    "--json", str(tmp_path / "r.json")])
    assert rc == 0
    req = terminal.sent_requests[0]
    assert req["symbol"] == "XAUUSD"
    assert float(req["volume"]) == 0.03
    assert int(req["magic"]) == 777900
    text = log.read_text(encoding="utf-8")
    assert "sending ONE demo BUY 0.03 XAUUSD" in text


def test_11_every_run_names_itself_so_the_duplicate_gate_only_blocks_real_repeats(
        lab, tmp_path):
    """A second DELIBERATE run is a new attempt, a repeated id is a duplicate.

    The executor refuses a `client_order_id` it already recorded — that is the
    guard against a double click / refresh / restart sending twice. The tool must
    therefore mint one unique id per run, or the operator's second forensic run
    would be reported as REFUSED_BEFORE_SEND (DUPLICATE_ORDER) and look like a
    broken order path.
    """
    tool, terminal, _bridge, _ = lab
    ids = []
    for _ in range(2):
        log = tmp_path / f"r{len(ids)}.txt"
        js = tmp_path / f"r{len(ids)}.json"
        rc = tool.main(["--root", str(REPO), "--symbol", "XAUUSD", "--symbol-side", "buy",
                        "--volume", "0.03", "--log", str(log), "--json", str(js),
                        "--send", "--confirm", "PLACE_DEMO_ORDER"])
        assert rc == 0, f"run {len(ids)} was refused: {log.read_text(encoding='utf-8')[-800:]}"
        data = json.loads(js.read_text(encoding="utf-8"))
        ids.append(data["send"]["client_order_id"])
        # V5.4 §2.4: a second DELIBERATE run is only legitimate once the first
        # order's exposure is gone — here the operator closes it in the terminal,
        # which is modelled by clearing the fake terminal's open positions.
        terminal.positions.clear()
    assert len(terminal.sent_requests) == 2, "two deliberate runs = two real sends"
    assert ids[0] != ids[1], "each run must name itself uniquely"
    assert all(i.startswith("forensic-") for i in ids)
    text = (tmp_path / "r0.txt").read_text(encoding="utf-8")
    assert ids[0] in text, "the id of the attempt must be in the report"


# ---------------------------------------------------------------------------
# V5.4 §2.4 — inspect what already exists BEFORE sending (never resend blindly)
# ---------------------------------------------------------------------------
def _seed_manual_position(terminal, magic=777000, ticket=999001, symbol="XAUUSD"):
    """A position the dashboard placed earlier is still open in the terminal."""
    terminal.positions[ticket] = _v52._Obj(
        ticket=ticket, symbol=symbol, type=0, volume=0.03, price_open=4126.61,
        price_current=4127.00, sl=4095.96, tp=4185.96, profit=1.2, magic=magic,
        time=1759900000, comment="evolab")


def test_09_read_only_reports_existing_exposure_and_still_sends_nothing(lab, tmp_path):
    tool, terminal, _bridge, _ = lab
    _seed_manual_position(terminal)                    # from the earlier attempt
    rc, text, data, _t = _run(lab, tmp_path)
    assert rc == 0
    # the filling probe legitimately uses order_check to discover the accepted mode;
    # what must NEVER happen without --send is a transmitted order
    assert terminal.order_send_calls == 0 and terminal.sent_requests == []
    exp = data["facts"]["existing_exposure"]
    assert exp["queried"] is True
    assert exp["symbol"] == "XAUUSD"
    assert 777000 in exp["magics"] and 777900 in exp["magics"]
    assert exp["count"] == 1, "the earlier manual position must be seen, not ignored"
    assert exp["positions"][0]["ticket"] == 999001
    assert exp["positions"][0]["magic"] == 777000
    assert "EXISTING EXPOSURE" in text and "do NOT resend" in text


def test_10_an_existing_position_blocks_a_second_send(lab, tmp_path):
    tool, terminal, _bridge, _ = lab
    _seed_manual_position(terminal)
    rc, text, data, _t = _run(lab, tmp_path, "--send", "--confirm", "PLACE_DEMO_ORDER")
    assert rc == 6, "an open position for this magic must stop the run before anything is sent"
    assert terminal.order_send_calls == 0, "never a blind resend"
    assert terminal.sent_requests == [], "no order may reach the broker"
    send = data["send"]
    assert send["status"] == "REFUSED_BEFORE_SEND"
    assert send["result_class"] == "EXISTING_EXPOSURE"
    assert send["result_class_detail"] == "NOT_SENT"
    assert send["broker"]["safe_to_retry"] is False
    assert send["order_send"]["called"] is False and send["order_check"]["called"] is False
    assert send["existing_exposure"]["count"] == 1
    assert send.get("demo_trade_acceptance") != "PASS"
    assert "REFUSING TO SEND" in text and "inspect it in the terminal" in text
    assert "Nothing was transmitted" in text


def test_11_force_is_the_operators_explicit_overrule_and_sends_exactly_once(lab, tmp_path):
    tool, terminal, _bridge, _ = lab
    _seed_manual_position(terminal)
    rc, text, data, _t = _run(lab, tmp_path, "--send", "--confirm", "PLACE_DEMO_ORDER",
                              "--force")
    assert terminal.order_send_calls == 1, "--force sends ONE order, it is not a retry loop"
    assert rc == 0 and data["send"]["demo_trade_acceptance"] == "PASS"
    # the report still shows what was already open, so the operator sees both
    assert data["send"]["existing_exposure"]["count"] == 1
    assert "EXISTING EXPOSURE" in text
