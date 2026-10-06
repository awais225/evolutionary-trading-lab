/* V4.7 node-detail / page render smoke harness (jsdom + React DOM).
 *
 * Renders every dashboard page and the shared node detail against deliberately
 * hostile data - nodes with no genome, legacy rows, missing backtests, empty
 * arrays, null columns, numbers where strings belong and objects where scalars
 * belong - and fails if:
 *
 *   * any component throws while rendering (a single bad node must never take
 *     the dashboard down), or
 *   * the rendered text contains "undefined", "NaN" or "[object Object]".
 *
 * The API layer is stubbed at the fetch boundary, so the harness exercises the
 * real components and their real data handling without a backend.
 */
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react-dom/test-utils";

import App, { LabContext } from "../src/App.jsx";
import NodeResearchDetail from "../src/components/NodeResearchDetail.jsx";
import HistoricalRunResults from "../src/components/HistoricalRunResults.jsx";
import HistoricalBacktestPanel from "../src/components/HistoricalBacktestPanel.jsx";
import BacktestMatrixTable from "../src/components/BacktestMatrixTable.jsx";
import Stats from "../src/pages/Stats.jsx";
import StrategyLab from "../src/pages/StrategyLab.jsx";
import BacktestMatrix from "../src/pages/BacktestMatrix.jsx";
import NodeEconomics from "../src/pages/NodeEconomics.jsx";
import Mt5Backtest from "../src/pages/Mt5Backtest.jsx";
import Mt5DemoTrading from "../src/pages/Mt5DemoTrading.jsx";
import LiveTesting from "../src/pages/LiveTesting.jsx";
import LiveTestResults from "../src/pages/LiveTestResults.jsx";
import PaperTrading from "../src/pages/PaperTrading.jsx";
import Population from "../src/pages/Population.jsx";
import Overview from "../src/pages/Overview.jsx";
import Activity from "../src/pages/Activity.jsx";
import Logs from "../src/pages/Logs.jsx";
import Settings from "../src/pages/Settings.jsx";
import MarketData from "../src/pages/MarketData.jsx";
import FinalTesting from "../src/pages/FinalTesting.jsx";

const HOSTILE_NODE = {
  node: {
    id: 1195,
    symbol: "XAUUSD",
    timeframe: "M15",
    direction: "LONG",
    status: "QUALIFIED",
    generation: 12,
    fitness: 0.8123,
    research_node_num: 42,
    run_id: "RUN-20261005-055251",
    parent_id: 1194,
    children: [{ id: 1200 }, { id: 1201 }],
    scope_note: null,
    shortlisted: false,
  },
  research: {
    backtest: { stage: "detail", total_return_pct: 0.4519, profit_factor: 1.609, win_rate: 0.3436, trades: 165, net_profit: -816.59, max_drawdown_pct: 9.78, sharpe: -2.145, sortino: null, expectancy: null },
    validation: { available: true, passed: false, oos: { metrics: { total_return_pct: null } } },
    robustness_score: null,
  },
  economics: {
    risk_per_trade_pct: 0.5,
    sl_atr_multiple: 1.5,
    tp_atr_multiple: { weird: "object-instead-of-number" },
    atr_reference: 2.5,
    reward_risk_ratio: null,
    trailing_stop: { nested: true },
    min_hold_bars: 1,
    max_hold_bars: 96,
    max_concurrent_positions: null,
    trade_stats: { trade_count: 165, win_rate: 0.3436, profit_factor: 1.609, net_profit: -816.59, max_drawdown_pct: 9.78 },
    unavailable: [{ metric: "risk_amount", reason: "risk amount is not stored" }, { reason: "reason without metric" }, null],
    note: null,
  },
  execution: {
    live_test: { total_trades: 0, total_pnl: null, status: null },
    mt5_demo: { total_trades: 0, total_pnl: 0, status: undefined },
    paper: { trades: 0, open: 0, total_pnl: null, records: [{ id: 1, symbol: null, side: "BUY", lots: null, pnl: null, status: null }] },
    mt5_backtest: { available: false },
    note: undefined,
  },
  section_errors: [{ section: "genome", field: "genome", error: "stored value is not valid JSON (JSONDecodeError)" }, null],
};

const EMPTY_NODE = { node: { id: 7, status: "GENERATED" } };
const LEGACY_NODE = { node: { id: 3, status: "LEGACY_TEST", data_source: "LEGACY_TEST" }, research: {}, economics: {}, execution: {} };
const NULLS_NODE = { node: { id: 1 }, research: null, economics: null, execution: null };
const OBJECTS_NODE = {
  node: { id: 2, children: { not: "an array" }, research_node_num: { weird: 1 } },
  research: { backtest: "not-an-object", validation: [] },
  economics: { unavailable: { metric: "x" }, trade_stats: 5 },
  execution: { paper: { records: "not-an-array" } },
};

const PAGES = [
  ["Overview", Overview], ["Stats", Stats], ["StrategyLab", StrategyLab],
  ["BacktestMatrix", BacktestMatrix], ["NodeEconomics", NodeEconomics], ["Mt5Backtest", Mt5Backtest],
  ["Mt5DemoTrading", Mt5DemoTrading], ["LiveTesting", LiveTesting], ["LiveTestResults", LiveTestResults],
  ["PaperTrading", PaperTrading], ["Population", Population], ["Activity", Activity],
  ["Logs", Logs], ["Settings", Settings], ["MarketData", MarketData], ["FinalTesting", FinalTesting],
];

const LAB_VALUE = {
  status: {
    app_version: "V4.7", lab: { running: false, mode: "idle", generation: 39, population_active: 10000 },
    bridge: { connected: false, is_simulated: true, active_bridge: "SIMULATOR" },
    connection: { internet: "ONLINE", mt5: "SIMULATOR", data_feed: "SIMULATED" },
  },
  events: [],
  connected: false,
  refreshStatus: () => {},
  openStrategy: () => {},
  selectedStrategyId: 1195,
  setSelectedStrategyId: () => {},
  shortlist: [],
  toggleShortlist: async () => ({}),
  navigateTab: () => {},
};

/** Data the stubbed API returns: valid shapes, hostile values. */
export function payloadFor(path) {
  if (path.includes("/api/strategies/")) return HOSTILE_NODE;
  if (path.includes("/api/stats/node")) return HOSTILE_NODE;
  if (path.includes("/api/stats/")) return { scope: "USER_RESEARCH", population: { total: 10000, legacy_test: 787 }, generations: [], top: [], nodes: [] };
  if (path.includes("/api/research/facets")) return { scope: "USER_RESEARCH", population: { total: 10000 }, generations: [], statuses: [], symbols: [], timeframes: [], sorts: [] };
  if (path.includes("/api/research/strategies")) return { nodes: [HOSTILE_NODE, EMPTY_NODE, LEGACY_NODE], total: 3, population: { total: 10000 } };
  if (path.includes("/api/research/matrix")) return { rows: [{ node_id: 1195, identity: {}, research: {}, economics: {} }], population: { total: 2 }, orders_placed: false };
  if (path.includes("/api/research/compare")) return { strategies: [HOSTILE_NODE], unavailable: [] };
  if (path.includes("/api/mt5-historical/capabilities")) return {
    label: "HISTORICAL MT5 BACKTEST", datasets: [{ symbol: "XAUUSD", timeframe: "M15", source: "MT5", dataset_id: "XAUUSD_M15_MT5_Raw Trading Ltd_ICMarketsSC-Demo_v1", bars: 1850, start: "2026-09-07T01:00:00Z", end: "2026-10-05T05:45:00Z", broker: "Raw Trading Ltd", server: "ICMarketsSC-Demo", eligible: true }],
    rejected_datasets: [{ symbol: "XAUUSD", timeframe: "M1", source: "SIMULATOR", reason: "Feature artifact missing on disk" }],
    limits: { min_bars: 300, max_queue: 5, one_at_a_time: true }, modes: { places_orders: false, order_execution_path: "never" }, scope: { default: "MT5", options: ["MT5", "SIMULATOR"] }, bridge: {},
  };
  if (path.includes("/api/mt5-historical/runs/")) return {
    run_id: "HRUN-20261006-155357-0AC891", status: "COMPLETED", strategy_id: 1195, symbol: "XAUUSD", timeframe: "M15",
    period: { start: "2026-09-07T01:00:00Z", end: "2026-10-05T05:45:00Z", bars: 1650, requested_start: null, requested_end: null, adjusted: false },
    data: { source: "MT5", dataset_id: "XAUUSD_M15_MT5_Raw Trading Ltd_ICMarketsSC-Demo_v1", broker: "Raw Trading Ltd" },
    results: { metrics: { trades: 56, net_profit: -978.2, profit_factor: 0.492, win_rate: 0.2321, max_drawdown_pct: 10.15, sharpe: -7.138, sortino: null, total_return_pct: -9.78, final_equity: 9021.8 }, derived: { largest_win: 165.21, source: "derived from the persisted trade list" }, unavailable: [{ metric: "recovery_factor", reason: "not produced by the engine" }, null] },
    provenance: { market_data: { dataset_source: "MT5", broker: "Raw Trading Ltd", file: { sha256: "8d4d0cb6" } }, engine_versions: { app: "V4.7" }, orders: { placed: false } },
    orders_placed: false, is_mt5_data: true,
  };
  if (path.includes("/api/mt5-historical/runs")) return { runs: [{ run_id: "HRUN-1", status: "COMPLETED", period: { start: "2026-09-07", end: "2026-10-05" }, data: { source: "MT5" }, trade_count: 56, results: { metrics: { net_profit: 1, profit_factor: 1 } } }], total: 1 };
  if (path.includes("/api/logs")) return { logs: [{ ts: "", level: null, module: null, message: null }], log_text: "" };
  if (path.includes("/api/jobs")) return { jobs: [] };
  if (path.includes("/api/shortlist")) return { shortlist: [] };
  if (path.includes("/api/lab/status")) return { running: false, mode: "idle" };
  return {};
}

export async function runSmoke() {
  const results = [];
  const container = document.createElement("div");
  document.body.appendChild(container);

  const render = async (label, element) => {
    const root = createRoot(container);
    try {
      await act(async () => {
        root.render(element);
        await Promise.resolve();
      });
      const text = container.textContent || "";
      const bad = ["undefined", "NaN", "[object Object]"].filter((token) => text.includes(token));
      if (bad.length) {
        const first = bad[0];
        const at = text.indexOf(first);
        const context = text.slice(Math.max(0, at - 90), at + 60).replace(/\s+/g, " ");
        results.push({ label, ok: false, error: `rendered text contains ${bad.join(", ")} … "${context}"` });
      } else {
        results.push({ label, ok: true });
      }
    } catch (e) {
      results.push({ label, ok: false, error: `${e && e.name}: ${e && e.message}` });
    } finally {
      await act(async () => { root.unmount(); });
      container.innerHTML = "";
    }
  };

  // 1. the whole dashboard shell
  await render("App (shell + overview)", <App />);

  // 2. every page inside the real context
  for (const [name, Page] of PAGES) {
    await render(`page:${name}`, (
      <LabContext.Provider value={LAB_VALUE}>
        <Page />
      </LabContext.Provider>
    ));
  }

  // 3. the shared node detail against hostile nodes
  for (const [label, fixture] of [["full", HOSTILE_NODE], ["empty", EMPTY_NODE], ["legacy", LEGACY_NODE],
                                  ["nulls", NULLS_NODE], ["objects", OBJECTS_NODE], ["nothing", null]]) {
    await render(`NodeResearchDetail:${label}`, (
      <NodeResearchDetail node={fixture} busy={false} onOpenStrategy={() => {}} emptyHint="pick a node" />
    ));
  }
  await render("NodeResearchDetail:busy", <NodeResearchDetail node={HOSTILE_NODE} busy />);
  await render("NodeResearchDetail:error", <NodeResearchDetail node={null} error="node 99999 not found" />);
  await render("NodeResearchDetail:no-id", <NodeResearchDetail node={{ node: {} }} busy={false} />);

  // 4. the V4.6 panels with hostile payloads
  await render("HistoricalRunResults:full", (
    <HistoricalRunResults runId="HRUN-20261006-155357-0AC891" onClose={() => {}} />
  ));
  await render("HistoricalBacktestPanel:no-strategy", <HistoricalBacktestPanel strategyId={null} />);
  await render("HistoricalBacktestPanel:node", <HistoricalBacktestPanel strategyId={1195} />);
  await render("BacktestMatrixTable:empty", <BacktestMatrixTable rows={[]} />);
  await render("BacktestMatrixTable:hostile", <BacktestMatrixTable rows={[{ node_id: 1, identity: null, research: "x", economics: null }]} />);

  const failures = results.filter((r) => !r.ok);
  return { total: results.length, failures, results };
}
