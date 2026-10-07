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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Card, StateBlock, Progress, ConfirmModal } from "../components/ui";
import StrategyDrawer from "../components/StrategyDrawer";

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
  const [showFailed, setShowFailed] = useState(false);       // §6: OFF by default
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
  const [drawerId, setDrawerId] = useState(null);
  const pollRef = useRef(null);

  const range = useMemo(() => presetRange(preset, from, to), [preset, from, to]);

  const load = useCallback(async () => {
    setError("");
    try {
      const params = {
        limit: pageSize,
        offset: page * pageSize,
        sort_by: sortBy,
        sort_desc: sortDesc,
        search: search || undefined,
        timeframe: tf || undefined,
      };
      const res = await api.researchShortlist(params);
      setRows(res.strategies || []);
      setTotal(res.total_matching || 0);
    } catch (e) {
      setError(e.message || String(e));
    }
  }, [page, pageSize, search, sortBy, sortDesc, tf]);

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

  const visible = rows.filter((r) => {
    if (starredOnly && !r.shortlisted) return false;
    if (!showFailed && (r.error_class === "STRATEGY" || r.error_class === "INFRASTRUCTURE")) return false;
    return true;
  });

  const toggleStar = async (row) => {
    try {
      const res = await api.toggleShortlist(row.id);
      setRows((prev) => prev.map((p) => (p.id === row.id ? { ...p, shortlisted: res.shortlisted } : p)));
    } catch (e) { setError(e.message || String(e)); }
  };

  const runOne = async (row, { silent = false } = {}) => {
    if (!range.valid) { setError("The custom range is invalid: start must be on or before end."); return; }
    if (!silent) setBusy(`run-${row.id}`);
    try {
      const res = await api.mt5HistoricalStartRun({
        strategy_id: row.id,
        symbol: row.symbol,
        timeframe: row.timeframe,
        start_date: range.start_date,
        end_date: range.end_date,
        schedule_at: schedule.mode === "at" && schedule.at ? new Date(schedule.at).toISOString() : undefined,
      });
      await loadRuns();
      if (!silent) setBusy("");
      return res;
    } catch (e) {
      if (!silent) setBusy("");
      setError(`${row.node_id || row.id}: ${e.message || e}`);
    }
  };

  const runMany = async (ids, label) => {
    if (!ids.length) return;
    setConfirm(null);
    setBusy(`run-${label}`);
    let ok = 0; const problems = [];
    for (const id of ids) {
      const row = rows.find((r) => r.id === id) || { id };
      try {
        // sequential on purpose: the API allows one active deep run at a time
        // eslint-disable-next-line no-await-in-loop
        const res = await runOne(row, { silent: true });
        if (res) ok += 1;
      } catch (e) { problems.push(`${id}: ${e.message || e}`); }
    }
    setBusy("");
    await loadRuns();
    if (problems.length) setError(`${ok} queued, ${problems.length} rejected — ${problems.slice(0, 3).join(" | ")}`);
  };

  const selectedIds = [...selected];
  const starredIds = rows.filter((r) => r.shortlisted).map((r) => r.id);
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
          <label className="row" style={{ gap: 6, alignItems: "center" }} title="Failed nodes stay hidden until you ask for them">
            <input type="checkbox" checked={showFailed} onChange={(e) => setShowFailed(e.target.checked)} />
            <span style={{ fontSize: 11 }}>Show Failed</span>
          </label>
          <label className="field" style={{ width: 90 }}>
            <span>Rows</span>
            <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}>
              {[10, 25, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <button className="btn ghost" onClick={() => { setSelected(new Set()); setSearch(""); setTf(""); setStarredOnly(false); }}>Reset view</button>
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
          <span className="muted" style={{ fontSize: 10 }}>
            runs will request {range.start_date || "—"} → {range.end_date || "—"} (MT5 history for this node's timeframe)
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
          <button className="btn ghost" disabled={!rows.length} onClick={load}>Refresh</button>
        </div>
        {busy.startsWith("run-") && <Progress pct={null} label="queueing deep backtests…" />}
        {error && <div className="error-note" style={{ marginTop: 8 }}>{error}</div>}
      </Card>

      <Card title="NODES" right={<span className="muted" style={{ fontSize: 10 }}>{total} match · page {page + 1}/{pages}</span>}>
        <StateBlock loading={!rows.length && !error} error={error && !rows.length ? error : ""} empty={!visible.length}
                    emptyHint={showFailed ? "No nodes match the current filters." : "No live nodes match — tick Show Failed to include failed/data-unavailable nodes."}
                    onRetry={load}>
          <div style={{ overflowX: "auto" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th style={{ width: 26 }}></th>
                  <th style={{ width: 26 }}>
                    <input type="checkbox"
                           checked={visible.length > 0 && visible.every((r) => selected.has(r.id))}
                           onChange={(e) => {
                             const next = new Set(selected);
                             visible.forEach((r) => { if (e.target.checked) next.add(r.id); else next.delete(r.id); });
                             setSelected(next);
                           }} />
                  </th>
                  <th>ID</th><th>Status</th><th>Strategy</th><th>Symbol</th><th>TF</th>
                  <th>IS return</th><th>PF</th><th>Win %</th><th>Trades</th><th>Max DD</th>
                  <th>Sharpe</th><th>Risk</th><th>Period</th><th>Last tested</th><th>Action</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <button className={"star-btn" + (r.shortlisted ? " on" : "")}
                              title={r.shortlisted ? "Remove from watchlist" : "Add to watchlist"}
                              onClick={() => toggleStar(r)}>
                        {r.shortlisted ? "★" : "☆"}
                      </button>
                    </td>
                    <td>
                      <input type="checkbox" checked={selected.has(r.id)}
                             onChange={(e) => {
                               const next = new Set(selected);
                               if (e.target.checked) next.add(r.id); else next.delete(r.id);
                               setSelected(next);
                             }} />
                    </td>
                    <td className="mono">{r.node_id}</td>
                    <td><StatusCell row={r} /></td>
                    <td style={{ fontSize: 11 }}>{r.direction || "both"} · {String(r.mutation_type || r.stage || "").slice(0, 22)}</td>
                    <td>{r.symbol}</td>
                    <td>{r.timeframe}</td>
                    <td className={"mono " + (r.total_return_pct > 0 ? "pos" : r.total_return_pct < 0 ? "neg" : "")}>
                      {r.trades ? `${(r.total_return_pct * 100).toFixed(2)}%` : "—"}
                    </td>
                    <td className="mono">{r.trades ? r.profit_factor.toFixed(2) : "—"}</td>
                    <td className="mono">{r.trades ? `${(r.win_rate * 100).toFixed(1)}%` : "—"}</td>
                    <td className="mono">{r.trades || "—"}</td>
                    <td className="mono">{r.trades ? `${(r.max_drawdown_pct * 100).toFixed(1)}%` : "—"}</td>
                    <td className="mono">{r.trades ? r.sharpe.toFixed(2) : "—"}</td>
                    <td className="mono">{r.risk_per_trade != null ? `${(r.risk_per_trade * 100).toFixed(2)}%` : "—"}</td>
                    <td className="mono" style={{ fontSize: 10 }}>
                      {r.period?.dataset_id
                        ? <>{r.period.dataset_id}{r.period.window ? ` · win ${r.period.window[0]}–${r.period.window[1]}` : ""}</>
                        : <span className="muted">not tested yet</span>}
                    </td>
                    <td className="mono" style={{ fontSize: 10 }}>
                      {r.last_tested_at ? new Date(r.last_tested_at * 1000).toLocaleString() : <span className="muted">never</span>}
                    </td>
                    <td>
                      <div className="row" style={{ gap: 4 }}>
                        <button className="btn ghost" style={{ fontSize: 10 }}
                                onClick={() => setDrawerId(r.id)}>Details</button>
                        <button className="btn" style={{ fontSize: 10 }} disabled={!!busy}
                                onClick={() => runOne(r)}>Deep test</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
            <button className="btn ghost" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>← Prev</button>
            <span className="muted" style={{ fontSize: 11 }}>{total} nodes · {visible.length} shown on this page</span>
            <button className="btn ghost" disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)}>Next →</button>
          </div>
        </StateBlock>
      </Card>

      <Card title="DEEP RUNS" right={<button className="btn ghost" onClick={loadRuns}>Refresh</button>}>
        <div className="muted" style={{ fontSize: 10, marginBottom: 6 }}>
          {caps?.bridge?.note || "Deep runs execute over stored historical bars with the local engine."}
          {caps?.bridge?.is_simulated === false ? " MT5 terminal attached." : ""}
        </div>
        {!runs.length ? <div className="muted" style={{ fontSize: 11 }}>No deep runs yet.</div> : (
          <table className="tbl">
            <thead><tr><th>Run</th><th>Node</th><th>Status</th><th>Period</th><th>Progress</th><th>Result</th><th></th></tr></thead>
            <tbody>
              {runs.map((run) => {
                const st = RUN_STATES[String(run.status || "").toUpperCase()] || { tone: "var(--muted)", label: run.status };
                const p = run.progress ?? run.percent ?? null;
                return (
                  <tr key={run.run_id}>
                    <td className="mono" style={{ fontSize: 10 }}>{run.run_id}</td>
                    <td className="mono">{run.strategy_id != null ? `Node_${run.strategy_id}` : (run.node_id || "—")}</td>
                    <td><span className="mono" style={{ color: st.tone, fontSize: 10 }}>● {st.label}</span></td>
                    <td className="mono" style={{ fontSize: 10 }}>
                      {run.period?.start || run.start_date || "—"} → {run.period?.end || run.end_date || "—"}
                      {run.config?.scheduled_at && String(run.status).toUpperCase() === "QUEUED"
                        ? <div className="muted">scheduled {new Date(run.config.scheduled_at * 1000).toLocaleString()}</div>
                        : null}
                    </td>
                    <td style={{ minWidth: 110 }}>
                      {p != null ? <Progress pct={p} /> : <span className="muted" style={{ fontSize: 10 }}>{String(run.stage || run.status || "")}</span>}
                    </td>
                    <td className="mono" style={{ fontSize: 10 }}>
                      {run.metrics?.total_return_pct != null
                        ? `${(run.metrics.total_return_pct * 100).toFixed(2)}% · PF ${Number(run.metrics.profit_factor || 0).toFixed(2)} · ${run.metrics.trades || 0} trades`
                        : (run.error || run.message || "—")}
                    </td>
                    <td>
                      {["QUEUED", "RUNNING"].includes(String(run.status || "").toUpperCase()) && (
                        <button className="btn ghost" style={{ fontSize: 10 }}
                                onClick={async () => { await api.mt5HistoricalCancel(run.run_id); loadRuns(); }}>Cancel</button>
                      )}
                    </td>
                  </tr>
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
          Runs execute one at a time; each node keeps its own timeframe.
        </div>
      </ConfirmModal>
    </div>
  );
}
