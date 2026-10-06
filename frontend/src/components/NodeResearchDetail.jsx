import React, { useMemo } from "react";
import { fmt } from "../api.js";
import { Card, ErrorNote, Pill, Spinner } from "./common.jsx";

/* V4.4/V4.5 shared node detail - RESEARCH RESULTS / NODE ECONOMICS /
 * EXECUTION RECORDS.
 *
 * This is the single implementation of the per-node research view: the Stats
 * page (V4.4) and the Strategy Lab (V4.5) both render it, so a research number,
 * an economics value and an execution record are displayed next to each other
 * in exactly one place - always in three separate, labelled columns. Values the
 * stored data cannot support are rendered as N/A with the reason reported by
 * the API; nothing is recalculated in the browser.
 */

export function NA({ reason }) {
  return <span className="muted" title={reason || "not available from stored data"}>N/A</span>;
}

export function val(v, render, reason) {
  if (v === null || v === undefined || (typeof v === "number" && Number.isNaN(v))) {
    return <NA reason={reason} />;
  }
  return render ? render(v) : fmt.num(v, 2);
}

export const pct = (v, d = 1, reason) =>
  val(v, (x) => <span className={x > 0 ? "pos" : x < 0 ? "neg" : ""}>{fmt.pct(x, d)}</span>, reason);
export const num = (v, d = 2, reason) => val(v, (x) => fmt.num(x, d), reason);
export const ratio = (v, d = 2, reason) => val(v, (x) => fmt.ratio(x, d), reason);
export const money = (v, d = 2, reason) => val(v, (x) => fmt.pnl(x, d), reason);

export function naMapOf(node) {
  const m = {};
  (node?.economics?.unavailable || []).forEach((u) => { m[u.metric] = u.reason; });
  return m;
}

export default function NodeResearchDetail({ node, error, busy, onOpenStrategy, emptyHint }) {
  const naMap = useMemo(() => naMapOf(node), [node]);

  if (error) return <ErrorNote err={error} />;
  if (busy) return <Card><Spinner /> loading node...</Card>;
  if (!node) {
    return emptyHint
      ? <Card><div className="muted" style={{ fontSize: 12 }}>{emptyHint}</div></Card>
      : null;
  }

  return (
    <>
{/* ---------------- Node detail: research vs economics vs execution ---------------- */}
{nodeErr && <ErrorNote err={nodeErr} />}
{nodeBusy && <Card><Spinner /> loading node…</Card>}
{node && !nodeBusy && (
  <Card style={{ marginBottom: 14 }}>
    <h3>
      Node_{node.node?.id}
      <span className="mono muted" style={{ marginLeft: 10, fontSize: 12 }}>
        {node.node?.symbol || "—"} {node.node?.timeframe || ""} {node.node?.direction || ""}
      </span>
      <Pill status={node.node?.status} />
      {node.node?.shortlisted ? <span className="pill SHORTLISTED" style={{ marginLeft: 6 }}>SHORTLISTED</span> : null}
      <button className="btn" style={{ marginLeft: "auto", float: "right" }}
              onClick={() => openStrategy && openStrategy(node.node.id)}>open in workspace</button>
    </h3>
    <div className="muted" style={{ fontSize: 11.5, marginBottom: 8 }}>{node.node?.scope_note}</div>

    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(280px, 1fr))", gap: 14 }}>
      {/* research results */}
      <div>
        <h4 className="muted" style={{ fontSize: 12 }}>RESEARCH RESULTS <span className="pill">BACKTEST</span></h4>
        <table className="tbl">
          <tbody>
            <tr><td>status / generation</td><td className="mono">{node.node?.status} · gen {fmt.num(node.node?.generation, 0)}</td></tr>
            <tr><td>fitness</td><td className="mono">{num(node.node?.fitness, 4)}</td></tr>
            <tr><td>research node #</td><td className="mono">{node.node?.research_node_num ?? <NA />}</td></tr>
            <tr><td>run</td><td className="mono" style={{ fontSize: 11 }}>{node.node?.run_id || <NA />}</td></tr>
            <tr><td>parent / children</td><td className="mono">{(node.node?.parent_id ? `Node_${node.node.parent_id}` : "—")} / {(node.node?.children || []).length}</td></tr>
            <tr><td>backtest stage</td><td className="mono">{node.research?.backtest?.stage || <NA />}</td></tr>
            <tr><td>return</td><td>{pct(node.research?.backtest?.total_return_pct, 2)}</td></tr>
            <tr><td>profit factor</td><td>{ratio(node.research?.backtest?.profit_factor)}</td></tr>
            <tr><td>win rate</td><td>{pct(node.research?.backtest?.win_rate)}</td></tr>
            <tr><td>trades</td><td>{node.research?.backtest?.trades != null ? fmt.num(node.research.backtest.trades, 0) : <NA />}</td></tr>
            <tr><td>net profit</td><td>{money(node.research?.backtest?.net_profit)}</td></tr>
            <tr><td>max drawdown</td><td>{pct(node.research?.backtest?.max_drawdown_pct)}</td></tr>
            <tr><td>sharpe / sortino</td><td>{ratio(node.research?.backtest?.sharpe)} / {ratio(node.research?.backtest?.sortino)}</td></tr>
            <tr><td>expectancy</td><td>{ratio(node.research?.backtest?.expectancy, 4)}</td></tr>
            <tr><td>robustness</td><td>{ratio(node.research?.robustness_score, 4)}</td></tr>
          </tbody>
        </table>
        <h4 className="muted" style={{ fontSize: 12, marginTop: 10 }}>VALIDATION</h4>
        <table className="tbl">
          <tbody>
            <tr><td>validation record</td><td className="mono">{node.research?.validation?.available ? "present" : <NA reason="no validation row stored for this node" />}</td></tr>
            <tr><td>passed</td><td className="mono">{node.research?.validation?.passed == null ? <NA /> : (node.research.validation.passed ? "YES" : "NO")}</td></tr>
            <tr><td>OOS return</td><td>{pct(node.research?.validation?.oos?.metrics?.total_return_pct, 2, naMap["validation_oos"])}</td></tr>
          </tbody>
        </table>
      </div>

      {/* node economics */}
      <div>
        <h4 className="muted" style={{ fontSize: 12 }}>NODE ECONOMICS</h4>
        <table className="tbl">
          <tbody>
            <tr><td>risk per trade</td><td>{pct((node.economics?.risk_per_trade_pct ?? null) !== null ? node.economics.risk_per_trade_pct / 100 : null, 2, naMap["risk_per_trade_pct"])}</td></tr>
            <tr><td>risk amount</td><td><NA reason={naMap["risk_amount"]} /></td></tr>
            <tr><td>position size (lots)</td><td><NA reason={naMap["position_size_lots"]} /></td></tr>
            <tr><td>SL distance</td><td>{node.economics?.sl_atr_multiple != null
              ? `${fmt.num(node.economics.sl_atr_multiple, 2)} × ATR${node.economics.atr_reference ? ` (${node.economics.atr_reference})` : ""}`
              : <NA reason={naMap["sl_atr_multiple"]} />}</td></tr>
            <tr><td>TP distance</td><td>{node.economics?.tp_atr_multiple != null
              ? `${fmt.num(node.economics.tp_atr_multiple, 2)} × ATR${node.economics.atr_reference ? ` (${node.economics.atr_reference})` : ""}`
              : <NA reason={naMap["tp_atr_multiple"]} />}</td></tr>
            <tr><td>reward / risk</td><td>{ratio(node.economics?.reward_risk_ratio, 3)}</td></tr>
            <tr><td>trailing stop</td><td className="muted" style={{ fontSize: 11 }}>{node.economics?.trailing_stop || "—"}</td></tr>
            <tr><td>hold bars (min/max)</td><td className="mono">{fmt.num(node.economics?.min_hold_bars, 0)} / {fmt.num(node.economics?.max_hold_bars, 0)}</td></tr>
            <tr><td>max concurrent</td><td className="mono">{node.economics?.max_concurrent_positions ?? <NA />}</td></tr>
            <tr><td>estimated exposure</td><td><NA reason={naMap["estimated_exposure"]} /></td></tr>
            <tr><td>trade count <span className="pill" style={{ fontSize: 9 }}>BACKTEST</span></td>
                <td>{node.economics?.trade_stats?.trade_count != null
                  ? fmt.num(node.economics.trade_stats.trade_count, 0) : <NA />}</td></tr>
            <tr><td>win rate</td><td>{pct(node.economics?.trade_stats?.win_rate)}</td></tr>
            <tr><td>profit factor</td><td>{ratio(node.economics?.trade_stats?.profit_factor)}</td></tr>
            <tr><td>net profit</td><td>{money(node.economics?.trade_stats?.net_profit)}</td></tr>
            <tr><td>max drawdown</td><td>{pct(node.economics?.trade_stats?.max_drawdown_pct)}</td></tr>
          </tbody>
        </table>
        <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
          {node.economics?.note}
        </div>
        {(node.economics?.unavailable || []).length > 0 && (
          <details style={{ marginTop: 6 }}>
            <summary className="muted" style={{ fontSize: 11, cursor: "pointer" }}>
              N/A reasons ({(node.economics.unavailable || []).length})
            </summary>
            <ul className="muted" style={{ fontSize: 11, paddingLeft: 18 }}>
              {(node.economics.unavailable || []).map((u) => (
                <li key={u.metric}><b>{u.metric}</b>: {u.reason}</li>
              ))}
            </ul>
          </details>
        )}
      </div>

      {/* execution records — separate layer */}
      <div>
        <h4 className="muted" style={{ fontSize: 12 }}>EXECUTION RECORDS <span className="pill" style={{ fontSize: 9 }}>NOT RESEARCH</span></h4>
        <table className="tbl">
          <tbody>
            <tr><td>LIVE TEST trades</td><td className="mono">{fmt.num(node.execution?.live_test?.total_trades ?? 0, 0)}
              <span className="muted" style={{ marginLeft: 6 }}>{node.execution?.live_test?.status || "IDLE"}</span></td></tr>
            <tr><td>LIVE TEST net P/L</td><td>{money(node.execution?.live_test?.total_pnl)}</td></tr>
            <tr><td>MT5 DEMO trades</td><td className="mono">{fmt.num(node.execution?.mt5_demo?.total_trades ?? 0, 0)}
              <span className="muted" style={{ marginLeft: 6 }}>{node.execution?.mt5_demo?.status || "STOPPED"}</span></td></tr>
            <tr><td>MT5 DEMO net P/L</td><td>{money(node.execution?.mt5_demo?.total_pnl)}</td></tr>
            <tr><td>PAPER trades</td><td className="mono">{fmt.num(node.execution?.paper?.trades ?? 0, 0)}
              <span className="muted" style={{ marginLeft: 6 }}>open {fmt.num(node.execution?.paper?.open ?? 0, 0)}</span></td></tr>
            <tr><td>PAPER net P/L</td><td>{money(node.execution?.paper?.total_pnl)}</td></tr>
            <tr><td>MT5 backtest</td><td className="mono">{node.execution?.mt5_backtest?.available ? "present" : <NA reason="no MT5 backtest record stored" />}</td></tr>
          </tbody>
        </table>
        <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>{node.execution?.note}</div>
        {(node.execution?.paper?.records || []).length > 0 && (
          <div className="scroll-y" style={{ maxHeight: 150, marginTop: 6 }}>
            <table className="tbl">
              <thead><tr><th>PAPER</th><th>sym</th><th>side</th><th>lots</th><th>P/L</th><th>status</th></tr></thead>
              <tbody>
                {(node.execution.paper.records || []).map((t) => (
                  <tr key={t.id}>
                    <td className="mono">{t.id}</td><td className="mono">{t.symbol || "—"}</td>
                    <td>{t.side || "—"}</td><td className="mono">{num(t.lots, 2)}</td>
                    <td>{money(t.pnl)}</td><td>{t.status || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  </Card>
)}

    </>
  );
}
