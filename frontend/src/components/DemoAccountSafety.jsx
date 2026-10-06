/* V4.8 — MT5 Demo account & safety block (§10).
 *
 * DEMO ACCOUNT ONLY must be impossible to miss, and the trading decision has to
 * be made on facts, so this block shows, before any order can be attempted:
 *   account type · account identifier · broker/server · connection state ·
 *   whether real-money execution is permitted · the exact blocking reason.
 *
 * A real (non-demo) account is rejected here and in the backend guard; this
 * panel never falls back to a made-up account number or balance.
 */
import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { NA_TEXT, numOrNull, objOrNull, txt } from "../lib/safe.js";
import { Badge, Card, Kpi, StateBlock, useInterval } from "./ui.jsx";

export default function DemoAccountSafety() {
  const [state, setState] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setState(await api.mt5ExecutionState());
      setErr(null);
    } catch (e) { setErr(e.message || String(e)); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useInterval(load, 10000, true);

  const safety = objOrNull(state?.account_safety) || {};
  const account = objOrNull(safety.account) || {};
  const bridge = objOrNull(state?.bridge) || {};
  const simulated = safety.is_simulated === true || bridge.is_simulated === true;
  const demoVerified = safety.demo_verified === true;
  const allowed = state?.execution_allowed === true;
  const accountType = String(account.type ?? account.account_type ?? "").toUpperCase();
  const realAccount = accountType.includes("REAL") || account.trade_mode === 0;
  const mode = numOrNull(account.trade_mode);

  return (
    <Card title="Account & safety state"
          right={<Badge tone={realAccount ? "warn" : "info"}>DEMO ACCOUNT ONLY</Badge>}>
      {realAccount && (
        <div className="kit-banner sim" style={{ background: "#2a1013", borderColor: "#991b1b", color: "#fecaca" }}>
          <b>REAL ACCOUNT DETECTED — ORDERS ARE REFUSED.</b>
          <div className="muted" style={{ marginTop: 3, fontSize: 12 }}>
            Account {txt(account.login, NA_TEXT)} reports type {txt(accountType, "REAL")}. The backend safety
            guard only permits a verified DEMO account; no order will be sent from this page.
          </div>
        </div>
      )}
      <StateBlock loading={loading} error={err} onRetry={load}>
        <div className="grid cols-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px,1fr))" }}>
          <Kpi label="Account type" value={accountType || (simulated ? "DEMO (SIMULATOR)" : NA_TEXT)}
               sub={txt(account.trade_mode_comment, "demo account required")} />
          <Kpi label="Account identifier" value={txt(account.login, NA_TEXT)} sub={txt(account.server || state?.symbol?.source, "")} />
          <Kpi label="Connection" value={bridge.connected === true ? "CONNECTED" : (state?.bridge ? "NOT CONNECTED" : NA_TEXT)}
               tone={bridge.connected === true ? "pos" : "neg"} sub={txt(bridge.name || bridge.source, "")} />
          <Kpi label="Safety gating" value={demoVerified ? "CONFIRMED" : (safety.blocked_code ? "BLOCKED" : NA_TEXT)}
               tone={demoVerified ? "pos" : "neg"} sub={txt(safety.blocked_code, "no block code")} />
        </div>

        <div className="kit-cols" style={{ marginTop: 10 }}>
          <div style={{ flex: "1 1 280px" }}>
            <div className="kit-kv"><span className="k">Real-money execution</span>
              <span className={allowed ? "neg" : "pos"}>{allowed ? "PERMITTED (should never be true)" : "BLOCKED — $0 real money"}</span></div>
            <div className="kit-kv"><span className="k">Bridge</span><span className="mono">{txt(bridge.name || bridge.source, NA_TEXT)}</span></div>
            <div className="kit-kv"><span className="k">Data source</span><span className="mono">{txt(bridge.source, NA_TEXT)}</span></div>
            <div className="kit-kv"><span className="k">MT5 package installed</span><span className="mono">{txt(safety.mt5_package_installed, NA_TEXT)}</span></div>
            <div className="kit-kv"><span className="k">Balance / equity / margin</span>
              <span className="mono">{numOrNull(account.balance) === null ? NA_TEXT : numOrNull(account.balance).toFixed(2)}
                {" / "}{numOrNull(account.equity) === null ? NA_TEXT : numOrNull(account.equity).toFixed(2)}
                {" / "}{numOrNull(account.margin_free) === null ? NA_TEXT : numOrNull(account.margin_free).toFixed(2)}</span></div>
            <div className="kit-kv"><span className="k">Kill switch</span><span className="mono">{txt(state?.kill_switch_engaged, NA_TEXT)}</span></div>
          </div>
          <div style={{ flex: "1 1 280px" }}>
            <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 4 }}>
              Why trading is {allowed ? "allowed" : "refused"}
            </div>
            <div className={allowed ? "kit-ok" : "kit-inline-err"}>
              {txt(safety.blocked_reason, allowed ? "Demo account verified; only explicitly confirmed manual demo orders are permitted." : "No blocking reason reported by the backend.")}
            </div>
            {mode !== null && (
              <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
                Broker trade_mode = {mode} ({mode === 0 ? "DEMO" : mode === 1 ? "CONTEST" : mode === 2 ? "REAL" : "unknown"}).
              </div>
            )}
          </div>
        </div>
      </StateBlock>
    </Card>
  );
}
