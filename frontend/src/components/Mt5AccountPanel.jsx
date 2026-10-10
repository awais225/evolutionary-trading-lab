/* V6.5.1 §7 — MT5 account information panel.
 *
 * Balance / equity / free margin of the ACTUAL connected MT5 account, read
 * through the established bridge (`GET /api/mt5/account`) and refreshed through
 * the page's normal polling. Truthfulness rules:
 *
 *   * figures come from the connected terminal only — never hardcoded, never a
 *     simulator's numbers presented as live account data (a simulator is
 *     labelled SIMULATED);
 *   * a disconnected / unavailable state is explicit, with the reason, and the
 *     numeric fields show N/A — never 0 and never a stale value without its
 *     age;
 *   * each snapshot carries the backend's own ``checked_at`` timestamp so the
 *     operator can see how fresh the figures are;
 *   * polling is modest (10 s) and the panel is read-only: refreshing it never
 *     places an order and never touches strategy execution.
 */
import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { NA_TEXT, numOrNull, objOrNull, txt } from "../lib/safe.js";
import { Badge, Card, Kpi, useInterval } from "./ui.jsx";

function fmtAge(checkedAt) {
  const t = numOrNull(checkedAt);
  if (t === null) return NA_TEXT;
  const age = Math.max(0, Date.now() / 1000 - t);
  if (age < 90) return `as of ${Math.round(age)} s ago`;
  return `as of ${Math.round(age / 60)} min ago`;
}

export default function Mt5AccountPanel({ refreshKey = 0 }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setData(await api.mt5Account());
      setErr(null);
    } catch (e) {
      setErr(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load, refreshKey]);
  useInterval(load, 10000);

  const acct = objOrNull(data?.account) || {};
  const connected = data?.connected === true;
  const simulated = data?.is_simulated === true;
  const available = data?.available === true;
  const kind = txt(data?.data_kind, "UNAVAILABLE");
  const money = (v) => {
    const n = numOrNull(v);
    if (n === null) return NA_TEXT;
    const cur = txt(acct.currency, "");
    return `${n.toFixed(2)}${cur && cur !== NA_TEXT ? ` ${cur}` : ""}`;
  };

  const connTone = connected ? (simulated ? "warn" : "ok") : "danger";
  const connLabel = connected ? (simulated ? "SIMULATOR" : "CONNECTED") : "NOT CONNECTED";

  return (
    <Card
      title="MT5 account"
      right={
        <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
          <Badge tone={connTone} title={`bridge ${txt(data?.source, NA_TEXT)} — ${connected ? "connected" : "not connected"}`}>
            {connLabel}
          </Badge>
          <Badge tone={available ? (simulated ? "warn" : "ok") : "mute"}>data: {kind}</Badge>
        </span>
      }
    >
      {err && (
        <div className="kit-inline-err">
          account endpoint unavailable — {err} · the values below are not live
        </div>
      )}
      {!err && !loading && !available && (
        <div className="kit-banner sim" role="status">
          <b>ACCOUNT DATA UNAVAILABLE</b>
          <div className="muted" style={{ marginTop: 3, fontSize: 12 }}>
            {txt(data?.unavailable_reason, "no account data available from the bridge")}
            {" — "}balance / equity / free margin are reported as {NA_TEXT}, never as 0 or
            as a simulated figure. Connect (or log in) the MT5 terminal and this panel
            will show the real values.
          </div>
        </div>
      )}
      <div className="kit-strip">
        <Kpi label="Balance" value={money(acct.balance)}
             sub={`${txt(acct.currency, "")} · ${fmtAge(data?.checked_at)}`} />
        <Kpi label="Equity" value={money(acct.equity)}
             sub={numOrNull(acct.balance) !== null && numOrNull(acct.equity) !== null
               ? `vs balance ${(numOrNull(acct.equity) - numOrNull(acct.balance)).toFixed(2)}`
               : txt(acct.currency, "")} />
        <Kpi label="Free margin" value={money(acct.margin_free)}
             sub={acct.leverage !== null && acct.leverage !== undefined
               ? `leverage 1:${txt(acct.leverage)}` : "leverage unavailable"} />
      </div>
      <div className="muted" style={{ marginTop: 6, fontSize: 11 }}>
        Account <b className="mono">{txt(acct.login, NA_TEXT)}</b>
        {" · server "}<b className="mono">{txt(acct.server, NA_TEXT)}</b>
        {" · type "}<b>{txt(acct.trade_mode_name, NA_TEXT)}</b>
        {" · source "}<b className="mono">{txt(data?.source, NA_TEXT)}</b>
        {data?.is_simulated ? " (SIMULATED figures — not a live account)" : ""}
        {" · "}read-only snapshot ({fmtAge(data?.checked_at)}) — refreshing never places an order.
      </div>
    </Card>
  );
}
