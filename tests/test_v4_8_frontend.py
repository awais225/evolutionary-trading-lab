"""V4.8 dashboard-UI frontend tests.

Two layers:

  * static wiring that always runs — the cross-page navigation the V4.8 dashboard
    promises (node selection carried into MT5 Backtest / Live Testing / Backtest
    Matrix), the safety wording the spec requires, no placeholder buttons, and no
    interactive control without a handler;
  * the jsdom smoke harness (frontend/tests/v48_ui_smoke.mjs) which renders the
    new/rewritten V4.8 components against hostile payloads, drives the manual
    order panel through preview -> confirmation -> broker result, and fails on a
    crash, on any "undefined"/"NaN"/"[object Object]" output, or when a required
    safety statement is missing from the screen. It needs the frontend
    node_modules and is skipped with a clear message when they are absent.
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

V48_FILES = [
    "components/ui.jsx",
    "components/PipelinePath.jsx",
    "components/DatasetAvailability.jsx",
    "components/PopulationSummary.jsx",
    "components/LiveTradeCounter.jsx",
    "components/DemoAccountSafety.jsx",
    "components/LiveTestingPanels.jsx",
    "components/NodeDetailDrawer.jsx",
    "components/NewResearchRunModal.jsx",
    "pages/NodeEconomics.jsx",
    "pages/StrategyLab.jsx",
    "pages/LiveTesting.jsx",
    "pages/Mt5DemoTrading.jsx",
    "pages/Mt5Backtest.jsx",
    "pages/FinalTesting.jsx",
    "pages/PaperTrading.jsx",
    "pages/MarketData.jsx",
    "pages/Population.jsx",
    "pages/Activity.jsx",
    "pages/Logs.jsx",
]


def _read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# the V4.8 files exist and are wired into the router
# --------------------------------------------------------------------------- #
def test_v48_components_exist():
    for rel in V48_FILES:
        assert (SRC / rel).exists(), f"missing V4.8 file: {rel}"


def test_quick_navigation_targets_are_real_tab_keys():
    """Node Economics' quick nav must point at tab keys that actually exist."""
    app = (SRC / "App.jsx").read_text(encoding="utf-8", errors="replace")
    keys = set(re.findall(r'\["([a-z0-9_]+)", "\d+",', app))
    assert len(keys) >= 18, keys
    for target in ("mt5_backtest", "live_test", "matrix", "economics", "lab", "population",
                   "final_testing", "mt5_demo"):
        assert target in keys, f"{target} is not a real tab key"


def test_node_economics_carries_the_selected_node_across_pages():
    econ = _read("pages/NodeEconomics.jsx")
    assert "useLab" in econ
    assert 'quickNav("mt5_backtest"' in econ or "quickNav('mt5_backtest'" in econ
    assert 'quickNav("live_test"' in econ or "quickNav('live_test'" in econ
    assert 'quickNav("matrix"' in econ or "quickNav('matrix'" in econ
    # the node travels with the navigation (second argument = strategy id)
    assert re.search(r"navigateTab\(\s*\w+\s*,\s*", econ), "the quick nav must pass the node id"


def test_live_testing_mounts_the_v48_panels_and_starts_idle():
    page = _read("pages/LiveTesting.jsx")
    for needle in ("RiskStrip", "ManualOrderPanel", "LiveMarketPanel", "StageTimeline",
                   "LiveTradeCounter"):
        assert needle in page, f"Live Testing is missing {needle}"
    assert "IDLE ON ENTRY" in page
    # nothing may auto-start the engine on mount
    assert "liveTestingActivate(" not in page.split("const handleStartAll")[0]


def test_mt5_backtest_states_that_the_real_terminal_is_unavailable():
    page = _read("pages/Mt5Backtest.jsx")
    assert "REAL MT5 TRADING UNAVAILABLE" in page
    assert "SIMULATOR MODE ACTIVE" in page
    assert "SimulatorBanner" in page


def test_demo_page_states_demo_only_and_removed_fake_account_fallbacks():
    page = _read("pages/Mt5DemoTrading.jsx")
    assert "DemoAccountSafety" in page
    assert "DEMO ACCOUNT ONLY" in page
    # the old hard-coded demo account/balance fallbacks must be gone
    for fake in ("DEMO-100294", "MetaQuotes-Demo"):
        assert fake not in page, f"fabricated account default still present: {fake}"
    assert "demoStatus?.balance || 10000" not in page


def test_manual_order_panel_states_amount_is_risk_not_margin():
    panel = _read("components/LiveTestingPanels.jsx")
    assert "money at risk" in panel
    assert "rounded" in panel and "down" in panel
    # the confirmation phrase comes from the backend, not from a local literal copy
    assert "confirmation_phrase" in panel
    assert "PLACE_DEMO_ORDER" in panel            # only as the last-resort default
    assert "ConfirmModal" in panel


def test_every_v48_page_handles_loading_empty_error_and_unavailable():
    """Each page touched by V4.8 must show loading, error and empty states."""
    for rel in ("pages/NodeEconomics.jsx", "pages/StrategyLab.jsx", "pages/Mt5Backtest.jsx",
                "pages/LiveTesting.jsx", "pages/Mt5DemoTrading.jsx", "pages/FinalTesting.jsx",
                "pages/Population.jsx", "pages/MarketData.jsx"):
        text = _read(rel)
        has_states = ("StateBlock" in text or "Spinner" in text or "loading" in text.lower())
        assert has_states, f"{rel} has no loading/empty/error handling"
        has_error = ("error" in text.lower() or "Erreur" in text)
        assert has_error, f"{rel} never surfaces an error"


def test_no_placeholder_buttons_anywhere_in_the_dashboard():
    bad = re.compile(r"(coming soon|not implemented|todo:|placeholder button|dummy button)", re.I)
    offenders = []
    for path in SRC.rglob("*.jsx"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in bad.finditer(text):
            line = text[:m.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(SRC)}:{line}: {m.group(0)}")
    assert not offenders, f"placeholder UI text found: {offenders}"


def test_interactive_buttons_have_a_handler_or_a_disabled_guard():
    """Every button in the V4.8 files must act or be explicitly disabled."""
    # destructive/plain buttons only; a button may be disabled by an expression,
    # so the guard is simply "has onClick or disabled somewhere in the tag".
    tag = re.compile(r"<button\b(.*?)(?:/>|>)", re.S)
    offenders = []
    for rel in V48_FILES:
        text = _read(rel)
        for m in tag.finditer(text):
            body = m.group(1)
            if "onClick" in body or "disabled" in body:
                continue
            line = text[:m.start()].count("\n") + 1
            offenders.append(f"{rel}:{line}")
    assert not offenders, f"buttons without an action or a disabled guard: {offenders}"


def test_destructive_actions_ask_for_confirmation():
    """§1 population reset and the manual demo order must be confirmed first."""
    modal = _read("components/NewResearchRunModal.jsx")
    # §1 requires the operator to type the exact current node count before the
    # destructive choices can run, and the confirm button stays disabled until
    # the typed value matches.
    assert "confirmMatches" in modal
    assert "Type the exact current node count" in modal
    assert "disabled={!canRun(option)}" in modal          # confirm button gated by the typed count
    assert "typed && !confirmMatches" in modal            # and says why it is disabled
    # the backend's own confirmation tokens are used, not invented ones
    assert "BACKUP_AND_RESET" in modal and "RESET_USER_RESEARCH" in modal
    assert "researchBlockReason" in modal              # plain-English blocked banner
    # the shared confirmation dialog is used for the irreversible manual order
    assert "ConfirmModal" in _read("components/LiveTestingPanels.jsx")
    settings = _read("pages/Settings.jsx")
    assert "clearModal" in settings and "CLEAR ALL DATA" in settings


def test_readable_dead_reasons_are_rendered():
    page = _read("pages/FinalTesting.jsx")
    assert "dead_reason" in page
    assert "reason not recorded by the engine" in page
    assert "colSpan={17}" in page                       # the extra reason column exists
    assert "pageOffset" in page and "total_matching" in page   # server-side paging


def test_market_data_distinguishes_row_from_physical_file():
    comp = _read("components/DatasetAvailability.jsx")
    assert "Physical availability" in comp
    assert "no physical file on disk" in comp
    assert "SIMULATED" in comp
    page = _read("pages/MarketData.jsx")
    assert "DatasetAvailability" in page


def test_population_and_tree_never_hide_dead_nodes():
    pop = _read("components/PopulationSummary.jsx")
    assert "dead nodes are shown here by design" in pop.lower()
    tree = _read("pages/EvolutionTree.jsx")
    assert "DEAD_STATUSES" in tree
    # no dead-hiding switch may be introduced in the tree
    assert "hideDead" not in tree


def test_logs_group_by_stage_and_filter_by_node():
    logs = _read("pages/Logs.jsx")
    for group in ("Research", "Backtest", "Validation", "MT5", "Live", "Demo", "System"):
        assert f'"{group}"' in logs, f"Logs is missing the {group} group"
    assert "nodeFilter" in logs
    assert "StructuredError" in _read("pages/Logs.jsx")


# --------------------------------------------------------------------------- #
# the render + interaction smoke
# --------------------------------------------------------------------------- #
def _have_node_tooling() -> tuple[bool, str]:
    if not shutil.which("node"):
        return False, "node is not installed"
    if not (FRONTEND / "node_modules" / "jsdom").exists():
        return False, "frontend/node_modules is missing (run `npm install` in frontend/)"
    if not (FRONTEND / "node_modules" / "esbuild").exists():
        return False, "esbuild is missing from frontend/node_modules"
    return True, ""


def test_v48_ui_renders_and_the_order_flow_is_real():
    ok, why = _have_node_tooling()
    if not ok:
        pytest.skip(f"V4.8 UI smoke not run: {why}")

    proc = subprocess.run(["node", "tests/v48_ui_smoke.mjs"], cwd=str(FRONTEND),
                          capture_output=True, text=True, timeout=600)
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, out[-4000:]
    assert "FAIL" not in out, out[-4000:]
    assert "no page or node-detail render crashed" in out
    assert "renders OK" in out


def test_v47_render_smoke_still_passes():
    """The V4.7 baseline (every page + the node detail) must keep passing."""
    ok, why = _have_node_tooling()
    if not ok:
        pytest.skip(f"render smoke not run: {why}")

    proc = subprocess.run(["node", "tests/v47_render_smoke.mjs"], cwd=str(FRONTEND),
                          capture_output=True, text=True, timeout=600)
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, out[-4000:]
    assert "FAIL" not in out, out[-4000:]
    # V5 added a page (Deep Backtest): the harness prints the live count, so assert
    # the line's shape and that every rendered page succeeded.
    import re as _re
    m = _re.search(r"(\d+)/(\d+) renders OK", out)
    assert m and m.group(1) == m.group(2), out[-2000:]
    assert int(m.group(1)) >= 39, out[-2000:]


# --------------------------------------------------------------------------- #
# interaction-QA regressions (V4.8 phase 2)
#
# Every test below pins a defect found while driving the real dashboard in a
# browser, so the behaviour that was fixed cannot silently come back:
#   * a status probe that cannot be read was reported as a failed backend;
#   * a non-JSON 2xx response surfaced as "Unexpected token '<'";
#   * the dev proxy did not forward the root /health endpoint the app probes;
#   * a no-node-id lookup was a silent no-op that left stale data on screen;
#   * revealing a dead node hid the stored failure reason;
#   * the raw payload view dumped the entire response into the page;
#   * "Resume" was offered on an empty study although the backend refuses it.
# --------------------------------------------------------------------------- #
def test_startup_banner_never_calls_a_failed_probe_a_failed_backend():
    src = _read("components/StartupBanner.jsx")
    assert "Backend status could not be read" in src
    assert "retry now" in src
    # the unreachable branch must be evaluated before the startup-failure banner
    assert src.index("Backend status could not be read") < src.index("Backend startup failed")
    assert "setUnreachable" in src and "last attempt" in src


def test_api_client_reports_a_non_json_response_clearly():
    src = _read("api.js")
    assert "did not reach the API" in src
    assert "contentType" in src
    assert "parseError" in src
    # the raw parse must be guarded so a SyntaxError never reaches the UI
    i = src.index("return await res.json();")
    assert "try {" in src[max(0, i - 400):i]
    assert "} catch (parseErr) {" in src[i:]


def test_dev_server_proxy_forwards_the_root_health_endpoint():
    cfg = (FRONTEND / "vite.config.js").read_text(encoding="utf-8")
    assert '"/health"' in cfg                     # the endpoint api.health() calls
    assert '"/api"' in cfg and '"/ws"' in cfg
    assert 'target: "http://127.0.0.1:8787"' in cfg


def test_node_lookup_refuses_input_without_a_node_id():
    src = _read("pages/NodeEconomics.jsx")
    assert "does not contain a node id" in src
    assert "last successfully loaded node" in src
    # the lookup must not silently keep the previously loaded node
    assert "if (n !== null) { setSid(n); setShowDead(false); }" not in src


def test_revealed_dead_node_keeps_the_stored_reason():
    src = _read("pages/NodeEconomics.jsx")
    blocked = src.index("Dead node hidden")
    revealed = src.index("DEAD NODE —")
    assert src.index("Reason on record", blocked) < revealed      # shown while blocked
    assert src.index("Reason on record", revealed) > revealed     # and after revealing
    assert "survival_reason || node?.creation_reason" in src


def test_raw_payload_view_is_collapsed_and_sized():
    src = _read("components/common.jsx")
    assert "hidden so it does not flood the page" in src
    assert "show raw JSON" in src and "defaultOpen = false" in src
    assert "could not be serialised" in src                      # hosts a cyclic payload safely


def test_resume_option_is_blocked_without_an_active_population():
    src = _read("components/NewResearchRunModal.jsx")
    assert "no active USER_RESEARCH population to resume" in src
    assert "optionBlocked" in src and "Not available" in src
    assert "addValid && !noPopulation" in src
