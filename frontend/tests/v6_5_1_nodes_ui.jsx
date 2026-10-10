/* V6.5.1 §11 — frontend acceptance harness for the unified Nodes table.
 *
 * Drives the REAL LiveNodeTable / Mt5AccountPanel / BuildIdentityChip in jsdom
 * against a stateful fetch stub and asserts the acceptance behaviours the
 * operator's defect report requires:
 *
 *   - STOPPED shows START -> STARTING (pending) -> backend-confirmed RUNNING
 *     shows STOP -> STOPPING -> STOPPED shows START again;
 *   - failures are visible and accurate (a failed start restores the state; a
 *     failed stop never claims STOPPED);
 *   - duplicate clicks cannot fire duplicate lifecycle requests;
 *   - a refresh (remount) restores the backend's authoritative state;
 *   - a STALE polling response can never revert RUNNING to STOPPED;
 *   - nodes have independent lifecycle states;
 *   - Risk / Max trades / SL offset / TP offset are editable INLINE in the row,
 *     saved through the node-config API, with saving/error states — a failed
 *     edit is never displayed as saved, and the saved value comes from the
 *     backend's response;
 *   - both risk modes are selectable and validated;
 *   - the MT5 account panel shows live balance/equity/free margin or an
 *     explicit unavailable state (never 0, never a fake);
 *   - the table exposes ONE risk editor per node (no conflicting duplicates);
 *   - the release identity from /system/build is visible.
 */
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react-dom/test-utils";

import { LabContext } from "../src/App.jsx";
import { LiveNodeTable } from "../src/components/LiveNodeIndex.jsx";
import { RiskStrip } from "../src/components/LiveTestingPanels.jsx";
import Mt5AccountPanel from "../src/components/Mt5AccountPanel.jsx";
import BuildIdentityChip from "../src/components/BuildIdentityChip.jsx";

const LAB_VALUE = {
  status: { app_version: "V6.5.1" }, events: [], connected: true, refreshStatus: () => {},
  openStrategy: () => {}, selectedStrategyId: null, setSelectedStrategyId: () => {},
  shortlist: [], toggleShortlist: async () => ({}), navigateTab: () => {},
};
const wrap = (el) => <LabContext.Provider value={LAB_VALUE}>{el}</LabContext.Provider>;

/* ------------------------------------------------------------ server state */
function makeRow(id, over = {}) {
  return {
    node_id: id, strategy_id: id, research_node_num: id - 700,
    node_label: `Node #${id}`, research_label: `research #${id - 700}`,
    experiment: "RUN-TEST", run_id: "RUN-TEST", generation: 2, parent_id: null,
    innovation: null, family: null, symbol: "XAUUSD", timeframe: "M15",
    direction: "both", fitness: 1.25,
    signal_logic: { entry_long: "RSI cross" },
    bucket: "qualified", bucket_label: "Qualified", bucket_reason: "survived",
    v5_status: "QUALIFIED", qualification: "QUALIFIED", survival_evidence: "ok",
    failure_reason: null,
    metrics: { return_pct: 0.0067, profit_factor: 1.4, max_drawdown_pct: 0.02,
               win_rate: 0.55, trades: 40, net_profit: 12, expectancy: 0.3,
               avg_trade: 0.3, sharpe: 1.1 },
    robustness: { score: 0.72, passed: true },
    backtest_coverage: { source: "XAUUSD/M15", bars: 5000 },
    risk: { pct: 1.0, override_pct: null, source: "global_default",
            limits: { risk_pct_default: 1.0, risk_pct_max: 5.0 } },
    risk_mode: { mode: "PERCENT", capital_basis: "EQUITY", risk_pct: null,
                 risk_amount: null, valid: true, error: null },
    schedule: { description: "Mon–Fri, all sessions", days: [0, 1, 2, 3, 4],
                sessions: ["london"], timeframes: ["M15"], windows: null,
                timezone: "UTC", enabled: true, configured: true, is_active: true },
    live: { status: "IDLE", trades: 0, closed: 0, total_pnl: null, today_pnl: null,
            win_rate: null },
    position: null, position_open: false, starred: false, eligibility_note: null,
    is_active: false,
    worker: { state: "IDLE", running: false, task_id: null, last_error: "" },
    lifecycle: "STOPPED",
    max_active_trades: null, max_active_trades_source: "DEFAULT",
    max_active_trades_effective: 1, max_active_trades_default: 1,
    sl_offset_pips: null, tp_offset_pips: null, offsets_active: false,
    ...over,
  };
}

export const server = {
  nodes: [makeRow(718), makeRow(719), makeRow(720)],
  posts: [],            // {url, body}
  getCounts: {},
  account: {
    ok: true, available: true, connected: true, is_simulated: false,
    source: "MT5", data_kind: "LIVE_ACCOUNT", checked_at: Date.now() / 1000,
    account: { login: 51041234, server: "Demo-Server", currency: "USD",
               balance: 10000.0, equity: 10250.5, margin_free: 9800.25,
               leverage: 100, trade_mode: 0, trade_mode_name: "DEMO" },
  },
  build: {
    app: "EVOLUTIONARY TRADING RESEARCH LAB", release: "V6.5.1",
    git_commit: "68a183b31363", src_hash: "abc123def4567890",
    dist_matches_src: true, dist_status: "FRESH",
    served: { index_sha256: "f9af174818039dafa41813d6e285a328e9f0b74a66dff1a0b226a0dfc909d83a",
              entry_assets: ["assets/index-BeNlcShL.js"] },
    process: { code_hash: "c073de982d0f", start_token: "LAB-TEST", running: true },
    loaded: { index_sha256: "f9af174818039dafa41813d6e285a328e9f0b74a66dff1a0b226a0dfc909d83a",
              entry_assets: ["assets/index-BeNlcShL.js"] },
  },
  failNext: null,        // {match: RegExp, message: "..."| object detail}
  hold: false,           // hold GET /api/nodes responses for the stale test
  held: [],
  failFlags: { start: false, stop: false, config: false },
};

function snapshotNodes() {
  return {
    ok: true, filter: "all", total: server.nodes.length,
    nodes: JSON.parse(JSON.stringify(server.nodes)),
    counts: {}, filter_totals: {}, truncated: false,
    experiment: { run_id: "RUN-TEST", population: server.nodes.length,
                  node_number_range: [700, 702], is_empty: false },
    boundary_counts: { population_live_eligible: server.nodes.length },
    note: "",
  };
}

export function payloadFor(url, method = "GET") {
  const u = String(url);
  server.getCounts[u.split("?")[0]] = (server.getCounts[u.split("?")[0]] || 0) + 1;
  if (server.failNext && server.failNext.match.test(u)) {
    const f = server.failNext;
    server.failNext = null;
    const err = new Error(f.message || "injected failure");
    err.detail = f.detail || { message: f.message || "injected failure" };
    err.data = err.detail;
    throw err;
  }
  if (u.startsWith("/api/nodes?") || u === "/api/nodes") {
    if (method === "GET") {
      const body = snapshotNodes();
      if (server.hold) {
        let resolveFn;
        const p = new Promise((res) => { resolveFn = res; });
        server.held.push({ url: u, body, resolve: resolveFn });
        return p;                        // delivered late via releaseHeld()
      }
      return body;
    }
  }
  if (u.startsWith("/api/nodes/populations")) {
    return { state: { TOTAL: server.nodes.length, QUALIFIED: server.nodes.length }, authority: "test" };
  }
  const startM = u.match(/\/api\/live-testing\/nodes\/(\d+)\/start/);
  if (startM && method === "POST") {
    if (server.failFlags.start) {
      const err = new Error("simulated start failure");
      err.detail = { code: "WORKER_START_FAILED", message: "simulated start failure" };
      err.data = err.detail;
      throw err;
    }
    const id = Number(startM[1]);
    const row = server.nodes.find((r) => r.node_id === id);
    row.worker = { state: "RUNNING", running: true, task_id: `ltw-${id}-test`, last_error: "" };
    row.lifecycle = "RUNNING";
    row.is_active = true;
    row.live = { ...row.live, status: "RUNNING" };
    return { ok: true, strategy_id: id, is_active: true, status: "RUNNING",
             worker: row.worker, worker_running: true, already_running: false,
             task_id: row.worker.task_id, note: "worker running" };
  }
  const stopM = u.match(/\/api\/live-testing\/nodes\/(\d+)\/stop/);
  if (stopM && method === "POST") {
    if (server.failFlags.stop) {
      const err = new Error("simulated stop failure");
      err.detail = { code: "WORKER_STOP_FAILED", message: "simulated stop failure" };
      err.data = err.detail;
      throw err;
    }
    const id = Number(stopM[1]);
    const row = server.nodes.find((r) => r.node_id === id);
    row.worker = { state: "STOPPED", running: false, task_id: `ltw-${id}-test`, last_error: "" };
    row.lifecycle = "STOPPED";
    row.is_active = false;
    row.live = { ...row.live, status: "STOPPED" };
    return { ok: true, strategy_id: id, is_active: false, status: "STOPPED",
             worker: row.worker, worker_stopped: true, already_stopped: false,
             positions_touched: false, note: "stopped" };
  }
  if (u === "/api/live-testing/nodes/stop-all" && method === "POST") {
    for (const row of server.nodes) {
      row.worker = { state: "STOPPED", running: false, task_id: null, last_error: "" };
      row.lifecycle = "STOPPED"; row.is_active = false;
    }
    return { ok: true, stopped: server.nodes.length, unenrolled_nodes: server.nodes.length,
             positions_touched: false };
  }
  const cfgM = u.match(/\/api\/live-testing\/nodes\/(\d+)\/config/);
  if (cfgM && method === "POST") {
    if (server.failFlags.config) {
      const err = new Error("simulated config failure");
      err.detail = { code: "CONFIG_FAILED", message: "simulated config failure" };
      err.data = err.detail;
      throw err;
    }
    const id = Number(cfgM[1]);
    const row = server.nodes.find((r) => r.node_id === id);
    const body = server.lastConfigBody || {};
    if ("risk_mode" in body || "risk_pct" in body || "risk_amount" in body || "risk_capital_basis" in body) {
      const mode = body.risk_mode !== undefined ? body.risk_mode : row.risk_mode.mode;
      const basis = body.risk_capital_basis !== undefined ? body.risk_capital_basis : row.risk_mode.capital_basis;
      const amount = body.risk_amount !== undefined ? body.risk_amount : row.risk_mode.risk_amount;
      const pct = body.risk_pct !== undefined ? body.risk_pct : row.risk.risk_pct;
      if (mode === "AMOUNT" && (amount === null || amount === undefined || Number(amount) <= 0)) {
        const err = new Error("RISK_AMOUNT_INVALID: constant monetary risk needs a positive risk_amount");
        err.detail = { code: "RISK_AMOUNT_INVALID", message: "constant monetary risk needs a positive risk_amount" };
        err.data = err.detail;
        throw err;
      }
      row.risk_mode = { ...row.risk_mode, mode: mode || "PERCENT", capital_basis: basis || "EQUITY",
                        risk_amount: amount === undefined ? null : amount,
                        risk_pct: pct === undefined ? null : pct };
      if (mode === "PERCENT" && pct !== null && pct !== undefined) {
        row.risk = { ...row.risk, pct: Number(pct), override_pct: Number(pct), source: "CUSTOM" };
      }
      if (mode === "AMOUNT") row.risk = { ...row.risk, pct: 0, override_pct: null, source: "CUSTOM" };
    }
    if ("max_active_trades" in body) {
      row.max_active_trades = body.max_active_trades;
      row.max_active_trades_source = body.max_active_trades === null ? "DEFAULT" : "CUSTOM";
      row.max_active_trades_effective = body.max_active_trades === null
        ? row.max_active_trades_default : Number(body.max_active_trades);
    }
    if ("sl_offset_pips" in body) {
      row.sl_offset_pips = body.sl_offset_pips;
      row.offsets_active = Boolean(row.sl_offset_pips || row.tp_offset_pips);
    }
    if ("tp_offset_pips" in body) {
      row.tp_offset_pips = body.tp_offset_pips;
      row.offsets_active = Boolean(row.sl_offset_pips || row.tp_offset_pips);
    }
    return { ok: true, strategy_id: id,
             config: { strategy_id: id, risk_pct: row.risk.override_pct,
                       risk_mode: row.risk_mode.mode, risk_amount: row.risk_mode.risk_amount,
                       risk_capital_basis: row.risk_mode.capital_basis,
                       max_positions: row.max_active_trades,
                       sl_offset_pips: row.sl_offset_pips, tp_offset_pips: row.tp_offset_pips },
             risk_limits: { risk_pct_default: 1, risk_pct_max: 5 } };
  }
  if (u === "/api/mt5/account") return JSON.parse(JSON.stringify(server.account));
  if (u === "/system/build") return JSON.parse(JSON.stringify(server.build));
  if (u.startsWith("/api/live-testing/settings") && method === "POST") {
    return { ok: true, live_testing: { risk_pct_default: 1, risk_pct_max: 5 } };
  }
  return {};
}

/* held-response helpers (stale-response test) */
export function holdMode(on) { server.hold = !!on; }
export function releaseHeld() {
  // deliver the held (stale) responses late: their promises resolve with the
  // payload captured at REQUEST time
  const held = server.held.splice(0, server.held.length);
  for (const h of held) h.resolve(h.body);
  return held;
}

/* ------------------------------------------------------------------ helpers */
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function flush() {
  await act(async () => { await Promise.resolve(); await wait(1); await Promise.resolve(); });
}
function textOf(container) { return container.textContent || ""; }
function buttons(container) { return Array.from(container.querySelectorAll("button")); }
function findButton(container, re) {
  return buttons(container).find((b) => re.test((b.textContent || "") + " " + (b.getAttribute("title") || "")));
}
async function click(el) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    await Promise.resolve();
  });
  await flush();
}
function setNativeValue(input, value) {
  const proto = Object.getPrototypeOf(input);
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  act(() => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
function rowFor(container, nodeId) {
  const link = Array.from(container.querySelectorAll("a"))
    .find((a) => (a.textContent || "").includes(`Node #${nodeId}`));
  return link ? link.closest("tr") : null;
}

export async function runSmoke() {
  const results = [];
  const R = (label, fn) => fn()
    .then(() => results.push({ label, ok: true }))
    .catch((e) => results.push({ label, ok: false, error: e.message || String(e) }));

  const render = async (el) => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(wrap(el)); await Promise.resolve(); await Promise.resolve(); });
    await flush();
    return { container, root, unmount: async () => { await act(async () => { root.unmount(); }); container.remove(); } };
  };

  /* 1-3 + 5-6 + 9: the lifecycle story on two independent nodes ------------- */
  await R("lifecycle:STOPPED shows START, START->STARTING->RUNNING shows STOP", async () => {
    server.nodes = [makeRow(718), makeRow(719)];
    const { container, unmount } = await render(<LiveNodeTable />);
    await flush();
    const r718 = rowFor(container, 718);
    if (!r718) throw new Error("node 718 row not rendered");
    const startBtn = buttons(r718).find((b) => b.textContent.trim() === "START");
    if (!startBtn) throw new Error(`a STOPPED node must show START (row: ${textOf(r718).slice(0, 160)})`);

    await click(startBtn);
    // after the POST + reload the backend confirmed RUNNING -> STOP
    const r718b = rowFor(container, 718);
    const stopBtn = buttons(r718b).find((b) => b.textContent.trim() === "STOP");
    if (!stopBtn) throw new Error(`a backend-confirmed RUNNING node must show STOP (row: ${textOf(r718b).slice(0, 200)})`);
    if (!textOf(r718b).includes("RUNNING")) throw new Error("the Lifecycle cell must show RUNNING");
    // node 719 must be untouched
    const r719 = rowFor(container, 719);
    if (!buttons(r719).some((b) => b.textContent.trim() === "START")) {
      throw new Error("starting node 718 must not change node 719's control");
    }
    await unmount();
  });

  await R("lifecycle:STOP->STOPPING->STOPPED shows START again", async () => {
    const { container, unmount } = await render(<LiveNodeTable />);
    await flush();
    const r = rowFor(container, 718);
    const stopBtn = buttons(r).find((b) => b.textContent.trim() === "STOP");
    if (!stopBtn) throw new Error("the RUNNING node must show STOP before the click");
    await click(stopBtn);
    const r2 = rowFor(container, 718);
    if (!buttons(r2).some((b) => b.textContent.trim() === "START")) {
      throw new Error(`after a confirmed stop the control must be START (row: ${textOf(r2).slice(0, 200)})`);
    }
    if (!textOf(r2).includes("STOPPED")) throw new Error("the Lifecycle cell must show STOPPED");
    await unmount();
  });

  await R("lifecycle:pending states STARTING/STOPPING are visible and block duplicates", async () => {
    // make the start POST slow via a wrapped fetch counter
    let startCalls = 0;
    const origFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts = {}) => {
      const u = String(url);
      if (u.includes("/start") && opts.method === "POST") {
        startCalls += 1;
        await wait(30);
      }
      return origFetch(url, opts);
    };
    try {
      server.nodes = [makeRow(718), makeRow(719)];
      server.failFlags = { start: false, stop: false, config: false };
      const { container, unmount } = await render(<LiveNodeTable />);
      await flush();
      const r = rowFor(container, 718);
      const startBtn = buttons(r).find((b) => b.textContent.trim() === "START");
      // two rapid clicks: the second must NOT fire a second POST
      await act(async () => {
        startBtn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
        await Promise.resolve();
      });
      const midText = textOf(rowFor(container, 718));
      if (!midText.includes("STARTING")) {
        throw new Error(`clicking START must immediately show STARTING (row: ${midText.slice(0, 160)})`);
      }
      // duplicate click while pending must be blocked (disabled + no 2nd POST)
      const again = buttons(rowFor(container, 718)).find((b) => /STARTING/.test(b.textContent || ""));
      if (!again) throw new Error("the pending control must read STARTING…");
      if (!again.disabled) throw new Error("the STARTING control must be disabled");
      await act(async () => {
        again.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
        await Promise.resolve();
      });
      if (startCalls > 1) throw new Error(`duplicate click fired ${startCalls} START requests`);
      await wait(60);
      await flush();
      if (!buttons(rowFor(container, 718)).some((b) => b.textContent.trim() === "STOP")) {
        throw new Error("after the confirm the control must be STOP");
      }
      if (startCalls !== 1) throw new Error(`expected exactly 1 START request, saw ${startCalls}`);
      await unmount();
    } finally {
      globalThis.fetch = origFetch;
    }
  });

  await R("lifecycle:start failure shows the error and restores backend state", async () => {
    server.nodes = [makeRow(718)];
    server.failFlags = { start: true, stop: false, config: false };
    const { container, unmount } = await render(<LiveNodeTable />);
    await flush();
    const r = rowFor(container, 718);
    await click(buttons(r).find((b) => b.textContent.trim() === "START"));
    const body = textOf(container);
    if (!/simulated start failure|WORKER_START_FAILED/i.test(body)) {
      throw new Error(`a failed start must show the error (page: ${body.slice(0, 300)})`);
    }
    const r2 = rowFor(container, 718);
    if (!buttons(r2).some((b) => b.textContent.trim() === "START")) {
      throw new Error("after a failed start the control must be restored from backend state (START)");
    }
    server.failFlags.start = false;
    await unmount();
  });

  await R("lifecycle:stop failure keeps STOP and never claims STOPPED", async () => {
    server.nodes = [makeRow(718, {
      is_active: true, lifecycle: "RUNNING",
      worker: { state: "RUNNING", running: true, task_id: "ltw-718", last_error: "" },
    })];
    server.failFlags = { start: false, stop: true, config: false };
    const { container, unmount } = await render(<LiveNodeTable />);
    await flush();
    const r = rowFor(container, 718);
    await click(buttons(r).find((b) => b.textContent.trim() === "STOP"));
    const body = textOf(container);
    if (!/simulated stop failure|WORKER_STOP_FAILED/i.test(body)) {
      throw new Error("a failed stop must show the error");
    }
    const r2 = rowFor(container, 718);
    if (!buttons(r2).some((b) => b.textContent.trim() === "STOP")) {
      throw new Error("a failed stop must keep showing STOP (the node is still running)");
    }
    if (textOf(r2).includes("STOPPED")) throw new Error("a failed stop must not display STOPPED");
    server.failFlags.stop = false;
    await unmount();
  });

  await R("lifecycle:refresh restores state from the backend (no hardcoded default)", async () => {
    server.nodes = [makeRow(718, {
      is_active: true, lifecycle: "RUNNING",
      worker: { state: "RUNNING", running: true, task_id: "ltw-718", last_error: "" },
    })];
    const { container, unmount } = await render(<LiveNodeTable />);
    await flush();
    const r = rowFor(container, 718);
    if (!buttons(r).some((b) => b.textContent.trim() === "STOP")) {
      throw new Error("after remount (refresh) a RUNNING node must show STOP without clicking START");
    }
    await unmount();
  });

  await R("lifecycle:a stale response cannot revert RUNNING to STOPPED", async () => {
    server.nodes = [makeRow(718, {
      is_active: true, lifecycle: "RUNNING",
      worker: { state: "RUNNING", running: true, task_id: "ltw-718", last_error: "" },
    })];
    const { container, unmount } = await render(<LiveNodeTable />);
    await flush();
    if (!buttons(rowFor(container, 718)).some((b) => b.textContent.trim() === "STOP")) {
      throw new Error("setup: the RUNNING node must show STOP");
    }
    // hold a request that captures the CURRENT truth (RUNNING) ... then flip the
    // server to the OPPOSITE truth (STOPPED) and answer a newer request first.
    holdMode(true);
    const reloadBtn = findButton(container, /^reload$/i) || findButton(container, /Reload/);
    await act(async () => {
      reloadBtn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await Promise.resolve();
    });
    // change the authoritative truth mid-flight
    server.nodes[0].lifecycle = "STOPPED";
    server.nodes[0].is_active = false;
    server.nodes[0].worker = { state: "STOPPED", running: false, task_id: "ltw-718", last_error: "" };
    holdMode(false);
    // a NEWER request (different sort direction -> different GET path, so the
    // in-flight de-dup cannot alias it to the held one) returns the new truth
    const sortBtn = findButton(container, /desc ▼|asc ▲/);
    await click(sortBtn);
    await flush();
    if (!buttons(rowFor(container, 718)).some((b) => b.textContent.trim() === "START")) {
      throw new Error("the newer response (STOPPED) must be applied");
    }
    // now deliver the STALE held response — it was captured BEFORE the flip, so
    // it carries RUNNING rows. It must be discarded (its load was superseded),
    // never reverting the table to STOP.
    const held = releaseHeld();
    if (!held.length) throw new Error("the first reload did not get held");
    await flush();
    const r = rowFor(container, 718);
    if (!buttons(r).some((b) => b.textContent.trim() === "START")) {
      throw new Error("a stale response reverted the lifecycle state (must stay at the newer truth)");
    }
    await unmount();
  });

  /* inline editors ---------------------------------------------------------- */
  await R("risk:editable inline; saved value comes from the backend response", async () => {
    server.nodes = [makeRow(718), makeRow(719)];
    const { container, unmount } = await render(<LiveNodeTable />);
    await flush();
    const r = rowFor(container, 718);
    const riskBtn = buttons(r).find((b) => /Risk:/i.test(b.getAttribute("title") || ""));
    if (!riskBtn) throw new Error("the Risk cell must be visibly editable (click target with a Risk title)");
    await click(riskBtn);
    const row = rowFor(container, 718);
    const inputs = Array.from(row.querySelectorAll("input[type=number]"));
    const selects = Array.from(row.querySelectorAll("select"));
    if (!inputs.length) throw new Error("clicking the Risk cell must open a numeric editor");
    setNativeValue(inputs[0], "2.5");
    const saveBtn = buttons(row).find((b) => b.textContent.trim() === "Save");
    if (!saveBtn) throw new Error("the editor must offer a clear Save action");
    server.lastConfigBody = null;
    const origFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts = {}) => {
      if (String(url).includes("/config") && opts.method === "POST") {
        server.lastConfigBody = JSON.parse(opts.body || "{}");
      }
      return origFetch(url, opts);
    };
    try {
      await click(saveBtn);
    } finally {
      globalThis.fetch = origFetch;
    }
    if (!server.lastConfigBody || Number(server.lastConfigBody.risk_pct) !== 2.5) {
      throw new Error(`the saved risk must be posted to the node-config API (got ${JSON.stringify(server.lastConfigBody)})`);
    }
    const after = textOf(rowFor(container, 718));
    if (!after.includes("2.5")) throw new Error(`the saved value must be shown from the result (row: ${after.slice(0, 200)})`);
    if (!/\(node\)/.test(after)) throw new Error("the Risk cell must say the value is a node override");
    await unmount();
  });

  await R("risk:Escape cancels and a failed save is never shown as saved", async () => {
    server.nodes = [makeRow(718)];
    const { container, unmount } = await render(<LiveNodeTable />);
    await flush();
    let r = rowFor(container, 718);
    await click(buttons(r).find((b) => /Risk:/i.test(b.getAttribute("title") || "")));
    r = rowFor(container, 718);
    const input = r.querySelector("input[type=number]");
    setNativeValue(input, "9.9");
    // Escape must cancel
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      await Promise.resolve();
    });
    await flush();
    if (textOf(rowFor(container, 718)).includes("9.9")) {
      throw new Error("Escape must cancel the edit without applying it");
    }
    // now a failing save
    await click(buttons(rowFor(container, 718)).find((b) => /Risk:/i.test(b.getAttribute("title") || "")));
    r = rowFor(container, 718);
    const input2 = r.querySelector("input[type=number]");
    setNativeValue(input2, "4.4");
    server.failFlags.config = true;
    server.lastConfigBody = null;
    await click(buttons(r).find((b) => b.textContent.trim() === "Save"));
    const rowText = textOf(rowFor(container, 718));
    if (!/simulated config failure|CONFIG_FAILED/i.test(rowText + textOf(container))) {
      throw new Error("a failed save must display the error");
    }
    // the FAILED edit must never be presented as the saved value: the display
    // (post-reload authoritative row) must not carry 4.4 as the risk
    const displayCell = buttons(rowFor(container, 718))
      .find((b) => /Risk:/i.test(b.getAttribute("title") || ""));
    if (displayCell && /4\.4/.test(displayCell.textContent || "")) {
      throw new Error("a FAILED edit must not be displayed as saved");
    }
    server.failFlags.config = false;
    await unmount();
  });

  await R("risk:both modes selectable and validated (Mode A % / Mode B money)", async () => {
    server.nodes = [makeRow(718)];
    const { container, unmount } = await render(<LiveNodeTable />);
    await flush();
    await click(buttons(rowFor(container, 718)).find((b) => /Risk:/i.test(b.getAttribute("title") || "")));
    let r = rowFor(container, 718);
    const modeSel = Array.from(r.querySelectorAll("select"))
      .find((s) => Array.from(s.options).some((o) => o.value === "AMOUNT"));
    if (!modeSel) throw new Error("the Risk editor must offer both risk modes");
    const opts = Array.from(modeSel.options).map((o) => o.value);
    if (!opts.includes("PERCENT") || !opts.includes("AMOUNT")) {
      throw new Error(`both modes must be selectable (got ${opts.join(",")})`);
    }
    await act(async () => {
      modeSel.value = "AMOUNT";
      modeSel.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });
    await flush();
    r = rowFor(container, 718);
    // invalid amount -> validation error, nothing saved
    const input = r.querySelector("input[type=number]");
    setNativeValue(input, "-5");
    server.lastConfigBody = null;
    await click(buttons(r).find((b) => b.textContent.trim() === "Save"));
    const rowText = textOf(rowFor(container, 718));
    if (!/must be a positive number/i.test(rowText)) {
      throw new Error("an invalid risk amount must produce a clear validation error");
    }
    if (server.lastConfigBody) {
      throw new Error("an invalid value must not be posted/saved");
    }
    // valid amount -> saved via AMOUNT mode
    setNativeValue(r.querySelector("input[type=number]"), "25");
    const origFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts = {}) => {
      if (String(url).includes("/config") && opts.method === "POST") {
        server.lastConfigBody = JSON.parse(opts.body || "{}");
      }
      return origFetch(url, opts);
    };
    try {
      await click(buttons(rowFor(container, 718)).find((b) => b.textContent.trim() === "Save"));
    } finally {
      globalThis.fetch = origFetch;
    }
    if (!server.lastConfigBody || server.lastConfigBody.risk_mode !== "AMOUNT"
        || Number(server.lastConfigBody.risk_amount) !== 25) {
      throw new Error(`Mode B must post risk_mode=AMOUNT with the amount (got ${JSON.stringify(server.lastConfigBody)})`);
    }
    await unmount();
  });

  await R("limits:Max trades / SL offset / TP offset editable in the row", async () => {
    server.nodes = [makeRow(718)];
    const { container, unmount } = await render(<LiveNodeTable />);
    await flush();
    const posts = [];
    const origFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts = {}) => {
      if (String(url).includes("/config") && opts.method === "POST") {
        posts.push(JSON.parse(opts.body || "{}"));
      }
      return origFetch(url, opts);
    };
    try {
      // max trades
      let r = rowFor(container, 718);
      await click(buttons(r).find((b) => /max active trades/i.test(b.getAttribute("title") || "")));
      r = rowFor(container, 718);
      setNativeValue(r.querySelector("input[type=number]"), "3");
      await click(buttons(r).find((b) => b.textContent.trim() === "OK"));
      // SL offset
      r = rowFor(container, 718);
      await click(buttons(r).find((b) => /SL offset/i.test(b.getAttribute("title") || "")));
      r = rowFor(container, 718);
      setNativeValue(r.querySelector("input[type=number]"), "5");
      await click(buttons(r).find((b) => b.textContent.trim() === "OK"));
      // TP offset
      r = rowFor(container, 718);
      await click(buttons(r).find((b) => /TP offset/i.test(b.getAttribute("title") || "")));
      r = rowFor(container, 718);
      setNativeValue(r.querySelector("input[type=number]"), "-2");
      await click(buttons(r).find((b) => b.textContent.trim() === "OK"));
    } finally {
      globalThis.fetch = origFetch;
    }
    const fields = posts.map((p) => Object.keys(p).sort().join(",")).join(" | ");
    if (!posts.some((p) => Number(p.max_active_trades) === 3)) throw new Error(`max trades not saved (${fields})`);
    if (!posts.some((p) => Number(p.sl_offset_pips) === 5)) throw new Error(`SL offset not saved (${fields})`);
    if (!posts.some((p) => Number(p.tp_offset_pips) === -2)) throw new Error(`TP offset not saved (${fields})`);
    const after = textOf(container);
    if (!after.includes("3") || !after.includes("5")) throw new Error("saved values must be visible after save");
    await unmount();
  });

  await R("schedule:structured editor is reachable from the row", async () => {
    server.nodes = [makeRow(718)];
    const { container, unmount } = await render(<LiveNodeTable />);
    await flush();
    const r = rowFor(container, 718);
    const schedBtn = buttons(r).find((b) => ["edit", "default", "disabled"].includes((b.textContent || "").trim()));
    if (!schedBtn) throw new Error("the Schedule cell must open the structured schedule editor");
    await click(schedBtn);
    // the dialog mounts (ScheduleDialog) — the page must not go blank and the
    // schedule is edited through the structured dialog, not a bare text box
    const body = textOf(container);
    if (!body.length) throw new Error("the page went blank opening the schedule");
    if (!/schedule/i.test(body)) throw new Error("the structured schedule dialog did not open");
    await unmount();
  });

  await R("table:exactly one risk editor surface per node (no duplicate editors)", async () => {
    server.nodes = [makeRow(718)];
    const { container, unmount } = await render(<LiveNodeTable />);
    await flush();
    const riskCells = buttons(rowFor(container, 718))
      .filter((b) => /Risk:/i.test(b.getAttribute("title") || ""));
    if (riskCells.length !== 1) {
      throw new Error(`the row must expose exactly ONE risk editor (found ${riskCells.length})`);
    }
    // the RiskStrip summary must be read-only: no node-risk inputs
    const c2 = document.createElement("div");
    document.body.appendChild(c2);
    const root2 = createRoot(c2);
    await act(async () => {
      root2.render(wrap(<RiskStrip nodes={server.nodes} onChanged={() => {}} lab={{ equity: 10000 }} />));
      await Promise.resolve();
    });
    await flush();
    const summaryInputs = Array.from(c2.querySelectorAll("input")).filter((i) => i.getAttribute("placeholder") === "%");
    if (summaryInputs.length !== 0) {
      throw new Error("the per-node risk summary must not contain a second editor (placeholder '%' inputs)");
    }
    await act(async () => { root2.unmount(); });
    await unmount();
  });

  /* account panel ----------------------------------------------------------- */
  await R("account:live balance/equity/free margin with connection + freshness", async () => {
    const { container, unmount } = await render(<Mt5AccountPanel />);
    await flush();
    const body = textOf(container);
    for (const v of ["10000.00", "10250.50", "9800.25"]) {
      if (!body.includes(v)) throw new Error(`account value ${v} not shown (${body.slice(0, 300)})`);
    }
    if (!/CONNECTED/.test(body)) throw new Error("the connection state must be visible");
    if (!/as of/.test(body)) throw new Error("the snapshot freshness must be visible");
    await unmount();
  });

  await R("account:unavailable state is explicit (N/A, never 0)", async () => {
    server.account = {
      ok: true, available: false, connected: false, is_simulated: false,
      source: "MT5", data_kind: "UNAVAILABLE", checked_at: Date.now() / 1000,
      account: null, unavailable_reason: "no account is logged in (account_info() returned nothing)",
    };
    const { container, unmount } = await render(<Mt5AccountPanel />);
    await flush();
    const body = textOf(container);
    if (!/ACCOUNT DATA UNAVAILABLE/.test(body)) throw new Error("the unavailable state must be explicit");
    if (!/no account is logged in/.test(body)) throw new Error("the reason must be shown");
    if (/Balance[^0-9]*0(\.00)?\s*USD/.test(body)) throw new Error("an unavailable balance must never render as 0");
    // restore
    server.account = {
      ok: true, available: true, connected: true, is_simulated: false,
      source: "MT5", data_kind: "LIVE_ACCOUNT", checked_at: Date.now() / 1000,
      account: { login: 51041234, server: "Demo-Server", currency: "USD",
                 balance: 10000.0, equity: 10250.5, margin_free: 9800.25,
                 leverage: 100, trade_mode: 0, trade_mode_name: "DEMO" },
    };
    await unmount();
  });

  /* release identity -------------------------------------------------------- */
  await R("identity:the release identity from /system/build is visible", async () => {
    const { container, unmount } = await render(<BuildIdentityChip />);
    await flush();
    const body = textOf(container);
    if (!body.includes("V6.5.1")) throw new Error(`the release id must be visible (${body.slice(0, 200)})`);
    if (!body.includes("68a183b")) throw new Error("the served commit must be visible");
    await unmount();
  });

  return { total: results.length, failures: results.filter((r) => !r.ok), results };
}
