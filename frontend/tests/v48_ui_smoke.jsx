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
import { RiskStrip, ManualOrderPanel, LiveMarketPanel, StageTimeline,
         LiveMarketHeader, ScheduleDialog } from "../src/components/LiveTestingPanels.jsx";
import { LiveNodeTable } from "../src/components/LiveNodeIndex.jsx";
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
import DeepBacktest from "../src/pages/DeepBacktest.jsx";
import { arr as safeArr, numOrNull, objOrNull } from "../src/lib/safe.js";

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

let MARKET = {
  symbol: "XAUUSD", source: "SIMULATOR", bid: 2400.0, ask: 2400.3, spread: 0.3,
  session_status: "newyork", trading_available: false,
  reasons: ["simulator feed: no live ticks"], tick_age_s: 12.5, market_open: false,
  indicators: null, bar_close_in: null,
};

let MARKET_HEADER = {
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
/** the most recent preview request body, so a stub can echo a real calculation */
let LAST_PREVIEW_BODY = {};
let PLACE_RESPONSE = { ok: true, order_ticket: 5512345, retcode: 10009,
                       result_message: "Request executed", volume: 0.06, symbol: "XAUUSD", side: "BUY" };
let PLACE_OK = true;

/** Drive the manual order panel's preview with a controlled backend answer. */
export function setPreviewResponse(value) { PREVIEW_RESPONSE = value; }
/* §3 — the panel's own market snapshot, so a "no quote" case can be rendered. */
/** Save/restore the market stubs exactly, so a case that mutates them cannot
 *  change what a later case renders. */
export function snapshotMarket() { return { m: MARKET, h: MARKET_HEADER }; }
export function restoreMarketSnapshot(snap) { MARKET = snap.m; MARKET_HEADER = snap.h; }
export function setMarket(value) {
  MARKET = value;
  /* the manual order panel reads the *header* endpoint — drive both from one call */
  MARKET_HEADER = { ...MARKET_HEADER, market: { ...(MARKET_HEADER.market || {}), ...value } };
}
let HEALTH_FAILS = false;
export function setHealthFailure(value) { HEALTH_FAILS = value; }
/** Drive the manual order panel's submit with a controlled backend answer. */
export function setPlaceResponse(value, ok = true) { PLACE_RESPONSE = value; PLACE_OK = ok; }

/** V5: every POST the UI sent (path + body), so the smoke can assert the real
 *  endpoints are called — a cosmetic button would fail here. */
export const nodesTablePosts = [];
/* §8 — every GET the client makes to the qualified-node index, so the smoke can
 * prove which filter / sort the page actually requested. */
export const nodeIndexCalls = [];
/** V5.1a: every body sent to the per-node schedule endpoint. */
export const schedulePosts = [];
export const shutdownCalls = [];
/** §3/§5 — every risk/lot preview request body, so "recalculate on any input
 *  change, and never stale" is asserted against what was really sent. */
export const previewPosts = [];
export const dataSyncCalls = [];
export const seenUrls = [];
export const shortlistToggles = [];



export function payloadFor(path, method = "GET") {
  const isPost = String(method || "GET").toUpperCase() === "POST";
  if (path === "/health" && HEALTH_FAILS) throw new Error("Failed to fetch");   // unreachable status endpoint
  if (path.includes("/api/live-testing/nodes/")) { nodesTablePosts.push(path); return { ok: true, is_active: true, status: "RUNNING" }; }
  if (path.includes("/api/power/shutdown")) { return { ok: true, dry_run: true, steps: [], backend_stopping: false }; }
  if (path.includes("/api/mt5-execution/preview")) {
    return typeof PREVIEW_RESPONSE === "function" ? PREVIEW_RESPONSE(LAST_PREVIEW_BODY) : PREVIEW_RESPONSE;
  }
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
  if (path.includes("/api/live-testing/schedule/")) {
    // the dialog edits the *stored* config and offers every group the engine
    // enforces; the stub mirrors the real GET payload (config + options + node)
    const cfg = {
      days: [0, 1, 2, 3, 4], sessions: ["london"], regimes: ["trending"],
      timeframes: ["M15"], conditions: { entry_long: true, entry_short: false },
      windows: [{ start: "07:00", end: "16:00" }], enabled: true, timezone: "UTC",
      cooldown_minutes: 30, max_trades_per_day: 3, spread_limit_points: 25, max_positions: 1,
    };
    return {
      allowed: false, reason: "sessions: 05:42 UTC against london 07:00–16:00 UTC",
      local_time: "2026-10-07T05:42:00+00:00", utc_time: "2026-10-07T05:42:00+00:00", timezone: "UTC",
      weekday: "Wednesday", trades_today: 0, last_entry_ts: null, strategy_id: 1195,
      description: "Mon/Tue/Wed/Thu/Fri, London, trending, M15, 07:00–16:00 UTC",
      rules: [{ rule: "days", ok: true, detail: "Wednesday is an active trading day" },
              { rule: "sessions", ok: false, detail: "05:42 UTC against london 07:00–16:00 UTC" }],
      config: cfg, schedule: cfg,
      node: { strategy_id: 1195, research_node_num: 42, run_id: "RUN-20261005-055251",
              symbol: "XAUUSD", timeframe: "M15", status: "QUALIFIED",
              conditions: ["entry_long", "entry_short"] },
      options: {
        days: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
          .map((label, value) => ({ value, label })),
        sessions: ["asia", "london", "newyork", "london_ny_overlap", "custom"]
          .map((value) => ({ value, label: value, hours_utc: [0, 8] })),
        regimes: [{ value: "trending", label: "Trending" }, { value: "ranging", label: "Ranging" }],
        timeframes: ["M1", "M5", "M15", "M30", "H1", "H4", "D1"].map((v) => ({ value: v, label: v })),
        conditions: [{ value: "entry_long", label: "long entry rule" },
                     { value: "entry_short", label: "short entry rule" }],
        timezones: ["UTC", "Pakistan", "London"],
        node_timeframe: "M15",
        window_semantics: "start > end = an overnight window",
      },
      ...(isPost ? { ok: true, persisted: true, normalized: cfg } : {}),
    };
  }
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
  if (path.includes("/api/nodes")) {
    nodeIndexCalls.push(path);
    const mk = (num, id, status, bucket, liveStatus) => ({
      node_id: id, strategy_id: id, research_node_num: num, node_label: `Node_${num}`,
      experiment: "RUN-20261005-055251", run_id: "RUN-20261005-055251", generation: 0,
      symbol: "XAUUSD", timeframe: "M15", direction: "both", fitness: 0.63,
      bucket, bucket_label: bucket === "qualified" ? "Qualified" : "Failed",
      bucket_reason: "Cleared the research gates against real data.", qualification: status,
      v5_status: bucket === "qualified" ? "VALID" : "STRATEGY_FAILED",
      survival_evidence: "Passed backtest criteria (PF=1.77)",
      metrics: { return_pct: 0.0067, profit_factor: 1.769, max_drawdown_pct: 0.0048,
                 win_rate: 0.8182, trades: 11, net_profit: 66.73, sharpe: 5.4 },
      robustness: { score: null, passed: null },
      backtest_coverage: { dataset_id: "XAUUSD_M15", source: "MT5", start: "2025-09-30",
                           end: "2026-09-25", bars: 24768, available: true },
      risk: { pct: 1.0, source: "global" },
      schedule: { description: "Mon/Wed, London, M15", configured: true, enabled: 1, is_active: false },
      live: { status: liveStatus || "IDLE", trades: 0, total_pnl: null, today_pnl: null,
              win_rate: null, last_result: null },
      position: null, position_open: false, starred: false,
    });
    const all = [
      mk(23, 837, "SURVIVED", "qualified"),
      mk(63, 877, "SURVIVED", "qualified", "ACTIVE"),
      mk(99, 1499, "FAILED", "failed"),
    ];
    /* Filtering is server-side in the real API, so the stub filters too — a
     * client-side-only smoke would never catch a broken filter parameter. */
    const m = /[?&]filter=([^&]*)/.exec(path);
    const wanted = m ? decodeURIComponent(m[1]) : "qualified";
    const match = (b) => wanted === "all" || (wanted === "alive" || wanted === "eligible"
      ? b === "qualified" || b === "alive" : b === wanted);
    const rowsOut = all.filter((r) => match(r.bucket));
    return {
      ok: true, filter: wanted,
      filters: ["qualified", "alive", "eligible", "all", "failed", "excluded", "blocked", "unknown"],
      sort_by: "fitness", sort_key: "fitness",
      counts: { qualified: 2, failed: 1, alive: 0, eligible: 0, excluded: 0, blocked: 0, unknown: 0 },
      filter_totals: { qualified: 2, alive: 2, eligible: 2, all: 3, failed: 1, excluded: 0, blocked: 0, unknown: 0 },
      total: rowsOut.length, returned: rowsOut.length, limit: 100, offset: 0,
      experiment: { run_id: "RUN-20261005-055251", population: 10000,
                    node_number_range: [1, 10000], next_node_number: 10001, is_empty: false,
                    node_numbering: "experiment-local (1..population)" },
      nodes: rowsOut,
      risk: { global_risk_pct: 1.0, limits: {} },
      note: "Metrics are the node's own recorded research/live results.",
    };
  }
  if (path.includes("/api/mt5-demo/status")) return {
    status: "DEMO_ACCOUNT_READY", demo_banner: "DEMO ACCOUNT ONLY — NO REAL MONEY AT RISK",
    bridge_type: "SIMULATOR_BRIDGE", is_connected: true, account_id: "0", broker: "LAB_SIMULATOR",
    server: "SIM", balance: 10000.0, equity: 10000.0, free_margin: 9500.0,
    active_strategies_count: 1, open_positions_count: 0, today_pnl: 0, total_pnl: 0, active_strategies: [240],
  };
  if (path.includes("/api/strategies/") && path.includes("/authoritative")) return AUTHORITATIVE;
  if (path.includes("/api/strategies/")) return DRAWER_NODE;
  if (path.includes("/api/lab/status")) return { current_nodes: 10000, alive: 33, dead: 9967, qualified: 5, generation_number: 39, running: false };
  if (path.includes("/api/mt5-historical/runs/")) {
    return {
      run_id: "HRUN-1", status: "COMPLETED", strategy_id: 837, symbol: "XAUUSD", timeframe: "M15",
      orders_placed: false, is_mt5_data: true,
      data: { scope: "MT5", source: "MT5", dataset_id: "XAUUSD_M15_MT5_v1" },
      schedule: { configured: true, applied_to_bars: true, bars_allowed: 288, bars_blocked: 1562,
                  description: "Mon/Wed, London, M15" },
      verdict: "Completed — 2 trade(s) evaluated by the engine.",
      /* §32 — the run's own data-loading report, as the API returns it */
      coverage: { requested: { start: "2026-09-07T00:00:00+00:00", end: "2026-10-05T23:59:59+00:00",
                               start_date: "2026-09-07", end_date: "2026-10-05" },
                  actual: { start: "2026-09-07T01:00:00+00:00", end: "2026-10-05T05:45:00+00:00",
                            bars: 1850, window: [0, 1850] },
                  dataset: { id: "XAUUSD_M15_MT5_v1", source: "MT5", bars: 1850 },
                  bars_used: 1850, expected_bars: 2708, completeness_pct: 68.316, complete: false,
                  source_label: "MT5 (the terminal's own historical bars)", is_mt5_data: true,
                  period_adjusted: false,
                  quality: { bars: 1850, gapgaps: 0, gaps: 20, missing_bars_total: 858,
                             monotonic_increasing: true, duplicate_timestamps: 0 },
                  missing_periods: [{ after: "2026-09-07T21:15:00+00:00",
                                      before: "2026-09-08T01:00:00+00:00", missing_bars: 14 },
                                    { after: "2026-09-08T23:45:00+00:00",
                                      before: "2026-09-09T01:00:00+00:00", missing_bars: 4 }],
                  interpretation: "completeness is measured against a continuous 24/7 grid for this timeframe; the bars themselves are the stored dataset's own bars and 20 gap(s) are listed for inspection" },
      results: { metrics: { trades: 2, total_return_pct: -0.0008, profit_factor: 0.687,
                            max_drawdown_pct: 0.0025, win_rate: 0.5, net_profit: -0.83,
                            avg_trade: -0.41, expectancy: -0.0001,
                            start_balance: 10000.0, end_balance: 9999.17 },
                 derived: { avg_win: 12.5, avg_loss: -6.25, largest_win: 18.0, largest_loss: -7.0 },
                 coverage: null, unavailable: [] },
    };
  }
  if (path.includes("/api/mt5-historical/runs")) return {
    runs: [{
      run_id: "HRUN-1", status: "COMPLETED", strategy_id: 837, symbol: "XAUUSD", timeframe: "M15",
      period: { start: "2026-09-07", end: "2026-10-05" },
      verdict: "Completed — 2 trade(s) evaluated by the engine.",
      schedule: { configured: true, applied_to_bars: true, bars_allowed: 288, bars_blocked: 1562,
                  description: "Mon/Wed, London, M15" },
      results: { metrics: { trades: 2, total_return_pct: -0.0008, profit_factor: 0.687 } },
    }],
    total: 1,
  };
  if (path.includes("/api/tree")) return { nodes: [], edges: [], total_strategies: 10000 };
  if (path.includes("/api/shortlist")) return { shortlist: [] };
  if (path.includes("/api/logs")) return { logs: [], log_text: "" };
  if (path.includes("/api/jobs")) return { jobs: [] };
  return {};
}

/* ------------------------------------------------------------------- the run */
export async function runSmoke() {
  const results = [];
  /* §5 — value safety. A missing value must stay missing: `Number(null)` is 0,
   * which is how "no quote" became an entry price of 0.00 and how an unset risk
   * became $0. These helpers are the ones every panel renders through. */
  {
    try {
      const asZero = [null, undefined, "", " ", "abc", false, [], {}].filter((v) => numOrNull(v) === 0);
      if (asZero.length) {
        throw new Error(`these missing values were coerced to 0: ${JSON.stringify(asZero)}`);
      }
      if (numOrNull(0) !== 0 || numOrNull("2400.5") !== 2400.5 || numOrNull(12) !== 12) {
        throw new Error("real numbers must still pass through numOrNull");
      }
      // SQLite JSON text must be decoded, or a stored schedule renders as empty
      if (JSON.stringify(safeArr("[0, 2]")) !== "[0,2]") throw new Error("arr() did not decode JSON text");
      if ((objOrNull('{"entry_long":true}') || {}).entry_long !== true) {
        throw new Error("objOrNull() did not decode JSON text");
      }
      results.push({ label: "safe-values:missing-stays-missing", ok: true });
    } catch (e) {
      results.push({ label: "safe-values:missing-stays-missing", ok: false, error: e.message || String(e) });
    }
  }
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
    if (urlStr.includes("/api/mt5-execution/preview")) {
      let body = {};
      try { body = JSON.parse(opts.body || "{}"); } catch { body = {}; }
      previewPosts.push({ url: urlStr, body });
      LAST_PREVIEW_BODY = body;      // lets a stub answer *what was actually asked*
    }
    if (urlStr.includes("/api/live-testing/schedule/") && String(opts.method || "GET").toUpperCase() === "POST") {
      let body = {};
      try { body = JSON.parse(opts.body || "{}"); } catch { body = {}; }
      schedulePosts.push({ url: urlStr, body });
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
  // 5c. V5.1a §6/§7/§32 — Deep Backtest lists qualified nodes with real metrics,
  // multi-selects them, and shows what a completed run actually produced.
  {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => {
        root.render(wrap(<DeepBacktest />));
        await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
      });
      const text = container.textContent || "";
      if (!text.includes("Node_23")) throw new Error(`the qualified node list is missing Node_23 … "${text.slice(0, 200)}"`);
      if (!text.includes("Qualified")) throw new Error("the node filter does not default to Qualified");
      if (!text.includes("RUN-20261005-055251")) throw new Error("the experiment the list describes is not shown");
      if (!text.toLowerCase().includes("deep backtest all qualified")) {
        throw new Error("no 'deep backtest all' action for the selected filter");
      }
      const rowBoxes = Array.from(container.querySelectorAll('input[type="checkbox"]'));
      if (rowBoxes.length < 2) throw new Error("the node rows have no selection checkboxes");
      // selecting a node must arm the batch action with that node
      await act(async () => {
        rowBoxes[rowBoxes.length - 1].dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await Promise.resolve();
      });
      if (!/Deep backtest selected \(1\)/.test(container.textContent || "")) {
        throw new Error("selecting a node did not update the batch action");
      }
      // the runs table must show the verdict + the schedule that was applied
      if (!text.includes("Completed — 2 trade(s) evaluated by the engine.")) {
        throw new Error("a completed run must state its verdict in the table");
      }
      if (!/applied · 288/.test(text)) {
        throw new Error(`the applied schedule is not reported … "${text.slice(0, 400)}"`);
      }

      /* §32/§33 — opening a run's details must show the data-loading report
       * (requested vs used, bars, completeness, gaps, source) and the balance
       * fields, with the derived avg win/loss coming from the backend. */
      /* the node rows also carry a "Details" button (which opens the drawer), so
       * the runs table's own action is the last one on the page */
      const detailsBtns = Array.from(container.querySelectorAll("button"))
        .filter((b) => (b.textContent || "").trim() === "Details");
      const open = detailsBtns[detailsBtns.length - 1];
      if (!open) throw new Error("a run row has no Details action");
      await act(async () => { open.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
      await act(async () => { await new Promise((r) => setTimeout(r, 120)); });
      const det = container.textContent || "";

      if (!/Bars used/.test(det) || !/1850/.test(det)) throw new Error("the run details do not show the bar count actually used");
      if (!/Completeness/.test(det) || !/68\.32/.test(det)) throw new Error("the completeness of the loaded data is not shown");
      if (!/requested/i.test(det) || !/used 2026-09-07/.test(det)) {
        throw new Error(`the requested vs used range is not shown … "${det.slice(0, 300)}"`);
      }
      if (!/missing periods/i.test(det)) throw new Error("the missing periods of the loaded data are not offered");
      if (!det.includes("MT5 (the terminal's own historical bars)")) throw new Error("the data source is not named");
      if (!det.includes("12.5")) throw new Error("avg win from the backend's derived block is not shown");
      if (!det.includes("10000")) throw new Error("the start balance is not shown");
      results.push({ label: "page:DeepBacktest-qualified", ok: true });
    } catch (e) {
      results.push({ label: "page:DeepBacktest-qualified", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
    }
  }

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
      await clickText("Recalculate now");
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

  {
    /* (c) V5.1a §3/§5 — recalculation is unconditional and never silently zero.
     *
     * The reported defect was that with no live quote the panel stopped
     * recalculating (the preview effect returned early while `entry === null`),
     * so input changes produced stale numbers and a zero lot size. Here the
     * market snapshot carries no bid/ask at all, and the panel must still ask
     * the backend, must show the backend's own reason, and must send the typed
     * entry price on the next recalculation. */
    const marketSnapshot = snapshotMarket();
    setMarket({ symbol: "XAUUSD", source: "MT5", bid: null, ask: null, spread: null,
                session_status: "london", trading_available: true, reasons: [],
                tick_age_s: 1.0, market_open: true, indicators: null, bar_close_in: null });
    setPreviewResponse(() => ({ ...PREVIEW_BLOCKED,
      blocked: { code: "INVALID_ENTRY_PRICE",
                 message: "Entry price unavailable for XAUUSD: cannot size the trade",
                 detail: {} },
      symbol_info: { symbol: "XAUUSD", digits: 2, point: 0.01, tick_size: null,
                     tick_value: null, volume_min: null, volume_max: null,
                     volume_step: null, source: "SIMULATOR" } }));
    previewPosts.length = 0;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(wrap(<ManualOrderPanel />)); });
      await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
      if (!previewPosts.length) {
        throw new Error("no quote: the panel did not ask the backend for a preview (the stale-value defect)");
      }
      const text = container.textContent || "";
      if (!text.includes("INVALID_ENTRY_PRICE")) {
        throw new Error(`the backend's refusal is not shown … "${text.slice(0, 220)}"`);
      }
      const entryInput = Array.from(container.querySelectorAll("input"))
        .find((i) => /no quote/.test(i.placeholder || ""));
      if (!entryInput) {
        throw new Error("the entry field does not say that a price has to be supplied");
      }
      // typing an entry price must immediately trigger a new calculation that carries it
      await setValue("Entry", "2400.5");
      await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
      const last = previewPosts[previewPosts.length - 1];
      if (!last || Number(last.body.entry) !== 2400.5) {
        throw new Error(`the typed entry price was not sent (last preview: ${JSON.stringify(last)})`);
      }
      // changing the stop-loss distance must recalculate too (no cached answer)
      const before = previewPosts.length;
      await setValue("Stop loss (pips — required)", "150");
      await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
      if (previewPosts.length <= before) {
        throw new Error("changing the stop-loss distance did not trigger a recalculation");
      }
      if (Number(previewPosts[previewPosts.length - 1].body.sl_pips) !== 150) {
        throw new Error("the new stop-loss distance was not sent to the backend");
      }
      results.push({ label: "ManualOrderPanel:recomputes-without-quote", ok: true });
    } catch (e) {
      results.push({ label: "ManualOrderPanel:recomputes-without-quote", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
      restoreMarketSnapshot(marketSnapshot);
      setPreviewResponse(PREVIEW_OK);
    }
  }

    } catch (e) {
      results.push({ label: "ManualOrderPanel:preview+confirm+result", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
      setExecutionState(EXEC_STATE);          // back to the honest simulator state
    }
  }

  {
    /* V5.1a §11 — a broker-spec sizing round trip in the DOM.
     *
     * The stub answers the way the *backend* answers (the maths itself lives in
     * app/live_testing/risk.py and is covered by the Python broker-spec tests):
     * entry = ask for BUY / bid for SELL, stop and target from pips, and a
     * volume that depends on the money at risk. What is verified here is the
     * panel: it must ask on every input change, must carry the risk amount and
     * the correct side's entry price, and must show what the backend returned —
     * never a stale number and never a zero. */
    const marketSnapshot = snapshotMarket();
    const brokerMarket = { symbol: "XAUUSD", source: "MT5", bid: 2400.0, ask: 2400.3,
                           spread: 3, session_status: "london", trading_available: true,
                           reasons: [], tick_age_s: 0.4, market_open: true,
                           indicators: null, bar_close_in: 12 };
    setMarket(brokerMarket);
    const PIP = 0.1;                     // broker spec: 1 pip = 10 points = 0.10
    setPreviewResponse((body) => {
      const side = String(body.side || "BUY").toUpperCase();
      const entry = Number(body.entry);
      const slPips = Number(body.sl_pips || 0);
      const tpPips = Number(body.tp_pips || 0);
      const sl = side === "BUY" ? entry - slPips * PIP : entry + slPips * PIP;
      const tp = side === "BUY" ? entry + tpPips * PIP : entry - tpPips * PIP;
      const risk = Number(body.risk_amount || 0);
      const volume = risk > 0 ? Number((risk / 100).toFixed(2)) : null;   // 1 lot risks $100/300 pips
      return {
        ok: true, mode: "risk_to_lot",
        symbol_info: { symbol: "XAUUSD", digits: 2, point: 0.01, tick_size: 0.01, tick_value: 1.0,
                       volume_min: 0.01, volume_max: 100, volume_step: 0.01, source: "MT5" },
        quote: { bid: 2400.0, ask: 2400.3, ts: Date.now() / 1000, source: "MT5" },
        levels: { entry, sl, tp, sl_pips: slPips, tp_pips: tpPips, pip_size: PIP,
                  points_per_pip: 10, sl_from_pips: true, tp_from_pips: true, warnings: [] },
        sizing: { volume, actual_risk: risk, risk_per_lot: 100, rounded_down: true },
        estimate: { loss_at_sl: -risk, profit_at_tp: risk * 2, reward_risk: 2,
                    basis: "broker tick size/value" },
        blocked: null,
      };
    });
    previewPosts.length = 0;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(wrap(<ManualOrderPanel />)); });
      await act(async () => { await new Promise((r) => setTimeout(r, 400)); });

      const first = previewPosts[previewPosts.length - 1];
      if (!first) throw new Error("the panel never asked for a preview");
      if (Number(first.body.risk_amount) !== 10) {
        throw new Error(`the default target amount was not sent as the risk amount: ${JSON.stringify(first.body)}`);
      }
      if (Number(first.body.sl_pips) !== 300) {
        throw new Error(`the default stop distance was not sent: ${JSON.stringify(first.body)}`);
      }
      if (Number(first.body.entry) !== 2400.3) {
        throw new Error(`a BUY must be sized from the live ASK (2400.3), sent ${first.body.entry}`);
      }
      let text = container.textContent || "";
      if (!text.includes("0.1")) {
        throw new Error(`the broker-spec volume is not shown … "${text.slice(0, 240)}"`);
      }

      // changing the target amount recalculates immediately, from the new value
      const setField = async (labelText, value) => {
        const labels = Array.from(container.querySelectorAll("label"));
        const label = labels.find((l) => (l.textContent || "").includes(labelText));
        const input = label && label.querySelector("input, select");
        if (!input) {
          throw new Error(`no input for "${labelText}" (labels: ${labels.map((l) => (l.textContent || "").trim()).join(" | ")})`);
        }
        await act(async () => {
          const proto = input.tagName === "SELECT" ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
          Object.getOwnPropertyDescriptor(proto, "value").set.call(input, String(value));
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
          await Promise.resolve();
        });
      };
      const beforeRisk = previewPosts.length;
      await setField("Amount / risk", "20");
      await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
      if (previewPosts.length <= beforeRisk) {
        throw new Error("changing the target amount did not trigger a recalculation");
      }
      const second = previewPosts[previewPosts.length - 1];
      if (Number(second.body.risk_amount) !== 20) {
        throw new Error(`the new target amount was not sent: ${JSON.stringify(second.body)}`);
      }
      text = container.textContent || "";
      if (!text.includes("0.2")) {
        throw new Error(`the volume did not follow the target amount (expected 0.2) … "${text.slice(0, 240)}"`);
      }

      // a SELL must be sized from the live BID, and its levels mirrored
      const beforeSide = previewPosts.length;
      await setField("Side", "SELL");
      await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
      const sidePost = previewPosts.slice(beforeSide).find((p) => String(p.body.side).toUpperCase() === "SELL")
        || previewPosts[previewPosts.length - 1];
      if (String(sidePost.body.side).toUpperCase() !== "SELL") {
        throw new Error(`switching to SELL did not reach the backend: ${JSON.stringify(sidePost.body)}`);
      }
      if (Number(sidePost.body.entry) !== 2400.0) {
        throw new Error(`a SELL must be sized from the live BID (2400.0), sent ${sidePost.body.entry}`);
      }

      // and nothing in the panel reports a zero for a size the backend gave
      const zeros = Array.from(container.querySelectorAll(".mono"))
        .map((n) => (n.textContent || "").trim())
        .filter((t) => /^(0|0\.0+|\$0(\.00)?)$/.test(t));
      if (zeros.length) {
        throw new Error(`the panel shows a zero where a real value is expected: ${zeros.join(", ")}`);
      }
      results.push({ label: "ManualOrderPanel:broker-spec-sizing", ok: true });
    } catch (e) {
      results.push({ label: "ManualOrderPanel:broker-spec-sizing", ok: false, error: e.message || String(e) });
    } finally {
      try { await act(async () => { root.unmount(); }); } catch {}
      restoreMarketSnapshot(marketSnapshot);
      setPreviewResponse(PREVIEW_OK);
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
      /* §8 §9 — the table is fed by the qualified-node index and shows the
       * experiment the node numbers belong to, not a bare row id. */
      if (!text.includes("Node_23")) throw new Error(`the live node table did not list the qualified node … "${text.slice(0, 200)}"`);
      if (!text.includes("RUN-20261005-055251")) {
        throw new Error("the table must state which experiment the node numbers belong to");
      }
      if (!text.includes("0.67 %")) throw new Error("the node's recorded research return is not shown");
      if (!text.includes("(global)")) throw new Error("the table must show whether risk is global or a node override");
      // the default filter is Qualified/Alive/Eligible — a FAILED node must not be in it
      const filterSel = Array.from(container.querySelectorAll("select"))
        .find((x) => Array.from(x.options).some((o) => /Qualified \/ Alive/.test(o.textContent || "")));
      if (!filterSel) throw new Error("no node filter control with the Qualified/Alive/Eligible default");
      if (filterSel.value !== "qualified") throw new Error(`the default filter is ${filterSel.value}, not qualified`);
      const qualOpt = Array.from(filterSel.options).find((o) => /Qualified \/ Alive/.test(o.textContent || ""));
      if (!/\(2\)/.test(qualOpt.textContent || "")) {
        throw new Error(`the filter options must be labelled with the real counts ("${qualOpt.textContent}")`);
      }
      if (!/Failed \(1\)/.test(Array.from(filterSel.options).map((o) => o.textContent).join(" | "))) {
        throw new Error("the Failed option is not labelled with its count");
      }
      if (text.includes("Node_99")) throw new Error("a FAILED node is in the default Qualified/Alive/Eligible view");
      if (!nodeIndexCalls.some((u) => /[?&]filter=qualified/.test(u))) {
        throw new Error(`the index was not asked for the qualified filter (${JSON.stringify(nodeIndexCalls)})`);
      }

      // switching to the Failed filter must actually re-query the index
      await act(async () => {
        filterSel.value = "failed";
        filterSel.dispatchEvent(new Event("change", { bubbles: true }));
        await Promise.resolve(); await Promise.resolve();
      });
      if (!nodeIndexCalls.some((u) => /[?&]filter=failed/.test(u))) {
        throw new Error("changing the filter did not request the failed bucket");
      }
      if (!((container.textContent || "").includes("Node_99"))) {
        throw new Error("the Failed filter did not reveal the failed node");
      }

      // START goes through the real endpoint (POST .../start) and reports back
      await act(async () => { filterSel.value = "qualified"; filterSel.dispatchEvent(new Event("change", { bubbles: true })); await Promise.resolve(); await Promise.resolve(); });
      const start = Array.from(container.querySelectorAll("button")).find((b) => (b.textContent || "").trim() === "START");
      if (!start) throw new Error("no START action for the node that is not enrolled yet");
      await act(async () => { start.dispatchEvent(new MouseEvent("click", { bubbles: true })); await Promise.resolve(); await Promise.resolve(); });
      if (!(nodesTablePosts || []).some((u) => /\/live-testing\/nodes\/837\/start$/.test(u))) {
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

      /* V5.1a §11 — the dialog is fully editable and every control is real:
       * groups for days, sessions, regimes, timeframes and the node's own signal
       * conditions; a window editor; a working Enabled switch; Select all /
       * Clear all; and a summary line that reflects the edit. */
      const boxes = Array.from(container.querySelectorAll('input[type="checkbox"]'));
      if (boxes.length < 12) {
        throw new Error(`expected the schedule groups to render real checkboxes, found ${boxes.length}`);
      }
      if (!/Regimes|Market regimes/.test(text) || !/Timeframes/.test(text) || !/Signal conditions/.test(text)) {
        throw new Error("the dialog must offer regimes, timeframes and the node's signal conditions");
      }
      const labelOfBox = (b) => (b.parentElement?.textContent || "").toLowerCase();
      if (!boxes.some((b) => labelOfBox(b).includes("trending"))) {
        throw new Error("the regimes group has no checkbox");
      }
      if (!boxes.some((b) => labelOfBox(b).includes("m15"))) {
        throw new Error("the timeframes group has no checkbox");
      }
      if (!boxes.some((b) => labelOfBox(b).includes("long entry rule"))) {
        throw new Error("the signal-condition group has no checkbox");
      }
      // a day checkbox must actually toggle (controlled input, not a decoration)
      const monBox = boxes.find((b) => labelOfBox(b).trim().startsWith("monday"));
      if (!monBox || !monBox.checked) throw new Error("Monday should start checked (it is in the stored schedule)");
      await act(async () => { monBox.dispatchEvent(new MouseEvent("click", { bubbles: true })); await Promise.resolve(); });
      if (monBox.checked) throw new Error("clicking a day checkbox did not change the state");
      if (!/unsaved changes/.test(container.textContent || "")) {
        throw new Error("the summary must say the changes are unsaved");
      }
      /* V5.1a §5/§6 — the regime control is a real multi-select driven by the
       * backend's own vocabulary, and its empty-selection meaning is stated in
       * the dialog rather than left to guesswork. */
      if (!/No regime restriction|engine checks this rule before every order/.test(text)) {
        throw new Error("the regimes group must state what its selection means");
      }
      const regimeBoxes = boxes.filter((b) => /trending|ranging|breakout/.test(labelOfBox(b)));
      if (regimeBoxes.length < 2) throw new Error("the regimes group is not a multi-select");
      const trendingBox = regimeBoxes.find((b) => labelOfBox(b).includes("trending"));
      const rangingBox = regimeBoxes.find((b) => labelOfBox(b).includes("ranging"));
      if (!trendingBox?.checked) throw new Error("the stored regime (trending) is not shown as selected");
      if (rangingBox.checked) throw new Error("an unselected regime must not start checked");
      await act(async () => { rangingBox.dispatchEvent(new MouseEvent("click", { bubbles: true })); await Promise.resolve(); });
      if (!rangingBox.checked) throw new Error("selecting a second regime did not check it");
      if (!trendingBox.checked) throw new Error("adding a regime must not clear the first one");

      // Select all must restore the whole group
      const selectAll = Array.from(container.querySelectorAll("button")).find((b) => (b.textContent || "").trim() === "Select all");
      if (!selectAll) throw new Error("no Select all control for the day group");
      await act(async () => { selectAll.dispatchEvent(new MouseEvent("click", { bubbles: true })); await Promise.resolve(); });
      if (!monBox.checked) throw new Error("Select all did not re-check Monday");

      // saving must send every group, not only the legacy fields
      const save = Array.from(container.querySelectorAll("button")).find((b) => (b.textContent || "").includes("Save & enforce"));
      if (!save) throw new Error("no Save & enforce action");
      await act(async () => { save.dispatchEvent(new MouseEvent("click", { bubbles: true })); await Promise.resolve(); await Promise.resolve(); });
      const sent = schedulePosts[schedulePosts.length - 1];
      if (!sent) throw new Error("Save did not call the schedule endpoint");
      for (const key of ["days", "sessions", "regimes", "timeframes", "conditions", "windows", "enabled", "timezone"]) {
        if (!(key in sent.body)) throw new Error(`the saved payload is missing "${key}": ${JSON.stringify(sent.body)}`);
      }
      if (sent.body.enabled !== true) throw new Error("the Enabled switch was not sent");
      if (!(sent.body.regimes || []).includes("trending")) throw new Error("the regimes selection was not sent");
      if (!(sent.body.regimes || []).includes("ranging")) {
        throw new Error(`the multi-select regime selection was not sent: ${JSON.stringify(sent.body.regimes)}`);
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
