import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api, fmt } from "../api.js";
import { NA, money, pct, ratio, val } from "./NodeResearchDetail.jsx";
import { arr, txt } from "../lib/safe.js";
import { StatusPill } from "./HistoricalBacktestPanel.jsx";

/* V4.6 — HISTORICAL MT5 BACKTEST result view.
 *
 * Shows run information, performance, the equity curve, paginated trade history
 * and the provenance of exactly what was tested. Every number comes from the
 * run's own persisted result; anything the engine did not produce is rendered
 * as N/A with the stored reason (never 0, never undefined / null / NaN /
 * [object Object]). This view is labelled HISTORICAL MT5 BACKTEST and is never
 * labelled LIVE / DEMO / PAPER / FORWARD TEST.
 */

const CARD = { background: "#0f172a", border: "1px solid #1e293b", borderRadius: 10, padding: 12 };
const CELL = { fontSize: 11, color: "#94a3b8" };
const GRID = { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))", gap: 8 };

function MetricBox({ label, value, tone, title }) {
  const color = tone === "pos" ? "#4ade80" : tone === "neg" ? "#f87171" : "#e2e8f0";
  return (
    <div style={{ background: "#020617", border: "1px solid #1e293b", borderRadius: 8, padding: "6px 8px" }}>
      <div style={{ fontSize: 10, color: "#94a3b8" }}>{label}</div>
      <div className="mono" style={{ fontSize: 13, color }} title={title}>{value}</div>
    </div>
  );
}

function signedTone(v) {
  if (v == null) return undefined;
  return v > 0 ? "pos" : v < 0 ? "neg" : undefined;
}

/** Deterministic inline-SVG equity sparkline (no chart library, no CDN). */
function EquityChart({ points, initialBalance }) {
  const path = useMemo(() => {
    if (!points || points.length < 2) return null;
    const pts = points.filter((p) => Array.isArray(p) && p.length >= 2 && Number.isFinite(p[1]));
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const y0 = Math.min(...ys), y1 = Math.max(...ys);
    const W = 640, H = 140, PAD = 6;
    const sx = (x) => PAD + (x1 === x0 ? 0 : ((x - x0) / (x1 - x0)) * (W - 2 * PAD));
    const sy = (y) => H - PAD - (y1 === y0 ? 0 : ((y - y0) / (y1 - y0)) * (H - 2 * PAD));
    const d = pts.map((p, i) => `${i === 0 ? "M" : "L"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(" ");
    return { d, W, H, y0, y1, base: initialBalance != null && initialBalance >= y0 && initialBalance <= y1
      ? sy(initialBalance) : null };
  }, [points, initialBalance]);

  if (!path) return <div className="muted" style={CELL}>no equity points yet</div>;
  return (
    <div>
      <svg viewBox={`0 0 ${path.W} ${path.H}`} width="100%" height={path.H} role="img"
           aria-label="equity curve">
        <rect x="0" y="0" width={path.W} height={path.H} fill="#020617" />
        {path.base != null && (
          <line x1="0" x2={path.W} y1={path.base} y2={path.base} stroke="#475569" strokeDasharray="4 4" />
        )}
        <path d={path.d} fill="none" stroke="#38bdf8" strokeWidth="1.6" />
      </svg>
      <div className="muted mono" style={{ fontSize: 10 }}>
        equity range {fmt.num(path.y0, 2)} … {fmt.num(path.y1, 2)}
        {initialBalance != null ? <> · dashed line = initial balance {fmt.num(initialBalance, 2)}</> : null}
      </div>
    </div>
  );
}

export default function HistoricalRunResults({ runId, onClose, runsForSelection }) {
  const [run, setRun] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [trades, setTrades] = useState(null);
  const [tPage, setTPage] = useState(0);
  const [equity, setEquity] = useState(null);
  const [showProvenance, setShowProvenance] = useState(false);
  const perPage = 25;

  const load = useCallback(async (id) => {
    if (!id) { setRun(null); return; }
    setBusy(true); setErr(null);
    try {
      const r = await api.mt5HistoricalRun(id);
      setRun(r);
      setTPage(0);
      setEquity(null); setTrades(null);
      if (r.trade_count > 0) {
        api.mt5HistoricalTrades(id, { limit: perPage, offset: 0 }).then(setTrades).catch(() => setTrades(null));
      }
      api.mt5HistoricalEquity(id, { max_points: 500 }).then(setEquity).catch(() => setEquity(null));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { load(runId); }, [runId, load]);

  const loadTradePage = async (page) => {
    if (!run) return;
    setTPage(page);
    try {
      const t = await api.mt5HistoricalTrades(run.run_id, { limit: perPage, offset: page * perPage });
      setTrades(t);
    } catch (e) {
      setErr(e);
    }
  };

  if (!runId) return null;
  const m = run?.results?.metrics || {};
  const d = run?.results?.derived || {};
  // V4.7: a run payload can legitimately contain a null/partial entry here;
  // it must never crash the panel (a bad record shows as an unnamed N/A reason)
  const unavailable = arr(run?.results?.unavailable).filter(Boolean);
  const unavailableFor = (key) => unavailable.find((u) => txt(u?.metric, "") === key);
  const pv = run?.provenance || {};

  return (
    <div style={CARD}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 12, color: "#e2e8f0" }}>HISTORICAL MT5 BACKTEST</strong>
        <span className="pill" style={{ fontSize: 9 }}>RESEARCH — NOT LIVE / DEMO / PAPER</span>
        {run && <StatusPill status={run.status} />}
        <span className="mono muted" style={{ fontSize: 11 }}>{runId}</span>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={() => load(runId)} title="reload this run">refresh</button>
        {onClose ? <button className="btn" onClick={onClose}>close</button> : null}
      </div>

      {err && <div className="warn-banner" style={{ marginTop: 8, fontSize: 11 }}>{String(err.message || err)}</div>}
      {busy && !run && <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>loading run…</div>}

      {run && (
        <>
          {/* ---------------- run information ---------------- */}
          <div style={{ marginTop: 10, ...GRID }}>
            <MetricBox label="node" value={<span>Node_{run.strategy_id}</span>} />
            <MetricBox label="symbol / timeframe" value={`${run.symbol} ${run.timeframe}`} />
            <MetricBox label="period (UTC)" value={`${String(run.period?.start || "").slice(0, 10)} → ${String(run.period?.end || "").slice(0, 10)}`} />
            <MetricBox label="bars" value={run.period?.bars ?? "N/A"} />
            <MetricBox label="data source" value={run.data?.source || "N/A"}
                       title={run.is_mt5_data ? "stored MT5 market data" : "not MT5 data — run is labelled accordingly"} />
            <MetricBox label="runtime" value={run.runtime_ms == null ? "N/A" : `${fmt.num(run.runtime_ms, 0)} ms`} />
            <MetricBox label="created" value={String(run.created_iso || "").replace("T", " ").slice(0, 19)} />
            <MetricBox label="finished" value={run.finished_iso ? String(run.finished_iso).replace("T", " ").slice(0, 19) : "N/A"} />
          </div>

          {run.error && (
            <div className="warn-banner" style={{ marginTop: 8, fontSize: 11 }}>
              <b>run error:</b> <span className="mono">{String(run.error)}</span>
            </div>
          )}

          {/* ---------------- performance ---------------- */}
          {run.status === "COMPLETED" && (
            <>
              <h4 style={{ fontSize: 11.5, margin: "12px 0 6px", color: "#cbd5e1" }}>Performance (engine output)</h4>
              <div style={GRID}>
                <MetricBox label="trades" value={m.trades ?? "N/A"} />
                <MetricBox label="win rate" value={m.win_rate == null ? "N/A" : pct(m.win_rate, 2)} />
                <MetricBox label="net P&L" value={m.net_profit == null ? "N/A" : money(m.net_profit)} tone={signedTone(m.net_profit)} />
                <MetricBox label="return" value={m.total_return_pct == null ? "N/A" : pct(m.total_return_pct, 2)} tone={signedTone(m.total_return_pct)} />
                <MetricBox label="profit factor" value={m.profit_factor == null ? "N/A" : ratio(m.profit_factor, 3)} />
                <MetricBox label="max drawdown" value={m.max_drawdown_pct == null ? "N/A" : pct(m.max_drawdown_pct, 2)} />
                <MetricBox label="expectancy" value={m.expectancy == null ? "N/A" : fmt.num(m.expectancy, 6)} />
                <MetricBox label="sharpe" value={m.sharpe == null ? "N/A" : fmt.num(m.sharpe, 3)} />
                <MetricBox label="sortino" value={m.sortino == null ? "N/A" : fmt.num(m.sortino, 3)} />
                <MetricBox label="avg trade" value={m.avg_trade == null ? "N/A" : fmt.num(m.avg_trade, 3)} tone={signedTone(m.avg_trade)} />
                <MetricBox label="final equity" value={m.final_equity == null ? "N/A" : fmt.num(m.final_equity, 2)} />
                <MetricBox label="consistency" value={m.consistency == null ? "N/A" : pct(m.consistency, 1)} />
                <MetricBox label="largest win*" value={d.largest_win == null ? "N/A" : money(d.largest_win)} />
                <MetricBox label="largest loss*" value={d.largest_loss == null ? "N/A" : money(d.largest_loss)} />
                <MetricBox label="avg win*" value={d.avg_win == null ? "N/A" : fmt.num(d.avg_win, 3)} />
                <MetricBox label="avg loss*" value={d.avg_loss == null ? "N/A" : fmt.num(d.avg_loss, 3)} />
                <MetricBox label="max consec. wins*" value={d.consecutive_wins ?? "N/A"} />
                <MetricBox label="max consec. losses*" value={d.consecutive_losses ?? "N/A"} />
              </div>
              <div className="muted" style={{ fontSize: 10, marginTop: 4 }}>
                * derived directly from this run's persisted trade list (labelled in the payload as derived).
                {" "}R:R is a stored genome parameter of the strategy, not a result of this run
                {pv?.strategy_identity?.genome_hash ? <> (genome {String(pv.strategy_identity.genome_hash).slice(0, 12)}…)</> : null}.
              </div>
            </>
          )}

          {/* ---------------- unavailable metrics ---------------- */}
          {unavailable.length > 0 && (
            <details style={{ marginTop: 8 }}>
              <summary className="muted" style={{ fontSize: 11, cursor: "pointer" }}>
                N/A metrics with reasons ({unavailable.length})
              </summary>
              <ul style={{ margin: "6px 0 0 16px", padding: 0 }}>
                {unavailable.map((u, i) => (
                  <li key={i} className="mono" style={{ fontSize: 10.5, color: "#94a3b8" }}>
                    <b>{txt(u?.metric, "metric")}</b>: {txt(u?.reason, "reason not recorded")}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {/* ---------------- equity ---------------- */}
          <h4 style={{ fontSize: 11.5, margin: "12px 0 6px", color: "#cbd5e1" }}>
            Equity {equity?.points_total ? <span className="muted" style={{ fontSize: 10 }}>
              ({equity.points_total} points{equity.downsampled ? `, showing ${equity.points.length}` : ""})
            </span> : null}
          </h4>
          {equity?.points?.length
            ? <EquityChart points={equity.points} initialBalance={equity.initial_balance} />
            : <div className="muted" style={CELL}>
                {(equity?.unavailable?.[0]?.reason) || "no equity artifact for this run"}
              </div>}

          {/* ---------------- trades ---------------- */}
          <h4 style={{ fontSize: 11.5, margin: "12px 0 6px", color: "#cbd5e1" }}>
            Trades {trades ? <span className="muted" style={{ fontSize: 10 }}>({trades.total} total, paginated)</span> : null}
          </h4>
          {!trades || trades.total === 0 ? (
            <div className="muted" style={CELL}>
              {(trades?.unavailable?.[0]?.reason) || "no trades persisted for this run"}
            </div>
          ) : (
            <>
              <div className="scroll-x" style={{ maxHeight: 300 }}>
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>#</th><th>side</th><th>entry (UTC)</th><th>exit (UTC)</th>
                      <th style={{ textAlign: "right" }}>entry</th><th style={{ textAlign: "right" }}>exit</th>
                      <th style={{ textAlign: "right" }}>lots</th><th style={{ textAlign: "right" }}>P&amp;L</th>
                      <th>reason</th><th style={{ textAlign: "right" }}>hold bars</th><th>session</th>
                    </tr>
                  </thead>
                  <tbody>
                    {arr(trades.trades).map((t, i) => (
                      <tr key={`${t.entry_ts}-${i}`}>
                        <td className="mono">{trades.offset + i + 1}</td>
                        <td className="mono">{t.side}</td>
                        <td className="mono">{new Date(t.entry_ts * 1000).toISOString().slice(0, 16).replace("T", " ")}</td>
                        <td className="mono">{t.exit_ts ? new Date(t.exit_ts * 1000).toISOString().slice(0, 16).replace("T", " ") : "—"}</td>
                        <td className="mono" style={{ textAlign: "right" }}>{fmt.num(t.entry_price, 3)}</td>
                        <td className="mono" style={{ textAlign: "right" }}>{t.exit_price == null ? "—" : fmt.num(t.exit_price, 3)}</td>
                        <td className="mono" style={{ textAlign: "right" }}>{fmt.num(t.lots, 2)}</td>
                        <td className="mono" style={{ textAlign: "right", color: t.pnl > 0 ? "#4ade80" : t.pnl < 0 ? "#f87171" : undefined }}>
                          {fmt.pnl(t.pnl)}
                        </td>
                        <td className="mono">{t.exit_reason || "—"}</td>
                        <td className="mono" style={{ textAlign: "right" }}>{t.hold_bars ?? "N/A"}</td>
                        <td className="mono">{t.session || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
                <button className="btn" disabled={tPage === 0} onClick={() => loadTradePage(tPage - 1)}>◀ prev</button>
                <span className="muted mono" style={{ fontSize: 11 }}>
                  page {tPage + 1} / {Math.max(1, Math.ceil(trades.total / perPage))}
                  {" "}· showing {trades.offset + 1}–{trades.offset + trades.trades.length} of {trades.total}
                </span>
                <button className="btn" disabled={(tPage + 1) * perPage >= trades.total}
                        onClick={() => loadTradePage(tPage + 1)}>next ▶</button>
              </div>
            </>
          )}

          {/* ---------------- provenance ---------------- */}
          <h4 style={{ fontSize: 11.5, margin: "12px 0 6px", color: "#cbd5e1" }}>
            Provenance <span className="muted" style={{ fontSize: 10 }}>exactly what was tested</span>
          </h4>
          <div className="mono" style={{ fontSize: 10.5, color: "#94a3b8", lineHeight: 1.55 }}>
            <div>dataset: <b>{pv.market_data?.dataset_id || run.data?.dataset_id || "N/A"}</b>
              {" "}· source <b>{pv.market_data?.dataset_source || run.data?.source || "N/A"}</b>
              {pv.market_data?.broker ? <> · {pv.market_data.broker}{pv.market_data.server ? ` / ${pv.market_data.server}` : ""}</> : null}</div>
            <div>dataset range: {String(pv.market_data?.dataset_range?.[0] || "").slice(0, 19)} → {String(pv.market_data?.dataset_range?.[1] || "").slice(0, 19)}
              {" "}· {pv.market_data?.dataset_bars_total ?? "N/A"} bars
              {pv.market_data?.dataset_fingerprint ? <> · fingerprint {pv.market_data.dataset_fingerprint}</> : null}</div>
            <div>data file: <span title={pv.market_data?.file?.path}>{String(pv.market_data?.file?.path || "N/A").split("/").slice(-3).join("/")}</span>
              {pv.market_data?.file?.sha256 ? <> · sha256 {String(pv.market_data.file.sha256).slice(0, 16)}…</> : null}
              {pv.market_data?.file?.size_bytes ? <> · {fmt.num(pv.market_data.file.size_bytes, 0)} bytes</> : null}</div>
            <div>executed window: rows [{run.config?.window?.[0]} … {run.config?.window?.[1]}) · {run.period?.bars} bars
              {run.config?.period_adjusted ? <> · <span style={{ color: "#fbbf24" }}>clipped to the stored data range</span> (requested {String(run.config?.start_date)} → {String(run.config?.end_date)})</> : null}</div>
            <div>strategy: node {pv.strategy_identity?.node_id ?? run.strategy_id}
              {" "}· generation {pv.strategy_identity?.generation ?? "N/A"}
              {" "}· status at run {pv.strategy_identity?.strategy_status_at_run || "N/A"}
              {" "}· genome {String(pv.strategy_identity?.genome_hash || "").slice(0, 12)}…</div>
            <div>cost multipliers: {JSON.stringify(run.config?.cost_multipliers || {})}
              {" "}· initial balance {fmt.num(run.config?.initial_balance, 2)}
              {" "}· risk per trade {run.config?.risk_per_trade ?? "N/A"}</div>
            <div>engine: {pv.execution?.engine || "app.backtest.engine"} · stage {pv.execution?.stage || "mt5_hist"}
              {" "}· versions {pv.engine_versions ? Object.entries(pv.engine_versions).slice(0, 6).map(([k, v]) => `${k}=${v}`).join(", ") : "N/A"}
              {pv.engine_versions?.git ? <> · git {pv.engine_versions.git}</> : null}</div>
            <div>reproduce: request key {pv.reproduce?.request_key || run.config?.request_key || "N/A"}
              {pv.fingerprint ? <> · experiment fingerprint {pv.fingerprint}</> : null}</div>
            <div style={{ color: "#4ade80" }}>
              orders placed by this run: <b>NO</b> — the historical backtest path cannot place, modify or close an order
              ({pv.orders?.note ? String(pv.orders.note).slice(0, 60) + "…" : "no MT5 demo order, no live-test order, no live trade"}).
            </div>
            {run.diagnostic_legacy ? (
              <div style={{ color: "#fbbf24" }}>DIAGNOSTIC LEGACY RUN — this node is a LEGACY_TEST record and the run is excluded from research statistics.</div>
            ) : null}
          </div>

          <details style={{ marginTop: 8 }} open={showProvenance} onToggle={(e) => setShowProvenance(e.target.open)}>
            <summary className="muted" style={{ fontSize: 11, cursor: "pointer" }}>raw provenance JSON</summary>
            <pre className="mono" style={{ fontSize: 10, maxHeight: 220, overflow: "auto", color: "#cbd5e1" }}>
{JSON.stringify(pv, null, 2)}
            </pre>
          </details>

          {runsForSelection ? <div style={{ marginTop: 8 }}>{runsForSelection}</div> : null}
        </>
      )}
    </div>
  );
}
