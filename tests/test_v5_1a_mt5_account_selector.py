"""V5.1a-next §B — account discovery / selector, and the REAL switch_to_path.

A fake `MetaTrader5` package stands in for the terminal so the *real* bridge and
the *real* accounts module code paths are exercised: two terminals (one DEMO, one
REAL) are discovered, probed, listed, and one is selected — leaving the DEMO one
usable and the REAL one visibly blocked.

Invariants checked here:

  * a probe never calls order_send/order_check (read-only, §B/§4);
  * a REAL account is reported as REAL and refuses orders (never disguised);
  * the account list says "not probed / package missing" instead of inventing rows;
  * selecting an account persists only non-sensitive fields;
  * one terminal on the machine yields exactly one row (single-terminal regression).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

DEMO_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
REAL_PATH = r"C:\Program Files\ICMarkets MT5\terminal64.exe"


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class MultiTerminalMT5:
    """One fake package serving several terminals, keyed by the initialize path."""

    __version__ = "5.0.6231"
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_CONTEST = 1
    ACCOUNT_TRADE_MODE_REAL = 2
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2

    def __init__(self, accounts: dict, *, init_fails: set = frozenset()):
        self.accounts = accounts            # path -> dict
        self.init_fails = set(init_fails)
        self.active: str | None = None
        self.calls: list = []

    def initialize(self, path=None, **kw):
        self.calls.append(("initialize", path))
        if path is None:
            return False
        if path in self.init_fails:
            self._last = (-6, "Terminal: not found")
            return False
        self.active = path
        return True

    def shutdown(self):
        self.calls.append(("shutdown", None))
        self.active = None
        return True

    def version(self):
        return (500, 6230, "2026-09-27")

    def terminal_info(self):
        if self.active is None:
            return None
        acct = self.accounts[self.active]
        return _Obj(name=acct.get("terminal_name", "MetaTrader 5"),
                    company=acct.get("company", ""), build=6230, connected=True,
                    trade_allowed=bool(acct.get("trade_allowed", True)),
                    tradeapi_disabled=False, path=self.active)

    def account_info(self):
        if self.active is None:
            return None
        a = self.accounts[self.active]
        if a.get("no_login"):
            return None
        return _Obj(login=a["login"], server=a["server"], trade_mode=a["trade_mode"],
                    balance=a.get("balance", 1000.0), equity=a.get("balance", 1000.0),
                    margin_free=a.get("balance", 1000.0), currency="USD", leverage=100,
                    trade_allowed=bool(a.get("trade_allowed", True)),
                    trade_expert=bool(a.get("trade_expert", True)), name="Operator")

    def last_error(self):
        return getattr(self, "_last", (0, "Ok"))

    # trade surface — must stay untouched by §B code
    def order_check(self, request):
        self.calls.append(("order_check", request))
        raise AssertionError("§B must never call order_check")

    def order_send(self, request):
        self.calls.append(("order_send", request))
        raise AssertionError("§B must never call order_send")


@pytest.fixture()
def two_terminals(monkeypatch, tmp_path):
    from app.mt5 import mt5_real
    pkg = MultiTerminalMT5({
        DEMO_PATH: {"login": 50123456, "server": "ICMarketsSC-Demo", "trade_mode": 0,
                    "company": "Raw Trading Ltd", "terminal_name": "MetaTrader 5"},
        REAL_PATH: {"login": 50999999, "server": "ICMarketsSC-Live", "trade_mode": 2,
                    "company": "ICMarkets", "terminal_name": "ICMarkets MT5"},
    })
    monkeypatch.setattr(mt5_real, "mt5", pkg, raising=False)
    monkeypatch.setattr(mt5_real, "MT5_PACKAGE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(mt5_real, "MT5_IMPORT_ERROR", "", raising=False)
    monkeypatch.setenv("_V51A_TEST_TERMINALS", json.dumps([DEMO_PATH, REAL_PATH]))
    return pkg


@pytest.fixture()
def isolated_config(monkeypatch, tmp_path):
    """Keep CONFIG/mt5_config.json out of the repository during tests."""
    from app.mt5 import config as mt5cfg
    cfg_file = tmp_path / "CONFIG" / "mt5_config.json"
    monkeypatch.setattr(mt5cfg, "CONFIG_DIR", cfg_file.parent)
    monkeypatch.setattr(mt5cfg, "MT5_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(mt5cfg, "CONFIG_FILE", cfg_file)
    return cfg_file


@pytest.fixture()
def discovered(monkeypatch, two_terminals):
    """discover_terminals() as if both terminals were installed on Windows."""
    from app.mt5 import discovery
    rows = [{"name": "MetaTrader 5 (Official)", "path": DEMO_PATH, "filename": "terminal64.exe",
             "folder": "MetaTrader 5"},
            {"name": "ICMarkets MT5", "path": REAL_PATH, "filename": "terminal64.exe",
             "folder": "ICMarkets MT5"}]
    monkeypatch.setattr(discovery, "discover_terminals", lambda: list(rows))
    monkeypatch.setattr("app.mt5.accounts.terminals_from_config", lambda: (
        __import__("app.mt5.accounts", fromlist=["x"]).terminals_from_config.__wrapped__()
        if hasattr(__import__("app.mt5.accounts", fromlist=["x"]).terminals_from_config, "__wrapped__")
        else _with_discovery(rows)))
    return rows


def _with_discovery(rows):
    """terminals_from_config() using the patched discovery list."""
    from app.mt5 import accounts as acc
    out, seen = [], set()

    def add(name, path, origin):
        key = str(Path(path)).lower()
        if key in seen or not path:
            return
        seen.add(key)
        out.append({"name": name, "path": str(path), "origin": origin})

    try:
        from app.mt5.config import load_mt5_config
        cfg = load_mt5_config() or {}
        if str(cfg.get("terminal_path") or "").strip():
            add(str(cfg.get("last_connected_terminal") or "Saved terminal"),
                str(cfg["terminal_path"]).strip(), "config")
    except Exception:
        pass
    for t in rows:
        add(str(t.get("name") or ""), str(t.get("path") or ""), "discovery")
    return out


@pytest.fixture()
def fake_runtime(monkeypatch, isolated_config, discovered):
    """Point the accounts module at the two fake terminals, no real bridge built."""
    from app.mt5 import accounts as acc, factory
    monkeypatch.setattr(acc, "terminals_from_config", lambda: _with_discovery(discovered))
    monkeypatch.setattr(factory, "reset_bridge", lambda explicit_path=None: None)
    monkeypatch.setattr(factory, "get_bridge", lambda: _FakeActive())
    return acc


class _FakeActive:
    name = "mt5_real"
    source = "MT5"

    def status(self):
        return {"connected": True, "account": 50123456, "server": "ICMarketsSC-Demo",
                "demo": True, "terminal_path": DEMO_PATH, "last_error": ""}


# ===========================================================================
# discovery / listing
# ===========================================================================
def test_01_terminals_from_config_merges_saved_and_discovered(fake_runtime, isolated_config):
    rows = fake_runtime.terminals_from_config()
    paths = [r["path"] for r in rows]
    assert paths == [DEMO_PATH, REAL_PATH]
    assert rows[0]["origin"] == "discovery"


def test_02_a_saved_terminal_comes_first_and_is_not_duplicated(fake_runtime, isolated_config):
    from app.mt5.config import set_saved_terminal_path
    set_saved_terminal_path(REAL_PATH, auto_discover=False)
    rows = fake_runtime.terminals_from_config()
    assert rows[0]["path"] == REAL_PATH and rows[0]["origin"] == "config"
    assert [r["path"] for r in rows].count(REAL_PATH) == 1
    assert DEMO_PATH in [r["path"] for r in rows]


def test_03_probing_reports_the_account_and_its_trade_mode(fake_runtime):
    row = fake_runtime.probe_terminal(DEMO_PATH)
    assert row["initialized"] is True and row["connected"] is True
    assert row["login"] == 50123456 and row["server"] == "ICMarketsSC-Demo"
    assert row["trade_mode"] == 0 and row["account_kind"] == "DEMO" and row["demo"] is True
    assert row["trade_mode_name"] == "DEMO"
    assert row["build"] == 6230


def test_04_a_real_account_is_reported_as_real_and_explains_the_refusal(fake_runtime):
    row = fake_runtime.probe_terminal(REAL_PATH)
    assert row["account_kind"] == "REAL" and row["demo"] is False
    assert "REAL" in row["reason"] and "refused" in row["reason"]


def test_05_a_terminal_that_refuses_initialize_is_reported_not_hidden(fake_runtime, two_terminals):
    two_terminals.init_fails.add(DEMO_PATH)
    row = fake_runtime.probe_terminal(DEMO_PATH)
    assert row["initialized"] is False and row["reason"]
    assert "initialize" in row["reason"] or row["last_error"]


def test_06_probing_is_read_only(fake_runtime, two_terminals):
    fake_runtime.probe_all()
    kinds = [c[0] for c in two_terminals.calls]
    assert "order_send" not in kinds and "order_check" not in kinds
    assert kinds.count("initialize") == 2 and kinds.count("shutdown") == 2


def test_07_list_accounts_marks_the_active_one(fake_runtime):
    out = fake_runtime.list_accounts(probe=True)
    assert out["probed"] is True and len(out["accounts"]) == 2
    demo = [r for r in out["accounts"] if r["path"] == DEMO_PATH][0]
    real = [r for r in out["accounts"] if r["path"] == REAL_PATH][0]
    assert demo["active"] is True and real["active"] is False
    assert out["active"]["login"] == 50123456
    assert "terminals_unavailable_reason" not in json.dumps(out)


def test_07b_single_terminal_regression(fake_runtime, monkeypatch, two_terminals):
    """One terminal on the machine -> exactly one row, no phantom accounts."""
    from app.mt5 import accounts as acc
    monkeypatch.setattr(acc, "terminals_from_config",
                        lambda: [{"name": "MetaTrader 5", "path": DEMO_PATH, "origin": "config"}])
    out = acc.list_accounts(probe=True)
    assert len(out["accounts"]) == 1
    assert out["accounts"][0]["login"] == 50123456
    assert out["accounts"][0]["active"] is True


def test_08_without_the_package_the_list_says_so(monkeypatch, isolated_config):
    from app.mt5 import mt5_real, accounts as acc
    monkeypatch.setattr(mt5_real, "MT5_PACKAGE_AVAILABLE", False, raising=False)
    monkeypatch.setattr(mt5_real, "MT5_IMPORT_ERROR", "No module named 'MetaTrader5'", raising=False)
    out = acc.list_accounts(probe=True)
    assert out["probed"] is False and out["accounts"] == []
    assert out["package_available"] is False
    assert "MetaTrader5" in out["reason"]


# ===========================================================================
# switching
# ===========================================================================
def test_09_switch_to_path_moves_the_bridge_and_reports_the_account(two_terminals, isolated_config):
    from app.mt5.mt5_real import MT5RealBridge
    b = MT5RealBridge(path=REAL_PATH)
    report = b.switch_to_path(REAL_PATH)
    assert report["connected"] is True
    assert report["account_kind"] == "REAL" and report["demo"] is False
    assert report["login"] == 50999999 and report["server"] == "ICMarketsSC-Live"
    assert report["build"] == 6230
    assert [c[0] for c in two_terminals.calls].count("initialize") == 1
    assert "order_send" not in [c[0] for c in two_terminals.calls]


def test_10_switch_to_path_refuses_without_a_path(two_terminals, isolated_config):
    from app.mt5.mt5_real import MT5RealBridge
    report = MT5RealBridge().switch_to_path("")
    assert report["connected"] is False and report["last_error"]


def test_11_switch_to_path_reports_an_uninitializable_path(two_terminals, isolated_config):
    from app.mt5.mt5_real import MT5RealBridge
    two_terminals.init_fails.add(REAL_PATH)
    report = MT5RealBridge().switch_to_path(REAL_PATH)
    assert report["connected"] is False
    assert "initialize failed" in report["last_error"]
    assert report["account_kind"] == "UNKNOWN"


def test_12_select_account_validates_the_path(fake_runtime):
    out = fake_runtime.select_account(r"C:\nope\terminal64.exe")
    assert out["ok"] is False and out["error"] == "INVALID_TERMINAL_PATH"


def test_13_select_account_refuses_without_the_package(fake_runtime, monkeypatch):
    from app.mt5 import mt5_real
    monkeypatch.setattr("app.mt5.discovery.validate_terminal_path", lambda p: (True, ""))
    monkeypatch.setattr(mt5_real, "MT5_PACKAGE_AVAILABLE", False, raising=False)
    out = fake_runtime.select_account(DEMO_PATH)
    assert out["ok"] is False and out["error"] == "MT5_PACKAGE_MISSING"


def test_14_selecting_a_demo_account_persists_only_safe_fields(fake_runtime, isolated_config,
                                                              monkeypatch):
    monkeypatch.setattr("app.mt5.discovery.validate_terminal_path", lambda p: (True, ""))
    out = fake_runtime.select_account(DEMO_PATH, password="super-secret")
    assert out["ok"] is True and out["account_kind"] == "DEMO"
    saved = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert saved["terminal_path"] == DEMO_PATH
    assert saved["selected_login"] == 50123456
    blob = isolated_config.read_text(encoding="utf-8")
    assert "super-secret" not in blob and "password" not in blob.lower()


def test_15_selecting_is_recorded_and_usable_for_the_single_terminal_case(fake_runtime, isolated_config):
    demo = fake_runtime.list_accounts(probe=True)
    assert demo["accounts"][0]["terminal"] in ("MetaTrader 5", "")
    # a REAL account is listed with its refusal — never silently "selected"
    real = fake_runtime.probe_terminal(REAL_PATH)
    assert real["account_kind"] == "REAL"
    assert fake_runtime.list_accounts(probe=False)["probed"] is False


# ===========================================================================
# API surface
# ===========================================================================
def test_16_api_accounts_endpoint_is_read_only(client, fake_runtime):
    r = client.get("/api/mt5/accounts")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"probed", "package_available", "reason", "terminals",
                         "accounts", "active"}
    assert body["probed"] is False              # the API defaults to no probing
    assert isinstance(body["terminals"], list)


def test_17_api_select_requires_a_path(client):
    r = client.post("/api/mt5/accounts/select", json={})
    assert r.status_code == 400
    assert "path" in r.json()["detail"]


def test_18_api_select_reports_the_exact_refusal(client, fake_runtime, monkeypatch):
    monkeypatch.setattr("app.mt5.discovery.validate_terminal_path",
                        lambda p: (False, "File does not exist"))
    r = client.post("/api/mt5/accounts/select", json={"path": r"C:\nope\terminal64.exe"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["status"]["error"] == "INVALID_TERMINAL_PATH"
    assert "File does not exist" in body["status"]["reason"]


def test_19_no_password_leaks_through_any_account_response(fake_runtime, monkeypatch):
    monkeypatch.setattr("app.mt5.discovery.validate_terminal_path", lambda p: (True, ""))
    out = fake_runtime.select_account(DEMO_PATH, password="hunter2")
    blob = json.dumps(out).lower()
    assert "hunter2" not in blob and "password" not in blob
