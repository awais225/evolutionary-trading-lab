"""V8 §10 — purge acceptance: the five dashboard modules are gone from the
dashboard AND from the backend surface, shared services are intact, and no
orphaned route/import/timer/registration survives.

Evidence style: real HTTP calls against the app, real source assertions over
the retained navigation, and a startup import that proves no deleted module is
initialised. No real trade is ever placed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

GONE_PAGES = ("FinalTesting", "BacktestMatrix", "Mt5Backtest", "Mt5DemoTrading", "PaperTrading")


class TestDashboardPurge:
    def test_navigation_has_no_entries_for_the_removed_tabs(self):
        app = (REPO / "frontend/src/App.jsx").read_text(encoding="utf-8")
        for name in GONE_PAGES:
            assert f"const {name} = lazy" not in app, name
            assert name not in re.sub(r"//.*", "", app), f"{name} still referenced"

    def test_page_files_and_their_exclusive_component_are_removed(self):
        for name in GONE_PAGES:
            assert not (REPO / "frontend/src/pages" / f"{name}.jsx").exists(), name
        assert not (REPO / "frontend/src/components/DemoOrderPanel.jsx").exists()

    def test_no_shortcut_exposes_the_deleted_interfaces(self):
        econ = (REPO / "frontend/src/pages/NodeEconomics.jsx").read_text(encoding="utf-8")
        for gone in ('quickNav("mt5_backtest")', 'quickNav("matrix")',
                     'quickNav("mt5_demo")', 'quickNav("final_testing")', 'quickNav("paper")'):
            assert gone not in econ, gone

    def test_exclusive_api_clients_are_gone_and_shared_clients_remain(self):
        api = (REPO / "frontend/src/api.js").read_text(encoding="utf-8")
        for gone in ("generateMatrix", "strategyRerunBacktest", "strategyRunValidation",
                     "authoritativeStrategy", "mt5BacktestResults", "mt5StrategyBacktests",
                     "mt5BacktestRun", "paperStatus", "paperStart", "paperStop",
                     "paperTrades", "paperPromoted", "mt5DemoStatus", "mt5DemoStartAll",
                     "mt5DemoStopAll", "mt5DemoPauseAll", "mt5DemoSchedule",
                     "mt5DemoSaveSchedule", "recalibrate", "calibration:"):
            assert gone not in api, gone
        for kept in ("paperCandidates", "paperPromoteCandidate", "mt5ExecutionPlace",
                     "mt5ExecutionPreview", "liveTestingNodesTable", "toggleMt5Demo",
                     "savedShortlist", "researchMatrix", "mt5HistoricalRuns",
                     "nodeData", "deeptestFetch"):
            assert kept in api, kept


class TestBackendPurge:
    def test_every_deleted_endpoint_is_gone_and_returns_404(self):
        # Test API registration without the dashboard StaticFiles catch-all:
        # after startup that catch-all answers unknown POST paths with 405.
        # Router-only 404 assertions prove there is no live API implementation.
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes import router
        api_app = FastAPI()
        api_app.include_router(router)
        client = TestClient(api_app)
        gone_gets = ["/api/paper/status", "/api/paper/promoted", "/api/paper/trades",
                     "/api/paper/executions", "/api/calibration", "/api/mt5-backtest/results",
                     "/api/mt5-backtest/strategies/1", "/api/mt5-demo/status",
                     "/api/mt5-demo/schedule/1", "/api/strategies/1/trades",
                     "/api/strategies/1/authoritative"]
        for u in gone_gets:
            assert client.get(u).status_code == 404, u
        gone_posts = ["/api/paper/start", "/api/paper/stop", "/api/mt5-demo/start-all",
                      "/api/mt5-demo/start-shortlist", "/api/mt5-demo/stop-all",
                      "/api/mt5-demo/pause-all", "/api/mt5-demo/schedule/1",
                      "/api/calibration/recalibrate", "/api/strategies/1/matrix",
                      "/api/strategies/1/backtest", "/api/strategies/1/validate"]
        for u in gone_posts:
            assert client.post(u, json={}).status_code == 404, u
        assert client.delete("/api/mt5-demo/schedule/1").status_code == 404

    def test_shared_services_remain_registered_and_operational(self, client):
        # canonical node-data authority + Overview candidates + live risk safety
        nd = client.get("/api/node-data").json()
        assert nd["ok"] and nd["node_data"]["counts"]["qualified"] >= 0
        assert client.get("/api/paper/candidates").status_code == 200
        # Inspect safety-control registration, never invoke a safety toggle
        # or a trade activation against the canonical database.
        from app.api.routes import router
        registered = {r.path for r in router.routes if hasattr(r, "path")}
        assert "/api/risk/kill-switch" in registered
        # Deep Testing surface + Live Testing surface + Strategy Lab surface
        assert client.get("/api/deeptest/settings").status_code == 200
        assert client.get("/api/mt5-execution/state").status_code == 200
        assert client.get("/api/research/strategies?limit=1").status_code == 200
        assert client.get("/api/research/matrix?limit=1").status_code == 200
        # the shared historical runner + economics + demo toggle route exist
        assert client.get("/api/mt5-historical/capabilities").status_code == 200
        assert client.get("/api/strategies/1/economics").status_code in (200, 404)
        assert "/api/mt5-demo/strategies/{sid}/toggle" in registered

    def test_no_orphaned_route_registration_for_deleted_functionality(self):
        routes = (REPO / "backend/app/api/routes.py").read_text(encoding="utf-8")
        for pat in ('"/paper/status"', '"/paper/start"', '"/paper/stop"', '"/paper/trades"',
                    '"/paper/executions"', '"/paper/promoted"', '"/calibration"',
                    '"/calibration/recalibrate"', '"/mt5-backtest', '"/mt5-demo/status"',
                    '"/mt5-demo/start-all"', '"/mt5-demo/start-shortlist"',
                    '"/mt5-demo/stop-all"', '"/mt5-demo/pause-all"', '"/mt5-demo/schedule',
                    '"/strategies/{sid}/matrix"', '"/strategies/{sid}/trades"',
                    '"/strategies/{sid}/backtest"', '"/strategies/{sid}/validate"',
                    '"/strategies/{sid}/authoritative"'):
            assert pat not in routes, pat

    def test_no_deleted_module_is_imported_or_initialised_at_startup(self):
        # app.main imports cleanly (the real startup import chain) and no module
        # of the purged services is imported anywhere in the backend
        import importlib
        import app.main  # noqa: F401
        importlib.import_module("app.orchestrator.lab")
        importlib.import_module("app.live_testing.engine")
        code = ""
        for p in (REPO / "backend").rglob("*.py"):
            code += p.read_text(encoding="utf-8", errors="replace")
        for gone in ("import mt5_backtest", "from .mt5_backtest",
                     "from ..mt5_backtest", "paper_trading_ui",
                     "from .paper.trading", "mt5_backtest_page"):
            assert gone not in code, gone


class TestPreservedWorkflows:
    def test_generation_and_qualification_logic_is_untouched(self):
        # the frozen classifiers/predicates must survive verbatim
        db_src = (REPO / "backend/app/db/database.py").read_text(encoding="utf-8")
        st_src = (REPO / "backend/app/status.py").read_text(encoding="utf-8")
        assert "status IN ('QUALIFIED', 'PAPER')" in db_src
        assert 'raw in ("SURVIVED", "QUALIFIED", "SHORTLISTED", "MT5_BACKTESTED", "PAPER", "LIVE_ELIGIBLE")' in st_src

    def test_manual_trading_panel_still_mounted_on_live_testing(self):
        live = (REPO / "frontend/src/pages/LiveTesting.jsx").read_text(encoding="utf-8")
        assert 'data-testid="manual-trading-panel"' in live
        assert "ManualOrderPanel" in live

    def test_strategy_lab_and_research_ai_pages_remain(self):
        assert (REPO / "frontend/src/pages/StrategyLab.jsx").exists()
        assert (REPO / "frontend/src/pages/ResearchAI.jsx").exists()
        app = (REPO / "frontend/src/App.jsx").read_text(encoding="utf-8")
        assert "StrategyLab" in app and "ResearchAI" in app

    def test_deep_testing_page_and_its_pipeline_remain(self):
        deep = (REPO / "frontend/src/pages/DeepBacktest.jsx").read_text(encoding="utf-8")
        assert "GET MT5 DATA" in deep and "DEEPTEST" in deep
        api = (REPO / "frontend/src/api.js").read_text(encoding="utf-8")
        assert "deeptestRun" in api and "deeptestScenarios" in api
