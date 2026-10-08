"""V5.1a §8-§10 — the MT5 runtime truth.

What is verified:

1. **Interpreter detection** — the report names the interpreter actually running
   the backend and the interpreter the launcher (`start.bat` ->
   `scripts/run_backend.bat`) would pick, and says whether they are the same.
   The decisive check runs `import MetaTrader5` in the launcher's interpreter in
   a real subprocess, so "the package is installed somewhere" cannot be confused
   with "the package is installed where the app runs".
2. **Package/import diagnostics** — the running interpreter's own import state is
   reported with the exact error text.
3. **Wheel reality** — the report states the published wheel range and whether
   this Python is inside it (verified against PyPI: MetaTrader5 5.0.6231 ships
   cp36..cp314 win_amd64 wheels; Python 3.13 is NOT required).
4. **Terminal detection + honesty** — a terminal found/connected state is
   reported as found; the simulator's own `connected=True` can never make a
   "real terminal connected" check pass.
5. **Simulator-vs-real** — when MT5 is not usable the verdict is SIMULATOR_ONLY
   with the first failing check as the reason, and the order-validation path
   still refuses with MT5_UNAVAILABLE rather than pretending.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.mt5 import runtime_report as rr


# ===========================================================================
# 1. interpreter detection
# ===========================================================================
def test_01_the_report_names_the_running_interpreter():
    rep = rr.mt5_runtime_report(probe_launcher=False)
    py = rep["python"]
    assert py["executable"] == sys.executable
    assert py["version"].startswith(f"{sys.version_info.major}.{sys.version_info.minor}")
    assert py["bits"] in (32, 64)
    assert isinstance(py["is_venv"], bool)
    assert py["platform"] in ("windows", "linux", "darwin")


def test_02_the_launcher_interpreter_is_resolved_from_the_checkout(tmp_path):
    """`start.bat` looks for .venv (root, then backend), then a bare `venv`, then PATH."""
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    expected = tmp_path / ".venv" / "Scripts" / "python.exe"
    expected.write_bytes(b"")                      # existence is what the launcher tests
    info = rr._launcher_python(tmp_path)
    assert info["found"] is True
    assert info["label"] == "root/.venv"
    assert Path(info["path"]) == expected


def test_03_without_a_venv_the_launcher_falls_back_to_path_and_says_so(tmp_path):
    info = rr._launcher_python(tmp_path)
    assert info["found"] is False
    assert info["label"] == "PATH"
    assert "PATH" in info.get("note", "")


def test_04_the_probe_actually_runs_the_interpreter_it_is_given():
    """The probe must be a subprocess check, not an assumption: with an
    interpreter that cannot import MetaTrader5 the result is 'not importable'."""
    probe = rr._package_probe(sys.executable)
    assert probe["checked"] is True
    assert probe["interpreter"] == sys.executable
    if probe["importable"]:
        assert probe.get("version")                 # a real version string came back
    else:
        assert "MetaTrader5" in (probe.get("error") or "")


def test_05_a_missing_interpreter_is_reported_not_crashed(tmp_path):
    probe = rr._package_probe(str(tmp_path / "nope" / "python.exe"))
    assert probe["checked"] is False
    assert "not found" in probe["reason"]


def test_06_the_launcher_interpreter_is_probed_and_compared_with_the_running_one(tmp_path):
    """The §9 case: the backend runs one Python, the launcher would use another.
    The report must probe that interpreter and say whether it is the same one."""
    fake = tmp_path / ".venv" / "Scripts"
    fake.mkdir(parents=True)
    stub = fake / "python.exe"
    stub.write_text("#!/bin/sh\nexit 3\n")            # exists, but is not an interpreter
    stub.chmod(0o755)
    rep = rr.mt5_runtime_report(tmp_path, probe_launcher=True)
    lp = rep["launcher_package_probe"]
    assert lp["checked"] is True, lp
    assert lp["importable"] is False
    assert lp["same_as_running"] is False
    assert rep["launcher_python"]["path"].endswith("python.exe")


def test_06b_when_the_launcher_interpreter_is_the_running_one_it_says_so(tmp_path):
    fake = tmp_path / ".venv" / "bin"
    fake.mkdir(parents=True)
    link = fake / "python"
    link.symlink_to(sys.executable)                    # same interpreter, different path
    rep = rr.mt5_runtime_report(tmp_path, probe_launcher=True)
    lp = rep["launcher_package_probe"]
    assert lp["checked"] is True and lp["same_as_running"] is True, lp
    assert lp["importable"] == rr.mt5_runtime_report(probe_launcher=False)["metatrader5_package"]["importable"]


# ===========================================================================
# 2/3. package + wheel facts
# ===========================================================================
def test_07_package_state_matches_reality():
    rep = rr.mt5_runtime_report(probe_launcher=False)
    pkg = rep["metatrader5_package"]
    assert isinstance(pkg["importable"], bool)
    if pkg["importable"]:
        assert pkg.get("version")
    else:
        assert pkg.get("error")


def test_08_the_wheel_rule_states_that_3_13_is_not_required():
    py = rr.mt5_runtime_report(probe_launcher=False)["python"]
    assert "3.13 is not required" in py["wheel_range"]
    assert py["wheel_supported"] is (rr.MT5_WHEEL_MIN <=
                                    (sys.version_info.major, sys.version_info.minor) <= rr.MT5_WHEEL_MAX)
    assert rr.MT5_WHEEL_MIN == (3, 6) and rr.MT5_WHEEL_MAX == (3, 14)


# ===========================================================================
# 4. terminal + honesty
# ===========================================================================
def test_09_terminal_facts_are_reported_even_when_none_exists():
    t = rr.mt5_runtime_report(probe_launcher=False)["terminal"]
    assert isinstance(t["discovered"], list)
    assert t["discovered_count"] == len(t["discovered"])
    assert t["supported_platform"] is sys.platform.startswith("win")


def test_09b_the_platform_fact_is_reported_from_the_host(monkeypatch):
    rep_linux = rr.mt5_runtime_report(probe_launcher=False)
    assert {c["id"]: c for c in rep_linux["checks"]}["platform_supported"]["ok"] is rr._host_is_windows()
    monkeypatch.setattr(rr, "_host_is_windows", lambda: True)
    assert {c["id"]: c for c in rr.mt5_runtime_report(probe_launcher=False)["checks"]}["platform_supported"]["ok"] is True


def test_10_a_simulator_connection_can_never_pass_the_real_terminal_check(monkeypatch):
    """The simulator bridge reports connected=True for its own purposes. If that
    leaked into the MT5 check, the report would claim a real terminal is
    connected when it is not — the exact dishonesty this iteration forbids."""
    monkeypatch.setattr(rr, "_bridge_facts", lambda: {
        "active_bridge": "simulator", "source": "SIMULATOR", "connected": True,
        "account": {}, "trade_allowed": None})
    monkeypatch.setattr(rr, "_terminal_facts", lambda: {
        "supported_platform": True, "discovered": [], "discovered_count": 0, "saved_path": ""})
    monkeypatch.setattr(rr, "_host_is_windows", lambda: True)
    rep = rr.mt5_runtime_report(probe_launcher=False)
    checks = {c["id"]: c for c in rep["checks"]}
    assert checks["connected"]["ok"] is False
    assert checks["bridge_real"]["ok"] is False
    assert rep["verdict"] == "SIMULATOR_ONLY"
    assert "MT5_UNAVAILABLE" in rep["reason"] or "MT5_NOT_CONNECTED" in rep["reason"]


def test_11_a_fully_real_environment_reports_ready(monkeypatch):
    """The positive case must exist too, otherwise the check is untestable."""
    monkeypatch.setattr(rr, "_current_package_facts",
                        lambda: {"importable": True, "version": "5.0.6231", "error": ""})
    monkeypatch.setattr(rr, "_terminal_facts", lambda: {
        "supported_platform": True, "discovered": [{"name": "IC Markets", "path": "C:/MT5/terminal64.exe"}],
        "discovered_count": 1, "saved_path": "C:/MT5/terminal64.exe"})
    monkeypatch.setattr(rr, "_bridge_facts", lambda: {
        "active_bridge": "mt5", "source": "MT5", "connected": True,
        "account": {"login": 12345, "server": "ICMarkets-Demo", "currency": "USD"},
        "trade_allowed": True, "terminal_company": "Raw Trading Ltd"})
    monkeypatch.setattr(rr, "_host_is_windows", lambda: True)
    rep = rr.mt5_runtime_report(probe_launcher=False)
    assert rep["verdict"] == "REAL_MT5_READY", rep["reason"]
    assert rep["failed_checks"] == []


def test_11b_on_a_non_windows_host_the_platform_is_named_as_the_reason():
    if rr._host_is_windows():
        pytest.skip("host is Windows")
    rep = rr.mt5_runtime_report(probe_launcher=False)
    assert rep["verdict"] == "SIMULATOR_ONLY"
    assert rep["failed_checks"][0] == "platform_supported"
    assert rep["reason"].startswith("PLATFORM_UNSUPPORTED")


def test_12_every_failing_check_is_listed_with_its_code(monkeypatch):
    monkeypatch.setattr(rr, "_current_package_facts",
                        lambda: {"importable": False, "error": "No module named 'MetaTrader5'"})
    monkeypatch.setattr(rr, "_host_is_windows", lambda: True)
    rep = rr.mt5_runtime_report(probe_launcher=False)
    for c in rep["checks"]:
        assert set(("id", "label", "ok", "detail", "code")) <= set(c)
    assert "package_importable" in rep["failed_checks"]
    assert "No module named" in rep["reason"]


# ===========================================================================
# 5. the report is reachable, the text rendering is truthful, and the order
#    path still refuses instead of simulating
# ===========================================================================
def test_13_the_text_report_shows_the_interpreter_and_the_verdict():
    rep = rr.mt5_runtime_report(probe_launcher=False)
    text = rr.format_report(rep)
    assert "MT5 RUNTIME DIAGNOSTIC" in text
    assert sys.executable in text
    assert rep["verdict"] in text
    assert "3.13 is not required" in text
    assert "never used to represent a real broker execution" in text


def test_14_the_cli_tool_runs_and_exits_10_when_mt5_is_not_ready(tmp_path):
    import subprocess
    tool = Path(__file__).resolve().parents[1] / "backend" / "tools" / "mt5_runtime_diagnostic.py"
    proc = subprocess.run([sys.executable, str(tool), "--root", str(tmp_path), "--json", "--no-probe"],
                          capture_output=True, text=True, timeout=120)
    payload = json.loads(proc.stdout)
    assert payload["verdict"] in ("REAL_MT5_READY", "SIMULATOR_ONLY")
    assert proc.returncode == (0 if payload["verdict"] == "REAL_MT5_READY" else 10)


def test_15_the_report_never_claims_a_broker_result():
    rep = rr.mt5_runtime_report(probe_launcher=False)
    blob = json.dumps(rep).lower()
    for forbidden in ("order_send", "retcode", "ticket", "position opened", "filled"):
        assert forbidden not in blob
    assert "never used to represent a real broker execution" in rep["note"]


def test_16_the_simulator_verdict_names_the_first_blocking_fact(monkeypatch):
    monkeypatch.setattr(rr, "_current_package_facts",
                        lambda: {"importable": False, "error": "No module named 'MetaTrader5'"})
    monkeypatch.setattr(rr, "_host_is_windows", lambda: True)
    rep = rr.mt5_runtime_report(probe_launcher=False)
    assert rep["verdict"] == "SIMULATOR_ONLY"
    assert rep["reason"].startswith("MT5_UNAVAILABLE")
    assert rep["failed_checks"][0] == "package_importable" or "wheel" in rep["failed_checks"][0]
    assert rep["simulator_available"] is True


@pytest.mark.parametrize("path", ["/api/mt5/runtime"])
def test_17_the_runtime_report_is_exposed_read_only(path):
    """The route exists, answers, and is a GET (read-only)."""
    import app.api.routes as routes
    from fastapi.testclient import TestClient
    from app.main import app

    methods = {r.path: getattr(r, "methods", set()) for r in routes.router.routes}
    assert path in methods, sorted(methods)
    assert methods[path] == {"GET"}, methods[path]

    client = TestClient(app)
    res = client.get(path)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["verdict"] in ("REAL_MT5_READY", "SIMULATOR_ONLY")
    assert "python" in body and "checks" in body


def test_18_order_validation_still_refuses_on_a_simulated_bridge():
    """§10 — with no real MT5 the *validation* must still fail closed: the report
    being honest is not a licence to simulate a fill."""
    from app.mt5.execution import validate_order_request
    from app.mt5.simulator import SimulatorBridge

    bridge = SimulatorBridge()
    bridge.connect()
    rep = validate_order_request(bridge, symbol="XAUUSD", side="BUY", volume=0.01,
                                 sl=1900.0, tp=2000.0)
    assert rep["placement_allowed"] is False
    codes = {e.get("code") for e in rep.get("errors", [])}
    assert "MT5_UNAVAILABLE" in codes, rep
    check = {c["id"]: c for c in rep["checks"]}["mt5_bridge"]
    assert check["ok"] is False


# ===========================================================================
# 6. §15 — the terminal's own order_check runs before order_send
# ===========================================================================
class _FakeTerminal:
    """Minimal MT5 module double: only what the pre-send check touches."""

    TRADE_RETCODE_DONE = 10009

    def __init__(self, check_retcode=10009, comment="Done", has_check=True):
        self.check_retcode = check_retcode
        self.comment = comment
        self.checked = []
        self.sent = []
        self._has_check = has_check
        self.order_check = self._order_check if has_check else None

    def _order_check(self, request):
        self.checked.append(dict(request))
        return _Result(self.check_retcode, self.comment)

    def order_send(self, request):
        self.sent.append(dict(request))
        return _Result(10009, "Done")


class _Result:
    def __init__(self, retcode, comment):
        self.retcode = retcode
        self.comment = comment
        self.order = 111
        self.deal = 222
        self.price = 2000.0
        self.volume = 0.01
        self.sl = 1900.0
        self.tp = 2100.0

    def _asdict(self):
        return {"retcode": self.retcode, "comment": self.comment, "order": self.order,
                "deal": self.deal, "price": self.price, "volume": self.volume,
                "sl": self.sl, "tp": self.tp}


def _real_bridge(monkeypatch, term):
    import app.mt5.mt5_real as real
    monkeypatch.setattr(real, "MT5_PACKAGE_AVAILABLE", True)
    monkeypatch.setattr(real, "mt5", term)
    b = real.MT5RealBridge()
    b._connected = True
    return b


REQUEST = {"symbol": "XAUUSD", "volume": 0.01, "type": 0, "price": 2000.0,
           "sl": 1900.0, "tp": 2100.0, "type_filling": 1, "magic": 1}


def test_19_a_refused_order_check_stops_the_order_before_it_is_sent(monkeypatch):
    term = _FakeTerminal(check_retcode=10019, comment="No money")
    bridge = _real_bridge(monkeypatch, term)
    out = bridge.send_market_order(REQUEST)
    assert out["ok"] is False
    # V5.4 §2 — the broker retcode BELONGS TO THE CHECK. Reporting it as the
    # order's own retcode is exactly the defect that made a refused preflight
    # look like a trade outcome: ``retcode``/``raw`` are the SEND result and are
    # None because nothing was sent.
    assert out["retcode"] is None and out["raw"] is None
    assert out["called"] is False and out["phase"] == "ORDER_CHECK_REFUSED"
    assert out["check"]["retcode"] == 10019
    assert out["refused_by"] == "mt5.order_check"
    assert out["check"]["ok"] is False
    assert term.sent == [], "order_send was called although order_check refused"
    assert term.checked and term.checked[0]["symbol"] == "XAUUSD"


def test_20_an_accepted_order_check_allows_the_send_and_reports_both_retcodes(monkeypatch):
    term = _FakeTerminal(check_retcode=10009)
    bridge = _real_bridge(monkeypatch, term)
    out = bridge.send_market_order(REQUEST)
    assert out["ok"] is True
    assert out["retcode"] == 10009
    assert out["check"]["ok"] is True
    assert len(term.checked) == 1 and len(term.sent) == 1


def test_21_a_build_without_order_check_still_works_and_says_so(monkeypatch):
    term = _FakeTerminal()
    term.order_check = None                              # what an older build looks like
    bridge = _real_bridge(monkeypatch, term)
    out = bridge.send_market_order(REQUEST)
    assert out["ok"] is True
    assert out["check"]["unsupported"] is True
    assert "order_check" in out["check"]["error"]
    assert len(term.sent) == 1


def test_22_a_disconnected_bridge_never_touches_the_terminal(monkeypatch):
    term = _FakeTerminal()
    bridge = _real_bridge(monkeypatch, term)
    bridge._connected = False
    out = bridge.send_market_order(REQUEST)
    assert out["ok"] is False and out["unsupported"] is True
    assert term.checked == [] and term.sent == []


def test_23_the_check_runs_on_the_identical_request(monkeypatch):
    """A check on a different request would be theatre: it must be the same dict
    (symbol/volume/price/sl/tp/filling) that is then sent."""
    term = _FakeTerminal()
    bridge = _real_bridge(monkeypatch, term)
    bridge.send_market_order(REQUEST)
    assert term.checked[0] == term.sent[0] == REQUEST
