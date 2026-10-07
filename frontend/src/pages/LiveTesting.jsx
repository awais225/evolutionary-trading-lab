import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { useLab } from "../App.jsx";
import StructuredError from "../components/StructuredError.jsx";
import LiveTestingControl from "../components/LiveTestingControl.jsx";
import LiveTradeCounter from "../components/LiveTradeCounter.jsx";
import {
  LiveMarketHeader, LiveMarketPanel, LiveNodeTable, ManualOrderPanel, RiskStrip, StageTimeline,
} from "../components/LiveTestingPanels.jsx";
import { arr, NA_TEXT, objOrNull, rows as safeRows, txt } from "../lib/safe.js";
import { Badge, Kpi, SectionTitle, StateBlock, useInterval } from "../components/ui.jsx";

/**
 * V5 §11-§16 — Live Testing.
 *
 * The page is built around what the engine actually does, in this order:
 *
 *   1. the live MT5 market header (bid/ask/spread/price/time and the selected
 *      node's own indicator values, resolved by the engine against real bars);
 *   2. the manual order panel, which places real demo orders through the same
 *      execution path the backend exposes (pips or prices, risk or lots);
 *   3. the combined node table: research metrics + live P/L + effective risk +
 *      schedule + START/STOP that really enrols/un-enrols the node;
 *   4. the engine activation panel (nothing runs until the operator confirms);
 *   5. the live activity: condition truth table, trade counter, stage timeline;
 *   6. the forward-test statistics that really exist (null when they do not).
 */
export default function LiveTesting() {
  const { shortlist, toggleShortlist, setSelectedStrategyId, navigateTab } = useLab() || {};
  const [summary, setSummary] = useState(null);
  const [engine, setEngine] = useState(null);
  const [results, setResults] = useState(null);
  const [risk, setRisk] = useState(null);
  const [error, setError] = useState(null);
  const [selectedNode, setSelectedNode] = useState("");
  const [marketSymbol, setMarketSymbol] = useState("XAUUSD");

  const load = useCallback(async () => {
    setError(null);
    try {
      const [sum, eng, res, rk] = await Promise.all([
        api.liveTestStatus(),
        api.liveTestingStatus(),
        api.liveTestResults({}),
        api.liveTestingRisk(),
      ]);
      setSummary(sum);
      setEngine(eng);
      setResults(res);
      setRisk(rk);
    } catch (e) {
      setError(e);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useInterval(load, 10000);

  const stats = objOrNull(results) || {};
  const prop = objOrNull(results?.prop_firm) || {};
  const unavailable = arr(results?.unavailable);
  // `engine` is the live-testing engine itself; `summary` carries the recorded
  // live-trade counters. Both are shown as they are — never merged into a number
  // the backend did not produce.
  const active = Boolean(engine?.active ?? summary?.active);
  const enrolled = safeRows(engine?.nodes);

  const openNode = (nodeId) => {
    if (setSelectedStrategyId) setSelectedStrategyId(nodeId);
    if (navigateTab) navigateTab("StrategyLab", nodeId);
  };

  return (
    <div className="page">
      <SectionTitle
        right={
          <div className="btn-row" style={{ margin: 0 }}>
            <Badge tone={active ? "ok" : "mute"}>engine {active ? "RUNNING" : "STOPPED"}</Badge>
            <Badge tone="warn">bridge {txt(summary?.bridge_source || summary?.source, "unavailable")}</Badge>
            <Badge tone="sim">demo only · real money $0</Badge>
          </div>
        }
      >
        Live Testing
      </SectionTitle>

      {error && <StructuredError error={error} onDismiss={() => setError(null)} />}

      {/* entering this page never starts anything: the engine is idle until the
          operator activates it and starts a node (spec §12) */}
      <div className={"kit-banner " + (active ? "real" : "sim")} role="status">
        <b>{active ? "LIVE TESTING IS ACTIVE" : "IDLE ON ENTRY — NOTHING IS RUNNING"}</b>
        <div className="muted" style={{ marginTop: 3, fontSize: 12 }}>
          {active
            ? <>The engine loop is running ({txt(engine?.cycle_count, "0")} cycles). Orders go only to a
                connected, positively identified DEMO terminal, and only when the node's own schedule allows it.</>
            : <>Opening this page starts nothing. A node trades only after activation <b>and</b> an explicit
                START on that node — and its schedule is then enforced by the engine before every order.</>}
        </div>
      </div>

      <div className="kit-strip">
        <Kpi label="Enrolled nodes" value={txt(engine?.node_count ?? summary?.active_strategies, "0")}
             sub={`${txt(engine?.excluded_count, "0")} excluded`} />
        <Kpi label="Open positions" value={txt(summary?.open_positions, "0")} />
        <Kpi label="Today P/L" value={txt(summary?.today_pnl ?? stats.current_daily_pnl, NA_TEXT)}
             tone={Number(summary?.today_pnl) > 0 ? "pos" : Number(summary?.today_pnl) < 0 ? "neg" : undefined} />
        <Kpi label="Total live P/L" value={txt(summary?.total_pnl ?? stats.net_profit, NA_TEXT)}
             tone={Number(summary?.total_pnl) > 0 ? "pos" : Number(summary?.total_pnl) < 0 ? "neg" : undefined} />
        <Kpi label="Balance" value={txt(stats.current_balance, NA_TEXT)} sub={`start ${txt(stats.starting_balance, "—")}`} />
        <Kpi label="Profit factor" value={txt(stats.profit_factor, NA_TEXT)}
             sub={stats.profit_factor === null ? "not enough closed trades" : "closed trades only"} />
        <Kpi label="Win rate" value={stats.win_rate === null || stats.win_rate === undefined ? NA_TEXT : `${stats.win_rate} %`} />
        <Kpi label="Max drawdown" value={stats.max_drawdown_pct === null || stats.max_drawdown_pct === undefined ? NA_TEXT : `${stats.max_drawdown_pct} %`} />
        <Kpi label="Global risk / trade" value={risk?.global_risk_pct === undefined || risk?.global_risk_pct === null ? NA_TEXT : `${risk.global_risk_pct} %`}
             sub={`${txt(risk?.override_count, "0")} node override(s)`} />
      </div>

      {prop.verdict && (
        <div className={"kit-banner " + (prop.verdict === "BREACHED" ? "danger" : prop.verdict === "TARGET_REACHED" ? "real" : "sim")}>
          <b>Prop-firm monitor: {txt(prop.verdict)}</b>
          <div className="muted" style={{ fontSize: 12, marginTop: 3 }}>
            account {txt(prop.account_size, "—")} · daily loss limit {txt(prop.daily_loss_limit, "—")}
            {" "}(used {txt(prop.daily_loss_used, "—")}) · max loss {txt(prop.max_loss_limit, "—")} ·
            profit target {txt(prop.profit_target, "—")} · {txt(prop.trading_days, "0")} of{" "}
            {txt(prop.min_trading_days, "—")} trading day(s)
            {arr(prop.rule_violations).length > 0 ? ` · violations: ${arr(prop.rule_violations).join(", ")}` : ""}
          </div>
        </div>
      )}

      <LiveMarketHeader
        symbol={marketSymbol}
        nodeId={selectedNode}
        nodes={enrolled.map((n) => ({ node_id: n.node_id ?? n.strategy_id, market: n.symbol ?? n.market, timeframe: n.timeframe })).filter((n) => n.node_id !== undefined && n.node_id !== null)}
        onSymbolChange={setMarketSymbol}
        onNodeChange={setSelectedNode}
      />

      <ManualOrderPanel defaultSymbol={marketSymbol} />

      <RiskStrip nodes={enrolled} onChanged={load}
                 lab={{ equity: stats.current_balance ?? prop.equity }} />

      <LiveNodeTable
        onOpenNode={openNode}
        onToggleStar={toggleShortlist}
        globalRisk={risk?.global_risk_pct}
        onRiskChanged={load}
      />

      <LiveTestingControl />

      <LiveMarketPanel nodes={enrolled} engineRunning={active} />


      <LiveTradeCounter />

      <StageTimeline nodes={enrolled} />

      {unavailable.length > 0 && (
        <div className="panel">
          <div className="kit-head">
            <b>Statistics that could not be computed</b>
            <Badge tone="mute">{unavailable.length}</Badge>
          </div>
          <div className="muted" style={{ fontSize: 11.5 }}>
            These are reported as unavailable rather than filled in with a placeholder value:
          </div>
          <ul style={{ margin: "6px 0 0 16px", fontSize: 11.5 }}>
            {unavailable.map((u) => (
              <li key={typeof u === "string" ? u : JSON.stringify(u)}>
                {typeof u === "string" ? u : `${txt(u.metric || u.name)} — ${txt(u.reason)}`}
              </li>
            ))}
          </ul>
        </div>
      )}

      {arr(results?.trades).length > 0 && (
        <div className="panel">
          <div className="kit-head">
            <b>Recent live trades</b>
            <Badge tone="mute">{arr(results.trades).length} shown · read-only</Badge>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="table compact">
              <thead>
                <tr><th>Node</th><th>Symbol</th><th>Side</th><th>Lots</th><th>Entry</th>
                  <th>Exit</th><th>P/L</th><th>Opened</th><th>Closed</th><th>Reason</th><th>Status</th></tr>
              </thead>
              <tbody>
                {arr(results.trades).slice(0, 40).map((t, i) => (
                  <tr key={i}>
                    <td className="mono">{txt(t.strategy_id, NA_TEXT)}</td>
                    <td className="mono">{txt(t.symbol, NA_TEXT)}</td>
                    <td className="mono">{txt(t.side, NA_TEXT)}</td>
                    <td className="mono">{txt(t.lots, NA_TEXT)}</td>
                    <td className="mono">{txt(t.entry_price ?? t.exec_price, NA_TEXT)}</td>
                    <td className="mono">{txt(t.exit_price, NA_TEXT)}</td>
                    <td className="mono">{txt(t.pnl, NA_TEXT)}</td>
                    <td className="mono">{txt(t.open_ts, NA_TEXT)}</td>
                    <td className="mono">{txt(t.close_ts, NA_TEXT)}</td>
                    <td className="mono">{txt(t.close_reason, NA_TEXT)}</td>
                    <td className="mono">{txt(t.status, NA_TEXT)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="muted" style={{ fontSize: 11.5 }}>
            {txt(results?.trade_sample_note, "read-only view of the recorded live-test trades")}
          </div>
        </div>
      )}

      <StateBlock title="What this page can and cannot do" tone="info">
        <ul style={{ marginLeft: 16 }}>
          <li>Placing an order requires a connected, positively identified <b>DEMO</b> terminal; otherwise the
            backend refuses and the panel shows its refusal code.</li>
          <li>START enrols a node in the live engine (after the confirmation dialog). STOP removes it from the
            active set — it never closes a position, because closing is a separate operator decision.</li>
          <li>The schedule popup saves the rules the engine evaluates before <i>every</i> order: days, sessions,
            time window, timezone, cooldown, daily trade cap, spread limit and position cap.</li>
          <li>Every statistic on this page is computed from the recorded live trades. A value that cannot be
            computed is reported as unavailable, never as zero.</li>
        </ul>
      </StateBlock>
    </div>
  );
}
