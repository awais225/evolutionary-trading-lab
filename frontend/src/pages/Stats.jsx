import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import { Card, ErrorNote, Metric, Pill, Spinner } from "../components/common.jsx";

/* V4.4 — Research Statistics & Node Economics.
 *
 * Read-only analytics page: it consumes the backend aggregates
 * (/api/stats/overview, /api/stats/nodes, /api/stats/node/{id}) and renders them
 * without recalculating anything in the browser. Research results
 * (BACKTEST / VALIDATION) and execution records (PAPER / MT5 DEMO / LIVE TEST)
 * are always labelled separately, and a value the stored data cannot support is
 * shown as N/A with the reason from the API.
 */

function NA({ reason }) {
  return <span className="muted" title={reason || "not available from stored data"}>N/A</span>;
}

function val(v, render, reason) {
  if (v === null || v === undefined || (typeof v === "number" && Number.isNaN(v))) {
    return <NA reason={reason} />;
  }
  return render ? render(v) : fmt.num(v, 2);
}

const pct = (v, d = 1, reason) =>
  val(v, (x) => <span className={x > 0 ? "pos" : x < 0 ? "neg" : ""}>{fmt.pct(x, d)}</span>, reason);
const num = (v, d = 2, reason) => val(v, (x) => fmt.num(x, d), reason);
const ratio = (v, d = 2, reason) => val(v, (x) => fmt.ratio(x, d), reason);
const money = (v, d = 2, reason) => val(v, (x) => fmt.pnl(x, d), reason);

const STATUS_FILTERS = ["BORN", "BACKTESTING", "SURVIVED", "VALIDATING", "QUALIFIED",
                        "PAPER", "FAILED", "KILLED", "RETIRED"];

export default function Stats() {
  const { openStrategy } = useLab() || {};
  const [overview, setOverview] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [listState, setListState] = useState({ status: "", search: "", sort: "fitness", offset: 0 });
  const [listErr, setListErr] = useState(null);
  const [node, setNode] = useState(null);
  const [nodeErr, setNodeErr] = useState(null);
  const [nodeBusy, setNodeBusy] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [showAllGens, setShowAllGens] = useState(false);

  const loadOverview = useCallback(async () => {
    setBusy(true);
    try {
      setOverview(await api.statsOverview());
      setErr(null);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }, []);

  const loadList = useCallback(async (state) => {
    try {
      const params = { limit: 50, offset: state.offset, sort: state.sort };
      if (state.status) params.status = state.status;
      if (state.search) params.search = state.search;
      const res = await api.statsNodes(params);
      setRows(res.nodes || []);
      setTotal(res.total || 0);
      setListErr(null);
    } catch (e) {
      setListErr(e);
    }
  }, []);

  // aggregate payload only — refreshed on an interval, never per render
  useEffect(() => { loadOverview(); }, [loadOverview]);
  useEffect(() => {
    const t = setInterval(() => loadOverview(), 20000);
    return () => clearInterval(t);
  }, [loadOverview]);

  // node list: paged, reloaded when the filter changes (details load only on demand)
  useEffect(() => { loadList(listState); }, [listState, loadList]);

  const openNode = async (sid) => {
    setSelectedId(sid);
    setNodeBusy(true);
    setNodeErr(null);
    try {
      setNode(await api.statsNode(sid));
    } catch (e) {
      setNodeErr(e);
      setNode(null);
    } finally {
      setNodeBusy(false);
    }
  };

  const pop = overview?.population;
  const evo = overview?.evolution;
  const perf = overview?.research_performance;
  const exec = overview?.execution_records;
  const scope = overview?.scope;

  const genRows = useMemo(() => (evo?.generations || []).slice(0, showAllGens ? 40 : 12), [evo, showAllGens]);
  const naMap = useMemo(() => {
    const m = {};
    (node?.economics?.unavailable || []).forEach((u) => { m[u.metric] = u.reason; });
    return m;
  }, [node]);

  const stageRows = perf?.backtest?.stages || [];

  return (
    <div>
      <h2 className="page-title">Research Statistics &amp; Node Economics</h2>
      <div className="page-sub">
        Research analytics only — the engine's generation, qualification, death, retirement and
        evolution logic is untouched. Every number below is read from the stored research data;
        nothing is re-simulated and nothing is invented.
      </div>

      {scope && (
        <div className="panel" style={{ marginBottom: 14, display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center" }}>
          <span className={`pill ${scope.population === "USER_RESEARCH" ? "QUALIFIED" : ""}`}>
            SCOPE: {scope.population}
          </span>
          <span className="mono">{fmt.num(scope.user_research_nodes, 0)} research nodes counted</span>
          <span className="muted">{fmt.num(scope.legacy_excluded_nodes, 0)} LEGACY_TEST infrastructure records excluded</span>
          <span className="muted" style={{ fontSize: 11.5 }}>
            predicate: <code>{scope.predicate}</code>
          </span>
          <span className="muted" style={{ marginLeft: "auto", fontSize: 11.5 }}>
            {busy ? "refreshing…" : overview ? `updated ${fmt.ts(overview.generated_at)}` : ""}
          </span>
        </div>
      )}
      <ErrorNote err={err} />

      {/* ---------------- Population ---------------- */}
      <h3>Population <span className="muted" style={{ fontSize: 11.5 }}>(USER_RESEARCH)</span></h3>
      <div className="metric-grid" style={{ marginBottom: 12 }}>
        <Metric label="TOTAL" value={pop ? fmt.num(pop.total, 0) : <Spinner />} />
        <Metric label="TARGET" value={fmt.num(pop?.target, 0)} />
        <Metric label="REMAINING" value={fmt.num(pop?.remaining, 0)} sub={pop?.progress_pct != null ? `${fmt.num(pop.progress_pct, 1)}% of target` : undefined} />
        <Metric label="ALIVE" value={fmt.num(pop?.alive, 0)} />
        <Metric label="DEAD" value={fmt.num(pop?.dead, 0)} />
        <Metric label="QUALIFIED" value={fmt.num(pop?.qualified, 0)} />
        <Metric label="FAILED" value={fmt.num(pop?.failed, 0)} />
        <Metric label="RETIRED" value={fmt.num(pop?.retired, 0)} />
        <Metric label="KILLED" value={fmt.num(pop?.killed, 0)} />
        <Metric label="SURVIVED / EVALUATED" value={fmt.num(pop?.survived, 0)} />
        <Metric label="PAPER" value={fmt.num(pop?.paper, 0)} />
        <Metric label="BACKTESTING" value={fmt.num(pop?.backtesting, 0)} />
        <Metric label="VALIDATING" value={fmt.num(pop?.validating, 0)} />
        <Metric label="BORN / QUEUED" value={fmt.num(pop?.born, 0)} />
      </div>
      <div className="muted" style={{ fontSize: 11.5, marginBottom: 14 }}>
        Source: {pop?.source || "—"}. {pop?.scope_note}
      </div>

      {/* ---------------- Evolution ---------------- */}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 1fr) minmax(420px, 1.4fr)", gap: 14, marginBottom: 14 }}>
        <Card>
          <h3>Evolution</h3>
          <table className="tbl">
            <tbody>
              <tr><td>Current generation</td><td className="mono">{fmt.num(evo?.current_generation, 0)}</td></tr>
              <tr><td>Generations recorded</td><td className="mono">{fmt.num(evo?.generations_recorded, 0)}</td></tr>
              <tr><td>Nodes generated</td><td className="mono">{fmt.num(evo?.nodes_generated, 0)}</td></tr>
              <tr><td>Nodes evaluated <span className="muted" title={evo?.evaluated_definition}>ⓘ</span></td>
                  <td className="mono">{fmt.num(evo?.nodes_evaluated, 0)}</td></tr>
              <tr><td>Nodes validated</td><td className="mono">{fmt.num(evo?.nodes_validated, 0)}</td></tr>
              <tr><td>Nodes remaining</td><td className="mono">{fmt.num(evo?.nodes_remaining, 0)}</td></tr>
              <tr><td>Qualification rate <span className="muted" title={evo?.rates_definition}>ⓘ</span></td>
                  <td className="mono">{pct(evo?.qualification_rate, 2)}</td></tr>
              <tr><td>Survival rate <span className="muted" title={evo?.rates_definition}>ⓘ</span></td>
                  <td className="mono">{pct(evo?.survival_rate, 2)}</td></tr>
              <tr><td>Best fitness</td><td className="mono">{num(evo?.best_fitness, 4)}</td></tr>
              <tr><td>Nodes / minute</td><td className="mono">{num(evo?.nodes_per_minute, 1)}</td></tr>
            </tbody>
          </table>
          <div className="muted" style={{ fontSize: 11 }}>{evo?.evaluated_definition}</div>
        </Card>

        <Card>
          <h3>Generations
            <button className="btn" style={{ marginLeft: 10 }}
                    onClick={() => setShowAllGens((v) => !v)}>
              {showAllGens ? "show latest 12" : "show up to 40"}
            </button>
          </h3>
          <ResponsiveContainer width="100%" height={150}>
            <BarChart data={genRows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="#1c2333" />
              <XAxis dataKey="generation" tick={{ fill: "#8b93a7", fontSize: 10 }} stroke="#2a3348" />
              <YAxis tick={{ fill: "#8b93a7", fontSize: 10 }} stroke="#2a3348" width={44} />
              <Tooltip contentStyle={{ background: "#151a26", border: "1px solid #232b3d", fontSize: 12 }} />
              <Bar dataKey="nodes" fill="#4f8ef7" name="nodes" />
            </BarChart>
          </ResponsiveContainer>
          <div className="scroll-y" style={{ maxHeight: 190 }}>
            <table className="tbl">
              <thead><tr><th>gen</th><th>nodes</th><th>evaluated</th><th>qualified</th><th>survived</th><th>dead</th><th>best fitness</th></tr></thead>
              <tbody>
                {genRows.map((g) => (
                  <tr key={g.generation}>
                    <td className="mono">{g.generation}</td>
                    <td>{fmt.num(g.nodes, 0)}</td>
                    <td>{fmt.num(g.evaluated, 0)}</td>
                    <td>{fmt.num(g.qualified, 0)}</td>
                    <td>{fmt.num(g.survived, 0)}</td>
                    <td>{fmt.num(g.dead, 0)}</td>
                    <td className="mono">{num(g.best_fitness, 4)}</td>
                  </tr>
                ))}
                {!genRows.length && <tr><td colSpan={7}><Spinner /></td></tr>}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {/* ---------------- Research performance ---------------- */}
      <Card style={{ marginBottom: 14 }}>
        <h3>Research performance
          <span className="pill" style={{ marginLeft: 10 }}>{perf?.backtest?.layer || "BACKTEST"}</span>
          <span className="pill" style={{ marginLeft: 6 }}>{perf?.validation?.layer || "VALIDATION"}</span>
        </h3>
        <div className="muted" style={{ fontSize: 11.5, marginBottom: 8 }}>{perf?.backtest?.note}</div>
        <div className="scroll-x">
          <table className="tbl">
            <thead><tr>
              <th>stage</th><th>backtests</th><th>nodes</th><th>avg return</th><th>best</th><th>worst</th>
              <th>avg PF</th><th>best PF</th><th>avg win rate</th><th>avg max DD</th><th>avg sharpe</th>
              <th>avg trades</th><th>total trades</th><th>sum net profit</th>
            </tr></thead>
            <tbody>
              {stageRows.map((s) => (
                <tr key={s.stage}>
                  <td className="mono">{s.stage}</td>
                  <td>{fmt.num(s.backtests, 0)}</td>
                  <td>{fmt.num(s.nodes, 0)}</td>
                  <td>{pct(s.avg_return_pct, 2)}</td>
                  <td>{pct(s.best_return_pct, 2)}</td>
                  <td>{pct(s.worst_return_pct, 2)}</td>
                  <td>{ratio(s.avg_profit_factor)}</td>
                  <td>{ratio(s.best_profit_factor)}</td>
                  <td>{pct(s.avg_win_rate)}</td>
                  <td>{pct(s.avg_max_drawdown_pct)}</td>
                  <td>{ratio(s.avg_sharpe)}</td>
                  <td>{num(s.avg_trades, 1)}</td>
                  <td>{fmt.num(s.total_trades, 0)}</td>
                  <td>{money(s.total_net_profit, 0)}</td>
                </tr>
              ))}
              {!stageRows.length && <tr><td colSpan={14}><Spinner /></td></tr>}
            </tbody>
          </table>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginTop: 12 }}>
          <div>
            <h4 className="muted" style={{ fontSize: 12 }}>Validation ({perf?.validation?.layer})</h4>
            <table className="tbl">
              <tbody>
                <tr><td>validation records</td><td className="mono">{fmt.num(perf?.validation?.records, 0)}</td></tr>
                <tr><td>nodes validated</td><td className="mono">{fmt.num(perf?.validation?.nodes, 0)}</td></tr>
                <tr><td>passed</td><td className="mono">{fmt.num(perf?.validation?.passed, 0)}</td></tr>
                <tr><td>pass rate</td><td className="mono">{pct(perf?.validation?.pass_rate)}</td></tr>
                <tr><td>avg robustness</td><td className="mono">{ratio(perf?.validation?.avg_robustness, 4)}</td></tr>
                <tr><td>best robustness</td><td className="mono">{ratio(perf?.validation?.best_robustness, 4)}</td></tr>
              </tbody>
            </table>
          </div>
          <div>
            <h4 className="muted" style={{ fontSize: 12 }}>Top research nodes (by fitness)</h4>
            <div className="scroll-y" style={{ maxHeight: 190 }}>
              <table className="tbl">
                <thead><tr><th>node</th><th>status</th><th>fitness</th><th>return</th><th>PF</th><th>win</th></tr></thead>
                <tbody>
                  {(perf?.top_nodes || []).map((n) => (
                    <tr key={n.id} onClick={() => openNode(n.id)} style={{ cursor: "pointer" }}>
                      <td className="mono">{n.id}</td>
                      <td><Pill status={n.status} /></td>
                      <td className="mono">{num(n.fitness, 4)}</td>
                      <td>{pct(n.backtest_return_pct, 2)}</td>
                      <td>{ratio(n.backtest_profit_factor)}</td>
                      <td>{pct(n.backtest_win_rate)}</td>
                    </tr>
                  ))}
                  {!(perf?.top_nodes || []).length && <tr><td colSpan={6}><Spinner /></td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </Card>

      {/* ---------------- Per-node statistics ---------------- */}
      <Card style={{ marginBottom: 14 }}>
        <h3>Per-node statistics <span className="muted" style={{ fontSize: 11.5 }}>(USER_RESEARCH, paged)</span></h3>
        <div className="btn-row" style={{ marginBottom: 8 }}>
          <select value={listState.status}
                  onChange={(e) => setListState((s) => ({ ...s, status: e.target.value, offset: 0 }))}>
            <option value="">all statuses</option>
            {STATUS_FILTERS.map((s) => <option key={s}>{s}</option>)}
          </select>
          <select value={listState.sort}
                  onChange={(e) => setListState((s) => ({ ...s, sort: e.target.value, offset: 0 }))}>
            <option value="fitness">sort: fitness</option>
            <option value="id">sort: newest id</option>
            <option value="generation">sort: generation</option>
            <option value="status">sort: status</option>
            <option value="created">sort: created</option>
          </select>
          <input placeholder="search id / research # / symbol" value={listState.search}
                 onChange={(e) => setListState((s) => ({ ...s, search: e.target.value, offset: 0 }))}
                 style={{ minWidth: 220 }} />
          <button className="btn" onClick={() => loadList(listState)}>refresh</button>
          <span className="muted" style={{ fontSize: 11.5 }}>
            {fmt.num(total, 0)} matching · page {Math.floor(listState.offset / 50) + 1}
          </span>
          <button className="btn" disabled={listState.offset === 0}
                  onClick={() => setListState((s) => ({ ...s, offset: Math.max(0, s.offset - 50) }))}>‹ prev</button>
          <button className="btn" disabled={listState.offset + 50 >= total}
                  onClick={() => setListState((s) => ({ ...s, offset: s.offset + 50 }))}>next ›</button>
        </div>
        <ErrorNote err={listErr} />
        <div className="scroll-y" style={{ maxHeight: 380 }}>
          <table className="tbl">
            <thead><tr>
              <th>node</th><th>research #</th><th>status</th><th>gen</th><th>symbol</th><th>tf</th><th>dir</th>
              <th>fitness</th><th>return</th><th>PF</th><th>win</th><th>max DD</th><th>trades</th>
              <th>validation</th><th>exec (P/MT5/LT)</th><th>stage</th>
            </tr></thead>
            <tbody>
              {rows.map((n) => (
                <tr key={n.id} onClick={() => openNode(n.id)}
                    style={{ cursor: "pointer", background: selectedId === n.id ? "rgba(79,142,247,0.12)" : undefined }}>
                  <td className="mono" style={{ fontWeight: 600 }}>Node_{n.id}</td>
                  <td className="mono">{n.research_node_num ?? <NA />}</td>
                  <td><Pill status={n.status} /></td>
                  <td>{fmt.num(n.generation, 0)}</td>
                  <td className="mono">{n.symbol || <NA />}</td>
                  <td>{n.timeframe || <NA />}</td>
                  <td>{n.direction || <NA />}</td>
                  <td className="mono">{num(n.fitness, 4)}</td>
                  <td>{pct(n.research?.return_pct, 2)}</td>
                  <td>{ratio(n.research?.profit_factor)}</td>
                  <td>{pct(n.research?.win_rate)}</td>
                  <td>{pct(n.research?.max_drawdown_pct)}</td>
                  <td>{n.research?.trades != null ? fmt.num(n.research.trades, 0) : <NA />}</td>
                  <td>{n.research?.validation
                    ? (n.research.validation.passed ? "PASSED" : "FAILED") +
                      (n.research.validation.robustness_score != null
                        ? ` (${fmt.ratio(n.research.validation.robustness_score, 3)})` : "")
                    : <NA />}</td>
                  <td className="mono">
                    {fmt.num(n.execution?.paper_trades ?? 0, 0)} / {fmt.num(n.execution?.mt5_demo_trades ?? 0, 0)} /{" "}
                    {fmt.num(n.execution?.live_test_trades ?? 0, 0)}
                  </td>
                  <td className="muted" style={{ fontSize: 11 }}>{n.pipeline_stage || n.research?.latest_backtest_stage || "—"}</td>
                </tr>
              ))}
              {!rows.length && <tr><td colSpan={16}><Spinner /></td></tr>}
            </tbody>
          </table>
        </div>
        <div className="muted" style={{ fontSize: 11 }}>
          Research columns are BACKTEST/VALIDATION values; the exec column counts execution records
          (PAPER / MT5 DEMO / LIVE TEST) and never feeds a research metric. Click a row (or a Top node)
          to load its details — details are fetched on demand only.
        </div>
      </Card>

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

      {/* ---------------- Portfolio execution records ---------------- */}
      <Card>
        <h3>Execution records <span className="pill" style={{ fontSize: 9 }}>{exec?.layer || "EXECUTION"}</span></h3>
        <div className="muted" style={{ fontSize: 11.5, marginBottom: 8 }}>{exec?.note}</div>
        <div className="scroll-x">
          <table className="tbl">
            <thead><tr><th>layer</th><th>trades</th><th>open</th><th>closed</th><th>net P/L</th><th>win rate</th><th>last activity</th><th>source</th></tr></thead>
            <tbody>
              {[exec?.paper, exec?.mt5_demo, exec?.live_test].filter(Boolean).map((b) => (
                <tr key={b.layer}>
                  <td><span className="pill" style={{ fontSize: 9 }}>{b.layer}</span></td>
                  <td className="mono">{b.available === false ? <NA reason={b.reason} /> : fmt.num(b.trades, 0)}</td>
                  <td className="mono">{fmt.num(b.open, 0)}</td>
                  <td className="mono">{fmt.num(b.closed, 0)}</td>
                  <td>{money(b.total_pnl)}</td>
                  <td>{pct(b.win_rate)}</td>
                  <td className="mono">{fmt.dt(b.last_ts)}</td>
                  <td className="muted" style={{ fontSize: 10.5 }}>{b.source}</td>
                </tr>
              ))}
              {exec?.manual_orders && (
                <tr>
                  <td><span className="pill" style={{ fontSize: 9 }}>{exec.manual_orders.layer}</span></td>
                  <td className="mono" colSpan={2}>{exec.manual_orders.executions != null ? `${fmt.num(exec.manual_orders.executions, 0)} executions` : <NA />}</td>
                  <td className="mono" colSpan={2}>{exec.manual_orders.rejected != null ? `${fmt.num(exec.manual_orders.rejected, 0)} rejected` : <NA />}</td>
                  <td><NA /></td>
                  <td className="mono">{fmt.dt(exec.manual_orders.last_ts)}</td>
                  <td className="muted" style={{ fontSize: 10.5 }}>{exec.manual_orders.source}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
          These records never affect: {(exec?.never_affects || []).join(", ")}.
        </div>
      </Card>
    </div>
  );
}
