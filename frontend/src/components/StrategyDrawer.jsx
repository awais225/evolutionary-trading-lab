import React, { useEffect, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { api, fmt } from "../api.js";
import { Pill, SignedNum, JsonView, MatrixTable, Spinner, ErrorNote } from "./common.jsx";

export default function StrategyDrawer({ id, onClose, onOpen }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [tab, setTab] = useState("overview");

  useEffect(() => {
    setData(null);
    api.strategy(id).then(setData).catch((e) => setErr(e.message));
  }, [id]);

  if (err) return <Shell onClose={onClose}><ErrorNote err={err} /></Shell>;
  if (!data) return <Shell onClose={onClose}><Spinner /></Shell>;

  const s = data.strategy;
  const detail = data.backtests.find((b) => b.stage === "detail") ||
                 data.backtests.find((b) => b.stage === "screen");
  const m = detail?.metrics || {};

  return (
    <Shell onClose={onClose}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <h2 style={{ margin: 0, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span>Node_{s.id}</span>
          <span style={{ fontSize: "0.85rem", color: "#4f8ef7", fontWeight: 600, background: "rgba(79, 142, 247, 0.12)", padding: "2px 8px", borderRadius: 4 }}>
            {s.research_node_num ? `Research Node #${s.research_node_num.toLocaleString()}` : (s.data_source === "LEGACY_TEST" ? "Legacy Test Node" : `Research Node #${s.id}`)}
          </span>
          <Pill status={s.status} />
        </h2>
        <span className="muted">gen {s.generation} · {s.symbol} {s.timeframe} · {s.direction}</span>
      </div>
      <p className="muted mono" style={{ fontSize: 12 }}>{s.description}</p>

      <div className="tabs">
        {[["overview", "Overview"], ["genome", "Genome"], ["history", "Research History"],
          ["matrices", "Matrices"], ["validation", "Validation"], ["backtests", "Backtests"],
          ["paper", "Paper"], ["hypotheses", "Hypotheses"]].map(([k, label]) => (
          <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>

      {tab === "overview" && (
        <div>
          <div className="grid cols-4" style={{ marginBottom: 12 }}>
            {[
              ["Fitness", fmt.num(s.fitness, 3)],
              ["Net profit", <SignedNum v={m.net_profit} />],
              ["Return", <SignedNum v={m.total_return_pct} pct />],
              ["Profit factor", fmt.num(m.profit_factor, 2)],
              ["Max DD", fmt.pct(m.max_drawdown_pct)],
              ["Sharpe", fmt.num(m.sharpe, 2)],
              ["Sortino", fmt.num(m.sortino, 2)],
              ["Win rate", fmt.pct(m.win_rate)],
              ["Trades", m.trades ?? "–"],
              ["Avg trade", <SignedNum v={m.avg_trade} />],
              ["Expectancy", fmt.num(m.expectancy, 5)],
              ["Complexity", s.complexity],
              ["Consistency", fmt.pct(m.consistency, 0)],
              ["Avg hold", `${m.avg_hold_bars ?? "–"} bars`],
              ["Species", <span className="mono" style={{fontSize:11}}>{s.species_key}</span>],
              ["Mutation", s.mutation_type || "–"],
            ].map(([l, v]) => (
              <div className="panel metric-card" key={l}>
                <div className="label">{l}</div>
                <div className="value" style={{ fontSize: 16 }}>{v}</div>
              </div>
            ))}
          </div>
          {m.monthly_pnl && (
            <div className="panel">
              <h3>Monthly PnL</h3>
              <table className="tbl"><tbody><tr>
                {Object.entries(m.monthly_pnl).map(([k, v]) => (
                  <td key={k}><div className="muted" style={{fontSize:10}}>{k}</div><SignedNum v={v} /></td>
                ))}
              </tr></tbody></table>
            </div>
          )}
          {m.exit_reasons && (
            <div className="panel" style={{ marginTop: 12 }}>
              <h3>Exit reasons</h3>
              {Object.entries(m.exit_reasons).map(([k, v]) => (
                <span key={k} className="pill RETIRED" style={{ marginRight: 6 }}>{k}: {v}</span>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "genome" && (
        <div>
          <p className="muted" style={{fontSize:12}}>
            Machine-readable JSON genome (controlled DSL — strategies are data, never arbitrary code).
          </p>
          <JsonView data={s.genome} />
        </div>
      )}

      {tab === "history" && (
        <div>
          <div className="panel">
            <h3>Why this strategy exists (ancestry chain)</h3>
            <div className="lineage">
              {data.lineage.map((l, i) => (
                <div className="step" key={l.id}>
                  <b>#{l.id}</b> · gen {l.generation} · <Pill status={l.status} />
                  {" "}<span className="muted">origin: {l.origin}{l.mutation_type ? ` · ${l.mutation_type}` : ""}</span>
                  {l.fitness != null && <span className="muted"> · fitness {fmt.num(l.fitness, 3)}</span>}
                  <div className="why">{l.creation_reason || "generation-0 seed"}</div>
                </div>
              ))}
            </div>
          </div>
          <div className="panel" style={{ marginTop: 12 }}>
            <h3>Children ({data.children.length})</h3>
            <table className="tbl">
              <thead><tr><th>id</th><th>status</th><th>fitness</th><th>mutation</th><th>reason</th></tr></thead>
              <tbody>
                {data.children.map((c) => (
                  <ChildRow key={c.id} c={c} onOpen={onOpen} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "matrices" && (
        <div>
          {!data.matrices && <p className="muted">No diagnostic matrix yet — computed automatically when a strategy QUALIFIES (or run from Backtest Matrix page).</p>}
          {data.matrices && <>
            <MatrixTable title="TIMEFRAME matrix (same genome re-run per timeframe)" data={data.matrices.timeframe}
                         valueKeys={["trades", "profit", "pf", "dd", "return_pct", "sharpe", "fitness", "error"]} />
            <MatrixTable title="SESSION matrix" data={data.matrices.session} />
            <MatrixTable title="DAY matrix" data={data.matrices.day} />
            <MatrixTable title="REGIME matrix" data={data.matrices.regime} />
            <MatrixTable title="DIRECTION matrix" data={data.matrices.direction} />
            <p className="muted" style={{fontSize:12}}>
              Losing dimensions are NOT auto-removed — specialized children are proposed instead
              (see Hypotheses tab &amp; Research AI).
            </p>
          </>}
        </div>
      )}

      {tab === "validation" && (
        <div>
          {!data.validation && <p className="muted">Not validated yet. Top survivors enter the validation battery (OOS → walk-forward → perturbation → spread/slippage stress → Monte Carlo → regime holdout).</p>}
          {data.validation && <ValidationView v={data.validation} />}
        </div>
      )}

      {tab === "backtests" && (
        <div>
          {data.backtests.map((b) => (
            <div className="panel" key={b.id} style={{ marginBottom: 10 }}>
              <h3>stage: {b.stage} · verdict: {b.verdict} · {fmt.dt(b.created_at)}</h3>
              <div className="mono muted" style={{fontSize:11, marginBottom:6}}>
                dataset {b.dataset_id} · window {JSON.stringify(b.window)} · fitness {fmt.num(b.fitness,3)}
              </div>
              {b.metrics?.equity_curve?.length > 1 ? (
                <ResponsiveContainer width="100%" height={140}>
                  <LineChart data={b.metrics.equity_curve.map(([ts, eq]) => ({ ts, eq }))}>
                    <XAxis dataKey="ts" hide />
                    <YAxis domain={["auto", "auto"]} width={70} tick={{ fontSize: 10, fill: "#8b93a7" }} />
                    <Tooltip labelFormatter={(l) => fmt.dt(l)} formatter={(v) => fmt.num(v)} />
                    <ReferenceLine y={10000} stroke="#39415a" strokeDasharray="4 4" />
                    <Line dataKey="eq" stroke="#4f8ef7" dot={false} strokeWidth={1.4} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <div style={{ padding: "10px 14px", margin: "8px 0", background: "#131722", border: "1px dashed #39415a", borderRadius: 4, color: "#8b93a7", fontSize: 12, textAlign: "center", fontStyle: "italic" }}>
                  EQUITY CURVE DATA NOT FOUND FOR THIS STRATEGY
                </div>
              )}
              {b.metrics?.trades_sample && (
                <details style={{ marginTop: 8 }}>
                  <summary className="muted" style={{ cursor: "pointer", fontSize: 12 }}>
                    last {Math.min(20, b.metrics.trades_sample.length)} trades of {b.metrics.trades}
                  </summary>
                  <TradesTable trades={b.metrics.trades_sample.slice(-20)} />
                </details>
              )}
            </div>
          ))}
        </div>
      )}

      {tab === "paper" && (
        <div>
          <div className="grid cols-3" style={{ marginBottom: 12 }}>
            <div className="panel metric-card"><div className="label">paper trades (closed)</div>
              <div className="value">{data.paper_summary?.n ?? 0}</div></div>
            <div className="panel metric-card"><div className="label">paper PnL</div>
              <div className="value"><SignedNum v={data.paper_summary?.pnl} /></div></div>
            <div className="panel metric-card"><div className="label">paper win rate</div>
              <div className="value">{fmt.pct(data.paper_summary?.wr ?? null)}</div></div>
          </div>
          <p className="muted" style={{fontSize:12}}>
            Paper results are compared against backtest expectations; significant divergence raises a
            flag (visible on the Paper Trading page). Backtest assumptions are never silently altered.
          </p>
        </div>
      )}

      {tab === "hypotheses" && (
        <div>
          {!data.hypotheses.length && <p className="muted">No hypotheses for this strategy yet.</p>}
          {data.hypotheses.map((h) => (
            <div className="panel" key={h.id} style={{ marginBottom: 10 }}>
              <h3>hypothesis #{h.id} · {h.source} · <Pill status={h.status === "APPLIED" ? "QUALIFIED" : h.status === "REJECTED" ? "KILLED" : "BORN"} /></h3>
              <div style={{fontSize:13}}><b>Observation:</b> {h.observation}</div>
              <div style={{fontSize:13, marginTop:4}}><b>Hypothesis:</b> {h.hypothesis}</div>
              <div className="mono muted" style={{fontSize:11, marginTop:4}}>
                proposal: {h.proposal?.action} {JSON.stringify(h.proposal?.params)}
                {h.child_strategy_id ? ` → child #${h.child_strategy_id}` : ""}
              </div>
            </div>
          ))}
        </div>
      )}
    </Shell>
  );
}

function Shell({ children, onClose }) {
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="drawer">
        <button className="btn" style={{ float: "right" }} onClick={onClose}>✕ close</button>
        <div style={{ clear: "both" }}>{children}</div>
      </div>
    </>
  );
}

function ChildRow({ c, onOpen }) {
  return (
    <tr onClick={() => onOpen?.(c.id)}>
      <td>#{c.id}</td>
      <td><Pill status={c.status} /></td>
      <td>{fmt.num(c.fitness, 3)}</td>
      <td>{c.mutation_type}</td>
      <td className="muted" style={{ maxWidth: 320 }}>{c.creation_reason}</td>
    </tr>
  );
}

function TradesTable({ trades }) {
  return (
    <div className="scroll-y" style={{ maxHeight: 260 }}>
      <table className="tbl">
        <thead><tr><th>side</th><th>entry</th><th>exit</th><th>reason</th><th>lots</th><th>pnl</th><th>slip(pt)</th><th>session</th><th>regime</th></tr></thead>
        <tbody>
          {trades.map((t, i) => (
            <tr key={i} style={{ cursor: "default" }}>
              <td>{t.side}</td>
              <td>{fmt.dt(t.entry_ts)} @ {fmt.num(t.entry_price)}</td>
              <td>{fmt.dt(t.exit_ts)} @ {fmt.num(t.exit_price)}</td>
              <td>{t.exit_reason}</td>
              <td>{t.lots}</td>
              <td><SignedNum v={t.pnl} /></td>
              <td>{fmt.num(t.slippage_points, 1)}</td>
              <td>{t.session}</td>
              <td>{t.regime || "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ValidationView({ v }) {
  return (
    <div>
      <div className="grid cols-3" style={{ marginBottom: 12 }}>
        <div className="panel metric-card"><div className="label">Robustness score</div>
          <div className="value" style={{ color: v.passed ? "var(--green)" : "var(--red)" }}>
            {fmt.num(v.robustness_score, 3)}</div>
          <div className="delta">{v.passed ? "PASSED validation battery" : "FAILED"}</div></div>
        <div className="panel metric-card"><div className="label">OOS fitness</div>
          <div className="value">{fmt.num(v.oos?.fitness, 3)}</div>
          <div className="delta">degradation {fmt.pct(v.oos?.degradation)}</div></div>
        <div className="panel metric-card"><div className="label">Walk-forward</div>
          <div className="value">{v.walkforward?.positive_folds ?? "–"}/{v.walkforward?.n_folds ?? "–"}</div>
          <div className="delta">positive folds · consistency {fmt.pct(v.walkforward?.consistency)}</div></div>
      </div>
      {(v.notes || []).length > 0 && (
        <div className="warn-banner">Death-rule triggers: {v.notes.join(" · ")}</div>
      )}
      {v.walkforward?.folds && (
        <div className="panel" style={{ marginBottom: 12 }}>
          <h3>Walk-forward folds</h3>
          <table className="tbl">
            <thead><tr><th>fold</th><th>window</th><th>trades</th><th>PF</th><th>return</th><th>DD</th><th>fitness</th></tr></thead>
            <tbody>{v.walkforward.folds.map((f) => (
              <tr key={f.fold} style={{cursor:"default"}}>
                <td>{f.fold}</td><td className="mono">{f.window.join(" → ")}</td>
                <td>{f.trades}</td><td>{fmt.num(f.pf, 2)}</td>
                <td><SignedNum v={f.return_pct} pct /></td><td>{fmt.pct(f.dd)}</td>
                <td>{fmt.num(f.fitness, 3)}</td>
              </tr>))}
            </tbody>
          </table>
        </div>
      )}
      {v.perturbation && (
        <div className="panel" style={{ marginBottom: 12 }}>
          <h3>Parameter perturbation (threshold jitter)</h3>
          <div className="mono">runs: {v.perturbation.runs?.join(", ")} · mean {fmt.num(v.perturbation.mean_fitness,3)} vs base {fmt.num(v.perturbation.base_fitness,3)} · instability {fmt.pct(v.perturbation.instability)}</div>
        </div>
      )}
      {v.spread_stress?.scenarios && (
        <div className="panel" style={{ marginBottom: 12 }}>
          <h3>Spread / slippage / commission stress</h3>
          <table className="tbl">
            <thead><tr><th>scenario</th><th>fitness</th><th>degradation</th><th>PF</th><th>net</th></tr></thead>
            <tbody>{v.spread_stress.scenarios.map((sc) => (
              <tr key={sc.scenario} style={{cursor:"default"}}>
                <td>{sc.scenario}</td><td>{fmt.num(sc.fitness, 3)}</td>
                <td className={sc.degradation > 0.5 ? "neg" : ""}>{fmt.pct(sc.degradation)}</td>
                <td>{fmt.num(sc.pf, 2)}</td><td><SignedNum v={sc.net} /></td>
              </tr>))}
            </tbody>
          </table>
          <div className="muted" style={{fontSize:12, marginTop:6}}>worst degradation: {fmt.pct(v.spread_stress.worst_degradation)}</div>
        </div>
      )}
      {v.montecarlo && (
        <div className="panel" style={{ marginBottom: 12 }}>
          <h3>Monte Carlo (randomized trade sequences)</h3>
          <div className="mono">
            sims {v.montecarlo.sims} · return mean {fmt.pct(v.montecarlo.return_mean)} · p5 {fmt.pct(v.montecarlo.return_p5)} ·
            positive {fmt.pct(v.montecarlo.return_positive_frac, 0)} · worst DD {fmt.pct(v.montecarlo.dd_worst)} · mean PF {fmt.num(v.montecarlo.pf_mean, 2)}
          </div>
        </div>
      )}
      {v.regime_holdout?.per_regime && (
        <div className="panel">
          <h3>Regime holdout</h3>
          <MatrixTable title="" data={v.regime_holdout.per_regime} valueKeys={["trades", "pnl"]} />
          <div className="muted" style={{fontSize:12}}>profit concentration in single regime: {fmt.pct(v.regime_holdout.profit_concentration, 0)}</div>
        </div>
      )}
    </div>
  );
}
