import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { useLab } from "../App.jsx";
import StructuredError from "../components/StructuredError.jsx";
import { arr, NA_TEXT, objOrNull, rows as safeRows, txt } from "../lib/safe.js";
import { Badge, Card, Kpi, SectionTitle, StateBlock, useInterval } from "../components/ui.jsx";

/**
 * V5 §18 — Live Testing Results.
 *
 * Every number on this page is computed by the backend from the recorded live
 * trades (`/api/live-test/results`). A statistic that cannot be computed is
 * `null` and is shown as "n/a" with the backend's explanation — the page never
 * substitutes 0, 1.0 or "PASSING" for a value the lab did not measure. The
 * prop-firm block is the backend's own verdict with its real limits.
 */
export default function LiveTestResults() {
  const { shortlist, toggleShortlist, setSelectedStrategyId, navigateTab } = useLab() || {};
  const [data, setData] = useState(null);
  const [perNode, setPerNode] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState("node_id");
  const [sortDesc, setSortDesc] = useState(true);
  const [starredOnly, setStarredOnly] = useState(false);
  const [statusFilter, setStatusFilter] = useState("");
  const [selected, setSelected] = useState(null);
  const [starBusy, setStarBusy] = useState(false);
  const [starMsg, setStarMsg] = useState(null);

  const starNode = useCallback(async (nodeId) => {
    if (nodeId === undefined || nodeId === null) return;
    setStarBusy(true); setStarMsg(null);
    try {
      if (toggleShortlist) {
        await toggleShortlist(nodeId);
      } else {
        const res = await api.toggleShortlist(nodeId);
        setStarMsg(res?.starred ? "added to the shortlist" : "removed from the shortlist");
      }
      setSelected((cur) => (cur && cur.node_id === nodeId ? { ...cur, starred: !cur.starred } : cur));
    } catch (e) {
      setStarMsg(e?.detail || e?.message || String(e));
    } finally {
      setStarBusy(false);
    }
  }, [toggleShortlist]);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [res, table] = await Promise.all([
        api.liveTestResults({}),
        api.liveTestingNodesTable({ limit: 200, sort_by: sortBy, sort_desc: sortDesc }),
      ]);
      setData(res);
      setPerNode(table);
    } catch (e) {
      setError(e);
    } finally { setLoading(false); }
  }, [sortBy, sortDesc]);

  useEffect(() => { load(); }, [load]);
  useInterval(load, 20000);

  const s = objOrNull(data) || {};
  const prop = objOrNull(s.prop_firm) || {};
  const unavailable = arr(s.unavailable);
  const exitReasons = objOrNull(s.exit_reasons) || {};

  const na = (v, suffix = "") => (v === null || v === undefined
    ? <span className="muted">{NA_TEXT}</span>
    : <span className="mono">{`${v}${suffix}`}</span>);

  const nodeRows = safeRows(perNode?.nodes).filter((r) => {
    if (starredOnly && !r.starred) return false;
    if (statusFilter && String(r.v5_status || r.status || "").toUpperCase() !== statusFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      const hay = `node_${r.node_id} ${r.market || ""} ${r.timeframe || ""} ${r.v5_status || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  return (
    <div className="page">
      <SectionTitle
        right={<Badge tone={s.read_only ? "ok" : "mute"}>
          {loading ? "loading…" : "read-only · computed from recorded live trades"}
        </Badge>}
      >
        Live Testing Results
      </SectionTitle>

      {error && <StructuredError error={error} onDismiss={() => setError(null)} />}

      <div className="kit-strip">
        <Kpi label="Starting balance" value={txt(s.starting_balance, NA_TEXT)} />
        <Kpi label="Current balance" value={txt(s.current_balance, NA_TEXT)} />
        <Kpi label="Net profit" value={txt(s.net_profit, NA_TEXT)}
             tone={Number(s.net_profit) > 0 ? "pos" : Number(s.net_profit) < 0 ? "neg" : undefined} />
        <Kpi label="Return" value={s.return_pct === null || s.return_pct === undefined ? NA_TEXT : `${s.return_pct} %`} />
        <Kpi label="Profit factor" value={txt(s.profit_factor, NA_TEXT)} />
        <Kpi label="Win rate" value={s.win_rate === null || s.win_rate === undefined ? NA_TEXT : `${s.win_rate} %`} />
        <Kpi label="Closed trades" value={txt(s.closed_trades, "0")} sub={`${txt(s.open_trades, "0")} open`} />
        <Kpi label="Max drawdown" value={s.max_drawdown_pct === null || s.max_drawdown_pct === undefined ? NA_TEXT : `${s.max_drawdown_pct} %`} />
        <Kpi label="Sharpe" value={txt(s.sharpe, NA_TEXT)} />
        <Kpi label="Sortino" value={txt(s.sortino, NA_TEXT)} />
        <Kpi label="Expectancy / trade" value={txt(s.expectancy, NA_TEXT)} />
        <Kpi label="Exposure" value={s.exposure_pct === null || s.exposure_pct === undefined ? NA_TEXT : `${s.exposure_pct} %`} />
      </div>

      <Card title="Prop-firm monitor"
            right={<Badge tone={prop.verdict === "BREACHED" ? "danger"
              : prop.verdict === "TARGET_REACHED" ? "ok"
                : prop.verdict === "IN_PROGRESS" ? "info" : "mute"}>
              {txt(prop.verdict, "NO_DATA")}
            </Badge>}>
        <div className="kit-strip" style={{ border: "none", padding: 0 }}>
          <div className="item"><span className="k">Account size</span><span className="v mono">{txt(prop.account_size, NA_TEXT)}</span></div>
          <div className="item"><span className="k">Equity</span><span className="v mono">{txt(prop.equity, NA_TEXT)}</span></div>
          <div className="item"><span className="k">Daily loss limit</span>
            <span className="v mono">{txt(prop.daily_loss_limit, NA_TEXT)} (used {txt(prop.daily_loss_used, NA_TEXT)})</span></div>
          <div className="item"><span className="k">Max loss limit</span>
            <span className="v mono">{txt(prop.max_loss_limit, NA_TEXT)} (remaining {txt(prop.max_loss_remaining, NA_TEXT)})</span></div>
          <div className="item"><span className="k">Profit target</span>
            <span className="v mono">{txt(prop.profit_target, NA_TEXT)} (distance {txt(prop.profit_target_distance, NA_TEXT)})</span></div>
          <div className="item"><span className="k">Trading days</span>
            <span className="v mono">{txt(prop.trading_days, "0")} of {txt(prop.min_trading_days, NA_TEXT)} required</span></div>
          <div className="item"><span className="k">Rule violations</span>
            <span className="v">{arr(prop.rule_violations).length === 0
              ? <Badge tone="ok">none</Badge>
              : <>{arr(prop.rule_violations).map((v, i) => <Badge key={i} tone="danger">{txt(v)}</Badge>)}</>}</span></div>
        </div>
        {prop.verdict === "NO_DATA" && (
          <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
            No closed live trade exists yet, so no compliance statement can be made. The monitor reports
            NO_DATA instead of a passing verdict.
          </div>
        )}
      </Card>

      <Card title="Statistics that could not be computed"
            right={<Badge tone={unavailable.length ? "warn" : "ok"}>{unavailable.length}</Badge>}>
        {unavailable.length === 0
          ? <div className="muted" style={{ fontSize: 12 }}>Every statistic above is computable from the recorded trades.</div>
          : <ul style={{ marginLeft: 16, fontSize: 12 }}>
              {unavailable.map((u, i) => (
                <li key={i}>{typeof u === "string" ? u : `${txt(u.metric || u.name)} — ${txt(u.reason)}`}</li>
              ))}
            </ul>}
      </Card>

      <Card title="Per-node results"
            right={<span className="muted" style={{ fontSize: 11.5 }}>
              IS metrics come from the node's research backtest; live P/L from its recorded live trades
            </span>}>
        <div className="btn-row" style={{ marginBottom: 8, flexWrap: "wrap" }}>
          <input className="input" style={{ width: 150 }} placeholder="search node / market / tf"
                 value={search} onChange={(e) => setSearch(e.target.value)} />
          <select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">any status</option>
            {["VALID", "LIVE_ELIGIBLE", "LIVE_TESTING", "LIVE_COMPLETED", "MT5_DEMO"].map((x) => (
              <option key={x} value={x}>{x}</option>))}
          </select>
          <select className="input" value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
            <option value="node_id">sort: node</option>
            <option value="is_return_pct">sort: IS return</option>
            <option value="profit_factor">sort: profit factor</option>
            <option value="today_pnl">sort: today P/L</option>
            <option value="total_live_pnl">sort: total live P/L</option>
            <option value="live_trades">sort: trades</option>
          </select>
          <label className="muted" style={{ fontSize: 12 }}>
            <input type="checkbox" checked={sortDesc} onChange={(e) => setSortDesc(e.target.checked)} /> descending
          </label>
          <label className="muted" style={{ fontSize: 12 }}>
            <input type="checkbox" checked={starredOnly} onChange={(e) => setStarredOnly(e.target.checked)} /> starred only
          </label>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table className="table compact">
            <thead>
              <tr>
                <th>★</th><th>ID</th><th>Status</th><th>Market</th><th>TF</th>
                <th>IS return</th><th>PF</th><th>Today P/L</th><th>Total live P/L</th>
                <th>Trades (closed/all)</th><th>Live win rate</th><th>Risk</th><th>Schedule</th><th></th>
              </tr>
            </thead>
            <tbody>
              {nodeRows.map((r) => (
                <tr key={r.node_id}>
                  <td>{r.starred ? "⭐" : "☆"}</td>
                  <td className="mono">Node_{r.node_id}</td>
                  <td><Badge tone={r.is_active ? "ok" : "mute"}>{txt(r.v5_status || r.status, NA_TEXT)}</Badge></td>
                  <td className="mono">{txt(r.market, NA_TEXT)}</td>
                  <td className="mono">{txt(r.timeframe, NA_TEXT)}</td>
                  <td>{na(r.is_return_pct, " %")}</td>
                  <td>{na(r.profit_factor)}</td>
                  <td>{na(r.today_pnl)}</td>
                  <td>{na(r.total_live_pnl)}</td>
                  <td className="mono">{txt(r.live_closed, "0")} / {txt(r.live_trades, "0")}</td>
                  <td>{r.live_win_rate === null || r.live_win_rate === undefined ? NA_TEXT : `${r.live_win_rate} %`}</td>
                  <td className="mono">{txt(r.risk_pct, NA_TEXT)} % <span className="muted" style={{ fontSize: 10 }}>{r.risk_source === "CUSTOM" ? "node" : "global"}</span></td>
                  <td style={{ fontSize: 11 }}>{r.schedule?.active ? "active" : "—"}</td>
                  <td>
                    <button className="btn ghost" style={{ padding: "2px 8px" }}
                            onClick={() => setSelected(r)}>details</button>
                  </td>
                </tr>
              ))}
              {nodeRows.length === 0 && (
                <tr><td colSpan={14} className="muted">No node matches the current filter.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {selected && (
        <Card title={`Node_${selected.node_id} — live detail`}
              right={<button className="btn ghost" onClick={() => setSelected(null)}>close</button>}>
          <div className="kit-strip" style={{ border: "none", padding: 0 }}>
            <div className="item"><span className="k">Effective risk</span>
              <span className="v mono">{txt(selected.risk_pct, NA_TEXT)} % ({txt(selected.risk_source)})</span></div>
            <div className="item"><span className="k">Global default</span>
              <span className="v mono">{txt(selected.global_risk_pct, NA_TEXT)} %</span></div>
            <div className="item"><span className="k">Node override</span>
              <span className="v mono">{txt(selected.risk_pct_override, "none")}</span></div>
            <div className="item"><span className="k">Trades in IS backtest</span>
              <span className="v mono">{txt(selected.trades_is, NA_TEXT)}</span></div>
            <div className="item"><span className="k">Direction</span>
              <span className="v mono">{txt(selected.direction, NA_TEXT)}</span></div>
            <div className="item"><span className="k">Schedule</span>
              <span className="v mono">{selected.schedule?.start_time
                ? `${selected.schedule.start_time}–${selected.schedule.end_time} ${selected.schedule.timezone}`
                : "no window set"}</span></div>
          </div>
          <div className="btn-row" style={{ marginTop: 8 }}>
            <button className="btn" onClick={() => { if (setSelectedStrategyId) setSelectedStrategyId(selected.node_id); if (navigateTab) navigateTab("StrategyLab", selected.node_id); }}>
              open node (Trading Info included)
            </button>
            <button className="btn ghost" disabled={starBusy}
                    onClick={() => starNode(selected.node_id)}>
              {selected.starred ? "remove from shortlist" : "add to shortlist"}
            </button>
            {starMsg && <span className="muted" style={{ fontSize: 11.5 }}>{starMsg}</span>}
          </div>
        </Card>
      )}

      <Card title="Exit reasons" right={<Badge tone="mute">{Object.keys(exitReasons).length}</Badge>}>
        {Object.keys(exitReasons).length === 0
          ? <div className="muted" style={{ fontSize: 12 }}>No closed trade has recorded an exit reason yet.</div>
          : <table className="table compact">
              <thead><tr><th>Reason</th><th>Count</th><th>Net P/L</th></tr></thead>
              <tbody>
                {Object.entries(exitReasons).map(([reason, v]) => (
                  <tr key={reason}>
                    <td className="mono">{reason}</td>
                    <td className="mono">{txt(v?.count ?? v, NA_TEXT)}</td>
                    <td className="mono">{txt(v?.net ?? v?.pnl, NA_TEXT)}</td>
                  </tr>
                ))}
              </tbody>
            </table>}
      </Card>

      <StateBlock title="How to read this page" tone="info">
        <ul style={{ marginLeft: 16 }}>
          <li>Starting balance, net profit, profit factor, Sharpe/Sortino and drawdown are computed by the
            backend from the recorded live trades — the same rows shown in Live Testing.</li>
          <li>Sharpe and Sortino need at least 10 closed trades; profit factor needs at least one losing trade.
            Until then they are reported as unavailable, not as 1.0 or 0.</li>
          <li>The prop-firm verdict is the backend's: NO_DATA / IN_PROGRESS / TARGET_REACHED / BREACHED, with
            the real limits and used amounts.</li>
        </ul>
      </StateBlock>
    </div>
  );
}
