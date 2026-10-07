/* V4.8 dashboard-UI smoke harness (jsdom + React DOM).
 *
 * Renders the components introduced/rewritten by V4.8 against deliberately
 * hostile API payloads and asserts three things:
 *
 *   * nothing crashes (§ global UX: "no crashes from malformed payloads");
 *   * nothing renders "undefined" / "NaN" / "[object Object]";
 *   * the safety wording the spec requires is actually on screen — the manual
 *     order panel states that Amount is money at risk (not margin), the demo
 *     page says DEMO ACCOUNT ONLY, the MT5 backtest page says the real terminal
 *     is unavailable, and the live-testing page says everything is idle until
 *     the operator starts it.
 *
 * The API layer is stubbed at the fetch boundary, so the real components and
 * their real data handling are exercised without a backend.
 */
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react-dom/test-utils";

import { LabContext } from "../src/App.jsx";
import {
  Badge, Card, ConfirmModal, EmptyState, Field, Kpi, Progress, SourceChip,
  StageTrack, Star, StateBlock, SimulatorBanner, Val, fmtId, parseNodeId,
} from "../src/components/ui.jsx";
import PipelinePath from "../src/components/PipelinePath.jsx";
import DatasetAvailability from "../src/components/DatasetAvailability.jsx";
import PopulationSummary from "../src/components/PopulationSummary.jsx";
import LiveTradeCounter from "../src/components/LiveTradeCounter.jsx";
import DemoAccountSafety from "../src/components/DemoAccountSafety.jsx";
import { RiskStrip, ManualOrderPanel, LiveMarketPanel, StageTimeline, LiveNodeTable,
         LiveMarketHeader, ScheduleDialog } from "../src/components/LiveTestingPanels.jsx";
import PowerButton from "../src/components/PowerButton.jsx";
import { TradingInfoTab } from "../src/components/StrategyDrawer.jsx";
import Mt5Backtest from "../src/pages/Mt5Backtest.jsx";
import LiveTestResults from "../src/pages/LiveTestResults.jsx";
import NewResearchRunModal from "../src/components/NewResearchRunModal.jsx";
import NodeDetailDrawer from "../src/components/NodeDetailDrawer.jsx";
import LiveTesting from "../src/pages/LiveTesting.jsx";
import Mt5DemoTrading from "../src/pages/Mt5DemoTrading.jsx";
import NodeEconomics from "../src/pages/NodeEconomics.jsx";
import StartupBanner from "../src/components/StartupBanner.jsx";
import { JsonView } from "../src/components/common.jsx";
import FinalTesting from "../src/pages/FinalTesting.jsx";

const tabCalls = [];
const LAB_VALUE = {
  status: {
    app_version: "V4.8",
    lab: { running: false, mode: "idle", generation: 39, population_active: 10000 },
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
  navigateTab: (page, sid) => { tabCalls.push([page, sid ?? null]); },
};

const wrap = (element) => <LabContext.Provider value={LAB_VALUE}>{element}</LabContext.Provider>;

/* ------------------------------------------------------------------ payloads */
const EXEC_STATE = {
  execution_allowed: false,
  blocked_code: "MT5_UNAVAILABLE",
  blocked_reason: "Order execution blocked: the active market bridge is 'SIMULATOR', not a real MetaTrader 5 terminal.",
  confirmation_phrase: "PLACE_DEMO_ORDER",
  kill_switch_engaged: false,
  bridge: { name: "simulator", source: "SIMULATOR", is_simulated: true, connected: true },
  account_safety: {
    bridge: "simulator", source: "SIMULATOR", is_simulated: true, connected: true,
    demo_verified: false, blocked_code: "MT5_UNAVAILABLE",
    blocked_reason: "Demo order execution requires a connected MT5 terminal.",
    account: null, trade_allowed: null, mt5_package_installed: false,
  },
  symbol: { symbol: "XAUUSD", digits: 2, point: 0.01, volume_min: null, volume_max: null, volume_step: null, source: "SIMULATOR" },
  quote: null,
};

const PREVIEW_OK = {
  ok: true, mode: "risk_to_lot",
  sizing: {
    symbol: "XAUUSD", side: "BUY", entry: 2450.0, sl: 2405.0, stop_distance: 45.0,
    risk_amount: 300.0, tick_size: 0.01, tick_value: 1.0, volume_min: 0.01,
    volume_max: 100.0, volume_step: 0.01, risk_per_lot: 4500.0, raw_volume: 0.0667,
    volume: 0.06, actual_risk: 270.0, digits: 2,
  },
  blocked: null, symbol_info: { symbol: "XAUUSD", digits: 2 },
  quote: { bid: 2400.0, ask: 2400.3 },
  risk_limits: { risk_pct_default: 1.0, risk_pct_max: 5.0, max_active_trades: 1, require_sl: true },
  orders_placed: false, read_only: true, note: "preview only",
};

const PREVIEW_BLOCKED = {
  ok: false, mode: "risk_to_lot", sizing: null,
  blocked: {
    code: "VOLUME_BELOW_MINIMUM",
    message: "Risk 10.00 needs 0.0022 lots below the broker minimum 0.1 lots for XAUUSD",
    detail: { raw_volume: 0.0022, volume_min: 0.1 },
  },
  symbol_info: { symbol: "XAUUSD", volume_min: 0.1, volume_step: 0.01 },
  quote: null, orders_placed: false, read_only: true,
};

const MARKET = {
  symbol: "XAUUSD", source: "SIMULATOR", bid: 2400.0, ask: 2400.3, spread: 0.3,
  session_status: "newyork", trading_available: false,
  reasons: ["simulator feed: no live ticks"], tick_age_s: 12.5, market_open: false,
  indicators: null, bar_close_in: null,
};

const MARKET_HEADER = {
    available: true, reasons: [], reason: null, symbol: "XAUUSD", timeframe: "M15",
    market: { symbol: "XAUUSD", bid: 2400.1, ask: 2400.3, spread: 0.2, price: 2400.2,
              time: "2026-10-07T05:42:00+00:00", tick_age_s: 1.2, session_status: "london",
              source: "MT5", bridge_available: true, connected: true, data_fresh: true },
    node: { strategy_id: 1195, symbol: "XAUUSD", timeframe: "M15", direction: "both", status: "QUALIFIED",
            v5_status: "VALID", risk_pct: null, node_id: 1195 },
    indicators: [{ spec: "ema:50", value: 2399.4, error: null }, { spec: "rsi:14", value: 55.2, error: null },
                 { spec: "sma:200", value: null, error: "KeyError: sma:200" }],
    indicator_bar_time: 1791307700.0, indicators_available: true,
    conditions: [{ side: "LONG", met_count: 1, total_count: 2, gate_ok: true, signal: false,
                   conditions: [{ condition: "ema:50 > close", met: true },
                                { condition: "rsi:14 < 70", met: false }] },
                 { side: "SHORT", met_count: 0, total_count: 1, gate_ok: false, signal: false,
                   conditions: [{ condition: "sma:10 > close", met: false }] }],
};

const RESEARCH_NODES = {
  scope: "USER_RESEARCH", include_legacy: false, total: 36, legacy_excluded_total: 787,
  offset: 0, limit: 10, returned: 3, pages: 4, sort: "return", dir: "desc",
  nodes: [
    { id: 10825, node_id: "Node_10825", research_node_num: 10010, status: "SURVIVED",
      symbol: "XAUUSD", timeframe: "M30", direction: "long", shortlisted: true, research_eligible: true,
      updated_at: 1791354366.07, research: { return_pct: 0.277, profit_factor: 1.743, trades: 182,
                                              max_drawdown_pct: 0.1256, sharpe: 3.845 } },
    { id: 240, node_id: "Node_240", research_node_num: 240, status: "QUALIFIED",
      symbol: "XAUUSD", timeframe: "M15", direction: "LONG", shortlisted: false, research_eligible: true,
      updated_at: 1791200000.0, research: { return_pct: 0.207, profit_factor: 1.435, trades: 88,
                                             max_drawdown_pct: 0.21, sharpe: 2.1 } },
    { id: 30000, node_id: "Node_30000", research_node_num: 30000, status: "FAILED",
      symbol: "XAUUSD", timeframe: "M5", direction: "short", shortlisted: false, research_eligible: true,
      updated_at: null, research: { return_pct: null, profit_factor: null, trades: null,
                                     max_drawdown_pct: null, sharpe: null } },
    null,
  ],
};

const AUTHORITATIVE = {
  id: 240, node_id: "Node_240", research_node_num: 240, status: "QUALIFIED", v5_status: "VALID",
  symbol: "XAUUSD", timeframe: "M15", direction: "LONG", generation: 9, fitness: 0.63,
  is_return_pct: 0.207, profit_factor: 1.435, trades_is: 88, genome: {},
  backtests: [{ stage: "detail", metrics: { trades: 88, total_return_pct: 0.207, profit_factor: 1.435 } }],
};

const CAPS = {
  label: "HISTORICAL MT5 BACKTEST", stage: "mt5_hist", cached: false,
  scope: { default: "MT5", options: ["MT5", "SIMULATOR"] },
  bridge: { active_bridge: "simulator", source: "SIMULATOR", is_simulated: true,
            mt5_package_installed: false, terminal_build: null,
            note: "the lab never needs a broker/terminal order session for a historical backtest" },
  datasets: [
    { symbol: "XAUUSD", timeframe: "M15", source: "MT5", dataset_id: "XAUUSD_M15", bars: 1850,
      start: "2026-09-07T00:00:00+00:00", end: "2026-10-05T00:00:00+00:00",
      broker: "Raw Trading Ltd", server: "Live01", fingerprint: "sha256:9f2c",
      eligible: true, eligibility_reason: "eligible", min_bars: 300 },
    { symbol: "XAUUSD", timeframe: "M15", source: "SIMULATOR", dataset_id: "sim_xau_m15", bars: 900,
      start: "2026-01-01T00:00:00+00:00", end: "2026-03-01T00:00:00+00:00",
      broker: null, server: null, fingerprint: "sha256:11ab",
      eligible: true, eligibility_reason: "eligible", min_bars: 300 },
  ],
  datasets_by_symbol: { XAUUSD: ["M15", "M1"] },
  rejected_datasets: [{ symbol: "XAUUSD", timeframe: "M15", source: "SIMULATOR",
                        dataset_id: "sim_xau_m15_short",
                        reason: "dataset integrity: 88 rows, minimum 300 bars required" }],
  defaults: { initial_balance: 10000.0, risk_per_trade: 0.005 },
  limits: { min_bars: 300, max_queue: 5, max_active: 1, max_trades_limit: 500, one_at_a_time: true },
  modes: { historical_backtest: true, places_orders: false },
};

const NODES = [
  {
    id: 1195, node_id: "Node_1195", status: "QUALIFIED", risk_pct: 1.0, shortlisted: true, active: false,
    indicators: [{ name: "EMA 50", active: true }, { name: "RSI 14", active: false }],
    entry_conditions: { long: [{ text: "close > EMA50", met: true }, { text: "RSI < 70", met: false }] },
  },
  { id: 1908, status: "SURVIVED", risk_pct: null, shortlisted: false, active: true, indicators: "not-an-array", entry_conditions: { long: "not-an-array" } },
  null,
];

const RUN_STATE = {
  user_research_nodes: 10000, legacy_test_nodes: 787, total_strategies: 10787,
  run_id: "RUN-20261005-055251", run_nodes: 10000, target: 10000, remaining_in_run: 0,
  configured_fresh_target: 5000,
  legacy_notice: "787 LEGACY_TEST rows are never part of the research run",
  backups: [{ name: "research_backup_20261006.zip", size_mb: null }],
  operation: { busy: false, stage: null, stages: [], message: null, result: null, error: null },
};

const INVENTORY = {
  datasets: [
    { id: "A", symbol: "XAUUSD", timeframe: "M15", source: "MT5", broker: "Raw Trading Ltd", start_date: "2026-09-07", end_date: "2026-10-05", rows: 1850, size_mb: 0.4 },
    { id: "B", symbol: "XAUUSD", timeframe: "M1", source: "SIMULATOR", broker: null, start_date: null, end_date: null, rows: 0, size_mb: 0 },
    { id: "C", symbol: null, timeframe: null, source: null, start_date: null, end_date: null, rows: null, size_mb: null },
    null,
  ],
};

const MGMT = { data_source: "MT5 REAL", raw_datasets_count: 77, timeframes: { M1: "MISSING", M15: "FOUND" } };

const STATS = {
  population: { scope: "USER_RESEARCH", total: 10000, alive: 33, dead: 9967, qualified: 5, legacy_test: 787, status_counts: { FAILED: 9967, QUALIFIED: 5, SURVIVED: 28 } },
  research_performance: {
    backtest: { total_backtests: 488, nodes_with_backtest: 415, stages: [{ stage: "screen", nodes: 415 }] },
    validation: { records: 9, nodes: 9, passed: 5 },
  },
  execution_records: { paper: { trades: 8 }, mt5_demo: { trades: 0 }, live_test: { trades: 0 } },
};

const FACETS = {
  scope: "USER_RESEARCH",
  population: { scope: "USER_RESEARCH", total: 10000, alive: 33, dead: 9967, qualified: 5, legacy_test: 787, status_counts: { FAILED: 9967, QUALIFIED: 5, SURVIVED: 28 } },
  statuses: [{ value: "FAILED", count: 9967 }, { value: "SURVIVED", count: 28 }, { value: "QUALIFIED", count: 5 }],
  generations: [{ value: 39, count: 15 }, { value: 38, count: 72 }],
};

const DRAWER_NODE = {
  strategy: {
    id: 1195, node_id: "Node_1195", research_node_num: 42, run_id: "RUN-20261005-055251",
    status: "QUALIFIED", symbol: "XAUUSD", timeframe: "M15", direction: "LONG", generation: 12,
    parent_id: 1194, children: [{ id: 1200 }, { id: 1201 }, null, "x"],
    fitness: 0.8123, complexity: 6, genome: { features: { rsi: 14, tp_atr_multiple: { weird: "object" } } },
    failure_reason: null, survival_reason: "Passed full walkforward validation (score: 1.0)",
  },
  backtests: [{ id: 9, stage: "detail", metrics: { trades: 165, total_return_pct: 0.45, profit_factor: 1.609 } }],
  validation: { available: true, passed: true, robustness_score: 0.9388 },
  charts: { has_equity_curve: false, equity_curve: [], trades: [], message: "no equity curve stored for this node" },
  curve: { equity_curve: [[1, 10000], [2, null]], trades: [{ pnl: null, exit_reason: null }] },
  section_errors: [{ section: "lineage", error: "lineage unavailable" }, null],
};

let EXEC_STATE_STUB = EXEC_STATE;
/** Pretend a verified DEMO terminal is connected (only the harness does this). */
export function setExecutionState(value) { EXEC_STATE_STUB = value; }

let PREVIEW_RESPONSE = PREVIEW_OK;
let PLACE_RESPONSE = { ok: true, order_ticket: 5512345, retcode: 10009,
                       result_message: "Request executed", volume: 0.06, symbol: "XAUUSD", side: "BUY" };
let PLACE_OK = true;

/** Drive the manual order panel's preview with a controlled backend answer. */
export function setPreviewResponse(value) { PREVIEW_RESPONSE = value; }
let HEALTH_FAILS = false;
export function setHealthFailure(value) { HEALTH_FAILS = value; }
/** Drive the manual order panel's submit with a controlled backend answer. */
export function setPlaceResponse(value, ok = true) { PLACE_RESPONSE = value; PLACE_OK = ok; }

/** V5: every POST the UI sent (path + body), so the smoke can assert the real
 *  endpoints are called — a cosmetic button would fail here. */
export const nodesTablePosts = [];
export const shutdownCalls = [];
export const dataSyncCalls = [];
export const seenUrls = [];
export const shortlistToggles = [];



export function payloadFor(path) {
  if (path === "/health" && HEALTH_FAILS) throw new Error("Failed to fetch");   // unreachable status endpoint
  if (path.includes("/api/live-testing/nodes/")) { nodesTablePosts.push(path); return { ok: true, is_active: true, status: "RUNNING" }; }
  if (path.includes("/api/power/shutdown")) { return { ok: true, dry_run: true, steps: [], backend_stopping: false }; }
  if (path.includes("/api/mt5-execution/preview")) return PREVIEW_RESPONSE;
  if (path.includes("/api/mt5-execution/place")) {
    if (!PLACE_OK) {
      const err = new Error(JSON.stringify(PLACE_RESPONSE));
      err.status = 409;
      err.detail = PLACE_RESPONSE;
      throw err;
    }
    return PLACE_RESPONSE;
  }
  if (path.includes("/api/mt5-execution/state")) return EXEC_STATE_STUB;
  if (path.includes("/api/mt5-execution/")) return EXEC_STATE_STUB;
  if (path.includes("/api/live-testing/market") && !path.includes("market-header")) return MARKET;
  if (path.includes("/api/live-testing/counter")) return {
    activity: { active_total: 0, positions: 0, orders: 0, source: "MT5 (SIMULATOR)", counted: false, error: "broker positions/orders unavailable: the active bridge is 'SIMULATOR', not a real MT5 connection" },
    limit: { reached: false, limit: 1, counted: false, source: "MT5 (SIMULATOR)" },
  };
  if (path.includes("/api/live-testing/trades")) return { trades: [], count: 0 };
  if (path.includes("/api/live-testing/log")) return { events: [], count: 0 };
  if (path.includes("/api/live-testing/conditions")) return {
    available: true, enrolled_count: 17, engine_active: false,
    excluded: [{ node_id: 171, reason: "LEGACY_TEST node - never a live trading candidate" }, null],
    nodes: [
      { node_id: 1195, symbol: "XAUUSD", timeframe: "M15", direction: "both", available: true, reason: null, bar_time: 1791307700.0,
        sides: [{ side: "LONG", met_count: 3, total_count: 4, gate_ok: true, signal: false,
                  conditions: [{ condition: "ema:50 > close", met: true }, { condition: "rsi:14 < 70", met: true },
                               { condition: "atr:14 > 1", met: false }, { condition: "sma:200 < close", met: null, error: "KeyError: sma:200" }] },
                { side: "SHORT", met_count: 1, total_count: 1, gate_ok: false, signal: false,
                  conditions: [{ condition: "sma:10 > close", met: true }] }] },
      { node_id: 1908, symbol: "XAUUSD", timeframe: "M15", available: false, reason: "no closed bars available from the active market bridge", sides: [] },
      null,
    ],
  };
  if (path.includes("/api/live-testing/nodes-table")) return {
    nodes: [
      { node_id: 1195, starred: true, status: "QUALIFIED", v5_status: "VALID", market: "XAUUSD",
        timeframe: "M15", direction: "both", is_return_pct: 12.5, profit_factor: 1.42, trades_is: 210,
        today_pnl: null, total_live_pnl: null, live_trades: 0, live_closed: 0, live_win_rate: null,
        risk_pct: 1.0, risk_pct_override: null, risk_source: "GLOBAL", global_risk_pct: 1.0,
        is_active: false, schedule: { days: [0, 1, 2, 3, 4], sessions: ["london"], start_time: "00:00",
                                      end_time: "23:59", timezone: "UTC", active: false } },
      { node_id: 1908, starred: false, status: "SURVIVED", v5_status: "VALID", market: "XAUUSD",
        timeframe: "M15", direction: "short", is_return_pct: null, profit_factor: null, trades_is: null,
        today_pnl: null, total_live_pnl: null, live_trades: 0, live_closed: 0, live_win_rate: null,
        risk_pct: null, risk_pct_override: null, risk_source: "GLOBAL", global_risk_pct: 1.0,
        is_active: false, schedule: { active: false } },
      null,
    ],
    total: 2, limit: 25, offset: 0,
    risk: { global_risk_pct: 1.0, limits: { risk_pct_default: 1.0, risk_pct_max: 2.0, max_active_trades: 1 } },
    excluded: [], note: "IS metrics from the research backtest; live P/L from recorded live trades.",
  };
  if (path.includes("/api/live-testing/schedule/")) return {
    allowed: false, reason: "sessions: 05:42 UTC against london 07:00–16:00 UTC",
    local_time: "2026-10-07T05:42:00+00:00", utc_time: "2026-10-07T05:42:00+00:00", timezone: "UTC",
    weekday: "Wednesday", trades_today: 0, last_entry_ts: null, strategy_id: 1195,
    description: "Mon/Tue/Wed/Thu/Fri, London, 00:00–23:59 UTC",
    rules: [{ rule: "days", ok: true, detail: "Wednesday is an active trading day" },
            { rule: "sessions", ok: false, detail: "05:42 UTC against london 07:00–16:00 UTC" }],
    schedule: { days: [0, 1, 2, 3, 4], sessions: ["london"], start_time: "00:00", end_time: "23:59",
                timezone: "UTC", cooldown_minutes: 30, max_trades_per_day: 3, spread_limit_points: 25,
                max_positions: 1 },
  };
  if (path.includes("/api/live-testing/risk")) return {
    limits: { risk_pct_default: 1.0, risk_pct_max: 2.0, max_active_trades: 1 },
    global_risk_pct: 1.0, overrides: [{ strategy_id: 1908, risk_pct: 0.5, is_active: false }],
    override_count: 1, note: "effective risk = override when set, otherwise the global default",
  };
  if (path.includes("/api/live-testing/market-header")) return MARKET_HEADER;
  if (path.includes("/api/power/session")) return {
    session_id: "s-abc123", started_at: 1791300000, backend_pid: 4242, roles: ["backend"],
    owned: { backend: [{ role: "backend", pid: 4242, label: "dashboard backend / API", alive: true,
                         launched_by: "dashboard", external: false }] },
    owned_role_count: 1, owned_process_count: 1, accepting_tasks: true,
    never_touched: ["the browser and its tabs", "the operating system (no shutdown / restart / logoff / sleep)"],
    shutdown_phrase: "SHUTDOWN DASHBOARD", session_file: "LOGS/power_session.json",
    plan: [{ order: 1, step: "stop_new_tasks", description: "Stop accepting new tasks", skipped: false },
           { order: 2, step: "verify", description: "Verify every dashboard-owned process has stopped", skipped: false }],
    last_report: null,
  };
  if (path.includes("/api/power/")) return { ok: true, dry_run: true, steps: [] };
  if (path.includes("/api/strategies/") && path.includes("/trading-info")) return {
    ok: true, strategy_id: 1195, text: "IDENTITY\n  Node: Node_1195",
    sections: [{ id: "identity", title: "Identity", items: [{ label: "Symbol", value: "XAUUSD" }] }],
    node: { id: 1195 },
  };
  if (path.includes("/api/live-testing/nodes")) return { nodes: NODES, excluded: [7], limit: 1 };
  if (path.includes("/api/live-testing/status")) return {
    mode: "INACTIVE", active: false, bridge_source: "SIMULATOR", cycle_count: 0,
    nodes: NODES, excluded: [], limit: 1, risk: { risk_pct_default: 1.0, risk_pct_max: 5.0, require_sl: true },
    records: {}, stats: {},
  };
  if (path.includes("/api/live-test/status")) return { active_strategies: 0, open_positions: 0, today_pnl: 0, total_pnl: 0, cycle_count: 0, last_cycle_iso: null, last_noop_reason: "engine inactive" };
  if (path.includes("/api/live-test/results")) return { summary: {}, trades: [], prop_firm: {} };
  if (path.includes("/api/data/inventory")) return INVENTORY;
  if (path.includes("/api/mt5-historical/capabilities")) return CAPS;
  if (path.includes("/api/research/strategies")) return RESEARCH_NODES;
  if (path.includes("/api/research/shortlist/toggle")) return { ok: true, starred: true };
  if (path.includes("/api/data/sync")) return {
    symbol: "XAUUSD", timeframe: "M15", fetch_mode: "reuse_existing", new_bars: 0,
    total_bars: 1850, version: 3, dataset_id: "XAUUSD_M15", action: "REUSING EXISTING DATA",
  };
  if (path.includes("/api/data/management/status")) return MGMT;
  if (path.includes("/api/stats/overview")) return STATS;
  if (path.includes("/api/stats/")) return { scope: "USER_RESEARCH", total: 10000, nodes: [], population: STATS.population };
  if (path.includes("/api/research/facets")) return FACETS;
  if (path.includes("/api/research/filter")) return { total_evaluated: 415, total_matching: 9967, strategies: [], filters_applied: {} };
  if (path.includes("/api/research-run/state")) return RUN_STATE;
  if (path.includes("/api/research/strategies")) return { nodes: [], total: 0, population: { total: 10000 } };
  if (path.includes("/api/mt5-demo/status")) return {
    status: "DEMO_ACCOUNT_READY", demo_banner: "DEMO ACCOUNT ONLY — NO REAL MONEY AT RISK",
    bridge_type: "SIMULATOR_BRIDGE", is_connected: true, account_id: "0", broker: "LAB_SIMULATOR",
    server: "SIM", balance: 10000.0, equity: 10000.0, free_margin: 9500.0,
    active_strategies_count: 1, open_positions_count: 0, today_pnl: 0, total_pnl: 0, active_strategies: [240],
  };
  if (path.includes("/api/strategies/") && path.includes("/authoritative")) return AUTHORITATIVE;
  if (path.includes("/api/strategies/")) return DRAWER_NODE;
  if (path.includes("/api/lab/status")) return { current_nodes: 10000, alive: 33, dead: 9967, qualified: 5, generation_number: 39, running: false };
  if (path.includes("/api/mt5-historical/runs/")) return { run_id: "HRUN-1", status: "COMPLETED", results: { metrics: {} }, orders_placed: false, is_mt5_data: true, data: { source: "MT5" } };
  if (path.includes("/api/mt5-historical/runs")) return { runs: [], total: 0 };
  if (path.includes("/api/tree")) return { nodes: [], edges: [], total_strategies: 10000 };
  if (path.includes("/api/shortlist")) return { shortlist: [] };
  if (path.includes("/api/logs")) return { logs: [], log_text: "" };
  if (path.includes("/api/jobs")) return { jobs: [] };
  return {};
}

/* ------------------------------------------------------------------- the run */
export async function runSmoke() {
  const results = [];
  // capture the real requests the V5 controls send (the harness installs its own
  // fetch stub before this runs, so the wrapper has to go here)
  const _origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts = {}) => {
    const urlStr = String(url);
    if (urlStr.includes("/api/live-testing/nodes/")) nodesTablePosts.push(urlStr);
    if (urlStr.includes("/api/power/shutdown")) {
      let body = {};
      try { body = JSON.parse(opts.body || "{}"); } catch { body = {}; }
      shutdownCalls.push(body);
    }
    if (urlStr.includes("/api/research/shortlist/toggle")) {
      let body = {};
      try { body = JSON.parse(opts.body || "{}"); } catch { body = {}; }
      shortlistToggles.push(body);
    }
    if (urlStr.includes("/api/data/sync")) {
      let body = {};
      try { body = JSON.parse(opts.body || "{}"); } catch { body = {}; }
      dataSyncCalls.push(body);
    }
    seenUrls.push(urlStr);
    return _origFetch(url, opts);
  };
  const container = document.createElement("div");
  document.body.appendChild(container);

  const render = async (label, element, expect) => {
    const root = createRoot(container);
    try {
      let crashed = null;
      try {
        await act(async () => {
          root.render(element);
          await Promise.resolve();
        });
      } catch (e) { crashed = e; }
      const text = container.textContent || "";
      if (crashed) throw crashed;
      const unsafe = text.match(/undefined|NaN|\[object Object\]/);
      if (unsafe) {
        const at = text.indexOf(unsafe[0]);
        throw new Error("rendered text contains unsafe output (" + unsafe[0] + ") … \""
          + text.slice(Math.max(0, at - 90), at + 70) + "\"");
      }
      if (expect && !expect(text)) {
        throw new Error(`expected text missing … "${text.slice(0, 200)}"`);
      }
      results.push({ label, ok: true });
    } catch (e) {
      results.push({ label, ok: false, error: e && e.message ? e.message : String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
    }
  };

  // 1. the V4.8 UI kit primitives, including the hostile-value paths
  await render("ui:Kpi", <Kpi label="OOS Return" value={null} sub={undefined} tone="pos" />);
  await render("ui:Kpi-na", <Kpi label="Robustness" value="N/A" />);
  await render("ui:Progress", <Progress value={73.5} max={100} label="population" />);
  await render("ui:StateBlock-empty", <StateBlock loading={false} error={null} empty emptyText="nothing here yet" />);
  await render("ui:StateBlock-error", <StateBlock error={{ message: "endpoint failed", status: 500 }} onRetry={() => {}} />);
  await render("ui:ConfirmModal", (
    <ConfirmModal open title="Delete old population" tone="danger" confirmTone="danger"
                  requireText="RESET_USER_RESEARCH" requireHint="type the phrase"
                  dangerText="permanent, no undo" confirmLabel="Delete" busy={false}
                  onConfirm={() => {}} onCancel={() => {}}>
      <div>This deletes 10000 nodes.</div>
    </ConfirmModal>
  ));
  await render("ui:SimulatorBanner", <SimulatorBanner strict source="SIMULATOR" detail="no real terminal" />);
  await render("ui:SimulatorBanner-real", <SimulatorBanner source="MT5" detail="terminal bridge" />);
  await render("ui:SourceChip", <SourceChip source={null} />);
  await render("ui:StageTrack", <StageTrack stages={["QUALIFIED", "SHORTLISTED", null]} current="SHORTLISTED" />);
  await render("ui:EmptyState", <EmptyState title="No nodes" hint="run research first" />);
  await render("ui:Field+Bade+Star+Val", (
    <Card title="kit"><Field label="risk %"><input /></Field><Badge tone="sim">SIM</Badge>
      <Star on={true} /><Val value={null} /><span className="mono">{fmtId(parseNodeId("Node_10590"))}</span></Card>
  ));

  // 2. the shared node-detail drawer with hostile payloads
  await render("NodeDetailDrawer:open", wrap(<NodeDetailDrawer strategyId={1195} onClose={() => {}} />));
  await render("NodeDetailDrawer:no-id", wrap(<NodeDetailDrawer strategyId={null} onClose={() => {}} />));
  await render("NodeDetailDrawer:missing", wrap(<NodeDetailDrawer strategyId={999999} onClose={() => {}} />));

  // 3. the new research-run modal (three choices, typed confirmation)
  await render("NewResearchRunModal:closed", <NewResearchRunModal open={false} onClose={() => {}} onStarted={() => {}} />);
  await render("NewResearchRunModal:open", <NewResearchRunModal open onClose={() => {}} onStarted={() => {}} />, (t) =>
    t.includes("Start New") && t.includes("Resume Existing") && t.includes("Recommended"));

  // 4. §12 milestone path, §14 population counters, §15 availability, §7 panels
  await render("PipelinePath", wrap(<PipelinePath />), (t) =>
    t.includes("Research") && t.includes("Qualification") && t.includes("Final"));
  await render("PopulationSummary", wrap(<PopulationSummary onPick={() => {}} statusFilter="" setStatusFilter={() => {}} search="10590" setSearch={() => {}} />), (t) =>
    t.includes("Total") && t.includes("Dead"));
  await render("DatasetAvailability", wrap(<DatasetAvailability />), (t) =>
    t.includes("Physical availability") && t.includes("usable"));
  await render("LiveTradeCounter", wrap(<LiveTradeCounter />));
  await render("DemoAccountSafety", wrap(<DemoAccountSafety />), (t) =>
    t.includes("DEMO ACCOUNT ONLY"));
  await render("RiskStrip", wrap(<RiskStrip nodes={NODES} lab={{ risk: { risk_pct_default: 1.0, risk_pct_max: 5.0 } }} onChanged={() => {}} />));
  await render("ManualOrderPanel", wrap(<ManualOrderPanel />), (t) =>
    t.includes("money at risk") && t.includes("rounded"));
  await render("LiveMarketPanel", wrap(<LiveMarketPanel nodes={NODES} engineRunning={false} />));
  await render("StageTimeline", wrap(<StageTimeline nodes={NODES} />));

  // 5. the pages that carry the V4.8 safety wording
  await render("page:LiveTesting-idle", wrap(<LiveTesting />), (t) => t.includes("IDLE ON ENTRY"));
  await render("page:Mt5DemoTrading-demo-only", wrap(<Mt5DemoTrading />), (t) =>
    t.includes("DEMO ACCOUNT ONLY"));
  await render("page:Mt5Backtest-simulator", wrap(<Mt5Backtest />), (t) =>
    t.includes("REAL MT5 TRADING UNAVAILABLE") && t.includes("SIMULATOR MODE ACTIVE"));
  await render("page:NodeEconomics", wrap(<NodeEconomics />));
  await render("page:FinalTesting", wrap(<FinalTesting />));

  // 6. real button behaviour: the preview converts, the order panel refuses to
  // send without the confirmation dialog, and a refused submission is shown
  const clickTextIn = async (container, text) => {
    const buttons = Array.from(container.querySelectorAll("button"));
    const btn = buttons.find((b) => (b.textContent || "").includes(text));
    if (!btn) throw new Error(`no button labelled "${text}"`);
    await act(async () => {
      btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    return btn;
  };
  const clickText = async (text) => {
    const buttons = Array.from(container.querySelectorAll("button"));
    const btn = buttons.find((b) => (b.textContent || "").includes(text));
    if (!btn) throw new Error(`no button labelled "${text}"`);
    await act(async () => {
      btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    return btn;
  };
  const setValue = async (labelText, value) => {
    const labels = Array.from(container.querySelectorAll("label"));
    const label = labels.find((l) => (l.textContent || "").includes(labelText));
    const input = label && label.querySelector("input, select");
    if (!input) throw new Error(`no input for "${labelText}"`);
    await act(async () => {
      const proto = input.tagName === "SELECT" ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(input, String(value));
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });
  };

  {
    // (a) with the SIMULATOR bridge the send buttons must be disabled *with a reason*
    const root = createRoot(container);
    try {
      await act(async () => { root.render(wrap(<ManualOrderPanel />)); await Promise.resolve(); });
      const buy = Array.from(container.querySelectorAll("button")).find((b) => (b.textContent || "").trim() === "BUY");
      if (!buy) throw new Error("no BUY button");
      if (!buy.disabled) throw new Error("BUY must be disabled while execution is blocked");
      if (!String(buy.title || "").toLowerCase().includes("simulator")) {
        throw new Error(`the disabled BUY button must state why it is blocked (title was "${buy.title}")`);
      }
      results.push({ label: "ManualOrderPanel:disabled-with-reason", ok: true });
    } catch (e) {
      results.push({ label: "ManualOrderPanel:disabled-with-reason", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
    }
  }

  {
    // (b) a verified DEMO terminal: preview, confirmation dialog, broker result
    setExecutionState({
      ...EXEC_STATE, execution_allowed: true, blocked_code: null, blocked_reason: null,
      account_safety: { ...EXEC_STATE.account_safety, demo_verified: true, is_simulated: false,
                        blocked_code: null, blocked_reason: null,
                        account: { login: 51234567, type: "DEMO", trade_mode: 0, trade_mode_comment: "demo account required" } },
      bridge: { name: "mt5", source: "MT5", is_simulated: false, connected: true },
    });
    const root = createRoot(container);
    try {
      await act(async () => { root.render(wrap(<ManualOrderPanel />)); await Promise.resolve(); });
      await setValue("Stop loss", "2405");          // the pips field (300 by default)
      await setValue("Amount / risk", "300");
      await clickText("Recalculate (backend)");
      await act(async () => { await Promise.resolve(); await Promise.resolve(); });
      const text = container.textContent || "";
      if (!text.includes("0.06")) throw new Error(`the preview answer was not rendered … "${text.slice(0, 180)}"`);
      // the sent order requires the explicit confirmation dialog
      await clickText("BUY");
      const confirmText = container.textContent || "";
      if (!/Confirm BUY demo order/i.test(confirmText)) throw new Error("BUY did not open the confirmation dialog");
      if (!confirmText.includes("DEMO ACCOUNT ONLY")) throw new Error("the confirmation dialog does not state the demo-only rule");
      if (!confirmText.includes("cannot be retried")) throw new Error("the dialog does not warn that a submitted order is final");
      await clickText("Send BUY order");
      await act(async () => { await Promise.resolve(); await Promise.resolve(); });
      const after = container.textContent || "";
      if (!after.includes("5512345")) throw new Error(`the broker result was not shown … "${after.slice(0, 200)}"`);
      results.push({ label: "ManualOrderPanel:preview+confirm+result", ok: true });
    } catch (e) {
      results.push({ label: "ManualOrderPanel:preview+confirm+result", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
      setExecutionState(EXEC_STATE);          // back to the honest simulator state
    }
  }

  {
    // a broker-side refusal must be displayed, never swallowed
    setExecutionState({
      ...EXEC_STATE, execution_allowed: true, blocked_code: null, blocked_reason: null,
      account_safety: { ...EXEC_STATE.account_safety, demo_verified: true, is_simulated: false, blocked_code: null, blocked_reason: null,
                        account: { login: 51234567, type: "DEMO", trade_mode: 0 } },
      bridge: { name: "mt5", source: "MT5", is_simulated: false, connected: true },
    });
    setPlaceResponse({ detail: { code: "CONFIRMATION_REQUIRED", message: "Explicit confirmation required: send confirm='PLACE_DEMO_ORDER'" } }, false);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(wrap(<ManualOrderPanel />)); await Promise.resolve(); });
      await clickText("SELL");
      await clickText("Send SELL order");
      await act(async () => { await Promise.resolve(); await Promise.resolve(); });
      const text = container.textContent || "";
      if (!text.includes("CONFIRMATION_REQUIRED")) throw new Error(`a refused order was not reported … "${text.slice(0, 200)}"`);
      results.push({ label: "ManualOrderPanel:refusal-is-visible", ok: true });
    } catch (e) {
      results.push({ label: "ManualOrderPanel:refusal-is-visible", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
      setPlaceResponse({ ok: true, order_ticket: 5512345, retcode: 10009, result_message: "Request executed" }, true);
      setExecutionState(EXEC_STATE);
    }
  }

  // 5b. V5 — the live node table, the schedule dialog, START/STOP, the market
  // header, the Power button and the Trading Info tab
  {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(wrap(<LiveNodeTable />)); await Promise.resolve(); await Promise.resolve(); });
      let text = container.textContent || "";
      if (!text.includes("Node_1195")) throw new Error(`the live node table did not list the node … "${text.slice(0, 200)}"`);
      if (!/1 \.\. /.test(text) && !text.includes("1 %")) throw new Error("the effective risk is not shown");
      if (!text.includes("(global)") && !text.includes("(node override)")) {
        throw new Error("the table must show whether risk is global or a node override");
      }

      // START goes through the real endpoint (POST .../start) and reports back
      const start = Array.from(container.querySelectorAll("button")).find((b) => (b.textContent || "").trim() === "START");
      if (!start) throw new Error("no START action in the node table");
      await act(async () => { start.dispatchEvent(new MouseEvent("click", { bubbles: true })); await Promise.resolve(); await Promise.resolve(); });
      if (!(nodesTablePosts || []).some((u) => /\/live-testing\/nodes\/1195\/start$/.test(u))) {
        throw new Error(`START did not call the node start endpoint (posts: ${JSON.stringify(nodesTablePosts)})`);
      }

      // the schedule dialog shows the engine's own evaluation, not a client guess
      await act(async () => {
        root.render(wrap(<ScheduleDialog nodeId={1195} onClose={() => {}} onSaved={() => {}} />));
        await Promise.resolve(); await Promise.resolve();
      });
      text = container.textContent || "";
      if (!text.includes("may trade now") && !text.includes("BLOCKED")) {
        throw new Error("the schedule dialog does not show the engine's verdict");
      }
      if (!text.includes("sessions") || !text.includes("london 07:00")) {
        throw new Error(`the schedule dialog does not show the blocking rule … "${text.slice(0, 240)}"`);
      }
      results.push({ label: "LiveNodeTable:start+schedule", ok: true });
    } catch (e) {
      results.push({ label: "LiveNodeTable:start+schedule", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
    }
  }

  {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(wrap(<PowerButton />)); await Promise.resolve(); });
      await clickTextIn(container, "Power");
      await act(async () => { await Promise.resolve(); await Promise.resolve(); });
      let text = container.textContent || "";
      if (!/Are you sure you want to close the dashboard\?/i.test(text)) {
        throw new Error("the Power button did not ask the confirmation question");
      }
      for (const needle of ["Only the dashboard is closed", "operating system", "DATA"]) {
        if (!text.includes(needle)) throw new Error(`the Power dialog does not state: ${needle}`);
      }
      if (!text.includes("Yes, close the dashboard")) throw new Error("the dialog has no Yes button");
      // NO: the dialog closes and nothing is called
      shutdownCalls.length = 0;
      await clickTextIn(container, "Cancel");
      await act(async () => { await Promise.resolve(); });
      if (shutdownCalls.length !== 0) throw new Error("cancelling the dialog still called the shutdown endpoint");
      text = container.textContent || "";
      if (/Are you sure you want to close the dashboard\?/i.test(text)) throw new Error("the dialog did not close on No");

      // YES with a dry run: the plan and the report are shown, nothing stops
      await clickTextIn(container, "Power");
      await act(async () => { await Promise.resolve(); await Promise.resolve(); });
      await clickTextIn(container, "Dry run");
      await act(async () => { await Promise.resolve(); await Promise.resolve(); });
      if (!shutdownCalls.some((b) => b.dry_run === true && b.confirm === "SHUTDOWN DASHBOARD")) {
        throw new Error(`the dry run did not send the confirmation phrase (calls: ${JSON.stringify(shutdownCalls)})`);
      }
      results.push({ label: "PowerButton:yes/no+dry-run", ok: true });
    } catch (e) {
      results.push({ label: "PowerButton:yes/no+dry-run", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
    }
  }

  {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(wrap(<TradingInfoTab id={1195} />)); await Promise.resolve(); await Promise.resolve(); });
      const text = container.textContent || "";
      if (!text.includes("What this node actually trades")) throw new Error("the Trading Info tab did not render its sections");
      if (!text.includes("XAUUSD")) throw new Error("the Trading Info tab does not show the node's symbol");
      results.push({ label: "TradingInfo:renders-from-genome", ok: true });
    } catch (e) {
      results.push({ label: "TradingInfo:renders-from-genome", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
    }
  }

  {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(wrap(<LiveMarketHeader symbol="XAUUSD" nodeId={1195} nodes={[{ node_id: 1195, market: "XAUUSD", timeframe: "M15" }]} />)); await Promise.resolve(); await Promise.resolve(); });
      const text = container.textContent || "";
      for (const needle of ["Bid", "Ask", "Spread", "2400.1", "ema:50", "55.2"]) {
        if (!text.includes(needle)) throw new Error(`the market header is missing ${needle}`);
      }
      if (!/rsi:14/.test(text)) throw new Error("the header does not show the node's indicator values");
      results.push({ label: "LiveMarketHeader:market+indicators", ok: true });
    } catch (e) {
      results.push({ label: "LiveMarketHeader:market+indicators", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
    }
  }

  {
    // §18: the per-node detail card must open *and* its shortlist action must work —
    // an undefined handler here used to throw only when a row was opened.
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(wrap(<LiveTestResults />)); await Promise.resolve(); await Promise.resolve(); });
      const detailBtn = Array.from(container.querySelectorAll("button"))
        .find((b) => /detail/i.test(b.textContent || ""));
      if (!detailBtn) throw new Error("the results table has no detail action");
      await act(async () => { detailBtn.dispatchEvent(new MouseEvent("click", { bubbles: true })); await Promise.resolve(); await Promise.resolve(); });
      const text = container.textContent || "";
      if (!text.includes("add to shortlist") && !text.includes("remove from shortlist")) {
        throw new Error(`the node detail card has no shortlist action … "${text.slice(0, 200)}"`);
      }
      results.push({ label: "LiveTestResults:node-detail+shortlist", ok: true });
    } catch (e) {
      results.push({ label: "LiveTestResults:node-detail+shortlist", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
    }
  }

  {
    // §19: the node's own symbol/timeframe must be resolvable to a real stored dataset
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(wrap(<Mt5Backtest />)); await Promise.resolve(); await Promise.resolve(); });
      const text = container.textContent || "";
      for (const needle of ["Data for this node", "XAUUSD M15", "MT5 DATA", "1,850 bars", "sha256:9f2c",
                            "eligible", "dataset XAUUSD_M15", "dataset integrity: 88 rows, minimum 300 bars required"]) {
        if (!text.includes(needle)) throw new Error(`the node-data panel is missing ${needle} … "${text.slice(0, 260)}"`);
      }
      const fetchBtn = Array.from(container.querySelectorAll("button"))
        .find((b) => /fetch \/ extend stored data/.test(b.textContent || ""));
      if (!fetchBtn) throw new Error("the node-data panel has no fetch action");
      dataSyncCalls.length = 0;
      await act(async () => { fetchBtn.click(); await Promise.resolve(); await Promise.resolve(); });
      if (!dataSyncCalls.some((b) => b.symbol === "XAUUSD" && b.timeframe === "M15" && b.force === false)) {
        throw new Error(`the fetch action did not request the node's own data (calls: ${JSON.stringify(dataSyncCalls)}, disabled=${fetchBtn.disabled}, hasErr=${(container.textContent || "").includes("fetch failed")}, btn="${(fetchBtn.textContent || "")}"))`);
      }
      if (!(container.textContent || "").includes("REUSING EXISTING DATA")) {
        throw new Error("the engine's own answer was not shown back");
      }
      results.push({ label: "Mt5Backtest:node-data-availability+fetch", ok: true });
    } catch (e) {
      results.push({ label: "Mt5Backtest:node-data-availability+fetch", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
    }
  }

  {
    // §19: choose the node from real research rows — search, sort, star, paging, backtest this
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(wrap(<Mt5Backtest />)); await Promise.resolve(); await Promise.resolve(); });
      let text = container.textContent || "";
      for (const needle of ["Choose a node to backtest", "Node_10825", "Node_30000", "1.743", "27.70 %",
                            "12.6 %", "page 1 of 4", "legacy rows excluded: 787"]) {
        if (!text.includes(needle)) throw new Error(`the node picker is missing ${needle} … "${text.slice(0, 240)}"`);
      }
      // a failed row must be shown as failed with N/A metrics — never as a zero
      const failedRow = Array.from(container.querySelectorAll("tr"))
        .find((tr) => (tr.textContent || "").includes("Node_30000"));
      if (!failedRow || !(failedRow.textContent || "").includes("FAILED")) throw new Error("the failed node is not marked FAILED");
      if (!(failedRow.textContent || "").includes("N/A")) throw new Error("missing metrics must render as N/A, not 0");

      // star toggles through the real endpoint
      shortlistToggles.length = 0;
      const star = Array.from(container.querySelectorAll("button")).find((b) => (b.textContent || "").trim() === "☆");
      if (!star) throw new Error("no star action in the node picker");
      await act(async () => { star.click(); await Promise.resolve(); await Promise.resolve(); });
      if (!shortlistToggles.some((b) => b.strategy_id !== undefined && b.strategy_id !== null)) {
        throw new Error(`the star action did not call the shortlist endpoint (calls: ${JSON.stringify(shortlistToggles)})`);
      }

      // search + sort + starred-only must be re-requested from the backend
      seenUrls.length = 0;
      const searchBox = Array.from(container.querySelectorAll("input"))
        .find((i) => /search id/.test(i.placeholder || ""));
      if (!searchBox) throw new Error("the node picker has no search box");
      await act(async () => {
        // React tracks the previous value: set it through the native setter, as a
        // real browser would, otherwise onChange is never dispatched
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        setter.call(searchBox, "30000");
        searchBox.dispatchEvent(new Event("input", { bubbles: true }));
        await Promise.resolve();
      });
      await act(async () => { await Promise.resolve(); await Promise.resolve(); });
      if (!seenUrls.some((s) => s.includes("/api/research/strategies") && s.includes("search=30000"))) {
        throw new Error(`the search box did not re-query the backend (seen: ${JSON.stringify(seenUrls.slice(-3))})`);
      }

      // "backtest this" loads that node into the page
      const useBtn = Array.from(container.querySelectorAll("button"))
        .find((b) => (b.textContent || "").includes("backtest this"));
      if (!useBtn) throw new Error("no \"backtest this\" action in the node picker");
      seenUrls.length = 0;
      await act(async () => { useBtn.click(); await Promise.resolve(); await Promise.resolve(); });
      if (!seenUrls.some((s) => /\/api\/strategies\/10825\/authoritative$/.test(s))) {
        throw new Error(`\"backtest this\" did not load the node (seen: ${JSON.stringify(seenUrls.slice(-3))})`);
      }
      results.push({ label: "Mt5Backtest:node-picker+search+star", ok: true });
    } catch (e) {
      results.push({ label: "Mt5Backtest:node-picker+search+star", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
    }
  }

  // 6. a status probe that cannot be read must never be reported as a failed backend
  {
    const container = document.createElement("div");
    document.body.appendChild(container);
    setHealthFailure(true);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(wrap(<StartupBanner />)); await Promise.resolve(); await Promise.resolve(); });
      const text = container.textContent || "";
      if (text.includes("Backend startup failed"))
        throw new Error(`an unreachable status endpoint was reported as a failed backend: "${text.slice(0, 160)}"`);
      if (!text.includes("Backend status could not be read"))
        throw new Error(`the banner did not explain the failed probe: "${text.slice(0, 160)}"`);
      results.push({ label: "StartupBanner:probe-failure-is-not-a-backend-failure", ok: true });
    } catch (e) {
      results.push({ label: "StartupBanner:probe-failure-is-not-a-backend-failure", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
      setHealthFailure(false);
      container.remove();
    }
  }

  // 7. the raw payload view must be collapsed (it used to dump ~146 kB into the DOM)
  {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const big = { id: 10590, node_id: "Node_10590", blob: "x".repeat(4000), nested: { deep: { value: 1 } } };
    try {
      await act(async () => { root.render(<JsonView data={big} />); });
      let text = container.textContent || "";
      if (text.includes("xxxx")) throw new Error("the raw payload was rendered by default");
      if (!/hidden so it does not flood the page/.test(text)) throw new Error(`no size hint: "${text.slice(0, 120)}"`);
      const btn = Array.from(container.querySelectorAll("button")).find((b) => /show raw JSON/i.test(b.textContent || ""));
      if (!btn) throw new Error("no control to reveal the raw payload");
      await act(async () => { btn.click(); });
      text = container.textContent || "";
      if (!text.includes("xxxx")) throw new Error("revealing the payload did not render it");
      results.push({ label: "JsonView:collapsed-by-default-and-expandable", ok: true });
    } catch (e) {
      results.push({ label: "JsonView:collapsed-by-default-and-expandable", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
      container.remove();
    }
  }

  // 8. the navigation the V4.8 cross-links rely on must be reachable
  const nodeIdCases = [["10590", 10590], ["Node_10590", 10590], ["#10590", 10590], ["nope", null]];
  for (const [raw, expected] of nodeIdCases) {
    const got = parseNodeId(raw);
    results.push({
      label: `parseNodeId:${raw}`,
      ok: got === expected,
      error: got === expected ? undefined : `expected ${expected}, got ${got}`,
    });
  }

  const failures = results.filter((r) => !r.ok);
  return { total: results.length, failures, results, tabCalls };
}
