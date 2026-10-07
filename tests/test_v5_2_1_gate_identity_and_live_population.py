"""V5.2.1 §2/§6/§7/§11/§14 — regression suite for the execution-gate report, the
runtime-identity launcher and the Live Testing population.

What this file pins down, in the operator's own words:

  §1/§2  ``mt5.order_check`` retcode 0 + ``Done`` is a PASS, and the response
         that reaches the panel says so explicitly (``order_check.called``,
         ``passed``, ``retcode``, ``comment``, ``margin``, ``rule``) next to the
         ``order_send`` facts — never a bare ``called: false`` with no reason.
  §7     Live Testing consumes the one authoritative population
         (``GET /api/nodes`` + ``GET /api/nodes/populations``) and, when there is
         genuinely nothing to show, renders an explicit truthful empty state
         (TOTAL/ALIVE/QUALIFIED/FINAL/DEEP/LIVE + the reason) instead of a blank
         table. No invented node, no hard-coded count.
  §6     ``CHECK_RUNNING_DASHBOARD.bat`` cannot close on its own: the body runs
         inside ``cmd /k``, ends in ``pause``, and writes the whole identity to
         ``LOGS\\RUNTIME_IDENTITY_<stamp>.txt`` so the running build can always be
         identified. It never stops, starts or trades anything.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BAT = REPO / "CHECK_RUNNING_DASHBOARD.bat"
LIVE_PAGE = REPO / "frontend" / "src" / "pages" / "LiveTesting.jsx"
NODE_INDEX = REPO / "frontend" / "src" / "components" / "LiveNodeIndex.jsx"
STRIP = REPO / "frontend" / "src" / "components" / "NodePopulationStrip.jsx"

POP_NAMES = ["TOTAL", "ALIVE", "QUALIFIED", "FINAL_TESTING_ELIGIBLE",
             "DEEP_TESTING_ELIGIBLE", "LIVE_TESTING_ELIGIBLE"]


# ===========================================================================
# §1/§2 — the order_check verdict travels with the result
# ===========================================================================
def test_01_a_passing_check_is_not_a_refusal():
    from app.mt5.order_semantics import interpret_check_result
    v = interpret_check_result({"retcode": 0, "comment": "Done", "margin": 8.17})
    assert v["ok"] is True
    assert v["retcode"] == 0 and v["comment"] == "Done" and v["margin"] == 8.17
    assert "error_name" not in v, "a passing check must not carry a refusal name"
    assert "PASSING" in v["rule"] or "pass" in v["rule"].lower()


def test_02_a_real_error_code_is_named_not_generalised():
    from app.mt5.order_semantics import interpret_check_result
    v = interpret_check_result({"retcode": 10016, "comment": "Invalid stops", "margin": 0.0})
    assert v["ok"] is False
    assert v["retcode"] == 10016 and v["error_name"]
    assert "stop" in v["rule"].lower() or "10016" in v["rule"]


def test_03_the_result_carries_the_order_check_facts_for_the_panel():
    from app.mt5.execution import _order_check_facts
    raw = {"check": {"ok": True, "retcode": 0, "unsupported": False,
                     "verdict": {"retcode_name": "TRADE_RETCODE_DONE", "comment": "Done",
                                 "margin": 24.62,
                                 "rule": "retcode 0 with comment 'Done' is a PASSING "
                                         "MqlTradeCheckResult"}}}
    facts = _order_check_facts(raw)
    assert facts["called"] is True and facts["passed"] is True
    assert facts["retcode"] == 0 and facts["comment"] == "Done" and facts["margin"] == 24.62

    refused = _order_check_facts({"check": {"ok": False, "retcode": 10030,
                                            "verdict": {"comment": "Unsupported filling mode",
                                                        "rule": "retcode 10030 is not a pass code"}}})
    assert refused["called"] is True and refused["passed"] is False
    assert refused["retcode"] == 10030

    # an unsupported probe (an older terminal without order_check) is neither a
    # pass nor a refusal: it must not be reported as either
    unsupported = _order_check_facts({"check": {"ok": True, "unsupported": True,
                                                "retcode": None, "verdict": {}}})
    assert unsupported["passed"] is None and unsupported["unsupported"] is True

    # and when the check was never reached the reason is stated
    never = _order_check_facts({})
    assert never["called"] is False and "not reached" in never["rule"]


def test_04_both_execution_responses_carry_the_order_check_facts():
    """Contract: every result the manual panel can receive is built with
    `_order_check_facts`, so `order_send: {called: false}` always comes with the
    terminal's own reason for it (§2)."""
    src = (REPO / "backend" / "app" / "mt5" / "execution.py").read_text(encoding="utf-8")
    assert src.count('"order_check": _order_check_facts(raw)') == 2, \
        "the no-result response and the normal response must both carry the facts"
    assert '"last_error": order_send_last_error' in src
    assert '"call_count"' in src


def test_05_the_panel_payload_has_the_broker_and_ticket_fields_the_brief_lists():
    src = (REPO / "backend" / "app" / "mt5" / "execution.py").read_text(encoding="utf-8")
    for field in ('"retcode"', '"comment"', '"category"', '"safe_to_retry"',
                  '"ticket"', '"deal_ticket"', '"position_ticket"',
                  '"requested_volume"', '"executed_volume"',
                  '"sl_requested"', '"sl_broker"', '"tp_requested"', '"tp_broker"',
                  '"client_order_id"'):
        assert field in src, f"{field} missing from the execution payload"


# ===========================================================================
# §7 — Live Testing reads the one authoritative population
# ===========================================================================
@pytest.fixture()
def db(tmp_path):
    from app.db.database import Database
    d = Database(str(tmp_path / "v521_pop.db"))
    d.x("DELETE FROM live_test_configs")
    return d


def _node(d, node_id, status="SURVIVED", symbol="XAUUSD", timeframe="M15",
          data_source="USER_RESEARCH"):
    genome = {"symbol": symbol, "timeframe": timeframe, "direction": "long",
              "entry_long": {"op": "and", "clauses": [
                  {"type": "compare", "left": "close", "cmp": ">", "right": 0}]},
              "entry_short": None,
              "exit": {"atr_spec": "atr:14", "sl_atr_mult": 1.5, "tp_atr_mult": 3.0}}
    d.x("""INSERT OR REPLACE INTO strategies
            (id, hash, parent_id, generation, symbol, timeframe, direction, status,
             genome, complexity, fitness, created_at, updated_at, origin, run_id, data_source,
             research_node_num)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (node_id, f"h{node_id}", None, 39, symbol, timeframe, "long", status,
         json.dumps(genome), 3, 1.0, 1.0, 1.0, "research",
         "RUN-20261007-000000", data_source, node_id))
    return node_id


def test_06_the_index_and_the_population_authority_agree(db, monkeypatch):
    import app.api.routes as routes
    from app.research import populations as P
    _node(db, 11)
    _node(db, 12)
    _node(db, 13, status="STRATEGY_FAILED", data_source="LEGACY_TEST")
    monkeypatch.setattr(routes, "get_db", lambda: db, raising=False)

    index = routes.nodes_index(filter="qualified", limit=10)
    state = P.population_state(db=db)["state"]
    assert index["total"] == 2
    assert state["QUALIFIED"] == 2 == index["counts"]["qualified"]
    # the six names are the same vocabulary on both endpoints
    assert list(state.keys()) == POP_NAMES
    assert index["counts"]["excluded"] == 1, "the legacy row is excluded, not counted as a node"
    # the labels the page shows for the filters come from the server, not from a recount
    assert index["filter_totals"]["qualified"] == 2


def test_07_an_empty_population_is_reported_as_empty_not_invented(db, monkeypatch):
    import app.api.routes as routes
    from app.research import populations as P
    _node(db, 21, status="STRATEGY_FAILED")
    monkeypatch.setattr(routes, "get_db", lambda: db, raising=False)
    index = routes.nodes_index(filter="qualified", limit=10)
    state = P.population_state(db=db)["state"]
    assert index["total"] == 0 and index["nodes"] == []
    # the numbers that explain WHY are available to the page in the same payload
    assert index["counts"].get("qualified", 0) == 0
    assert state["TOTAL"] == 1 and state["QUALIFIED"] == 0
    assert state["ALIVE"] == 0
    # the page can also say which experiment it is looking at
    assert index["experiment"]["is_empty"] is False


def test_08_the_live_testing_page_renders_the_authoritative_population():
    page = LIVE_PAGE.read_text(encoding="utf-8")
    idx = NODE_INDEX.read_text(encoding="utf-8")
    strip = STRIP.read_text(encoding="utf-8")
    assert "NodePopulationStrip" in page and "LiveNodeTable" in page
    # the strip reads the single authority, with the six names
    assert "/api/nodes/populations" in (REPO / "frontend" / "src" / "api.js").read_text(encoding="utf-8")
    for name in POP_NAMES:
        assert name in strip, f"{name} missing from the population strip"
    # the table is fed by the qualified-node index, not by the legacy projection
    assert "api.nodes(" in idx
    assert "/api/live-testing/nodes-table" not in idx, \
        "the live index must not read a second, non-authoritative node source"


def test_09_zero_rows_produce_an_explicit_truthful_empty_state():
    idx = NODE_INDEX.read_text(encoding="utf-8")
    assert "NO LIVE-TESTING-ELIGIBLE NODE YET" in idx
    assert "api.nodePopulations()" in idx, "the empty state must fetch the real numbers"
    for name in POP_NAMES:
        assert f'"{name}"' in idx, f"{name} missing from the empty state"
    # a read failure is never dressed up as an empty population
    assert "THIS IS A READ FAILURE" in idx.upper() or "read failure, not an empty population" in idx
    # and it never fabricates a node list: rows only ever come from the API payload
    assert "mock" not in idx.lower() and "fake" not in idx.lower()
    assert "const rows = [" not in idx, "the table must not own a hard-coded node list"


# ===========================================================================
# §6 — the runtime identity launcher cannot close on its own
# ===========================================================================
def _bat_bytes() -> bytes:
    return BAT.read_bytes()


def test_10_the_launcher_keeps_its_window_open():
    data = _bat_bytes()
    assert b"\r\n" in data, "a .bat must ship with CRLF line endings"
    assert b"\n" not in data.replace(b"\r\n", b""), "stray LF line endings"
    text = data.decode("ascii")
    # the body runs inside a console that survives the script (keep-alive guard)
    assert 'if /I "%~1"=="--stay" goto :body' in text
    assert 'cmd /k call "%~f0" --stay' in text
    # and the last line waits for a key
    assert "\npause >nul" in text.replace("\r\n", "\n")
    assert text.rstrip().endswith("exit /b 0")


def test_11_the_launcher_reports_which_build_is_running():
    text = _bat_bytes().decode("ascii")
    for expected in ("rev-parse HEAD", "rev-parse origin/main", "git] commit",
                     "netstat -ano", "tasklist", "dashboard_diagnostic.py",
                     "system/build", "LOGS", "RUNTIME_IDENTITY_"):
        assert expected in text, f"{expected} missing from the identity launcher"
    # both a text copy and a JSON copy are written for support
    assert "%REPORT%" in text and "%TMPJSON%" in text


def test_12_the_launcher_never_stops_starts_or_trades_anything():
    text = _bat_bytes().decode("ascii")
    for banned in ("taskkill", "Stop-Process", "--stop", "npm run build", "order_send",
                   "check_port.py", "start.bat"):
        assert banned not in text, f"{banned} must not appear in a read-only identity check"
    assert "READ-ONLY" in text.upper()


def test_13_the_diagnostic_the_launcher_calls_is_read_only_too():
    src = (REPO / "backend" / "tools" / "dashboard_diagnostic.py").read_text(encoding="utf-8")
    assert "order_send(" not in src
    for banned in ("taskkill", "Stop-Process"):
        assert banned not in src
