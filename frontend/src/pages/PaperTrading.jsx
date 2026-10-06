import React, { useEffect, useState } from "react";
import { api, fmt } from "../api.js";
import { arr } from "../lib/safe.js";
import { useLab } from "../App.jsx";
import { Pill, SignedNum, ErrorNote, EventFeed, Metric } from "../components/common.jsx";
import StructuredError from "../components/StructuredError.jsx";

export default function PaperTrading() {
  const { events, openStrategy } = useLab();
  const [st, setSt] = useState(null);
  const [trades, setTrades] = useState({ trades: [], summary: {} });
  const [execs, setExecs] = useState([]);
  const [calib, setCalib] = useState(null);
  const [risk, setRisk] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState("");

  const load = () => {
    api.paperStatus().then(setSt).catch((e) => setErr(e.message));
    api.paperTrades(120).then(setTrades).catch(() => {});
    api.executions(60).then((r) => setExecs(r.executions)).catch(() => {});
    api.calibration().then(setCalib).catch(() => {});
    api.risk().then(setRisk).catch(() => {});
  };
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, []);

  const act = async (name, fn) => {
    setBusy(name);
    try { await fn(); load(); } catch (e) { setErr(e.message); }
    setBusy("");
  };

  const sum = trades.summary || {};
  const acct = st?.account || {};
  const limits = st?.limits || risk?.limits || {};
  const promoted = st?.promoted_strategies || [];
  /* V4.8 — dead nodes are never tradable paper candidates. They stay in the
   * full population record, but the execution candidate list excludes them and
   * says how many were excluded instead of hiding the fact. */
  const DEAD = new Set(["FAILED", "KILLED", "RETIRED", "DEAD"]);
  const tradablePromoted = promoted.filter((p) => !DEAD.has(String(p.status || "").toUpperCase())
                                                && !DEAD.has(String(p.paper_result || "").toUpperCase()));
  const excludedDead = promoted.length - tradablePromoted.length;
  const waitingCandidates = tradablePromoted.filter((p) => p.paper_result === "WAITING" || p.status === "QUALIFIED" || p.status === "PAPER_ELIGIBLE");

  return (
    <div>
      <h2 className="page-title">Internal Paper Trading (Simulated Execution Environment)</h2>
      <div className="page-sub">
        Internal paper portfolio simulation with realistic execution modeling and hard capital risk controls.
        (Note: For real-time forward evaluation, see <b>Live Testing</b>; for MetaTrader orders, see <b>MT5 Demo Trading</b>).
        Feed: <span className={"pill " + (st?.feed_is_simulated ? "sim" : "real")}>
          {st?.feed_source || "?"}</span>
      </div>
      {err && <StructuredError error={err} onDismiss={() => setErr(null)} />}

      <div className="btn-row" style={{ marginBottom: 14 }}>
        <button className="btn success" disabled={st?.running || busy === "start"}
                title={st?.running ? "paper trading is already running" : busy === "start" ? "starting…" : "start the internal paper portfolio simulation"}
                onClick={() => act("start", api.paperStart)}>▶ START PAPER TRADING</button>
        <button className="btn danger" disabled={!st?.running || busy === "stop"}
                title={!st?.running ? "paper trading is not running" : busy === "stop" ? "stopping…" : "stop paper trading"}
                onClick={() => act("stop", api.paperStop)}>⏹ STOP PAPER TRADING</button>
        <button className={"btn " + (risk?.kill_switch ? "success" : "danger")}
                onClick={() => act("kill", () => api.killSwitch(!risk?.kill_switch))}>
          {risk?.kill_switch ? "🔓 RELEASE KILL SWITCH" : "🛑 EMERGENCY KILL SWITCH"}
        </button>
        <button className="btn" onClick={() => act("recal", api.recalibrate)}>
          ⚖ RECALIBRATE execution model
        </button>
      </div>

      {/* V3 Paper Capital & Account Metrics (spec §22, §24) */}
      <div className="panel" style={{ marginBottom: 14, background: "var(--bg-panel)", border: "1px solid var(--border)" }}>
        <div className="flex justify-between items-center" style={{ marginBottom: 10 }}>
          <h3 style={{ margin: 0, fontSize: "0.95rem" }}>💰 SIMULATED ACCOUNT & CAPITAL PERFORMANCE (V3.2)</h3>
          <span className="mono muted" style={{ fontSize: "0.75rem" }}>Strict Simulated Capital Isolation</span>
        </div>
        <div className="grid cols-6" style={{ gap: "10px" }}>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>STARTING CAPITAL</div>
            <div style={{ fontSize: "1.15rem", fontWeight: 700, color: "var(--fg)", fontFamily: "monospace" }}>
              ${fmt.num(acct.starting_capital || limits.starting_capital || 10000, 2)}
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Initial balance</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>CURRENT BALANCE</div>
            <div style={{ fontSize: "1.15rem", fontWeight: 700, color: "var(--fg)", fontFamily: "monospace" }}>
              ${fmt.num(acct.current_balance || 10000, 2)}
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Realized capital</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>CURRENT EQUITY</div>
            <div style={{ fontSize: "1.15rem", fontWeight: 700, color: (acct.current_equity || 10000) >= (acct.starting_capital || 10000) ? "var(--green)" : "var(--red)", fontFamily: "monospace" }}>
              ${fmt.num(acct.current_equity || 10000, 2)}
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Balance + Floating</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>REALIZED P&amp;L</div>
            <div style={{ fontSize: "1.15rem", fontWeight: 700, fontFamily: "monospace" }}>
              <SignedNum v={acct.realized_pnl || sum.pnl || 0} />
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>{sum.n || 0} closed trades</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>UNREALIZED P&amp;L</div>
            <div style={{ fontSize: "1.15rem", fontWeight: 700, fontFamily: "monospace" }}>
              <SignedNum v={acct.unrealized_pnl || 0} />
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>{st?.open_positions?.length || 0} open positions</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>RETURN %</div>
            <div style={{ fontSize: "1.15rem", fontWeight: 700, fontFamily: "monospace" }}>
              <SignedNum v={acct.return_pct || 0} pct />
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Account Net ROI</div>
          </div>
        </div>
      </div>

      {/* V3 Hard Capital Risk Guardrails Dashboard (spec §22, §23, §24) */}
      <div className="panel" style={{ marginBottom: 14, background: "var(--bg-panel)", border: "1px solid var(--border)" }}>
        <div className="flex justify-between items-center" style={{ marginBottom: 10 }}>
          <div className="flex items-center" style={{ gap: "8px" }}>
            <h3 style={{ margin: 0, fontSize: "0.95rem" }}>🛡 HARD CAPITAL RISK GUARDRAILS &amp; ENFORCEMENT</h3>
            <span className={"pill " + ((acct.guardrail_status || "").includes("NOMINAL") ? "active" : "fail")}>
              {acct.guardrail_status || "ALL GUARDRAILS ACTIVE / NOMINAL"}
            </span>
          </div>
          <span className="mono muted" style={{ fontSize: "0.75rem" }}>Strictest applicable limit wins</span>
        </div>
        <div className="grid cols-6" style={{ gap: "10px" }}>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>DAILY LOSS</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: acct.daily_loss > 0 ? "var(--red)" : "var(--fg)", fontFamily: "monospace" }}>
              ${fmt.num(acct.daily_loss || 0, 2)}
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>
              Limit: ${fmt.num(limits.max_daily_loss_abs || 300, 0)} ({((limits.max_daily_loss_pct || 0.03)*100).toFixed(0)}%)
            </div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>MAX DRAWDOWN</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: acct.max_drawdown_pct > 5 ? "var(--amber)" : "var(--fg)", fontFamily: "monospace" }}>
              {fmt.num(acct.max_drawdown_pct || 0, 1)}%
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>
              Limit: {((limits.max_total_drawdown_pct || 0.10)*100).toFixed(0)}% (${fmt.num(acct.max_drawdown || 0, 1)})
            </div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>CURRENT EXPOSURE</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--fg)", fontFamily: "monospace" }}>
              {fmt.num(acct.current_exposure_pct || 0, 1)}%
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>
              Limit: {((limits.max_exposure_pct || 0.30)*100).toFixed(0)}%
            </div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>CONCURRENT POSITIONS</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--fg)", fontFamily: "monospace" }}>
              {st?.open_positions?.length || 0} / {limits.max_concurrent_positions || 3}
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Max allowed: {limits.max_concurrent_positions || 3}</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>RISK PER TRADE</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--fg)", fontFamily: "monospace" }}>
              {((limits.max_risk_per_trade_pct || 0.01)*100).toFixed(1)}% / ${fmt.num(limits.max_risk_per_trade_abs || 100, 0)}
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Strict lower applied</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>RISK BREACHES</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: (acct.risk_breaches || 0) > 0 ? "var(--red)" : "var(--green)", fontFamily: "monospace" }}>
              {acct.risk_breaches || 0}
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Total orders blocked</div>
          </div>
        </div>
      </div>

      {/* Promoted Strategies Table (spec §21, §24) */}
      <div className="panel" style={{ marginBottom: 14 }}>
        <div className="flex justify-between items-center" style={{ marginBottom: 8 }}>
          <h3 style={{ margin: 0 }}>🏆 PROMOTED CANDIDATES &amp; PAPER TRADING STRATEGIES</h3>
          <span className="muted" style={{ fontSize: "0.8rem" }}>
            {arr(promoted).length} Qualified Candidates ({arr(waitingCandidates).length} waiting for paper execution)
          </span>
        </div>
        <div className="scroll-y" style={{ maxHeight: 280 }}>
          {excludedDead > 0 && (
            <div className="kit-inline-err" style={{ marginBottom: 6 }}>
              <b>{excludedDead} dead node(s) excluded from the paper-trading candidate list.</b>
              They remain in the full population record and are never traded here.
            </div>
          )}
          <table className="tbl">
            <thead>
              <tr>
                <th>Node</th>
                <th>Strategy / Logic</th>
                <th>Status</th>
                <th>P&amp;L</th>
                <th>Return</th>
                <th>Win Rate</th>
                <th>Profit Factor</th>
                <th>Trades</th>
                <th>Drawdown</th>
                <th>Exposure</th>
                <th>Breaches</th>
                <th>Paper Result</th>
                <th>Qualification Reason</th>
              </tr>
            </thead>
            <tbody>
              {arr(tradablePromoted).map((p) => (
                <tr key={p.id} onClick={() => openStrategy(p.id)}>
                  <td className="mono" style={{ fontWeight: "bold" }}>{p.node}</td>
                  <td className="mono muted" style={{ fontSize: 11, maxWidth: 220 }}>#{p.id} · {p.desc}</td>
                  <td><Pill status={p.status} /></td>
                  <td><SignedNum v={p.pnl} /></td>
                  <td><SignedNum v={p.return_pct} pct /></td>
                  <td>{fmt.pct(p.win_rate / 100.0, 1)}</td>
                  <td>{fmt.num(p.profit_factor, 2)}</td>
                  <td>{p.trades}</td>
                  <td>{fmt.pct(p.drawdown / 100.0, 1)}</td>
                  <td>{fmt.pct(p.exposure / 100.0, 1)}</td>
                  <td>{p.risk_breaches > 0 ? <span style={{ color: "var(--red)", fontWeight: 700 }}>{p.risk_breaches}</span> : <span className="muted">0</span>}</td>
                  <td>
                    <span className={"pill " + (p.paper_result === "PASSED" ? "active" : p.paper_result === "FAILED" ? "fail" : p.paper_result === "TESTING" ? "sim" : "real")}>
                      {p.paper_result}
                    </span>
                  </td>
                  <td className="muted" style={{ fontSize: 10.5, maxWidth: 260 }}>
                    {p.qualification_reason}
                  </td>
                </tr>
              ))}
              {!arr(tradablePromoted).length && (
                <tr>
                  <td colSpan={13} className="muted" style={{ textAlign: "center", padding: "1.5rem" }}>
                    No candidates promoted yet — strategies must survive screening, detailed backtesting, and the validation battery before qualifying.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid cols-2" style={{ gridTemplateColumns: "1fr 1fr", marginBottom: 14 }}>
        <div className="panel">
          <h3>Active open positions ({st?.open_positions?.length || 0})</h3>
          <table className="tbl">
            <thead><tr><th>strategy</th><th>side</th><th>entry</th><th>lots</th><th>since</th><th>source</th></tr></thead>
            <tbody>
              {(st?.open_positions || []).map((p) => (
                <tr key={p.strategy_id} style={{ cursor: "default" }}>
                  <td onClick={() => openStrategy(p.strategy_id)}>#{p.strategy_id}</td>
                  <td className={p.side === "buy" ? "pos" : "neg"}>{p.side}</td>
                  <td>{fmt.num(p.entry_price)}</td><td>{p.lots}</td>
                  <td>{fmt.ts(p.entry_ts)}</td><td>{p.source}</td>
                </tr>
              ))}
              {(!st?.open_positions || st.open_positions.length === 0) && (
                <tr><td colSpan={6} className="muted">No currently open paper positions</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="panel">
          <h3>Live calibration — BACKTEST ASSUMPTION vs OBSERVED EXECUTION</h3>
          {calib && (
            <table className="tbl calib-tbl">
              <thead><tr><th>metric</th><th>backtest assumption</th><th>observed</th><th>Δ%</th><th>samples</th></tr></thead>
              <tbody>
                {arr(calib.assumption_vs_observed).map((r) => (
                  <tr key={r.metric} style={{ cursor: "default" }}>
                    <td>{r.metric}</td>
                    <td>{fmt.num(r.backtest_assumption, 2)}</td>
                    <td>{r.observed == null ? <span className="muted">no data</span> : fmt.num(r.observed, 2)}</td>
                    <td className={Math.abs(r.delta_pct || 0) > 40 ? "neg" : ""}>
                      {r.delta_pct == null ? "–" : `${r.delta_pct > 0 ? "+" : ""}${r.delta_pct}%`}</td>
                    <td>{r.samples}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>{calib?.note}</p>

          {st?.divergence_flags?.length > 0 && (
            <div className="warn-banner">
              ⚠ Paper-vs-backtest divergence flags:{" "}
              {arr(st.divergence_flags).map((f, i) => (
                <span key={f.strategy_id} style={{ marginRight: 10 }}>
                  #{f.strategy_id} (WR {fmt.pct(f.paper_win_rate, 0)} vs {fmt.pct(f.backtest_win_rate, 0)})
                </span>
              ))}
            </div>
          )}
          {risk?.recent_rejections?.length > 0 && (
            <div className="panel" style={{ marginTop: 8, background: "#2a1518" }}>
              <h3 style={{ color: "#fca5a5" }}>Recent risk-layer rejections</h3>
              {arr(risk.recent_rejections).slice(-5).reverse().map((r, i) => (
                <div key={i} className="mono" style={{ fontSize: 11 }}>
                  {fmt.ts(r.ts)} #{r.strategy_id} {r.side} {r.symbol} — {r.reason}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 14 }}>
        <h3>Paper trades (full execution trail)</h3>
        <div className="scroll-y" style={{ maxHeight: 300 }}>
          <table className="tbl">
            <thead><tr>
              <th>#</th><th>strategy</th><th>side</th><th>signal</th><th>req px</th>
              <th>bid/ask</th><th>spread(pt)</th><th>exec</th><th>px</th><th>delay(ms)</th>
              <th>slip(pt)</th><th>exit</th><th>reason</th><th>PnL</th><th>status</th><th>source</th>
            </tr></thead>
            <tbody>
              {arr(trades.trades).map((t, i) => (
                <tr key={t.id} style={{ cursor: "default" }}>
                  <td>{t.id}</td>
                  <td onClick={() => openStrategy(t.strategy_id)}>#{t.strategy_id}</td>
                  <td className={t.side === "buy" ? "pos" : "neg"}>{t.side}</td>
                  <td>{fmt.ts(t.signal_ts)}</td>
                  <td>{fmt.num(t.requested_price)}</td>
                  <td className="mono" style={{fontSize:10}}>{fmt.num(t.bid)}/{fmt.num(t.ask)}</td>
                  <td>{fmt.num(t.spread_points, 1)}</td>
                  <td>{fmt.ts(t.exec_ts)}</td>
                  <td>{fmt.num(t.exec_price)}</td>
                  <td>{fmt.num(t.exec_delay_ms, 0)}</td>
                  <td>{fmt.num(t.slippage_points, 1)}</td>
                  <td>{t.exit_price ? `${fmt.ts(t.exit_ts)} @ ${fmt.num(t.exit_price)}` : "–"}</td>
                  <td>{t.exit_reason || "–"}</td>
                  <td>{t.pnl == null ? "–" : <SignedNum v={t.pnl} />}</td>
                  <td><Pill status={t.status === "OPEN" ? "PAPER" : t.pnl > 0 ? "SURVIVED" : "FAILED"} /></td>
                  <td><span className={"pill " + (t.source === "SIMULATOR" ? "sim" : "real")}>{t.source}</span></td>
                </tr>
              ))}
              {!arr(trades.trades).length && <tr><td colSpan={16} className="muted">no paper trades yet</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid cols-2">
        <div className="panel">
          <h3>Execution log (signal → risk → fill)</h3>
          <div className="scroll-y" style={{ maxHeight: 260 }}>
            <table className="tbl">
              <thead><tr><th>time</th><th>src</th><th>side</th><th>#strat</th><th>result</th><th>delay</th><th>slip</th><th>rejected_by</th></tr></thead>
              <tbody>{arr(execs).map((e) => (
                <tr key={e.id} style={{ cursor: "default" }}>
                  <td>{fmt.ts(e.ts)}</td><td>{e.source}</td>
                  <td>{e.side}</td><td>{e.strategy_id ? `#${e.strategy_id}` : "–"}</td>
                  <td className={e.result === "FILLED" ? "pos" : "neg"}>{e.result}</td>
                  <td>{fmt.num(e.exec_delay_ms, 0)}</td><td>{fmt.num(e.slippage_points, 1)}</td>
                  <td className="muted" style={{fontSize:10}}>{e.rejected_by || ""}</td>
                </tr>))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="panel">
          <h3>Paper events</h3>
          <EventFeed events={events} limit={40}
                     filter={(e) => e.type.startsWith("paper") || e.type === "kill_switch" || e.type === "recalibrated"} />
        </div>
      </div>
    </div>
  );
}
