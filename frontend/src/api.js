// All requests use RELATIVE urls; the Vite dev server (or FastAPI in
// single-port mode) serves both the app and the API from the same origin.

/** Query string builder: drops empty/undefined/null values so a filter that is
 *  not set can never reach the API as the literal string "undefined". */
function qs(params = {}) {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    sp.set(k, String(v));
  });
  return sp.toString();
}

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

      try {
        return await res.json();
      } catch (parseErr) {
        // A 2xx that is not JSON means the request never reached the API (for
        // example a dev-server SPA fallback page). Surface that plainly instead
        // of leaking "Unexpected token '<' ... is not valid JSON" to the UI.
        const ctype = (res.headers && res.headers.get && res.headers.get("content-type")) || "unknown";
        const err = new Error(
          `${path} returned ${res.status} ${ctype} instead of JSON — ` +
          "the request did not reach the API (check the dev-server proxy / backend route)."
        );
        err.status = res.status;
        err.endpoint = path;
        err.contentType = ctype;
        err.parseError = String((parseErr && parseErr.message) || parseErr);
        err.timestamp = new Date().toISOString();
        throw err;
      }
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
  // V4.4 — research statistics & node economics (read-only analytics)
  statsOverview: () => req("/api/stats/overview"),
  statsScopeAudit: () => req("/api/stats/scope_audit"),
  statsNodes: (p = {}) => req(`/api/stats/nodes?${new URLSearchParams(p)}`),
  statsNode: (sid) => req(`/api/stats/node/${sid}`),

  // V4.5 — Strategy Lab / Backtest Matrix (research selection & comparison)
  researchStrategies: (p = {}) => req(`/api/research/strategies?${qs(p)}`),
  researchFacets: (p = {}) => req(`/api/research/facets?${qs(p)}`),
  researchMatrix: (p = {}) => req(`/api/research/matrix?${qs(p)}`),
  researchCompare: (ids) => req(`/api/research/compare?${qs({ ids: Array.isArray(ids) ? ids.join(",") : ids })}`),

  researchRunState: () => req("/api/research-run/state"),
  researchRunStatus: () => req("/api/research-run/status"),
  researchRunBackups: () => req("/api/research-run/backups"),
  researchRunBackup: (note = "") => req("/api/research-run/backup", { method: "POST", body: { note } }),
  researchRunStartFresh: (payload) => req("/api/research-run/start-fresh", { method: "POST", body: payload }),
  // §4 — read-only impact preview for a from-scratch reset (nothing is changed)
  researchRunFreshPreview: (mode = "backup_and_reset", target = null) =>
    req(`/api/research-run/fresh/preview?${qs({ mode, ...(target ? { target } : {}) })}`),
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

  // V4.6 — MT5 historical backtest execution (research; never places an order)
  // V5.2 §11–§14 — Deep Testing data requirements / suggested data / GET MT5 DATA /
  // readiness, all derived from the real deep-eligible nodes on the backend.
  // V5.2 §16 — the same schedule system on MT5 Demo Trading.
  mt5DemoSchedule: (sid) => req(`/api/mt5-demo/schedule/${sid}`),
  mt5DemoSaveSchedule: (sid, body) => req(`/api/mt5-demo/schedule/${sid}`, { method: "POST", body }),
  mt5DemoClearSchedule: (sid) => req(`/api/mt5-demo/schedule/${sid}`, { method: "DELETE" }),
  deepTestingState: () => req("/api/nodes/deep-testing/state"),
  deepTestingRequirements: () => req("/api/nodes/deep-testing/requirements"),
  deepTestingSuggested: () => req("/api/nodes/deep-testing/suggested-data"),
  deepTestingReadiness: () => req("/api/nodes/deep-testing/readiness"),
  deepTestingPlan: () => req("/api/nodes/deep-testing/plan"),
  deepTestingGetDataStart: (body = {}) => req("/api/nodes/deep-testing/get-data", { method: "POST", body }),
  deepTestingGetDataStatus: () => req("/api/nodes/deep-testing/get-data/status"),
  deepTestingGetDataCancel: () => req("/api/nodes/deep-testing/get-data/cancel", { method: "POST" }),
  mt5HistoricalCapabilities: (refresh = false) => req(`/api/mt5-historical/capabilities${refresh ? "?refresh=1" : ""}`),
  mt5HistoricalStartRun: (body) => req("/api/mt5-historical/runs", { method: "POST", body }),
  // §6 — several nodes in one action (a single run path per node, fanned out)
  mt5HistoricalStartBatch: (body) => req("/api/mt5-historical/runs/batch", { method: "POST", body }),
  // §6/§7 — the qualified-node index (filter: qualified|alive|eligible|all|failed|excluded|blocked|unknown)
  nodes: (params = {}) => req(`/api/nodes?${new URLSearchParams(params)}`),
  mt5HistoricalRuns: (params = {}) => {
    const q = qs(params);
    return req(`/api/mt5-historical/runs${q ? `?${q}` : ""}`);
  },
  mt5HistoricalRun: (runId) => req(`/api/mt5-historical/runs/${runId}`),
  mt5HistoricalTrades: (runId, params = {}) => req(`/api/mt5-historical/runs/${runId}/trades?${qs(params)}`),
  mt5HistoricalEquity: (runId, params = {}) => req(`/api/mt5-historical/runs/${runId}/equity?${qs(params)}`),
  mt5HistoricalCancel: (runId) => req(`/api/mt5-historical/runs/${runId}/cancel`, { method: "POST" }),

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
  // V4.8 §7 read-only per-condition truth table (never places an order)
  liveTestingConditions: (symbol) => req(`/api/live-testing/conditions${symbol ? `?symbol=${symbol}` : ""}`),
  liveTestingCounter: () => req("/api/live-testing/counter"),
  liveTestingLog: (params = {}) => req(`/api/live-testing/log?${new URLSearchParams(params)}`),
  liveTestingNodes: () => req("/api/live-testing/nodes"),
  liveTestingNodeConfig: (sid, body) => req(`/api/live-testing/nodes/${sid}/config`, { method: "POST", body }),
  liveTestingSettings: (body) => req("/api/live-testing/settings", { method: "POST", body }),
  liveTestingTrades: (params = {}) => req(`/api/live-testing/trades?${new URLSearchParams(params)}`),
  liveTestingReconcile: () => req("/api/live-testing/reconcile", { method: "POST" }),

  // V5 §10-§16 — redesigned Live Testing surface
  liveTestingMarketHeader: (symbol, strategyId) =>
    req(`/api/live-testing/market-header?${qs({ symbol, strategy_id: strategyId })}`),
  liveTestingNodesTable: (params = {}) => req(`/api/live-testing/nodes-table?${qs(params)}`),
  liveTestingSchedule: (sid) => req(`/api/live-testing/schedule/${sid}`),
  saveLiveTestingSchedule: (sid, body) =>
    req(`/api/live-testing/schedule/${sid}`, { method: "POST", body }),
  liveTestingStartNode: (sid) =>
    req(`/api/live-testing/nodes/${sid}/start`, { method: "POST", body: { confirmed: true } }),
  liveTestingStopNode: (sid) => req(`/api/live-testing/nodes/${sid}/stop`, { method: "POST" }),
  liveTestingRisk: () => req("/api/live-testing/risk"),
  liveTestingSetRisk: (risk_pct) =>
    req("/api/live-testing/risk", { method: "POST", body: { risk_pct } }),
  strategyTradingInfo: (sid) => req(`/api/strategies/${sid}/trading-info`),

  // V5 §10/§19-§21 — Power button / dashboard-owned shutdown
  powerSession: () => req("/api/power/session"),
  powerShutdown: (confirm, dryRun = false) =>
    req("/api/power/shutdown", { method: "POST", body: { confirm, dry_run: dryRun } }),
  powerShutdownReport: () => req("/api/power/shutdown-report"),
  powerHeartbeat: () => req("/api/power/heartbeat", { method: "POST" }),

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
  // V4.8 read-only risk <-> lot preview (never places an order)
  mt5ExecutionPreview: (payload) => req("/api/mt5-execution/preview", { method: "POST", body: payload }),
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
  // V5.1a-next §B — account discovery / explicit selection (read-only until select)
  mt5Accounts: (probe = false) => req(`/api/mt5/accounts${probe ? "?probe=true" : ""}`),
  mt5AccountSelect: (body) => req("/api/mt5/accounts/select", { method: "POST", body }),
  // V5.1a-next §D — the one authoritative population counter
  nodePopulations: () => req("/api/nodes/populations"),
  mt5Connect: (body = {}) => req("/api/mt5/connect", { method: "POST", body }),
  mt5Disconnect: () => req("/api/mt5/disconnect", { method: "POST" }),
  backup: () => req("/api/backup", { method: "POST" }),
  // V4.7 reliability surface
  health: () => req("/health", { signal: AbortSignal.timeout(6000) }),
  ready: () => req("/api/ready", { signal: AbortSignal.timeout(6000) }),
  lifecycle: () => req("/api/lifecycle"),
  backupAsync: () => req("/api/backup/async", { method: "POST" }),
  job: (jobId) => req(`/api/jobs/${encodeURIComponent(jobId)}`),
  exportLogsAsync: () => req("/api/logs/export/async", { method: "POST" }),
  exportLogsResult: (jobId) => req(`/api/logs/export/${encodeURIComponent(jobId)}`),
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

/* V4.7: every formatter is total - a value that is missing, an object, a string
 * that is not a number or a non-finite number renders as "–", never as NaN,
 * undefined or [object Object]. */
const DASH = "–";
function finite(v) {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}
function whenFinite(v, render) {
  const n = finite(v);
  return n === null ? DASH : render(n);
}

export const fmt = {
  num: (v, d = 2) => whenFinite(v, (n) => n.toLocaleString(undefined, { maximumFractionDigits: d })),
  pct: (v, d = 1) => whenFinite(v, (n) => `${(n * 100).toFixed(d)}%`),
  ret: (v, d = 1) => whenFinite(v, (n) => `${n * 100 >= 0 ? "+" : ""}${(n * 100).toFixed(d)}%`),
  currency: (v, d = 2) => whenFinite(v, (n) =>
    `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`),
  pnl: (v, d = 2) => whenFinite(v, (n) =>
    `${n >= 0 ? "+" : "-"}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`),
  ratio: (v, d = 2) => whenFinite(v, (n) => n.toFixed(d)),
  ts: (t) => (finite(t) ? new Date(Number(t) * 1000).toLocaleTimeString() : DASH),
  dt: (t) => (finite(t) ? new Date(Number(t) * 1000).toLocaleString() : DASH),
  signed: (v, d = 2) => whenFinite(v, (n) => (n >= 0 ? "+" : "") + n.toFixed(d)),
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
