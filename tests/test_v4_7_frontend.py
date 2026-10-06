"""
V4.7 frontend reliability tests.

Two layers:

  * static wiring checks that always run (code splitting is real, every page is
    lazy, every complex page is behind a local error boundary, the safe-render
    helpers are used by the node detail);
  * the jsdom render smoke (frontend/tests/v47_render_smoke.mjs), which renders
    every page and the node detail against hostile data and fails on a crash or
    on any rendered "undefined"/"NaN"/"[object Object]". It needs the frontend
    node_modules (jsdom + esbuild) and is skipped with a clear message when they
    are not installed.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "frontend" / "src"
FRONTEND = REPO / "frontend"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# static wiring
# --------------------------------------------------------------------------- #
def test_pages_are_lazy_loaded_and_awaited_with_suspense():
    app = _read(SRC / "App.jsx")
    assert "const Stats = lazy(() => import(" in app
    assert "const StrategyLab = lazy(() => import(" in app
    assert "const Mt5Backtest = lazy(() => import(" in app
    assert "<Suspense fallback={" in app
    # no page may stay a static top-level import (that is what kept the whole
    # dashboard in one bundle)
    static_page_imports = re.findall(r'^import\s+\w+\s+from\s+"\./pages/', app, re.M)
    assert not static_page_imports, static_page_imports


def test_complex_pages_are_behind_a_local_error_boundary():
    app = _read(SRC / "App.jsx")
    assert "LocalErrorBoundary" in app
    assert "<LocalErrorBoundary key={page}" in app          # the active page
    assert 'label="strategy detail drawer"' in app          # the node drawer
    boundary = _read(SRC / "components" / "LocalErrorBoundary.jsx")
    assert "getDerivedStateFromError" in boundary
    assert "componentDidCatch" in boundary                  # never swallowed silently
    assert "console.error" in boundary                      # evidence for diagnosis
    assert "Retry this section" in boundary


def test_node_detail_uses_the_safe_render_helpers():
    detail = _read(SRC / "components" / "NodeResearchDetail.jsx")
    assert 'from "../lib/safe.js"' in detail
    for helper in ("arr(", "txt(", "objOrNull("):
        assert helper in detail, helper
    # the V4.5 defect this iteration fixes: the component referenced identifiers
    # that were not in scope, so every node render threw
    for stray in ("nodeErr", "nodeBusy"):
        assert stray not in detail, f"{stray} is not defined in this component"
    safe = _read(SRC / "lib" / "safe.js")
    assert "NA_TEXT" in safe and "[object Object]" in safe


def test_formatters_are_total():
    api = _read(SRC / "api.js")
    assert "function finite(" in api
    assert "whenFinite" in api
    # every numeric formatter goes through the guard
    for formatter in ("num:", "pct:", "pnl:", "ratio:", "ret:", "currency:"):
        assert formatter in api


def test_async_operations_are_wired_with_a_fallback():
    logs = _read(SRC / "pages" / "Logs.jsx")
    assert "exportLogsAsync" in logs and "exportLogsResult" in logs
    assert "api.exportLogs()" in logs                        # synchronous fallback kept
    settings = _read(SRC / "pages" / "Settings.jsx")
    assert "backupAsync" in settings and "api.job(" in settings
    assert "api.backup()" in settings                        # synchronous fallback kept
    api_js = _read(SRC / "api.js")
    for helper in ("health:", "ready:", "lifecycle:", "backupAsync:", "job:", "exportLogsAsync:"):
        assert helper in api_js, helper


def test_the_v47_render_crash_sites_stay_fixed():
    """Regression pins for the exact places that threw during V4.7 testing.

    Each of these rendered `undefined` (or threw) against a hostile API payload
    before this iteration; they now coerce through the safe helpers. The full
    render smoke below is the behavioural guarantee - these pins keep the
    specific fixes from silently reverting.
    """
    pins = {
        "pages/Activity.jsx": ["[...arr(events)]"],
        "pages/MarketData.jsx": ["arr(ds?.datasets)", "arr(bars?.bars)"],
        "pages/PaperTrading.jsx": ["arr(promoted)", "arr(st.divergence_flags)",
                                   "arr(risk.recent_rejections)", "arr(trades.trades)", "arr(execs)"],
        "components/HistoricalRunResults.jsx": ["arr(run?.results?.unavailable)", "arr(trades.trades)"],
        "components/BacktestMatrixTable.jsx": ["txt(data.count", "txt(data.scope"],
        "components/DemoOrderPanel.jsx": ['mt5_package_installed === true ? "yes"'],
    }
    for rel, needles in pins.items():
        text = _read(SRC / rel)
        for needle in needles:
            assert needle in text, f"{rel} lost the V4.7 guard: {needle}"


# --------------------------------------------------------------------------- #
# the real render smoke (needs node + jsdom + esbuild)
# --------------------------------------------------------------------------- #
def _have_node_tooling() -> tuple[bool, str]:
    node = shutil.which("node")
    if not node:
        return False, "node is not installed"
    if not (FRONTEND / "node_modules" / "jsdom").exists():
        return False, "frontend/node_modules is missing (run `npm install` in frontend/)"
    if not (FRONTEND / "node_modules" / "esbuild").exists():
        return False, "esbuild is missing from frontend/node_modules"
    return True, ""


def test_pages_and_node_detail_render_with_hostile_data():
    """Every page + the node detail must survive malformed API data (§8/§16)."""
    ok, why = _have_node_tooling()
    if not ok:
        pytest.skip(f"render smoke not run: {why}")

    proc = subprocess.run(
        ["node", "tests/v47_render_smoke.mjs"],
        cwd=str(FRONTEND), capture_output=True, text=True, timeout=600,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, out[-4000:]
    assert "renders OK" in out
    assert "no page or node-detail render crashed" in out
    assert "FAIL" not in out
