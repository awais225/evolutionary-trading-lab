/* V4.8 — Live trade counter (§7).
 *
 * Open positions, live P/L, totals, winners/losers and the closed-trade reason
 * breakdown, all read from the running engine's own records
 * (/api/live-testing/counter, /api/live-test/status, /api/live-testing/trades).
 * When the broker cannot be counted (simulator bridge) the reason is shown
 * verbatim instead of a fabricated zero.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api, fmt } from "../api.js";
import { arr, NA_TEXT, numOrNull, objOrNull, txt } from "../lib/safe.js";
import { Badge, Card, Kpi, StateBlock, useInterval } from "./ui.jsx";

export default function LiveTradeCounter() {
  const [counter, setCounter] = useState(null);
  const [status, setStatus] = useState(null);
  const [trades, setTrades] = useState([]);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [c, s, t] = await Promise.all([
        api.liveTestingCounter().catch((e) => { throw e; }),
        api.liveTestStatus().catch(() => null),
        api.liveTestingTrades({ limit: 200 }).catch(() => ({ trades: [] })),
      ]);
      setCounter(c); setStatus(s); setTrades(arr(t?.trades)); setErr(null);
    } catch (e) { setErr(e.message || String(e)); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useInterval(load, 5000, true);

  const activity = objOrNull(counter?.activity) || {};
  const openPositions = numOrNull(activity.positions) ?? numOrNull(status?.open_positions);
  const livePnl = numOrNull(status?.today_pnl) ?? numOrNull(status?.total_pnl);
  const closed = useMemo(() => trades.filter((t) => String(t?.status || "").toUpperCase() !== "OPEN"), [trades]);
  const winners = closed.filter((t) => (numOrNull(t?.pnl) ?? 0) > 0);
  const losers = closed.filter((t) => (numOrNull(t?.pnl) ?? 0) < 0);
  const reasons = useMemo(() => {
    const m = {};
    closed.forEach((t) => {
      const r = String(t?.close_reason || t?.exit_reason || t?.reason || "not recorded");
      m[r] = (m[r] || 0) + 1;
    });
    return Object.entries(m).sort((a, b) => b[1] - a[1]);
  }, [closed]);

  return (
    <Card title="Live trade counter"
          right={<span className="muted" style={{ fontSize: 11 }}>
            source: {txt(activity.source, "unknown")}
          </span>}>
      <StateBlock loading={loading} error={err} onRetry={load}>
        <div className="grid cols-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px,1fr))" }}>
          <Kpi label="Open positions" value={openPositions === null ? NA_TEXT : fmt.num(openPositions, 0)}
               sub={`orders: ${txt(activity.orders, NA_TEXT)}`} />
          <Kpi label="Live P/L" value={livePnl === null ? NA_TEXT : fmt.pnl(livePnl)}
               tone={(livePnl ?? 0) >= 0 ? "pos" : "neg"} />
          <Kpi label="Total positions" value={fmt.num(trades.length, 0)} sub={`closed: ${closed.length}`} />
          <Kpi label="Winning positions" value={fmt.num(winners.length, 0)} tone="pos"
               sub={`losing: ${losers.length}`} />
        </div>
        {activity.counted === false && (
          <div className="kit-inline-err" style={{ marginTop: 8 }}>
            Broker positions could not be counted: {txt(activity.error, "no live MT5 connection")}.
            Live-test records are still counted below.
          </div>
        )}
        <div className="kit-cols" style={{ marginTop: 10 }}>
          <div style={{ flex: "1 1 260px" }}>
            <div className="kit-kv"><span className="k">Total P/L (records)</span>
              <span className={closed.reduce((a, t) => a + (numOrNull(t?.pnl) ?? 0), 0) >= 0 ? "pos" : "neg"}>
                {closed.length === 0 ? NA_TEXT : fmt.pnl(closed.reduce((a, t) => a + (numOrNull(t?.pnl) ?? 0), 0))}
              </span></div>
            <div className="kit-kv"><span className="k">Active trade limit</span>
              <span className="mono">{txt(counter?.limit?.limit, NA_TEXT)} ({counter?.limit?.reached ? "reached" : "not reached"})</span></div>
            <div className="kit-kv"><span className="k">Engine cycles</span><span className="mono">{txt(status?.cycle_count, NA_TEXT)}</span></div>
            <div className="kit-kv"><span className="k">Last cycle</span><span className="mono">{txt(status?.last_cycle_iso, "never")}</span></div>
            <div className="kit-kv"><span className="k">Last no-op reason</span><span className="muted">{txt(status?.last_noop_reason, "none")}</span></div>
          </div>
          <div style={{ flex: "1 1 260px" }}>
            <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 4 }}>
              Closed-trade reason breakdown
            </div>
            {reasons.length === 0
              ? <span className="muted">No closed trades recorded yet.</span>
              : reasons.map(([r, n]) => (
                <div className="kit-kv" key={r}><span className="k">{r}</span><span className="mono">{n}</span></div>
              ))}
          </div>
        </div>
      </StateBlock>
    </Card>
  );
}
