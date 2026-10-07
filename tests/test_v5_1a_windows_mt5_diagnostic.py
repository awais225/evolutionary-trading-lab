"""V5.1a-next §3-§6 — the Windows MT5 diagnostic and its exact failure layer.

The real machine is the operator's Windows box, so these tests do two things:

  * unit-test the classifier against every layer of the spec vocabulary — a
    diagnostic that only says "MT5 unavailable" is a defect;
  * drive the deep probe with a *fake MetaTrader5 package* on a host that claims
    to be Windows, proving the real code path (initialize -> terminal_info ->
    account_info -> symbol -> tick -> permissions -> order_check) and proving the
    hard safety rules: no order_send, nothing killed, no credentials printed.

Nothing here touches the real DATA tree or places anything.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "backend" / "app" / "mt5" / "windows_diagnostic.py"
CLI = REPO / "backend" / "tools" / "mt5_windows_diagnostic.py"
BAT = REPO / "CHECK_MT5_WINDOWS.bat"


def _diag():
    from app.mt5 import windows_diagnostic as wd
    return wd


# --------------------------------------------------------------------------- #
# a fake MetaTrader5 *package* (not a bridge): the diagnostic talks to the package
# --------------------------------------------------------------------------- #
class _Info:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeMT5:
    """Records every call so the tests can prove what was and was not called."""

    __version__ = "5.0.6231"
    __file__ = r"C:\fake\MetaTrader5\__init__.py"

    TRADE_RETCODE_DONE = 10009
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2

    def __init__(self, *, initialize=True, terminal=True, account=True, symbol=True,
                 tick=True, trade_allowed=True, symbol_trade_mode=4, check_retcode=10009):
        self.calls: list = []
        self._initialize = initialize
        self._terminal = terminal
        self._account = account
        self._symbol = symbol
        self._tick = tick
        self._trade_allowed = trade_allowed
        self._symbol_trade_mode = symbol_trade_mode
        self._check_retcode = check_retcode
        self._initialized_paths: list = []

    def initialize(self, path=None, **kw):
        self.calls.append(("initialize", path))
        self._initialized_paths.append(path)
        return self._initialize

    def shutdown(self):
        self.calls.append(("shutdown", None))
        return True

    def last_error(self):
        self.calls.append(("last_error", None))
        return (-10003, "IPC timeout" if not self._initialize else "ok")

    def terminal_info(self):
        self.calls.append(("terminal_info", None))
        if not self._terminal:
            return None
        return _Info(name="MetaTrader 5", company="Raw Trading Ltd", path=r"C:\Program Files\MT5\terminal64.exe",
                     data_path=r"C:\Users\op\AppData\Roaming\MetaQuotes\Terminal\ABC", build=6230,
                     connected=True, trade_allowed=self._trade_allowed, tradeapi_disabled=False,
                     dlls_allowed=False, community_account=False, maxbars=100000, language="English")

    def account_info(self):
        self.calls.append(("account_info", None))
        if not self._account:
            return None
        return _Info(login=51234567, trade_mode=0, server="ICMarketsSC-Demo", currency="USD",
                     balance=10000.0, equity=10012.5, margin_free=9800.0, leverage=500,
                     trade_allowed=self._trade_allowed, trade_expert=True, margin_mode=2,
                     name="Operator Name", company="Raw Trading Ltd")

    def symbol_info(self, symbol):
        self.calls.append(("symbol_info", symbol))
        if not self._symbol:
            return None
        return _Info(symbol=symbol, visible=True, digits=2, point=0.01, trade_tick_size=0.01,
                     trade_tick_value=1.0, trade_contract_size=100.0, volume_min=0.01,
                     volume_max=100.0, volume_step=0.01, trade_stops_level=0, freeze_level=0,
                     trade_mode=self._symbol_trade_mode, trade_allowed=True,
                     filling_mode=1, filling_modes=1, currency_profit="USD")

    def symbol_select(self, symbol, enable=True):
        self.calls.append(("symbol_select", symbol))
        return True

    def symbol_info_tick(self, symbol):
        self.calls.append(("symbol_info_tick", symbol))
        if not self._tick:
            return None
        return _Info(bid=2400.0, ask=2400.3, last=2400.15, spread=30, time=1791307800, volume=12)

    def order_check(self, request):
        self.calls.append(("order_check", dict(request)))
        return _Info(retcode=self._check_retcode, balance=10000.0, equity=10012.5, profit=12.5,
                     margin=30.0, margin_free=9800.0, comment="Done" if self._check_retcode == 10009 else "Invalid stops",
                     request=dict(request))

    def order_send(self, request):                                   # must never run
        self.calls.append(("order_send", dict(request)))
        raise AssertionError("the diagnostic must never call order_send")


@pytest.fixture()
def fake_mt5():
    return _FakeMT5()


@pytest.fixture()
def windows_host(monkeypatch):
    """Make the module believe it runs on Windows (the operator's machine)."""
    wd = _diag()
    monkeypatch.setattr(wd, "_host_is_windows", lambda: True)
    return wd


def _full_probe(wd, fake, *, terminal=None, machine_is_windows=True):
    term = terminal if terminal is not None else {
        "discovered": [{"name": "MetaTrader 5", "path": r"C:\Program Files\MT5\terminal64.exe"}],
        "discovered_count": 1, "running": [{"name": "terminal64.exe", "pid": "1234"}],
        "running_count": 1, "saved_path": r"C:\Program Files\MT5\terminal64.exe",
    }
    machine = {"_is_windows": machine_is_windows, "platform": "Windows" if machine_is_windows else "Linux"}
    return wd.windows_mt5_diagnostic(REPO, symbol="XAUUSD", mt5_mod=fake,
                                     terminal=term, machine=machine)


# ===========================================================================
# §5 — the classifier must name the exact layer, never "unavailable"
# ===========================================================================
def test_01_ready_requires_every_layer_and_names_the_done_retcode(windows_host, fake_mt5):
    rep = _full_probe(windows_host, fake_mt5)
    assert rep["environment"] == "MT5 READY"
    assert rep["classification"]["environment"] in windows_host.CLASSIFICATION_LABELS
    oc = rep["mt5"]["order_check"]
    assert oc["attempted"] is True and oc["ok"] is True and oc["retcode"] == 10009
    assert oc["sent"] is False
    assert [c[0] for c in fake_mt5.calls if c[0] == "order_send"] == []
    # the request the terminal validated is the project's own builder output
    assert oc["request"]["action"] == 1 and oc["request"]["symbol"] == "XAUUSD"
    assert oc["request"]["volume"] == 0.01 and oc["request"]["type"] == 0


@pytest.mark.parametrize("facts,expected", [
    ({"platform_supported": False}, "MT5 PLATFORM UNSUPPORTED"),
    ({"platform_supported": True, "python_found": False}, "MT5 PACKAGE MISSING"),
    ({"platform_supported": True, "package_importable": False}, "MT5 PACKAGE MISSING"),
    ({"platform_supported": True, "package_importable": True, "terminals_found": 0,
      "initialized": False, "terminal_process_running": False}, "MT5 TERMINAL NOT FOUND"),
    ({"platform_supported": True, "package_importable": True, "terminals_found": 1,
      "terminal_process_running": False, "initialized": False}, "MT5 TERMINAL NOT RUNNING"),
    ({"platform_supported": True, "package_importable": True, "terminals_found": 1,
      "terminal_process_running": True, "initialized": False}, "MT5 INITIALIZATION FAILED"),
    ({"platform_supported": True, "package_importable": True, "terminals_found": 1,
      "initialized": True, "account_available": False}, "MT5 ACCOUNT UNAVAILABLE"),
    ({"platform_supported": True, "package_importable": True, "terminals_found": 1,
      "initialized": True, "account_available": True, "symbol_available": False}, "MT5 SYMBOL UNAVAILABLE"),
    ({"platform_supported": True, "package_importable": True, "terminals_found": 1,
      "initialized": True, "account_available": True, "symbol_available": True,
      "market_data_available": False}, "MT5 MARKET DATA UNAVAILABLE"),
    ({"platform_supported": True, "package_importable": True, "terminals_found": 1,
      "initialized": True, "account_available": True, "symbol_available": True,
      "market_data_available": True, "trading_permissions_ok": False}, "MT5 TRADING DISABLED"),
    ({"platform_supported": True, "package_importable": True, "terminals_found": 1,
      "initialized": True, "account_available": True, "symbol_available": True,
      "market_data_available": True, "trading_permissions_ok": True,
      "order_check_attempted": True, "order_check_ok": False, "order_check_retcode": 10016},
     "MT5 ORDER VALIDATION FAILED"),
    ({"platform_supported": True, "package_importable": True, "terminals_found": 1,
      "initialized": True, "account_available": True, "symbol_available": True,
      "market_data_available": True, "trading_permissions_ok": True,
      "order_check_attempted": True, "order_check_ok": True, "order_check_retcode": 10009},
     "MT5 READY"),
])
def test_02_every_layer_of_the_vocabulary_is_reachable(facts, expected):
    wd = _diag()
    got = wd.classify_layers(facts)
    assert got["environment"] == expected
    assert got["reason"], "a classification without a reason is not a diagnosis"
    assert expected in wd.CLASSIFICATION_LABELS or expected == wd.MT5_PLATFORM_UNSUPPORTED


def test_03_the_package_missing_layer_names_the_interpreter_that_has_it():
    wd = _diag()
    got = wd.classify_layers({"platform_supported": True, "package_importable": False,
                              "package_importable_somewhere": r"C:\Python313\python.exe"})
    assert got["environment"] == "MT5 PACKAGE MISSING"
    assert r"C:\Python313\python.exe" in got["reason"]


def test_04_a_python_outside_the_wheel_range_is_reported_as_the_cause():
    wd = _diag()
    got = wd.classify_layers({"platform_supported": True, "package_importable": False,
                              "wheel_supported": False})
    assert got["environment"] == "MT5 PACKAGE MISSING"
    assert "3.6-3.14" in got["reason"]


def test_05_python_313_is_not_required_and_the_real_range_is_used():
    wd = _diag()
    assert wd._wheel_supported("3.13.5") is True
    assert wd._wheel_supported("3.11.9") is True
    assert wd._wheel_supported("3.6.8") is True
    assert wd._wheel_supported("3.5.0") is False
    assert wd._wheel_supported("3.15.0") is False
    text = wd.format_diagnostic({"classification": {"environment": "MT5 READY", "reason": "x"},
                                 "machine": {}, "python": {}, "terminal": {}, "mt5": {},
                                 "layer_facts": {}})
    assert "Python 3.13 is NOT required" in text
    assert "3.6-3.14" in text


# ===========================================================================
# §4/§5 — the deep probe against a fake terminal, per failure layer
# ===========================================================================
def test_06_no_terminal_on_disk_but_a_process_running_is_not_found(windows_host):
    """initialize() cannot reach a terminal that is not on disk, even while one runs."""
    rep = _full_probe(windows_host, _FakeMT5(initialize=False), terminal={
        "discovered": [], "discovered_count": 0,
        "running": [{"name": "terminal64.exe", "pid": "9"}], "running_count": 1, "saved_path": ""})
    assert rep["environment"] == "MT5 TERMINAL NOT FOUND"


def test_07_initialize_failure_without_a_process_says_not_running(windows_host):
    rep = _full_probe(windows_host, _FakeMT5(initialize=False), terminal={
        "discovered": [{"name": "MT5", "path": r"C:\MT5\terminal64.exe"}], "discovered_count": 1,
        "running": [], "running_count": 0, "saved_path": ""})
    assert rep["environment"] == "MT5 TERMINAL NOT RUNNING"
    assert "start the terminal" in rep["reason"]


def test_08_initialize_failure_with_a_process_running_says_initialization_failed(windows_host):
    rep = _full_probe(windows_host, _FakeMT5(initialize=False), terminal={
        "discovered": [{"name": "MT5", "path": r"C:\MT5\terminal64.exe"}], "discovered_count": 1,
        "running": [{"name": "terminal64.exe", "pid": "77"}], "running_count": 1, "saved_path": ""})
    assert rep["environment"] == "MT5 INITIALIZATION FAILED"
    assert "IPC timeout" in rep["reason"]


def test_09_initialize_attempts_follow_the_production_order(windows_host, fake_mt5):
    rep = _full_probe(windows_host, fake_mt5)
    hows = [a["how"] for a in rep["mt5"]["attempts"]]
    assert hows[0] == "saved terminal path", hows
    assert rep["mt5"]["shutdown_called"] is True          # the IPC link is released, terminal keeps running
    assert rep["mt5"]["terminal_info"]["build"] == 6230


def test_10_missing_account_is_reported_before_the_symbol(windows_host):
    rep = _full_probe(windows_host, _FakeMT5(account=False))
    assert rep["environment"] == "MT5 ACCOUNT UNAVAILABLE"
    assert rep["mt5"]["symbol"]["found"] is True           # the symbol was still inspected


def test_11_missing_symbol_is_reported(windows_host):
    rep = _full_probe(windows_host, _FakeMT5(symbol=False))
    assert rep["environment"] == "MT5 SYMBOL UNAVAILABLE"


def test_12_symbol_without_a_tick_is_market_data_unavailable(windows_host):
    rep = _full_probe(windows_host, _FakeMT5(tick=False))
    assert rep["environment"] == "MT5 MARKET DATA UNAVAILABLE"
    assert rep["mt5"]["order_check"]["attempted"] is False   # nothing to validate without a price


def test_13_a_symbol_in_close_only_mode_is_trading_disabled(windows_host):
    rep = _full_probe(windows_host, _FakeMT5(symbol_trade_mode=3))
    assert rep["environment"] == "MT5 TRADING DISABLED"


def test_14_a_refused_order_check_is_the_last_layer_and_sends_nothing(windows_host, fake_mt5):
    fake_mt5._check_retcode = 10016
    rep = _full_probe(windows_host, fake_mt5)
    assert rep["environment"] == "MT5 ORDER VALIDATION FAILED"
    assert "10016" in rep["reason"]
    assert [c[0] for c in fake_mt5.calls if c[0] == "order_send"] == []
    # the whole report is copy-pasteable and carries the layer facts
    text = windows_host.format_diagnostic(rep)
    assert "CLASSIFICATION: MT5 ORDER VALIDATION FAILED" in text
    assert "order_send is NEVER called" in text
    assert "retcode          : 10016 (REFUSED)" in text


def test_15_terminal_info_and_account_fields_are_safe(windows_host, fake_mt5):
    rep = _full_probe(windows_host, fake_mt5)
    acc = rep["mt5"]["account_info"]
    assert acc["trade_mode_name"] == "DEMO" and acc["login"] == 51234567
    assert acc["name_masked"] == "O***"                       # the holder name is never printed
    text = windows_host.format_diagnostic(rep)
    for forbidden in ("password", "Password", "Operator Name"):
        assert forbidden not in text


# ===========================================================================
# §3/§6 — honesty on this (non-Windows) host
# ===========================================================================
def test_16_this_linux_host_is_reported_as_platform_unsupported_not_as_a_windows_layer():
    wd = _diag()
    rep = wd.windows_mt5_diagnostic(REPO)
    assert rep["environment"] == "MT5 PLATFORM UNSUPPORTED"
    assert rep["mt5"]["attempted"] is False
    assert rep["mt5"]["order_check"]["attempted"] is False
    assert rep["mt5"]["order_check"]["sent"] is False


def test_17_the_diagnostic_source_contains_no_order_send_and_no_process_kill():
    src = MODULE.read_text(encoding="utf-8")
    # the invariant is "no call", not "no mention": the module documents that it
    # never sends, and the tests keep that promise honest by scanning for a call.
    assert "order_send(" not in src, "the diagnostic must never call order_send"
    assert '"order_send"' not in src, "the diagnostic must never resolve order_send by name"
    for banned in ("taskkill", "Stop-Process", "kill(", "terminate("):
        assert banned not in src
    cli = CLI.read_text(encoding="utf-8")
    assert "order_send(" not in cli and '"order_send"' not in cli
    for banned in ("taskkill", "Stop-Process"):
        assert banned not in cli


# ===========================================================================
# §4 — the batch file the operator runs
# ===========================================================================
def test_18_the_bat_exists_at_the_root_is_crlf_and_points_at_the_diagnostic():
    assert BAT.is_file(), "CHECK_MT5_WINDOWS.bat must live in the repository root"
    raw = BAT.read_bytes()
    assert b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n"), "the .bat must be CRLF-only"
    text = raw.decode("utf-8")
    assert "backend\\tools\\mt5_windows_diagnostic.py" in text
    assert "--save" in text and "MT5_WINDOWS_DIAGNOSTIC_" in text
    # the operator must be told the log path and that nothing is placed
    assert "full report saved to" in text
    assert "no order is placed" in text and "no process is started or killed" in text


def test_19_the_bat_never_kills_or_sends_and_falls_back_honestly():
    text = BAT.read_bytes().decode("utf-8").lower()
    for banned in ("taskkill", "stop-process", "order_send", "del ", "rmdir", "format "):
        assert banned not in text, f"the diagnostic .bat must not contain '{banned}'"
    assert "classification: mt5 package missing" in text      # the no-python path still classifies
    assert "where python" in text and "where py" in text


def test_20_the_bat_resolves_python_in_the_launcher_order():
    text = BAT.read_bytes().decode("utf-8")
    i_venv = text.index(".venv\\Scripts\\python.exe")
    i_backend_venv = text.index("backend\\.venv\\Scripts\\python.exe")
    i_py = text.index("where py")
    i_python = text.index("where python")
    assert i_venv < i_backend_venv < i_py < i_python, "the interpreter order must match start.bat"


def test_21_the_cli_runs_end_to_end_and_saves_the_report(tmp_path):
    py = sys.executable
    out = tmp_path / "diag.txt"
    proc = subprocess.run([py, str(CLI), "--root", str(REPO), "--save", str(out)],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode in (0, 3, 4)
    assert "MT5 WINDOWS DIAGNOSTIC" in proc.stdout
    assert "CLASSIFICATION:" in proc.stdout
    assert out.is_file() and "CLASSIFICATION:" in out.read_text(encoding="utf-8")
    # the JSON form is machine readable for the operator's paste-back
    proc2 = subprocess.run([py, str(CLI), "--root", str(REPO), "--json"],
                           capture_output=True, text=True, timeout=300)
    payload = json.loads(proc2.stdout)
    assert payload["safe"]["places_orders"] is False
    assert payload["environment"] in _diag().CLASSIFICATION_LABELS + (_diag().MT5_PLATFORM_UNSUPPORTED,)


# ===========================================================================
# §2 — the runtime report carries the same vocabulary (dashboard surface)
# ===========================================================================
def test_22_runtime_report_exposes_the_layer_class_consistently(client):
    body = client.get("/api/mt5/runtime").json()
    assert "environment_class" in body and "environment_reason" in body
    wd = _diag()
    assert body["environment_class"] in wd.CLASSIFICATION_LABELS + (wd.MT5_PLATFORM_UNSUPPORTED,)
    # a verdict of READY and a class of READY must never disagree
    assert (body["verdict"] == "REAL_MT5_READY") == (body["environment_class"] == wd.MT5_READY)
    assert body["environment_reason"]
