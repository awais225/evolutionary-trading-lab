// All requests use RELATIVE urls; the Vite dev server (or FastAPI in
// single-port mode) serves both the app and the API from the same origin.

const inflightGets = new Map();

/**
 * Format raw error detail into a human-readable string and structured diagnostics.
 * Prevents JavaScript string coercion from rendering '[object Object]'.
 */
export function formatErrorDetail(detail, statusText = "Request Failed") {
  if (!detail) return statusText;
  if (typeof detail === "string") return detail;

  // FastAPI 422 validation errors array: [{loc: [...], msg: "...", type: "..."}]
  if (Array.isArray(detail)) {
    const formatted = detail.map((d) => {
      const field = Array.isArray(d.loc)
        ? d.loc.filter((x) => x !== "body").join(".")
        : String(d.loc || "");
      return field ? `${field}: ${d.msg}` : (d.msg || JSON.stringify(d));
    });
    return formatted.join("; ") || statusText;
  }

  // Plain JSON error object: { message: "...", code: "..." }
  if (typeof detail === "object") {
    if (detail.message) return String(detail.message);
    if (detail.error) return String(detail.error);
    try {
      return JSON.stringify(detail);
    } catch {
      return statusText;
    }
  }

  return String(detail);
}

async function req(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();

  // Deduplicate and coalesce identical concurrent in-flight GET requests
  // to prevent browser HTTP/1.1 socket exhaustion (spec V2.9)
  if (method === "GET" && inflightGets.has(path)) {
    return inflightGets.get(path);
  }

  const execute = async () => {
    let controller = null;
    let timerId = null;
    let signal = opts.signal;

    if (!signal) {
      if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
        signal = AbortSignal.timeout(15000);
      } else if (typeof AbortController !== "undefined") {
        controller = new AbortController();
        timerId = setTimeout(() => controller.abort(), 15000);
        signal = controller.signal;
      }
    }

    try {
      const res = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...opts,
        signal,
        body: opts.body ? JSON.stringify(opts.body) : undefined,
      });

      if (!res.ok) {
        let rawDetail = res.statusText;
        let parsedJson = null;
        try {
          parsedJson = await res.json();
          rawDetail = parsedJson.detail || parsedJson.message || parsedJson;
        } catch {
          // Response body was not JSON
        }

        const formattedMsg = formatErrorDetail(rawDetail, res.statusText);
        const err = new Error(`${res.status}: ${formattedMsg}`);
        err.status = res.status;
        err.statusText = res.statusText;
        err.endpoint = path;
        err.detail = rawDetail;
        err.formattedMessage = formattedMsg;
        err.rawJson = parsedJson;
        err.timestamp = new Date().toISOString();
        throw err;
      }

      return await res.json();
    } finally {
      if (timerId) clearTimeout(timerId);
    }
  };

  if (method === "GET") {
    const promise = execute().finally(() => {
      inflightGets.delete(path);
    });
    inflightGets.set(path, promise);
    return promise;
  }

  return execute();
}

export const api = {
  // System & Lab Core
  status: () => req("/api/status"),
  labStatus: () => req("/api/lab/status"),
  labStart: (mode, target, run_type = "resume", run_id = null) =>
    req("/api/lab/start", { method: "POST", body: { mode, target, run_type, run_id } }),
  labPause: () => req("/api/lab/pause", { method: "POST" }),
  labResume: (target = null) => req("/api/lab/resume", { method: "POST", body: { target } }),
  labStop: () => req("/api/lab/stop", { method: "POST" }),
  labReset: () => req("/api/lab/reset", { method: "POST" }),
  labClearFailed: () => req("/api/lab/clear-failed", { method: "POST" }),
  recheck: () => req("/api/lab/recheck", { method: "POST" }),
  setTarget: (target) => req("/api/lab/target", { method: "POST", body: { target } }),
  getTarget: () => req("/api/lab/target"),
  labRuns: () => req("/api/lab/runs"),
  labRunDetail: (runId) => req(`/api/lab/runs/${runId}`),
  labSetActiveRun: (runId) => req("/api/lab/runs/active", { method: "POST", body: { run_id: runId } }),
  // V4.1 research-run lifecycle (START NEW RESEARCH RUN)
  researchRunState: () => req("/api/research-run/state"),
  researchRunStatus: () => req("/api/research-run/status"),
  researchRunBackups: () => req("/api/research-run/backups"),
  researchRunBackup: (note = "") => req("/api/research-run/backup", { method: "POST", body: { note } }),
  researchRunStartFresh: (payload) => req("/api/research-run/start-fresh", { method: "POST", body: payload }),
  researchRunResumeAdd: (payload) => req("/api/research-run/resume-add", { method: "POST", body: payload }),

  // Population & Strategy Research
  population: (p = {}) => req(`/api/population?${new URLSearchParams(p)}`),
  qualifiedStrategies: (p = {}) => req(`/api/strategies/qualified?${new URLSearchParams(p)}`),
  scatter: (p = {}) => req(`/api/scatter?${new URLSearchParams(p)}`),
  tree: (p = {}) => req(`/api/tree?${new URLSearchParams(p)}`),
  strategy: (id) => req(`/api/strategies/${id}`),
  strategyCharts: (sid) => req(`/api/strategies/${sid}/charts`),
  generateMatrix: (sid) => req(`/api/strategies/${sid}/matrix`, { method: "POST" }),
  reconcileTasks: () => req("/api/tasks/reconcile", { method: "POST" }),

  // Authoritative Strategy Data Model & Node Economics (V4)
  authoritativeStrategy: (sid) => req(`/api/strategies/${sid}/authoritative`),
  nodeEconomics: (sid) => req(`/api/strategies/${sid}/economics`),
  updatePipelineStage: (sid, stage, notes = "") =>
    req(`/api/strategies/${sid}/pipeline-stage`, { method: "POST", body: { stage, notes } }),

  // Research Filtering & Shortlist
  researchShortlist: (params = {}) => req(`/api/research/shortlist?${new URLSearchParams(params)}`),
  researchFilter: (body) => req("/api/research/filter", { method: "POST", body }),
  strategyTrades: (sid) => req(`/api/strategies/${sid}/trades`),
  strategyRerunBacktest: (sid) => req(`/api/strategies/${sid}/backtest`, { method: "POST" }),
  strategyRunValidation: (sid) => req(`/api/strategies/${sid}/validate`, { method: "POST" }),
  savedShortlist: () => req("/api/research/shortlist/saved"),
  toggleShortlist: (sid, notes = "") => req("/api/research/shortlist/toggle", { method: "POST", body: { strategy_id: sid, notes } }),
  clearShortlist: () => req("/api/research/shortlist/clear", { method: "POST" }),

  // MT5 Strategy Tester Backtest (V4)
  mt5BacktestRun: (sid, config = {}) => req(`/api/mt5-backtest/strategies/${sid}/run`, { method: "POST", body: config }),
  mt5BacktestResults: (sid = null) => req(`/api/mt5-backtest/results${sid ? `?strategy_id=${sid}` : ""}`),
  mt5StrategyBacktests: (sid) => req(`/api/mt5-backtest/strategies/${sid}`),

  // Live Testing & Schedule Controls (V4)
  liveTestStatus: () => req("/api/live-test/status"),
  liveTestConfig: (sid) => req(`/api/live-test/strategies/${sid}/config`),
  saveLiveTestConfig: (sid, config) => req(`/api/live-test/strategies/${sid}/config`, { method: "POST", body: config }),
  toggleLiveTest: (sid) => req(`/api/live-test/strategies/${sid}/toggle`, { method: "POST" }),
  liveTestStartAll: (payload = {}) => req("/api/live-test/start-all", { method: "POST", body: payload }),
  liveTestStartShortlist: () => req("/api/live-test/start-shortlist", { method: "POST" }),
  liveTestStopAll: () => req("/api/live-test/stop-all", { method: "POST" }),
  liveTestPauseAll: () => req("/api/live-test/pause-all", { method: "POST" }),
  liveTestResumeAll: () => req("/api/live-test/resume-all", { method: "POST" }),
  liveTestResults: (params = {}) => req(`/api/live-test/results?${new URLSearchParams(params)}`),

  // V4.3 — controlled live testing (demo only, INACTIVE by default)
  liveTestingStatus: () => req("/api/live-testing/status"),
  liveTestingConfirmation: () => req("/api/live-testing/confirmation"),
  liveTestingActivate: (body = {}) => req("/api/live-testing/activate", { method: "POST", body: { confirm: true, ...body } }),
  liveTestingDeactivate: (reason) => req("/api/live-testing/deactivate", { method: "POST", body: { reason } }),
  liveTestingStopNewTrades: (reason) => req("/api/live-testing/stop-new-trades", { method: "POST", body: { reason } }),
  liveTestingClosePositions: () => req("/api/live-testing/close-positions", { method: "POST" }),
  liveTestingMarket: (symbol) => req(`/api/live-testing/market${symbol ? `?symbol=${symbol}` : ""}`),
  liveTestingCounter: () => req("/api/live-testing/counter"),
  liveTestingLog: (params = {}) => req(`/api/live-testing/log?${new URLSearchParams(params)}`),
  liveTestingNodes: () => req("/api/live-testing/nodes"),
  liveTestingNodeConfig: (sid, body) => req(`/api/live-testing/nodes/${sid}/config`, { method: "POST", body }),
  liveTestingSettings: (body) => req("/api/live-testing/settings", { method: "POST", body }),
  liveTestingTrades: (params = {}) => req(`/api/live-testing/trades?${new URLSearchParams(params)}`),
  liveTestingReconcile: () => req("/api/live-testing/reconcile", { method: "POST" }),

  // MT5 Demo Trading (V4)
  mt5DemoStatus: () => req("/api/mt5-demo/status"),
  toggleMt5Demo: (sid, confirmed = true) =>
    req(`/api/mt5-demo/strategies/${sid}/toggle`, { method: "POST", body: { confirmed_demo_only: confirmed } }),
  mt5DemoStartAll: (confirmed = true) =>
    req("/api/mt5-demo/start-all", { method: "POST", body: { confirmed_demo_only: confirmed } }),
  mt5DemoStartShortlist: (confirmed = true) =>
    req("/api/mt5-demo/start-shortlist", { method: "POST", body: { confirmed_demo_only: confirmed } }),
  mt5DemoStopAll: () => req("/api/mt5-demo/stop-all", { method: "POST" }),
  mt5DemoPauseAll: () => req("/api/mt5-demo/pause-all", { method: "POST" }),

  // V4.2 — controlled MT5 demo order execution (manual, explicitly confirmed)
  mt5ExecutionState: () => req("/api/mt5-execution/state"),
  mt5ExecutionStatus: () => req("/api/mt5-execution/status"),
  mt5ExecutionValidate: (body) => req("/api/mt5-execution/validate", { method: "POST", body }),
  mt5ExecutionPlace: (body) => req("/api/mt5-execution/place", { method: "POST", body }),

  // Hypotheses & AI Researcher
  hypotheses: () => req("/api/hypotheses"),
  researchMemory: (p = {}) => req(`/api/research/memory?${new URLSearchParams(p)}`),
  applyHypothesis: (id) => req(`/api/hypotheses/${id}/apply`, { method: "POST" }),

  // Datasets & Market Data
  datasets: () => req("/api/data/datasets"),
  ingest: (body) => req("/api/data/ingest", { method: "POST", body }),
  bars: (id, limit = 300) => req(`/api/data/bars?dataset_id=${id}&limit=${limit}`),
  features: (id) => req(`/api/data/features?dataset_id=${id}`),
  ticks: (symbol, n = 1) => req(`/api/data/ticks?symbol=${symbol || ""}&n=${n}`),
  masterData: () => req("/api/data/master"),
  dataSync: (body) => req("/api/data/sync", { method: "POST", body }),

  // Internal Paper Trading
  paperStatus: () => req("/api/paper/status"),
  paperPromoted: () => req("/api/paper/promoted"),
  paperCandidates: (limit = 100) => req(`/api/paper/candidates?limit=${limit}`),
  paperPromoteCandidate: (id) => req(`/api/paper/candidates/${id}/promote`, { method: "POST" }),
  paperDemoteCandidate: (id) => req(`/api/paper/candidates/${id}/demote`, { method: "POST" }),
  paperBatchPromote: (strategyIds) => req("/api/paper/candidates/batch_promote", { method: "POST", body: { strategy_ids: strategyIds } }),
  paperStart: () => req("/api/paper/start", { method: "POST" }),
  paperStop: () => req("/api/paper/stop", { method: "POST" }),
  paperTrades: (limit = 150) => req(`/api/paper/trades?limit=${limit}`),
  executions: (limit = 150) => req(`/api/paper/executions?limit=${limit}`),
  calibration: () => req("/api/calibration"),
  recalibrate: () => req("/api/calibration/recalibrate", { method: "POST" }),

  // Risk Controls
  risk: () => req("/api/risk"),
  killSwitch: (on) => req("/api/risk/kill-switch", { method: "POST", body: { on } }),
  realExecution: (enabled) =>
    req("/api/risk/real-execution", {
      method: "POST",
      body: { enabled, confirm: "ENABLE REAL TRADING" },
    }),

  // Settings & Logs
  settings: () => req("/api/settings"),
  applySettings: (body) => req("/api/settings", { method: "POST", body }),
  putSettings: (section, values) =>
    req(`/api/settings/${section}`, { method: "PUT", body: values }),
  jobs: (limit = 50) => req(`/api/jobs?limit=${limit}`),
  jobDetail: (id) => req(`/api/jobs/${id}`),
  dataInventory: () => req("/api/data/inventory"),
  diagnosticLog: (limit = 300) => req(`/api/logs/diagnostic?limit=${limit}`),
  logs: (limit = 500, level, category) =>
    req(`/api/logs?limit=${limit}${level ? `&level=${encodeURIComponent(level)}` : ""}${category ? `&category=${encodeURIComponent(category)}` : ""}`),
  exportLogs: () => req("/api/logs/export"),
  pipelineState: () => req("/api/pipeline/state"),
  events: (limit = 120) => req(`/api/events?limit=${limit}`),
  activity: (p = {}) => req(`/api/activity?${new URLSearchParams(p)}`),
  currentTask: () => req("/api/activity/current_task"),
  resources: () => req("/api/resources"),
  mt5Status: () => req("/api/mt5/status"),
  mt5Terminals: () => req("/api/mt5/terminals"),
  mt5Config: () => req("/api/mt5/config"),
  mt5Connect: (body = {}) => req("/api/mt5/connect", { method: "POST", body }),
  mt5Disconnect: () => req("/api/mt5/disconnect", { method: "POST" }),
  backup: () => req("/api/backup", { method: "POST" }),
  backups: () => req("/api/backups"),
  versions: () => req("/api/system/versions"),
  systemStatusRow: () => req("/api/system/status_row"),
  workflowMilestones: () => req("/api/workflow/milestones"),
  workflowTaskState: () => req("/api/workflow/task_state"),
  featureTasks: (limit = 50) => req(`/api/features/tasks?limit=${limit}`),
  currentFeatureTask: () => req("/api/features/current_task"),
  featureStallCheck: () => req("/api/features/stall_check", { method: "POST" }),

  // Data Management
  dataManagementStatus: () => req("/api/data/management/status"),
  clearXauusdData: () => req("/api/data/clear/xauusd", { method: "POST", signal: AbortSignal.timeout(20000) }),
  clearCache: () => req("/api/data/clear/cache", { method: "POST", signal: AbortSignal.timeout(20000) }),
  clearNodes: () => req("/api/data/clear/nodes", { method: "POST", signal: AbortSignal.timeout(20000) }),
  clearGenomes: () => req("/api/data/clear/genomes", { method: "POST", signal: AbortSignal.timeout(20000) }),
  clearAllData: (confirm) => req("/api/data/clear/all", { method: "POST", body: { confirm }, signal: AbortSignal.timeout(25000) }),
};

export const fmt = {
  num: (v, d = 2) =>
    v == null || Number.isNaN(v) ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d }),
  pct: (v, d = 1) => (v == null || Number.isNaN(v) ? "–" : `${(v * 100).toFixed(d)}%`),
  ret: (v, d = 1) => {
    if (v == null || Number.isNaN(v)) return "–";
    const pctVal = v * 100;
    return `${pctVal >= 0 ? "+" : ""}${pctVal.toFixed(d)}%`;
  },
  currency: (v, d = 2) => {
    if (v == null || Number.isNaN(v)) return "–";
    const sign = v < 0 ? "-" : "";
    return `${sign}$${Math.abs(Number(v)).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
  },
  pnl: (v, d = 2) => {
    if (v == null || Number.isNaN(v)) return "–";
    const sign = v >= 0 ? "+" : "-";
    return `${sign}$${Math.abs(Number(v)).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
  },
  ratio: (v, d = 2) => (v == null || Number.isNaN(v) ? "–" : Number(v).toFixed(d)),
  ts: (t) => (t ? new Date(t * 1000).toLocaleTimeString() : "–"),
  dt: (t) => (t ? new Date(t * 1000).toLocaleString() : "–"),
  signed: (v, d = 2) => (v == null ? "–" : (v >= 0 ? "+" : "") + Number(v).toFixed(d)),
  stageBadge: (stage) => {
    const s = String(stage || "").toUpperCase();
    if (s.includes("QUALIFIED") || s.includes("SURVIVED")) return "bg-emerald-500/20 text-emerald-400 border-emerald-500/30";
    if (s.includes("MT5_BACKTESTED") || s.includes("SHORTLISTED")) return "bg-cyan-500/20 text-cyan-400 border-cyan-500/30";
    if (s.includes("LIVE_TESTING")) return "bg-blue-500/20 text-blue-400 border-blue-500/30";
    if (s.includes("MT5_DEMO") || s.includes("FINAL_CANDIDATE")) return "bg-purple-500/20 text-purple-400 border-purple-500/30";
    if (s.includes("KILLED") || s.includes("FAILED")) return "bg-rose-500/20 text-rose-400 border-rose-500/30";
    return "bg-slate-700/50 text-slate-300 border-slate-600/40";
  }
};
