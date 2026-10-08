import React, { useMemo } from "react";
import { fmt } from "../api.js";
import { Card, ErrorNote, Pill, Spinner } from "./common.jsx";
import { arr, objOrNull, txt } from "../lib/safe.js";
import { nodeLabel } from "./ui.jsx";

/* V4.4/V4.5 shared node detail - RESEARCH RESULTS / NODE ECONOMICS /
 * EXECUTION RECORDS.
 *
 * This is the single implementation of the per-node research view: the Stats
 * page (V4.4) and the Strategy Lab (V4.5) both render it, so a research number,
 * an economics value and an execution record are displayed next to each other
 * in exactly one place - always in three separate, labelled columns. Values the
 * stored data cannot support are rendered as N/A with the reason reported by
 * the API; nothing is recalculated in the browser.
 *
 * V4.7 hardening: this component must survive a node with incomplete, legacy or
 * malformed data (missing genome, no backtest, empty arrays, null columns,
 * objects where a scalar was expected, unknown node ids) without crashing the
 * page. Every uncontrolled value goes through the safe helpers, every list is
 * coerced with `arr()`, and the section errors reported by the API are shown
 * instead of being swallowed.
 */

export function NA({ reason }) {
  return <span className="muted" title={reason || "not available from stored data"}>N/A</span>;
}

export function val(v, render, reason) {
  // V4.7: only real, finite numbers/strings reach a formatter; anything else
  // (null, undefined, NaN, objects, arrays) renders as N/A
  const usable = typeof v === "number"
    ? Number.isFinite(v)
    : (typeof v === "string" ? v.trim() !== "" : (v !== null && v !== undefined && typeof v !== "object"));
  if (!usable) {
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
  arr(node?.economics?.unavailable).forEach((u) => {
    const metric = txt(u?.metric, "");
    if (metric) m[metric] = txt(u?.reason, "not available from stored data");
  });
  return m;
}

/** Section errors reported by the node-detail API (V4.7) - shown, never hidden. */
function SectionErrors({ errors }) {
  const list = arr(errors).filter((e) => e && (e.section || e.error));
  if (!list.length) return null;
  return (
    <details style={{ marginTop: 8 }}>
      <summary className="muted" style={{ fontSize: 11, cursor: "pointer" }}>
        {list.length} section(s) of this node could not be read — shown as N/A (diagnose)
      </summary>
      <ul className="muted" style={{ fontSize: 11, paddingLeft: 18, marginTop: 4 }}>
        {list.map((e, i) => (
          <li key={`${txt(e?.section, "section")}-${i}`}>
            <b>{txt(e?.section, "section")}</b>
            {e?.field ? ` · ${txt(e.field)}` : ""}: {txt(e?.error, "unreadable stored value")}
          </li>
        ))}
      </ul>
    </details>
  );
}

export default function NodeResearchDetail({ node, error, busy, onOpenStrategy, emptyHint }) {
  const naMap = useMemo(() => naMapOf(node), [node]);

  if (error) return <ErrorNote err={error} />;
  if (busy) return <Card><Spinner /> loading node...</Card>;
  if (!node || !objOrNull(node.node)) {
    return emptyHint
      ? <Card><div className="muted" style={{ fontSize: 12 }}>{emptyHint}</div></Card>
      : null;
  }

  const n = node.node;
  const econ = objOrNull(node.economics) || {};
  const exec = objOrNull(node.execution) || {};
  const research = objOrNull(node.research) || {};
  const backtest = objOrNull(research.backtest) || {};
  const validation = objOrNull(research.validation) || {};
  const tradeStats = objOrNull(econ.trade_stats) || {};
  const paper = objOrNull(exec.paper) || {};
  const paperRecords = arr(paper.records);
  const unavailable = arr(econ.unavailable);
  const children = arr(n.children);

  return (
    <Card style={{ marginBottom: 14 }}>
      <h3>
        {/* V5.3 §4 — the study-local identity (never the row id) */}
        {nodeLabel(n)}
        <span className="mono muted" style={{ marginLeft: 10, fontSize: 12 }}>
          {txt(n.symbol, "—")} {txt(n.timeframe, "")} {txt(n.direction, "")}
        </span>
        <Pill status={n.status} />
        {n.shortlisted ? <span className="pill SHORTLISTED" style={{ marginLeft: 6 }}>SHORTLISTED</span> : null}
        <button
          className="btn"
          style={{ marginLeft: "auto", float: "right" }}
          disabled={n.id === null || n.id === undefined || !onOpenStrategy}
          onClick={() => { if (onOpenStrategy && n.id !== null && n.id !== undefined) onOpenStrategy(n.id); }}
        >
          open in workspace
        </button>
      </h3>
      <div className="muted" style={{ fontSize: 11.5, marginBottom: 8 }}>{txt(n.scope_note, "")}</div>
      <SectionErrors errors={node.section_errors} />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(280px, 1fr))", gap: 14 }}>
        {/* research results */}
        <div>
          <h4 className="muted" style={{ fontSize: 12 }}>RESEARCH RESULTS <span className="pill">BACKTEST</span></h4>
          <table className="tbl">
            <tbody>
              <tr><td>status / generation</td><td className="mono">{txt(n.status, "—")} · gen {num(n.generation, 0, "no generation stored for this node")}</td></tr>
              <tr><td>fitness</td><td className="mono">{num(n.fitness, 4)}</td></tr>
              <tr><td>research node #</td><td className="mono">{n.research_node_num === null || n.research_node_num === undefined ? <NA /> : txt(n.research_node_num, "—")}</td></tr>
              <tr><td>run</td><td className="mono" style={{ fontSize: 11 }}>{n.run_id ? txt(n.run_id) : <NA />}</td></tr>
              <tr><td>parent / children</td><td className="mono">{(n.parent_id ? `Node_${txt(n.parent_id)}` : "—")} / {children.length}</td></tr>
              <tr><td>backtest stage</td><td className="mono">{backtest.stage ? txt(backtest.stage) : <NA />}</td></tr>
              <tr><td>return</td><td>{pct(backtest.total_return_pct, 2)}</td></tr>
              <tr><td>profit factor</td><td>{ratio(backtest.profit_factor)}</td></tr>
              <tr><td>win rate</td><td>{pct(backtest.win_rate)}</td></tr>
              <tr><td>trades</td><td>{num(backtest.trades, 0)}</td></tr>
              <tr><td>net profit</td><td>{money(backtest.net_profit)}</td></tr>
              <tr><td>max drawdown</td><td>{pct(backtest.max_drawdown_pct)}</td></tr>
              <tr><td>sharpe / sortino</td><td>{ratio(backtest.sharpe)} / {ratio(backtest.sortino)}</td></tr>
              <tr><td>expectancy</td><td>{ratio(backtest.expectancy, 4)}</td></tr>
              <tr><td>robustness</td><td>{ratio(research.robustness_score, 4)}</td></tr>
            </tbody>
          </table>
          <h4 className="muted" style={{ fontSize: 12, marginTop: 10 }}>VALIDATION</h4>
          <table className="tbl">
            <tbody>
              <tr><td>validation record</td><td className="mono">{validation.available ? "present" : <NA reason="no validation row stored for this node" />}</td></tr>
              <tr><td>passed</td><td className="mono">{validation.passed == null ? <NA /> : (validation.passed ? "YES" : "NO")}</td></tr>
              <tr><td>OOS return</td><td>{pct(objOrNull(validation.oos)?.metrics?.total_return_pct, 2, naMap["validation_oos"])}</td></tr>
            </tbody>
          </table>
        </div>

        {/* node economics */}
        <div>
          <h4 className="muted" style={{ fontSize: 12 }}>NODE ECONOMICS</h4>
          <table className="tbl">
            <tbody>
              <tr><td>risk per trade</td><td>{econ.risk_per_trade_pct != null ? pct(econ.risk_per_trade_pct / 100, 2, naMap["risk_per_trade_pct"]) : <NA reason={naMap["risk_per_trade_pct"]} />}</td></tr>
              <tr><td>risk amount</td><td><NA reason={naMap["risk_amount"]} /></td></tr>
              <tr><td>position size (lots)</td><td><NA reason={naMap["position_size_lots"]} /></td></tr>
              <tr><td>SL distance</td><td>{econ.sl_atr_multiple != null
                ? `${fmt.num(econ.sl_atr_multiple, 2)} × ATR${econ.atr_reference ? ` (${txt(econ.atr_reference)})` : ""}`
                : <NA reason={naMap["sl_atr_multiple"]} />}</td></tr>
              <tr><td>TP distance</td><td>{econ.tp_atr_multiple != null
                ? `${fmt.num(econ.tp_atr_multiple, 2)} × ATR${econ.atr_reference ? ` (${txt(econ.atr_reference)})` : ""}`
                : <NA reason={naMap["tp_atr_multiple"]} />}</td></tr>
              <tr><td>reward / risk</td><td>{ratio(econ.reward_risk_ratio, 3)}</td></tr>
              <tr><td>trailing stop</td><td className="muted" style={{ fontSize: 11 }}>{econ.trailing_stop ? txt(econ.trailing_stop) : "—"}</td></tr>
              <tr><td>hold bars (min/max)</td><td className="mono">{num(econ.min_hold_bars, 0)} / {num(econ.max_hold_bars, 0)}</td></tr>
              <tr><td>max concurrent</td><td className="mono">{econ.max_concurrent_positions === null || econ.max_concurrent_positions === undefined ? <NA /> : txt(econ.max_concurrent_positions, "—")}</td></tr>
              <tr><td>estimated exposure</td><td><NA reason={naMap["estimated_exposure"]} /></td></tr>
              <tr><td>trade count <span className="pill" style={{ fontSize: 9 }}>BACKTEST</span></td>
                  <td>{num(tradeStats.trade_count, 0)}</td></tr>
              <tr><td>win rate</td><td>{pct(tradeStats.win_rate)}</td></tr>
              <tr><td>profit factor</td><td>{ratio(tradeStats.profit_factor)}</td></tr>
              <tr><td>net profit</td><td>{money(tradeStats.net_profit)}</td></tr>
              <tr><td>max drawdown</td><td>{pct(tradeStats.max_drawdown_pct)}</td></tr>
            </tbody>
          </table>
          <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>{txt(econ.note, "")}</div>
          {unavailable.length > 0 && (
            <details style={{ marginTop: 6 }}>
              <summary className="muted" style={{ fontSize: 11, cursor: "pointer" }}>
                N/A reasons ({unavailable.length})
              </summary>
              <ul className="muted" style={{ fontSize: 11, paddingLeft: 18 }}>
                {unavailable.map((u, i) => (
                  <li key={`${txt(u?.metric, "metric")}-${i}`}>
                    <b>{txt(u?.metric, "metric")}</b>: {txt(u?.reason, "not available from stored data")}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>

        {/* execution records - separate layer */}
        <div>
          <h4 className="muted" style={{ fontSize: 12 }}>EXECUTION RECORDS <span className="pill" style={{ fontSize: 9 }}>NOT RESEARCH</span></h4>
          <table className="tbl">
            <tbody>
              <tr><td>LIVE TEST trades</td><td className="mono">{num(objOrNull(exec.live_test)?.total_trades ?? 0, 0)}
                <span className="muted" style={{ marginLeft: 6 }}>{txt(objOrNull(exec.live_test)?.status, "IDLE")}</span></td></tr>
              <tr><td>LIVE TEST net P/L</td><td>{money(objOrNull(exec.live_test)?.total_pnl)}</td></tr>
              <tr><td>MT5 DEMO trades</td><td className="mono">{num(objOrNull(exec.mt5_demo)?.total_trades ?? 0, 0)}
                <span className="muted" style={{ marginLeft: 6 }}>{txt(objOrNull(exec.mt5_demo)?.status, "STOPPED")}</span></td></tr>
              <tr><td>MT5 DEMO net P/L</td><td>{money(objOrNull(exec.mt5_demo)?.total_pnl)}</td></tr>
              <tr><td>PAPER trades</td><td className="mono">{num(paper.trades ?? 0, 0)}
                <span className="muted" style={{ marginLeft: 6 }}>open {num(paper.open ?? 0, 0)}</span></td></tr>
              <tr><td>PAPER net P/L</td><td>{money(paper.total_pnl)}</td></tr>
              <tr><td>MT5 backtest</td><td className="mono">{objOrNull(exec.mt5_backtest)?.available ? "present" : <NA reason="no MT5 backtest record stored" />}</td></tr>
            </tbody>
          </table>
          <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>{txt(exec.note, "")}</div>
          {paperRecords.length > 0 && (
            <div className="scroll-y" style={{ maxHeight: 150, marginTop: 6 }}>
              <table className="tbl">
                <thead><tr><th>PAPER</th><th>sym</th><th>side</th><th>lots</th><th>P/L</th><th>status</th></tr></thead>
                <tbody>
                  {paperRecords.map((t, i) => (
                    <tr key={t?.id ?? `paper-${i}`}>
                      <td className="mono">{txt(t?.id, "—")}</td><td className="mono">{txt(t?.symbol, "—")}</td>
                      <td>{txt(t?.side, "—")}</td><td className="mono">{num(t?.lots, 2)}</td>
                      <td>{money(t?.pnl)}</td><td>{txt(t?.status, "—")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
