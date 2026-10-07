// V5 §6–9 — DEEP BACKTEST
//
// One graphical table of every research node with its real evaluated metrics,
// plus a run panel that executes a *deep* (detail-stage, full-cost) backtest on
// the MT5 historical data for the node's own timeframe.
//
// Rules this page follows (spec V5):
//   * Failed / data-unavailable / backtest-error nodes exist but `Show Failed`
//     is OFF by default (§6, §21).
//   * The date preset (1/3/7/14 days, 1/3/6/12 months, Custom from/to) is sent
//     to the run as its real start/end dates — it is not a label (§8).
//   * A run that cannot get MT5 history reports DATA_UNAVAILABLE; a run whose
//     engine crashed reports BACKTEST_ERROR. Neither is shown as a strategy
//     failure (§3).
//   * Nothing here places an order.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Card, StateBlock, Progress, ConfirmModal, Badge } from "../components/ui";
import StrategyDrawer from "../components/StrategyDrawer";
import { fmt } from "../api";
import { arr, txt } from "../lib/safe.js";

/* §7 — the node filters the operator chooses from. ``qualified`` is the default
 * (spec §6): Deep Backtest lists every qualified node, and the wider research
 * states are one click away rather than hidden. */
export const NODE_FILTERS = [
  { key: "qualified", label: "Qualified", hint: "cleared the research gates against real data" },
  { key: "alive", label: "Alive", hint: "qualified + still in research (not a dead end)" },
  { key: "eligible", label: "Eligible", hint: "the live engine would accept it as a candidate" },
  { key: "all", label: "All", hint: "every node in the experiment (+ legacy rows)" },
  { key: "failed", label: "Failed", hint: "the strategy was judged and did not make it" },
  { key: "excluded", label: "Excluded", hint: "outside this experiment (LEGACY_TEST)" },
  { key: "blocked", label: "Blocked", hint: "data/infrastructure — the strategy was never judged" },
  { key: "unknown", label: "Unknown", hint: "status needs investigation" },
];

/* §31 — the run states the operator can see. ``COMPLETED`` never means "no
 * results": the verdict line states what actually happened. */
const RUN_METRIC_FIELDS = [
  ["Return %", (m) => (m.total_return_pct == null ? null : `${(m.total_return_pct * 100).toFixed(3)}%`)],
  ["Net profit", (m) => m.net_profit],
  ["Profit factor", (m) => (m.profit_factor == null ? null : Number(m.profit_factor).toFixed(3))],
  ["Max DD", (m) => m.max_drawdown],
  ["Max DD %", (m) => (m.max_drawdown_pct == null ? null : `${(m.max_drawdown_pct * 100).toFixed(3)}%`)],
  ["Win rate", (m) => (m.win_rate == null ? null : `${(m.win_rate * 100).toFixed(2)}%`)],
  ["Trades", (m) => m.trades],
  ["Avg trade", (m) => m.avg_trade],
  // avg win/loss are derived from the run's own persisted trades by the backend
  ["Avg win", (m, d) => m.avg_win ?? d.avg_win],
  ["Avg loss", (m, d) => m.avg_loss ?? d.avg_loss],
  ["Expectancy", (m) => m.expectancy],
  ["Sharpe", (m) => (m.sharpe == null ? null : Number(m.sharpe).toFixed(3))],
  ["Robustness", (m) => m.robustness_score],
  ["Start balance", (m) => m.start_balance ?? m.initial_balance],
  ["End balance", (m) => m.final_equity ?? m.end_balance],
  ["Timeframe", (m) => m.timeframe],
  ["Symbol", (m) => m.symbol],
  ["Runtime ms", (m) => m.runtime_ms],
];

export function RunMetrics({ metrics, derived, schedule, coverage, verdict, error, status }) {
  const m = metrics || {};
  const d = derived || {};
  const cov = coverage || {};
  return (
    <div style={{ fontSize: 11.5 }}>
      {verdict && <div style={{ marginBottom: 4 }}><b>{verdict}</b></div>}
      {error && <div className="kit-inline-err">{error}</div>}
      <div className="kit-strip" style={{ border: "none", padding: 0, flexWrap: "wrap" }}>
        <div className="item"><span className="k">Status</span><span className="v mono">{status}</span></div>
        <div className="item"><span className="k">Schedule applied</span>
          <span className="v mono">{schedule?.applied_to_bars ? "yes" : (schedule?.configured ? "configured (no bar filter)" : "none")}</span></div>
        <div className="item"><span className="k">Bars allowed / blocked</span>
          <span className="v mono">{schedule?.bars_allowed ?? "—"} / {schedule?.bars_blocked ?? "—"}</span></div>
        <div className="item"><span className="k">Data</span>
          <span className="v mono">{cov.source_label || cov.dataset?.source || coverage?.source || "—"}
            {cov.dataset?.id || coverage?.dataset_id ? ` · ${cov.dataset?.id || coverage?.dataset_id}` : ""}</span></div>
        {cov.bars_used !== undefined && (
          <div className="item"><span className="k">Bars used</span>
            <span className="v mono">{txt(cov.bars_used, "—")}{cov.expected_bars ? ` / ${cov.expected_bars} expected` : ""}</span></div>
        )}
        {cov.completeness_pct !== undefined && cov.completeness_pct !== null && (
          <div className="item"><span className="k">Completeness</span>
            <span className="v mono">{fmt.num(cov.completeness_pct, 2)} %{cov.complete === false ? " (gaps present)" : ""}</span></div>
        )}
        {cov.quality?.gaps !== undefined && (
          <div className="item"><span className="k">Gaps / missing bars</span>
            <span className="v mono">{txt(cov.quality.gaps, "—")} / {txt(cov.quality.missing_bars_total, "—")}</span></div>
        )}
      </div>
      {/* §32 — the data-loading report: requested vs actual, never a silent subset */}
      {(cov.requested || cov.actual) && (
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
          requested {txt(cov.requested?.start_iso || cov.requested?.start, "—")} → {txt(cov.requested?.end_iso || cov.requested?.end, "—")}
          {" · "}used {txt(cov.actual?.start, "—")} → {txt(cov.actual?.end, "—")}
          {cov.period_adjusted ? " (adjusted to the dataset's own range)" : ""}
        </div>
      )}
      {cov.interpretation && (
        <div className="muted" style={{ fontSize: 10.5, marginTop: 2 }}>{cov.interpretation}</div>
      )}
      {arr(cov.missing_periods).length > 0 && (
        <details style={{ marginTop: 4 }}>
          <summary className="muted" style={{ fontSize: 11, cursor: "pointer" }}>
            missing periods ({arr(cov.missing_periods).length}{cov.missing_periods_truncated ? "+" : ""})
          </summary>
          <ul style={{ margin: "4px 0 0 16px", fontSize: 11 }}>
            {arr(cov.missing_periods).slice(0, 12).map((g, i) => (
              <li key={i} className="mono">
                {txt(g.after, "?")} → {txt(g.before, "?")} · {txt(g.missing_bars, "?")} bar(s)
              </li>
            ))}
          </ul>
        </details>
      )}
      {schedule?.description && (
        <div className="muted" style={{ fontSize: 11 }}>schedule: {schedule.description}</div>
      )}
      <table className="tbl" style={{ marginTop: 6 }}>
        <tbody>
          {RUN_METRIC_FIELDS.map(([label, get]) => {
            const v = get(m, d);
            return (
              <tr key={label}>
                <td className="muted" style={{ width: 130 }}>{label}</td>
                <td className="mono">{v == null || v === "" ? <span className="muted">not reported</span> : String(v)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const PRESETS = [
  { key: "1d", label: "1 day", days: 1 },
  { key: "3d", label: "3 days", days: 3 },
  { key: "7d", label: "7 days", days: 7 },
  { key: "14d", label: "14 days", days: 14 },
  { key: "1m", label: "1 month", months: 1 },
  { key: "3m", label: "3 months", months: 3 },
  { key: "6m", label: "6 months", months: 6 },
  { key: "12m", label: "12 months", months: 12 },
  { key: "custom", label: "Custom", custom: true },
];

const RUN_STATES = {
  QUEUED: { tone: "var(--amber)", label: "Queued" },
  RUNNING: { tone: "var(--accent)", label: "Running" },
  COMPLETED: { tone: "var(--green)", label: "Completed" },
  FAILED: { tone: "var(--red)", label: "Failed" },
  DATA_UNAVAILABLE: { tone: "var(--amber)", label: "Data unavailable" },
  BACKTEST_ERROR: { tone: "var(--red)", label: "Backtest error" },
  CANCELLED: { tone: "var(--muted)", label: "Cancelled" },
};

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

/** Turn a preset (or custom range) into the real dates the run will use. */
export function presetRange(presetKey, from, to) {
  if (presetKey === "custom") {
    if (!from || !to) return { start_date: "", end_date: "", valid: false };
    return { start_date: from, end_date: to, valid: from <= to };
  }
  const p = PRESETS.find((x) => x.key === presetKey) || PRESETS[3];
  const end = new Date();
  const start = new Date(end);
  if (p.months) start.setMonth(start.getMonth() - p.months);
  else start.setDate(start.getDate() - (p.days || 7));
  return { start_date: isoDate(start), end_date: isoDate(end), valid: true };
}

const STATUS_TONE = {
  VALID: "var(--green)",
  TESTING: "var(--accent)",
  PENDING: "var(--muted)",
  NOT_TESTED: "var(--muted)",
  STRATEGY_FAILED: "var(--red)",
  DATA_UNAVAILABLE: "var(--amber)",
  DATA_CORRUPT: "var(--amber)",
  BACKTEST_ERROR: "var(--red)",
  LIVE_ELIGIBLE: "var(--accent)",
  LIVE_TESTING: "var(--accent)",
  LIVE_COMPLETED: "var(--green)",
  MT5_DEMO: "var(--green)",
};

export function StatusCell({ row }) {
  const tone = STATUS_TONE[row.v5_status] || "var(--muted)";
  return (
    <span className="mono" style={{ color: tone, fontSize: 10, whiteSpace: "nowrap" }} title={row.v5_status_hint || ""}>
      ● {row.v5_status_label || row.v5_status}
      {row.error_class === "INFRASTRUCTURE" ? <span className="muted" style={{ marginLeft: 4 }}>(infra)</span> : null}
      {row.error_class === "STRATEGY" ? <span className="muted" style={{ marginLeft: 4 }}>(strategy)</span> : null}
    </span>
  );
}

export default function DeepBacktest() {
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [tf, setTf] = useState("");
  const [sortBy, setSortBy] = useState("total_return_pct");
  const [sortDesc, setSortDesc] = useState(true);
  const [nodeFilter, setNodeFilter] = useState("qualified"); // §6: qualified by default
  /* §15 — which stored history a run uses. MT5 (the terminal's own bars) is the
   * default and is never substituted silently: when a symbol/timeframe only
   * exists in the lab's own dataset, the run is refused with the exact reason
   * and the operator picks the source here. ``runTf`` overrides the node's own
   * timeframe for a run, so a different timeframe really is a different test. */
  const [dataScope, setDataScope] = useState("MT5");
  const [runTf, setRunTf] = useState("");
  const [counts, setCounts] = useState({});
  const [experiment, setExperiment] = useState(null);
  const [openRun, setOpenRun] = useState(null);
  const [runDetail, setRunDetail] = useState({});
  const [starredOnly, setStarredOnly] = useState(false);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [selected, setSelected] = useState(() => new Set());
  const [preset, setPreset] = useState("7d");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [runs, setRuns] = useState([]);
  const [caps, setCaps] = useState(null);
  const [schedule, setSchedule] = useState({ mode: "now", at: "" });
  const [confirm, setConfirm] = useState(null);
  const [batchReport, setBatchReport] = useState(null);
  const [drawerId, setDrawerId] = useState(null);
  const pollRef = useRef(null);

  const range = useMemo(() => presetRange(preset, from, to), [preset, from, to]);

  const load = useCallback(async () => {
    setError("");
    try {
      const params = {
        filter: nodeFilter,
        limit: pageSize,
        offset: page * pageSize,
        sort_by: sortBy,
        sort_desc: sortDesc ? 1 : 0,
        search: search || undefined,
        timeframe: tf || undefined,
      };
      const res = await api.nodes(params);
      setRows(res.nodes || []);
      setTotal(res.total || 0);
      setCounts(res.counts || {});
      setExperiment(res.experiment || null);
    } catch (e) {
      setError(e.message || String(e));
    }
  }, [page, pageSize, search, sortBy, sortDesc, tf, nodeFilter]);

  const loadRuns = useCallback(async () => {
    try {
      const res = await api.mt5HistoricalRuns({ limit: 25 });
      setRuns(res.runs || res.items || []);
    } catch { /* the run list is informative; the table stays usable */ }
  }, []);

  const loadCaps = useCallback(async () => {
    try { setCaps(await api.mt5HistoricalCapabilities()); } catch { setCaps(null); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadRuns(); loadCaps(); }, [loadRuns, loadCaps]);
  useEffect(() => {
    pollRef.current = setInterval(() => { loadRuns(); }, 4000);
    return () => clearInterval(pollRef.current);
  }, [loadRuns]);

  /* The timeframes this installation actually stores (plus the standard set, so
   * the operator can ask for one and be told precisely what is missing). */
  const runTfOptions = useMemo(() => {
    const set = new Set();
    for (const d of arr(caps?.datasets)) { if (d?.timeframe) set.add(String(d.timeframe)); }
    for (const t of ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]) set.add(t);
    return [...set].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  }, [caps]);

  // the backend already filtered by bucket; only the local "starred" toggle applies
  const visible = starredOnly ? rows.filter((r) => r.starred) : rows;

  const toggleStar = async (row) => {
    try {
      const res = await api.toggleShortlist(row.node_id);
      setRows((prev) => prev.map((p) => (p.node_id === row.node_id ? { ...p, starred: res.shortlisted } : p)));
    } catch (e) { setError(e.message || String(e)); }
  };

  const runOne = async (row, { silent = false } = {}) => {
    if (!range.valid) { setError("The custom range is invalid: start must be on or before end."); return; }
    if (!silent) setBusy(`run-${row.node_id}`);
    try {
      const res = await api.mt5HistoricalStartRun({
        strategy_id: row.node_id,
        symbol: row.symbol,
        timeframe: runTf || row.timeframe,
        data_scope: dataScope,
        start_date: range.start_date,
        end_date: range.end_date,
        schedule_at: schedule.mode === "at" && schedule.at ? new Date(schedule.at).toISOString() : undefined,
      });
      await loadRuns();
      if (!silent) setBusy("");
      return res;
    } catch (e) {
      if (!silent) setBusy("");
      setError(`${row.node_label || row.node_id}: ${e.message || e}`);
    }
  };

  /* §6/§7 — one action for one / several / all selected nodes. The backend fans
   * the request out over the *same* single-run path, so a node that cannot be
   * queued comes back with its real reason instead of being dropped. */
  const runMany = async (ids, label) => {
    if (!ids.length) return;
    setConfirm(null);
    setBusy(`run-${label}`);
    try {
      const res = await api.mt5HistoricalStartBatch({
        strategy_ids: ids,
        data_scope: dataScope,
        timeframe: runTf || undefined,
        start_date: range.start_date,
        end_date: range.end_date,
        schedule_at: schedule.mode === "at" && schedule.at ? new Date(schedule.at).toISOString() : undefined,
      });
      await loadRuns();
      const failed = (res.runs || []).filter((r) => !r.ok);
      setBatchReport({
        label, requested: res.requested, started: res.started, failed: res.failed,
        run_ids: res.run_ids || [],
        problems: failed.map((f) => ({
          node_id: f.strategy_id,
          label: (rows.find((r) => r.node_id === f.strategy_id) || {}).node_label || `node ${f.strategy_id}`,
          reason: (f.errors || []).map((e) => `${e.field}: ${e.error}`).join("; ") || f.status || "rejected",
        })),
      });
      if (failed.length) {
        setError(`${res.started} queued, ${res.failed} rejected — every node that could not be queued is listed below with its reason.`);
      } else {
        setError("");
      }
    } catch (e) {
      setError(e.message || String(e));
    } finally { setBusy(""); }
  };

  const selectedIds = [...selected];
  const starredIds = rows.filter((r) => r.starred).map((r) => r.node_id);
  const allSelected = visible.length > 0 && visible.every((r) => selected.has(r.node_id));
  const pages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="stack">
      <Card title="DEEP BACKTEST" right={
        <span className="muted" style={{ fontSize: 10 }}>
          full-cost backtest on stored MT5 history for the node's own timeframe · never places an order
        </span>
      }>
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label className="field" style={{ minWidth: 180 }}>
            <span>Search node / strategy</span>
            <input value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} placeholder="Node_240, XAUUSD…" />
          </label>
          <label className="field" style={{ width: 110 }}>
            <span>Timeframe</span>
            <select value={tf} onChange={(e) => { setTf(e.target.value); setPage(0); }}>
              <option value="">All</option>
              {["M1", "M5", "M15", "M30", "H1"].map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          </label>
          <label className="field" style={{ width: 190 }}>
            <span>Sort</span>
            <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
              <option value="total_return_pct">IS return</option>
              <option value="profit_factor">Profit factor</option>
              <option value="win_rate">Win rate</option>
              <option value="trades">Trades</option>
              <option value="max_drawdown_pct">Max drawdown</option>
              <option value="sharpe">Sharpe</option>
              <option value="id">Node id</option>
              <option value="last_tested_at">Last tested</option>
            </select>
          </label>
          <button className="btn ghost" onClick={() => setSortDesc((v) => !v)}>{sortDesc ? "▼ desc" : "▲ asc"}</button>
          <label className="row" style={{ gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={starredOnly} onChange={(e) => setStarredOnly(e.target.checked)} />
            <span style={{ fontSize: 11 }}>Starred only</span>
          </label>
          <label className="field" style={{ width: 170 }} title={NODE_FILTERS.find((f) => f.key === nodeFilter)?.hint || ""}>
            <span>Nodes</span>
            <select value={nodeFilter} onChange={(e) => { setNodeFilter(e.target.value); setSelected(new Set()); setPage(0); }}>
              {NODE_FILTERS.map((f) => (
                <option key={f.key} value={f.key}>
                  {f.label}{counts[f.key] != null ? ` (${counts[f.key]})` : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="field" style={{ width: 90 }}>
            <span>Rows</span>
            <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}>
              {[10, 25, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <button className="btn ghost" onClick={() => { setSelected(new Set()); setSearch(""); setTf(""); setStarredOnly(false); setNodeFilter("qualified"); setPage(0); }}>Reset view</button>
        </div>

        <div className="row" style={{ gap: 10, marginTop: 10, flexWrap: "wrap", alignItems: "center" }}>
          <span className="muted" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>Run period</span>
          {PRESETS.map((p) => (
            <button key={p.key} className={"btn " + (preset === p.key ? "primary" : "ghost")}
                    onClick={() => setPreset(p.key)} style={{ fontSize: 11 }}>
              {p.label}
            </button>
          ))}
          {preset === "custom" && (
            <>
              <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
              <span className="muted">→</span>
              <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
            </>
          )}
          <label className="field" style={{ width: 190 }}>
            <span>Data source</span>
            <select value={dataScope} onChange={(e) => setDataScope(e.target.value)}>
              {arr(caps?.scope?.options).length > 0
                ? arr(caps.scope.options).map((o) => (
                    <option key={o} value={o}>{o === "MT5" ? "MT5 — terminal's own bars" : "Lab simulator dataset"}</option>
                  ))
                : [<option key="MT5" value="MT5">MT5 — terminal's own bars</option>,
                   <option key="SIM" value="SIMULATOR">Lab simulator dataset</option>]}
            </select>
          </label>
          <label className="field" style={{ width: 170 }}>
            <span>Run timeframe</span>
            <select value={runTf} onChange={(e) => setRunTf(e.target.value)}>
              <option value="">node's own (auto)</option>
              {runTfOptions.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>
          <span className="muted" style={{ fontSize: 10 }}>
            runs will request {range.start_date || "—"} → {range.end_date || "—"} from {dataScope}
            {runTf ? ` on ${runTf}` : " on each node's own timeframe"}
            {range.valid ? "" : " — invalid range"}
          </span>
        </div>

        <div className="row" style={{ gap: 8, marginTop: 10, flexWrap: "wrap", alignItems: "center" }}>
          <label className="field" style={{ width: 150 }}>
            <span>Schedule</span>
            <select value={schedule.mode} onChange={(e) => setSchedule((s) => ({ ...s, mode: e.target.value }))}>
              <option value="now">Run now</option>
              <option value="at">At a time</option>
            </select>
          </label>
          {schedule.mode === "at" && (
            <input type="datetime-local" value={schedule.at} onChange={(e) => setSchedule((s) => ({ ...s, at: e.target.value }))} />
          )}
          <button className="btn" disabled={!selectedIds.length || !!busy}
                  onClick={() => setConfirm({ kind: "selected", ids: selectedIds })}
                  title={selectedIds.length ? "" : "select nodes in the table first"}>
            Deep backtest selected ({selectedIds.length})
          </button>
          <button className="btn" disabled={!starredIds.length || !!busy}
                  onClick={() => setConfirm({ kind: "starred", ids: starredIds })}
                  title={starredIds.length ? "" : "star a node to use this"}>
            Deep backtest starred ({starredIds.length})
          </button>
          <button className="btn ghost" disabled={!!busy}
                  onClick={() => setConfirm({ kind: "filtered", ids: visible.map((r) => r.node_id) })}
                  title={`queue every node currently listed by the ${nodeFilter} filter`}>
            Deep backtest all {nodeFilter} ({visible.length})
          </button>
          <button className="btn ghost" disabled={!rows.length} onClick={load}>Refresh</button>
        </div>
        {busy.startsWith("run-") && <Progress pct={null} label="queueing deep backtests…" />}
        {error && <div className="error-note" style={{ marginTop: 8 }}>{error}</div>}
        {batchReport && (
          <div className="kit-strip" style={{ border: "none", padding: 0, marginTop: 8, flexWrap: "wrap" }}>
            <div className="item"><span className="k">Batch ({batchReport.label})</span>
              <span className="v mono">{batchReport.started}/{batchReport.requested} queued · {batchReport.failed} rejected</span></div>
            {batchReport.run_ids.length > 0 && (
              <div className="item"><span className="k">Run ids</span>
                <span className="v mono" style={{ fontSize: 10 }}>{batchReport.run_ids.join(", ")}</span></div>
            )}
          </div>
        )}
        {batchReport?.problems?.length > 0 && (
          <table className="tbl" style={{ marginTop: 6 }}>
            <thead><tr><th>Node</th><th>Reason it was not queued</th></tr></thead>
            <tbody>
              {batchReport.problems.map((p) => (
                <tr key={p.node_id}><td className="mono">{p.label}</td>
                  <td className="muted" style={{ fontSize: 11 }}>{p.reason}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title={`NODES — ${NODE_FILTERS.find((f) => f.key === nodeFilter)?.label || nodeFilter}`}
            right={<span className="muted" style={{ fontSize: 10 }}>
              {total} match · page {page + 1}/{pages}
              {experiment?.run_id ? ` · experiment ${experiment.run_id} (${experiment.population} nodes, numbered 1–${experiment.node_number_range?.[1] ?? "?"})` : ""}
            </span>}>
        <StateBlock loading={!rows.length && !error} error={error && !rows.length ? error : ""} empty={!visible.length}
                    emptyHint={`No ${nodeFilter} nodes match the current filters. Other buckets: ${Object.entries(counts).filter(([k]) => k !== nodeFilter).map(([k, v]) => `${k} ${v}`).join(", ") || "none"}.`}
                    onRetry={load}>
          <div style={{ overflowX: "auto" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th style={{ width: 26 }}></th>
                  <th style={{ width: 26 }}>
                    <input type="checkbox" checked={allSelected}
                           onChange={(e) => {
                             const next = new Set(selected);
                             visible.forEach((r) => { if (e.target.checked) next.add(r.node_id); else next.delete(r.node_id); });
                             setSelected(next);
                           }} />
                  </th>
                  <th>Node</th><th>State</th><th>Strategy</th><th>Symbol</th><th>TF</th>
                  <th>IS return</th><th>PF</th><th>Win %</th><th>Trades</th><th>Max DD</th>
                  <th>Sharpe</th><th>Generation</th><th>Schedule</th><th>Coverage</th><th>Evidence</th><th>Action</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((r) => {
                  const m = r.metrics || {};
                  const num = r.research_node_num ?? r.node_id;
                  const notTested = m.trades == null;
                  return (
                  <tr key={r.node_id}>
                    <td>
                      <button className={"star-btn" + (r.starred ? " on" : "")}
                              title={r.starred ? "Remove from watchlist" : "Add to watchlist"}
                              onClick={() => toggleStar(r)}>
                        {r.starred ? "★" : "☆"}
                      </button>
                    </td>
                    <td>
                      <input type="checkbox" checked={selected.has(r.node_id)}
                             onChange={(e) => {
                               const next = new Set(selected);
                               if (e.target.checked) next.add(r.node_id); else next.delete(r.node_id);
                               setSelected(next);
                             }} />
                    </td>
                    <td className="mono" title={`internal id ${r.node_id} · experiment ${r.run_id || "?"}`}>
                      Node_{num}
                      {r.node_label && r.node_label !== `Node_${num}` ? <span className="muted"> ({r.node_label})</span> : null}
                    </td>
                    <td>
                      <span className="mono" style={{ color: STATUS_TONE[r.v5_status] || "var(--muted)", fontSize: 10, whiteSpace: "nowrap" }}
                            title={`${r.bucket_reason || ""}${r.eligibility_note ? ` · ${r.eligibility_note}` : ""}`}>
                        ● {r.bucket_label || r.v5_status_label || r.v5_status}
                      </span>
                      {r.is_infrastructure_failure ? <span className="muted" style={{ fontSize: 9, marginLeft: 4 }}>infra</span> : null}
                    </td>
                    <td style={{ fontSize: 11 }}>
                      {r.direction || "both"} · {String(r.innovation || r.qualification || "").slice(0, 26)}
                    </td>
                    <td>{r.symbol}</td>
                    <td>{r.timeframe}</td>
                    <td className={"mono " + (m.return_pct > 0 ? "pos" : m.return_pct < 0 ? "neg" : "")}>
                      {notTested ? <span className="muted">—</span> : `${(m.return_pct * 100).toFixed(2)}%`}
                    </td>
                    <td className="mono">{notTested ? "—" : Number(m.profit_factor || 0).toFixed(2)}</td>
                    <td className="mono">{m.win_rate == null ? "—" : `${(m.win_rate * 100).toFixed(1)}%`}</td>
                    <td className="mono">{m.trades ?? "—"}</td>
                    <td className="mono">{m.max_drawdown_pct == null ? "—" : `${(m.max_drawdown_pct * 100).toFixed(1)}%`}</td>
                    <td className="mono">{m.sharpe == null ? "—" : Number(m.sharpe).toFixed(2)}</td>
                    <td className="mono">{r.generation ?? "—"}</td>
                    <td className="mono" style={{ fontSize: 10 }} title={r.schedule?.description || "no schedule configured"}>
                      {r.schedule?.description ? String(r.schedule.description).slice(0, 34) : <span className="muted">none</span>}
                    </td>
                    <td className="mono" style={{ fontSize: 10 }}>
                      {r.backtest_coverage?.available
                        ? <>{r.backtest_coverage.dataset_id}<div className="muted">
                            {r.backtest_coverage.start} → {r.backtest_coverage.end} · {r.backtest_coverage.bars} bars · {r.backtest_coverage.source}
                          </div></>
                        : <span className="muted">dataset not found</span>}
                    </td>
                    <td style={{ fontSize: 10, maxWidth: 220 }}>
                      <span className="muted">{String(r.survival_evidence || "—").slice(0, 80)}</span>
                    </td>
                    <td>
                      <div className="row" style={{ gap: 4 }}>
                        <button className="btn ghost" style={{ fontSize: 10 }}
                                onClick={() => setDrawerId(r.node_id)}>Details</button>
                        <button className="btn" style={{ fontSize: 10 }} disabled={!!busy}
                                onClick={() => runOne(r)}>Deep test</button>
                      </div>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
            <button className="btn ghost" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>← Prev</button>
            <span className="muted" style={{ fontSize: 11 }}>{total} {nodeFilter} nodes · {visible.length} shown · {selected.size} selected</span>
            <button className="btn ghost" disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)}>Next →</button>
          </div>
        </StateBlock>
      </Card>

      <Card title="DEEP RUNS" right={<button className="btn ghost" onClick={loadRuns}>Refresh</button>}>
        <div className="muted" style={{ fontSize: 10, marginBottom: 6 }}>
          {caps?.bridge?.note || "Deep runs execute over stored historical bars with the local engine."}
          {caps?.bridge?.is_simulated === false ? " MT5 terminal attached." : ""}
          {" "}A completed run always reports its metrics and an explicit verdict; a run that could not
          execute reports why (data unavailable, blocked by the schedule, engine error) — never "completed" without results.
        </div>
        {!runs.length ? <div className="muted" style={{ fontSize: 11 }}>No deep runs yet.</div> : (
          <table className="tbl">
            <thead><tr><th>Run</th><th>Node</th><th>Status</th><th>Period</th><th>Progress</th><th>Result</th><th>Schedule</th><th></th></tr></thead>
            <tbody>
              {runs.map((run) => {
                const st = RUN_STATES[String(run.status || "").toUpperCase()] || { tone: "var(--muted)", label: run.status };
                const prog = run.progress ?? run.percent ?? null;
                const m = run.results?.metrics || {};
                const upper = String(run.status || "").toUpperCase();
                const open = openRun === run.run_id;
                return (
                  <React.Fragment key={run.run_id}>
                  <tr>
                    <td className="mono" style={{ fontSize: 10 }}>{run.run_id}</td>
                    <td className="mono">{run.strategy_id != null ? `Node_${run.strategy_id}` : (run.node_id || "—")}</td>
                    <td title={run.error || ""}><span className="mono" style={{ color: st.tone, fontSize: 10 }}>● {st.label}</span></td>
                    <td className="mono" style={{ fontSize: 10 }}>
                      {run.period?.start || run.start_date || "—"} → {run.period?.end || run.end_date || "—"}
                      {run.config?.scheduled_at && upper === "QUEUED"
                        ? <div className="muted">scheduled {new Date(run.config.scheduled_at * 1000).toLocaleString()}</div>
                        : null}
                    </td>
                    <td style={{ minWidth: 110 }}>
                      {prog != null ? <Progress pct={prog} /> : <span className="muted" style={{ fontSize: 10 }}>{String(run.stage || run.status || "")}</span>}
                    </td>
                    <td className="mono" style={{ fontSize: 10, maxWidth: 280 }}>
                      {run.verdict ? run.verdict : null}
                      {!run.verdict && m.total_return_pct != null
                        ? `${(m.total_return_pct * 100).toFixed(2)}% · PF ${Number(m.profit_factor || 0).toFixed(2)} · ${m.trades || 0} trades`
                        : null}
                      {!run.verdict && m.total_return_pct == null
                        ? (run.error || (["QUEUED", "RUNNING"].includes(upper) ? "running…" : "no result reported"))
                        : null}
                    </td>
                    <td className="mono" style={{ fontSize: 10 }}>
                      {run.schedule?.configured
                        ? <span title={run.schedule.description || ""}>
                            {run.schedule.applied_to_bars
                              ? `applied · ${run.schedule.bars_allowed}/${(run.schedule.bars_allowed || 0) + (run.schedule.bars_blocked || 0)} bars allowed`
                              : "configured (no bar filter on this run)"}
                          </span>
                        : <span className="muted">none</span>}
                    </td>
                    <td>
                      <div className="row" style={{ gap: 4 }}>
                        <button className="btn ghost" style={{ fontSize: 10 }}
                                onClick={async () => {
                                  if (open) { setOpenRun(null); return; }
                                  setOpenRun(run.run_id);
                                  if (!runDetail[run.run_id]) {
                                    try {
                                      const full = await api.mt5HistoricalRun(run.run_id);
                                      setRunDetail((d) => ({ ...d, [run.run_id]: full }));
                                    } catch (e) { setError(e.message || String(e)); }
                                  }
                                }}>{open ? "Hide" : "Details"}</button>
                        {["QUEUED", "RUNNING"].includes(upper) && (
                          <button className="btn ghost" style={{ fontSize: 10 }}
                                  onClick={async () => { await api.mt5HistoricalCancel(run.run_id); loadRuns(); }}>Cancel</button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {open && (
                    <tr>
                      <td colSpan={8} style={{ background: "rgba(148,163,184,0.06)" }}>
                        {!runDetail[run.run_id]
                          ? <span className="muted" style={{ fontSize: 11 }}>loading the run's own record…</span>
                          : (
                            <RunMetrics
                              status={runDetail[run.run_id].status}
                              metrics={(runDetail[run.run_id].results?.metrics) || m}
                              derived={runDetail[run.run_id].results?.derived}
                              schedule={runDetail[run.run_id].schedule || run.schedule}
                              coverage={runDetail[run.run_id].coverage || runDetail[run.run_id].data}
                              verdict={runDetail[run.run_id].verdict}
                              error={runDetail[run.run_id].error}
                            />
                          )}
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      {drawerId != null && <StrategyDrawer id={drawerId} onClose={() => setDrawerId(null)} />}

      <ConfirmModal open={!!confirm} title={`Deep backtest ${confirm?.kind === "starred" ? "starred" : "selected"} nodes`}
                    tone="info" confirmLabel={`Queue ${confirm?.ids?.length || 0} run(s)`}
                    onCancel={() => setConfirm(null)}
                    onConfirm={() => runMany(confirm?.ids || [], confirm?.kind || "batch")}>
        <div style={{ fontSize: 12 }}>
          {confirm?.ids?.length || 0} node(s) will be deep-tested on stored MT5 history
          for <b>{range.start_date || "—"} → {range.end_date || "—"}</b>.
          Runs execute one at a time through the single-run path; each node keeps its own timeframe
          and its own saved schedule. This places no order.
        </div>
      </ConfirmModal>
    </div>
  );
}
