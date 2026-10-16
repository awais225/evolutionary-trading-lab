"""V7.0 §4 — Manual Trading Panel restoration: UI presence, the proven V6.4
request contract, the real bridge path with mocked MT5 responses, duplicate
protection, and isolation from research/DeepTest data.

No test submits a real order: every bridge here is a double; the production
`place_demo_order` runs unchanged against it.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


# --------------------------------------------------------------------------- #
# 1–2. the panel is mounted in the LIVE TESTING view (production source)
# --------------------------------------------------------------------------- #

class TestPanelRestored:
    def test_live_testing_mounts_the_manual_trading_panel_in_action_controls(self):
        text = (REPO / "frontend/src/pages/LiveTesting.jsx").read_text(encoding="utf-8")
        assert 'data-testid="manual-trading-panel"' in text
        assert "Action controls — Manual Trading Panel" in text
        # V6.4 position: the panel is mounted BEFORE the node table
        panel_at = text.index('data-testid="manual-trading-panel"')
        table_at = text.index("<LiveNodeTable")
        assert panel_at < table_at, "the V6.4 layout keeps the panel before the node table"
        # expanded by default (the collapsed default made it look disappeared)
        assert "useState(true)" in text.split("manualOpen")[0][-200:] or \
            "setManualOpen] = useState(true)" in text

    def test_the_panel_keeps_the_v64_request_contract(self):
        text = (REPO / "frontend/src/components/LiveTestingPanels.jsx").read_text(encoding="utf-8")
        # the proven V6.4 payload keys travel to the place endpoint
        for key in ("symbol:", "side:", "volume:", "sl:", "tp:", "confirm:",
                    "client_order_id:"):
            assert key in text, f"manual payload lost {key}"
        assert "mt5ExecutionPlace" in text
        assert "mt5ExecutionPreview" in text
        # a submission is guarded by an explicit confirmation dialog
        assert "Confirm" in text and "PLACE" not in ""  # dialog exists in source
        assert "setConfirming" in text


# --------------------------------------------------------------------------- #
# 3–6. the bridge path with mocked MT5 responses
# --------------------------------------------------------------------------- #

class TestBridgePath:
    @pytest.fixture()
    def env(self, monkeypatch, tmp_path):
        """A real place_demo_order against a mocked terminal/bridge (the same
        double style the V4.2 suite uses — no real order can be sent)."""
        from app.mt5 import execution as ex
        import app.mt5.factory as factory
        import app.mt5.mt5_real as real
        from app.db.database import Database

        sent = []

        class _Acct:
            login = 50999999
            trade_mode = 0            # ACCOUNT_TRADE_MODE_DEMO
            is_demo = True            # the bridge's own demo flag agrees
            balance = 10000.0
            equity = 10000.0
            margin_free = 10000.0
            currency = "USD"
            server = "Demo"
            name = "Demo"
            limit_orders = 0
            trade_allowed = True

        class FakeTerm:
            def __init__(self):
                self.result = {"ok": True, "retcode": 10009, "order": 555001,
                               "price": 2400.0, "volume": 0.01, "comment": "done"}
                self.fail_mode = None
            def account_info(self):
                return _Acct()

        class FakeBridge:
            source = "MT5"
            is_simulated = False
            name = "mt5_real"
            _connected = True
            fail_mode = None
            def profile(self):
                return {"source": "MT5", "broker": "Test", "server": "Demo",
                        "profile_key": "MT5|Test|Demo"}
            def account_info(self):
                return _Acct()
            def latest_tick(self, symbol):
                from app.mt5.bridge import Tick
                return Tick(ts=time.time(), bid=2399.5, ask=2400.5, source="MT5")
            def symbol_info(self, symbol):
                from types import SimpleNamespace
                return SimpleNamespace(
                    symbol=symbol, digits=2, point=0.01, trade_contract_size=100.0,
                    volume_min=0.01, volume_max=50.0, volume_step=0.01,
                    stops_level=50, trade_mode_raw=4, trade_allowed=True,
                    visible=True, bid=2399.5, ask=2400.5, source="MT5")
            def send_market_order(self, request):
                # the proven V4.2 handoff contract: ok/retcode/raw/request
                sent.append(dict(request))
                if self.fail_mode == "reject":
                    return {"ok": False, "retcode": 10016, "raw": {"retcode": 10016,
                            "comment": "rejected by broker"}, "request": dict(request)}
                if self.fail_mode == "disconnect":
                    return {"ok": False, "retcode": None,
                            "error": "terminal not available", "request": dict(request)}
                raw = {"retcode": 10009, "order": 555001, "price": 2400.0,
                       "volume": request.get("volume"), "comment": "done",
                       "sl": request.get("sl"), "tp": request.get("tp")}
                return {"ok": True, "retcode": 10009, "raw": raw,
                        "request": dict(request)}

        bridge = FakeBridge()
        term = FakeTerm()
        monkeypatch.setattr(factory, "get_bridge", lambda: bridge)
        monkeypatch.setattr(real, "MT5_PACKAGE_AVAILABLE", True)
        monkeypatch.setattr(ex, "mt5_module", lambda: term)
        db = Database(str(tmp_path / "v70_orders.db"))
        monkeypatch.setattr(ex, "_db", lambda: db)
        return ex, bridge, sent, db

    def _payload(self, **over):
        p = {"symbol": "XAUUSD", "side": "buy", "volume": 0.01,
             "sl": 2390.0, "tp": 2410.0, "confirm": "PLACE_DEMO_ORDER",
             "client_order_id": "v70-" + str(time.time_ns())}
        p.update(over)
        return p

    def test_valid_request_reaches_the_bridge_and_reports_the_result(self, env):
        ex, bridge, sent, db = env
        res = ex.place_demo_order(self._payload(), bridge=bridge)
        assert res.get("ok") is True, res
        # the request is the proven V6.4/MT5 handoff shape (type 0 = BUY market)
        assert sent and sent[0]["symbol"] == "XAUUSD" and sent[0]["type"] == 0
        assert sent[0]["sl"] == 2390.0 and sent[0]["tp"] == 2410.0
        assert sent[0].get("magic") is not None
        # the UI reads the actual result, not a fabricated success
        assert json.dumps(res).find("555001") >= 0

    def test_broker_rejection_is_never_translated_to_success(self, env):
        ex, bridge, sent, db = env
        bridge.fail_mode = "reject"
        res = ex.place_demo_order(self._payload(), bridge=bridge)
        assert res.get("ok") is not True
        assert "reject" in json.dumps(res).lower()

    def test_disconnected_terminal_produces_a_clear_failure(self, env):
        ex, bridge, sent, db = env
        bridge.fail_mode = "disconnect"
        res = ex.place_demo_order(self._payload(), bridge=bridge)
        assert res.get("ok") is not True

    def test_repeated_submission_cannot_duplicate_an_order(self, env):
        ex, bridge, sent, db = env
        cid = "v70-dup-" + str(time.time_ns())
        r1 = ex.place_demo_order(self._payload(client_order_id=cid), bridge=bridge)
        assert r1.get("ok") is True
        with pytest.raises(ex.MT5ExecutionError):
            # the second submission is REFUSED with the duplicate named —
            # a repeated click can never create a second order
            ex.place_demo_order(self._payload(client_order_id=cid), bridge=bridge)
        assert len(sent) == 1

    def test_missing_stop_loss_is_refused(self, env):
        """The documented risk safeguard (require_sl) is enforced on the
        risk-sized path the manual panel uses: sizing an SL-less trade is
        refused before anything is sent. (The V6.4 place semantics are
        preserved exactly; the SL gate lives in the shared risk layer.)"""
        from app.live_testing import risk as risk_mod
        ex, bridge, sent, db = env
        with pytest.raises(Exception) as e:
            risk_mod.compute_risk(node_id=None, strategy_id=None, symbol="XAUUSD",
                                  side="buy", entry=2400.0, sl=None,
                                  equity=10000.0, global_pct=1.0, override_pct=None,
                                  spec=bridge.symbol_info("XAUUSD"),
                                  risk_amount=10.0)
        assert "SL" in str(e.value).upper()
        assert not sent

    def test_manual_trading_never_mutates_research_or_deeptest_data(self, env, tmp_path):
        ex, bridge, sent, db = env
        from app.db.database import Database
        from app.deeptest import store as S
        research = Database(str(tmp_path / "research.db"))
        now = time.time()
        research.x("""INSERT INTO strategies (id, status, symbol, timeframe, generation,
                        fitness, created_at, updated_at, hash, genome, complexity, run_id)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (901, "QUALIFIED", "XAUUSD", "M15", 1, 0.5, now, now, "h901", "{}", 1,
                    "RUN-V70"))
        before = dict(research.one("SELECT * FROM strategies WHERE id=901"))
        S.ensure_schema(research)
        S.insert_scenario(research, {"run_id": "v70sc", "node_id": 901, "status": "COMPLETED",
                                     "signature": "s", "symbol": "XAUUSD", "timeframe": "M15"})
        before_sc = research.one("SELECT COUNT(*) c FROM deeptest_scenarios")["c"]
        res = ex.place_demo_order(self._payload(), bridge=bridge)
        assert res.get("ok") is True
        after = dict(research.one("SELECT * FROM strategies WHERE id=901"))
        assert json.dumps(before, sort_keys=True, default=str) == json.dumps(after, sort_keys=True, default=str)
        assert research.one("SELECT COUNT(*) c FROM deeptest_scenarios")["c"] == before_sc
        research.close()
        db.close()


# --------------------------------------------------------------------------- #
# 7. the endpoint wiring (route → place_demo_order) with the HTTP contract
# --------------------------------------------------------------------------- #

class TestEndpointWiring:
    def test_place_route_forwards_and_maps_failures(self, monkeypatch, tmp_path):
        import app.api.routes as R
        from app.mt5.execution import MT5ExecutionError
        captured = {}

        def fake_place(payload):
            captured["payload"] = payload
            return {"ok": True, "ticket": 7}

        import app.mt5.execution as ex
        monkeypatch.setattr(ex, "place_demo_order", fake_place)
        out = R.mt5_execution_place({"symbol": "XAUUSD", "side": "buy"})
        assert out["ok"] is True and captured["payload"]["symbol"] == "XAUUSD"

        def boom(payload):
            raise MT5ExecutionError("RISK_BLOCKED", "blocked", 422)

        monkeypatch.setattr(ex, "place_demo_order", boom)
        with pytest.raises(Exception):
            R.mt5_execution_place({"symbol": "XAUUSD"})

    def test_live_testing_safeguards_code_untouched(self):
        # V9.1 strengthens the old HEAD-diff check: it must still protect the
        # frozen semantics AFTER commit, while allowing the additive registry
        # schema/config decoder and read-only MT5 history diagnostics.
        import ast
        import subprocess
        baseline = "a717884604fd260024c45b9d77e56d18c11dc3fa"
        forbidden = ("backend/app/mt5/execution.py", "backend/app/mt5/bridge.py",
                     "backend/app/live_testing/", "backend/app/status.py")
        changed = subprocess.check_output(["git", "diff", "--name-only", baseline, "--", *forbidden],
                                          cwd=str(REPO), text=True)
        # V9.2 explicitly requests signed offsets, shared scenario entry gating
        # and log evidence fixes. Preserve the original execution prohibition,
        # and strengthen it with AST equivalence for every other live function.
        from v92_frozen_guard import assert_live_frozen, normalize_config_writer
        requested={'backend/app/live_testing/engine.py','backend/app/live_testing/offsets.py','backend/app/live_testing/schedule.py'}
        assert not (set(changed.splitlines())-requested), f"unintended frozen live/qualification change: {changed}"
        assert_live_frozen()

        def methods(source, class_name):
            klass = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == class_name)
            return {n.name: ast.dump(n, include_attributes=False) for n in klass.body if isinstance(n, ast.FunctionDef)}
        for path, klass, protected in [
            ("backend/app/db/database.py", "Database", ("get_qualified_strategies", "reconstruct_state", "set_live_test_config")),
            ("backend/app/mt5/mt5_real.py", "MT5RealBridge", ("account_info", "check_market_order", "_ensure_order_session", "verify_sent_order", "send_market_order", "positions_get", "orders_get", "close_position", "place_order", "real_market_order")),
        ]:
            original = subprocess.check_output(["git", "show", f"{baseline}:{path}"], cwd=str(REPO), text=True)
            before, after = methods(original, klass), methods(normalize_config_writer((REPO / path).read_text()) if klass == "Database" else (REPO / path).read_text(), klass)
            for method in protected:
                assert before[method] == after[method], f"frozen semantics changed: {klass}.{method}"
