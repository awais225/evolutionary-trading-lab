import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, fmt } from "../api.js";

/* V4.6 — MT5 HISTORICAL BACKTEST configuration + run control.
 *
 * One user action = one queued historical backtest of one existing
 * USER_RESEARCH strategy over a historical period of stored MT5 market data.
 * It NEVER places an order: no MT5 demo order, no live-test order, no live
 * trade. The panel shows the run id and status, refreshes on demand (plus a
 * slow 5 s poll only while the panel is open and the run is active) and
 * reports completion / failure verbatim.
 */

const CARD = { background: "#0f172a", border: "1px solid #1e293b", borderRadius: 10, padding: 12 };
const FIELD = { background: "#020617", border: "1px solid #334155", borderRadius: 6,
                color: "#e2e8f0", fontSize: 12, padding: "4px 6px" };
const LBL = { fontSize: 11, color: "#94a3b8", display: "block", marginBottom: 2 };

function fmtWhen(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 16) + "Z";
  } catch (e) {
    return String(iso);
  }
}

export function StatusPill({ status }) {
  const tone = {
    COMPLETED: "#22c55e", RUNNING: "#38bdf8", QUEUED: "#a78bfa",
    FAILED: "#ef4444", CANCELLED: "#f59e0b", "NOT AVAILABLE": "#94a3b8",
  }[status] || "#94a3b8";
  return (
    <span className="mono" style={{ fontSize: 10, padding: "1px 6px", borderRadius: 4,
                                     border: `1px solid ${tone}`, color: tone }}>
      {status}
    </span>
  );
}

export default function HistoricalBacktestPanel({ strategyId, strategyLabel, compact, onStarted,
                                                   showRuns = true, runsRefreshKey }) {
  const [caps, setCaps] = useState(null);
  const [capsErr, setCapsErr] = useState(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(null);
  const [errors, setErrors] = useState([]);
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState(null);
  const [runs, setRuns] = useState([]);
  const [runsErr, setRunsErr] = useState(null);
  const [notice, setNotice] = useState(null);
  const pollRef = useRef(null);

  // --- capabilities: what can actually be backtested right now -------------- */
  useEffect(() => {
    let alive = true;
    api.mt5HistoricalCapabilities()
      .then((c) => { if (alive) { setCaps(c); setCapsErr(null); } })
      .catch((e) => { if (alive) setCapsErr(e); });
    return () => { alive = false; };
  }, []);

  const mt5Datasets = useMemo(
    () => (caps?.datasets || []).filter((d) => String(d.source).toUpperCase() === "MT5" && d.eligible),
    [caps]);

  // default the form to the first real MT5 dataset once capabilities arrive
  useEffect(() => {
    if (form || !caps) return;
    const ds = mt5Datasets[0];
    setForm({
      strategy_id: strategyId,
      symbol: ds?.symbol || "XAUUSD",
      timeframe: ds?.timeframe || "M15",
      start_date: ds?.start ? String(ds.start).slice(0, 10) : "",
      end_date: ds?.end ? String(ds.end).slice(0, 10) : "",
      initial_balance: caps.defaults?.initial_balance ?? 10000,
      risk_per_trade: caps.defaults?.risk_per_trade ?? 0.005,
      spread_mult: 1, slippage_mult: 1, commission_mult: 1,
      data_scope: "MT5",
    });
  }, [caps, mt5Datasets, form, strategyId]);

  useEffect(() => { if (form) setForm((f) => ({ ...f, strategy_id: strategyId })); }, [strategyId]);

  const loadRuns = useCallback(() => {
    if (!strategyId) return;
    api.mt5HistoricalRuns({ strategy_id: strategyId, limit: 25 })
      .then((r) => { setRuns(r.runs || []); setRunsErr(null); })
      .catch((e) => setRunsErr(e));
  }, [strategyId]);

  useEffect(() => { if (showRuns) loadRuns(); }, [showRuns, loadRuns, runsRefreshKey]);

  const refreshRun = useCallback((runId) => {
    const id = runId || run?.run_id;
    if (!id) return;
    api.mt5HistoricalRun(id).then((r) => { setRun(r); onStarted?.(r); }).catch((e) => setErrors([{ field: "run", error: String(e.message || e) }]));
  }, [run?.run_id, onStarted]);

  // slow poll ONLY while this panel is open and a run is still active
  useEffect(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    const active = run && ["QUEUED", "RUNNING"].includes(run.status);
    if (!open || !active) return undefined;
    pollRef.current = setInterval(() => refreshRun(run.run_id), 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [open, run, refreshRun]);

  const start = async () => {
    if (!form || busy) return;
    setBusy(true); setErrors([]); setNotice(null);
    try {
      const payload = {
        strategy_id: Number(form.strategy_id),
        symbol: form.symbol, timeframe: form.timeframe,
        start_date: form.start_date, end_date: form.end_date,
        initial_balance: Number(form.initial_balance),
        risk_per_trade: Number(form.risk_per_trade),
        spread_mult: Number(form.spread_mult), slippage_mult: Number(form.slippage_mult),
        commission_mult: Number(form.commission_mult),
        data_scope: form.data_scope,
      };
      const res = await api.mt5HistoricalStartRun(payload);
      setNotice({ ok: true, text: `run ${res.run_id} ${res.status} — no order was placed` });
      setRun(res.run || { run_id: res.run_id, status: res.status });
      refreshRun(res.run_id);
      loadRuns();
    } catch (err) {
      const detail = err?.detail || err?.payload?.detail;
      const list = detail && Array.isArray(detail.errors) ? detail.errors : null;
      if (detail?.duplicate) {
        setNotice({ ok: false, text: `identical run already ${detail.existing_run_id ? "queued (" + detail.existing_run_id + ")" : "running"}` });
        if (detail.existing_run_id) refreshRun(detail.existing_run_id);
      } else if (list) {
        setErrors(list);
      } else {
        setErrors([{ field: "request", error: err?.message || String(err) }]);
      }
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!run?.run_id) return;
    try {
      const res = await api.mt5HistoricalCancel(run.run_id);
      setNotice({ ok: res.ok !== false, text: res.note || (res.cancelled ? "run cancelled" : "cancel requested") });
      refreshRun(run.run_id);
      loadRuns();
    } catch (e) {
      setErrors([{ field: "cancel", error: e?.message || String(e) }]);
    }
  };

  const mt5Range = mt5Datasets.map((d) => `${d.symbol} ${d.timeframe}`).join(", ") || "none";
  const selectedDs = (caps?.datasets || []).find(
    (d) => d.symbol === form?.symbol && d.timeframe === form?.timeframe
      && String(d.source).toUpperCase() === String(form?.data_scope).toUpperCase());

  return (
    <div style={CARD}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 12 }}>HISTORICAL MT5 BACKTEST</strong>
        <span className="pill" style={{ fontSize: 9 }}>RESEARCH EXECUTION — NO ORDERS</span>
        {strategyLabel ? <span className="mono muted" style={{ fontSize: 11 }}>{strategyLabel}</span> : null}
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={() => { setOpen(!open); if (!open) loadRuns(); }}>
          {open ? "hide backtest configuration" : "Run historical backtest"}
        </button>
        {showRuns ? <button className="btn" onClick={loadRuns} title="reload this node's historical runs">refresh runs</button> : null}
      </div>

      <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        Runs the existing research backtest engine over <b>stored historical bars</b> of the dataset it names
        {mt5Datasets.length ? <> (real MT5 data available: <span className="mono">{mt5Range}</span>)</> : null}. A historical
        backtest never places, modifies or closes an order — MT5 demo orders and live testing are separate, explicitly
        activated layers.
      </div>

      {capsErr && <div className="warn-banner" style={{ marginTop: 6, fontSize: 11 }}>capabilities unavailable: {String(capsErr.message || capsErr)}</div>}

      {open && form && (
        <div style={{ marginTop: 10, borderTop: "1px solid #1e293b", paddingTop: 10 }}>
          <div style={{ display: "grid", gridTemplateColumns: compact ? "repeat(2, minmax(120px, 1fr))" : "repeat(4, minmax(120px, 1fr))", gap: 8 }}>
            <label><span style={LBL}>symbol</span>
              <select className="input" style={FIELD} value={form.symbol}
                      onChange={(e) => setForm({ ...form, symbol: e.target.value })}>
                {[...new Set((caps?.datasets || []).map((d) => d.symbol))].map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <label><span style={LBL}>timeframe</span>
              <select className="input" style={FIELD} value={form.timeframe}
                      onChange={(e) => setForm({ ...form, timeframe: e.target.value })}>
                {[...new Set((caps?.datasets || []).filter((d) => d.symbol === form.symbol).map((d) => d.timeframe))].map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
            <label><span style={LBL}>data</span>
              <select className="input" style={FIELD} value={form.data_scope}
                      onChange={(e) => setForm({ ...form, data_scope: e.target.value })}>
                {(caps?.scope?.options || ["MT5"]).map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <label><span style={LBL}>max trades</span>
              <input className="input" style={FIELD} type="number" value={form.max_trades ?? 3000}
                     onChange={(e) => setForm({ ...form, max_trades: e.target.value })} />
            </label>
            <label><span style={LBL}>start date (UTC)</span>
              <input className="input" style={FIELD} type="date" value={form.start_date}
                     onChange={(e) => setForm({ ...form, start_date: e.target.value })} />
            </label>
            <label><span style={LBL}>end date (UTC)</span>
              <input className="input" style={FIELD} type="date" value={form.end_date}
                     onChange={(e) => setForm({ ...form, end_date: e.target.value })} />
            </label>
            <label><span style={LBL}>initial balance</span>
              <input className="input" style={FIELD} type="number" value={form.initial_balance}
                     onChange={(e) => setForm({ ...form, initial_balance: e.target.value })} />
            </label>
            <label><span style={LBL}>risk per trade (fraction)</span>
              <input className="input" style={FIELD} type="number" step="0.001" value={form.risk_per_trade}
                     onChange={(e) => setForm({ ...form, risk_per_trade: e.target.value })} />
            </label>
            <label><span style={LBL}>spread ×</span>
              <input className="input" style={FIELD} type="number" step="0.1" value={form.spread_mult}
                     onChange={(e) => setForm({ ...form, spread_mult: e.target.value })} />
            </label>
            <label><span style={LBL}>slippage ×</span>
              <input className="input" style={FIELD} type="number" step="0.1" value={form.slippage_mult}
                     onChange={(e) => setForm({ ...form, slippage_mult: e.target.value })} />
            </label>
            <label><span style={LBL}>commission ×</span>
              <input className="input" style={FIELD} type="number" step="0.1" value={form.commission_mult}
                     onChange={(e) => setForm({ ...form, commission_mult: e.target.value })} />
            </label>
          </div>

          {selectedDs && (
            <div className="muted mono" style={{ fontSize: 10.5, marginTop: 6 }}>
              dataset <b>{selectedDs.dataset_id}</b> · {selectedDs.source} · {selectedDs.bars} bars ·
              {" "}{fmtWhen(selectedDs.start)} → {fmtWhen(selectedDs.end)}
              {selectedDs.broker ? <> · {selectedDs.broker}{selectedDs.server ? ` / ${selectedDs.server}` : ""}</> : null}
              {" "}· min period {selectedDs.min_bars} bars
            </div>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn primary" disabled={busy} onClick={start}>
              {busy ? "starting…" : "Start historical backtest"}
            </button>
            {run?.run_id && ["QUEUED", "RUNNING"].includes(run.status) ? (
              <button className="btn danger" onClick={cancel}>cancel run</button>
            ) : null}
            <button className="btn" onClick={() => refreshRun()}>refresh status</button>
            <span className="muted" style={{ fontSize: 10.5 }}>
              one historical backtest runs at a time; identical requests are refused while active
            </span>
          </div>

          {errors.length > 0 && (
            <div className="warn-banner" style={{ marginTop: 8 }}>
              <b>request rejected:</b>
              <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                {errors.map((e, i) => <li key={i} className="mono" style={{ fontSize: 11 }}>
                  {e.field}: {String(e.error)}
                  {e.available ? <span className="muted"> (available: {JSON.stringify(e.available)})</span> : null}
                </li>)}
              </ul>
            </div>
          )}
          {notice && (
            <div className="muted mono" style={{ fontSize: 11, marginTop: 6 }}>
              {notice.ok ? "✓ " : "! "}{notice.text}
            </div>
          )}

          {run?.run_id && (
            <div style={{ marginTop: 8, fontSize: 11 }} className="mono">
              run <b>{run.run_id}</b> <StatusPill status={run.status} />
              {run.period ? <> · {String(run.period.start || "").slice(0, 10)} → {String(run.period.end || "").slice(0, 10)} ({run.period.bars} bars)</> : null}
              {run.trade_count != null ? <> · {fmt.num(run.trade_count, 0)} trades</> : null}
              {run.runtime_ms != null ? <> · {fmt.num(run.runtime_ms, 0)} ms</> : null}
              {run.error ? <div style={{ color: "#fca5a5" }}>{String(run.error)}</div> : null}
            </div>
          )}
        </div>
      )}

      {showRuns && (
        <div style={{ marginTop: 10 }}>
          <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>
            MT5 historical runs for this node ({runs.length}) — separate from the stored research backtests
          </div>
          {runsErr && <div className="warn-banner" style={{ fontSize: 11 }}>{String(runsErr.message || runsErr)}</div>}
          {runs.length === 0 ? <div className="muted" style={{ fontSize: 11 }}>no historical run yet</div> : (
            <div className="scroll-x">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>run</th><th>status</th><th>period</th><th>data</th>
                    <th style={{ textAlign: "right" }}>trades</th>
                    <th style={{ textAlign: "right" }}>net P&amp;L</th>
                    <th style={{ textAlign: "right" }}>return</th>
                    <th>created</th><th />
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.run_id}>
                      <td className="mono">{r.run_id}</td>
                      <td><StatusPill status={r.status} /></td>
                      <td className="mono">{String(r.period?.start || "").slice(0, 10)} → {String(r.period?.end || "").slice(0, 10)}</td>
                      <td className="mono">{r.data?.source} {r.is_mt5_data ? "" : "(sim)"}</td>
                      <td className="mono" style={{ textAlign: "right" }}>{r.trade_count == null ? "N/A" : fmt.num(r.trade_count, 0)}</td>
                      <td className="mono" style={{ textAlign: "right" }}>{r.results?.metrics?.net_profit == null ? "N/A" : fmt.pnl(r.results.metrics.net_profit)}</td>
                      <td className="mono" style={{ textAlign: "right" }}>{r.results?.metrics?.total_return_pct == null ? "N/A" : fmt.ret(r.results.metrics.total_return_pct, 2)}</td>
                      <td className="mono">{fmtWhen(r.created_iso)}</td>
                      <td><button className="btn" style={{ padding: "1px 6px" }} onClick={() => refreshRun(r.run_id)}>open</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
