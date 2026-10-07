/* V5.1a-next §B — MT5 account / terminal selector.
 *
 * One control, shared by MT5 Demo Trading and Live Testing, that answers "which
 * terminal and which account is the backend actually using?" and lets the
 * operator switch deliberately.
 *
 * Honesty rules (same as the Windows diagnostic):
 *   - the list comes from the backend (`/api/mt5/accounts`); nothing is invented
 *     when the MetaTrader5 package is not importable — the reason is shown;
 *   - a probe is an explicit action (it momentarily starts each terminal, which
 *     is not free) and it never sends an order;
 *   - a non-DEMO account is displayed as REAL/CONTEST and flagged as refused —
 *     selecting it does not make it tradable;
 *   - the active account is marked from the backend's own view, not guessed.
 */
import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { NA_TEXT, objOrNull, rows as safeRows, txt } from "../lib/safe.js";
import { Badge, Card, StateBlock } from "./ui.jsx";

function kindTone(kind) {
  const k = String(kind || "").toUpperCase();
  if (k === "DEMO") return "pos";
  if (k === "REAL" || k === "CONTEST") return "warn";
  return "info";
}

function AccountRow({ a }) {
  const kind = String(a.account_kind || a.trade_mode_name || "UNKNOWN").toUpperCase();
  const label = a.login
    ? `${txt(a.login, NA_TEXT)} · ${txt(a.server, NA_TEXT)}`
    : txt(a.name || a.path, NA_TEXT);
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <Badge tone={kindTone(kind)}>{kind}</Badge>
      <span style={{ fontVariantNumeric: "tabular-nums" }}>{label}</span>
      {a.active && <Badge tone="info">ACTIVE</Badge>}
    </div>
  );
}

export default function Mt5AccountSelector({ title = "MT5 terminal & account" }) {
  const [state, setState] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);
  const [probing, setProbing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const load = useCallback(async (probe) => {
    try {
      setState(await api.mt5Accounts(!!probe));
      setErr(null);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(false); }, [load]);

  const probe = async () => {
    setProbing(true);
    setMsg(null);
    try { await load(true); } finally { setProbing(false); }
  };

  const select = async (path) => {
    if (!path) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.mt5AccountSelect({ path });
      const st = objOrNull(res.status) || {};
      setMsg({
        ok: res.ok === true,
        text: res.ok === true
          ? `Switched to ${txt(st.terminal || st.path, "terminal")} · ${txt(st.account_kind, "UNKNOWN")}`
            + (st.login ? ` · account ${st.login}` : "")
          : `Refused: ${txt(st.reason || st.last_error, "unknown reason")}`,
      });
      await load(true);
    } catch (e) {
      setMsg({ ok: false, text: e.message || String(e) });
    } finally {
      setBusy(false);
    }
  };

  const accounts = safeRows(state?.accounts);
  const terminals = safeRows(state?.terminals);
  const active = objOrNull(state?.active) || {};
  const selectable = accounts.length ? accounts : terminals;
  const packageAvailable = state?.package_available !== false;
  const realSelected = accounts.some((a) => a.active && String(a.account_kind).toUpperCase() !== "DEMO");

  return (
    <Card title={title} right={<Badge tone="info">READ-ONLY UNTIL YOU SELECT</Badge>}>
      <StateBlock loading={loading} error={err} onRetry={() => load(true)}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <label className="muted" style={{ fontSize: 12 }} htmlFor="mt5-account-select">
            Terminal / account
          </label>
          <select id="mt5-account-select" className="input" style={{ minWidth: 300 }}
                  value={active.terminal_path && selectable.some((x) => (x.path || "") === active.terminal_path)
                    ? active.terminal_path : ""}
                  disabled={busy || !selectable.length}
                  onChange={(e) => select(e.target.value)}>
            <option value="">
              {selectable.length ? "— choose a terminal —" : "no terminal available"}
            </option>
            {selectable.map((x) => (
              <option key={txt(x.path, String(x.login || x.name))} value={x.path || ""}>
                {x.login ? `${txt(x.login, NA_TEXT)} · ${txt(x.server, NA_TEXT)}`
                         : `${txt(x.name || x.path, NA_TEXT)}`}
                {x.account_kind ? ` — ${x.account_kind}` : ""}
              </option>
            ))}
          </select>
          <button className="btn" onClick={probe} disabled={probing || !packageAvailable}>
            {probing ? "Detecting…" : "Detect accounts"}
          </button>
        </div>

        <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
          Detecting starts each installed terminal for a moment to read its account, then closes it —
          it never sends an order. {state?.probed === false ? "Accounts are not probed yet." : ""}
        </div>

        {active.active_bridge ? (
          <div style={{ marginTop: 8 }}>
            <AccountRow a={{ login: active.login, server: active.server,
                             account_kind: active.account_kind, name: active.terminal_path,
                             active: true }} />
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
              bridge {txt(active.active_bridge, NA_TEXT)}
              {active.is_simulated ? " — SIMULATOR (no real MT5)" : ""}
              {active.reason ? ` · ${active.reason}` : ""}
            </div>
          </div>
        ) : (
          <div className="kit-banner sim" style={{ marginTop: 8, fontSize: 12 }}>
            {state?.reason || "No MT5 account is active — the backend is not connected to a terminal."}
          </div>
        )}

        {realSelected && (
          <div className="kit-banner sim"
               style={{ background: "#2a1013", borderColor: "#991b1b", color: "#fecaca", marginTop: 8 }}>
            <b>REAL ACCOUNT DETECTED — ORDERS ARE REFUSED.</b>
            <div className="muted" style={{ marginTop: 3, fontSize: 12 }}>
              The selected account is not a DEMO account. The demo guard refuses every order from it;
              selecting another account here changes nothing about that rule.
            </div>
          </div>
        )}

        {msg && (
          <div className={msg.ok ? "kit-banner real" : "kit-banner unavail"}
               style={{ marginTop: 8, fontSize: 12 }}>
            {msg.ok ? "✓ " : "× "}{msg.text}
          </div>
        )}

        {!packageAvailable && (
          <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
            MetaTrader5 is not importable in this interpreter, so no account can be probed here.
            Run CHECK_MT5_WINDOWS.bat on the trading machine for the exact failure layer.
          </div>
        )}
      </StateBlock>
    </Card>
  );
}
