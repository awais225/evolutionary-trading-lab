import React, { useCallback, useEffect, useState } from "react";
import { api, fmt } from "../api.js";
import { NA_TEXT, arr as safeArr, objOrNull, txt } from "../lib/safe.js";

/* V6.4 §7 — the LIVE positions table with explicit close controls.
 *
 * Every row is the broker's own open position (ticket, symbol, side, volume,
 * open/current price, SL, TP, floating PnL, timestamps, magic). CLOSE is per
 * row; CLOSE ALL requires an explicit scope and a confirmation that lists
 * EXACTLY what will close. A close is only reported as closed when the backend
 * verified it at the broker (closed_verified) — never on submit. Positions with
 * other magic numbers are never touched silently: they need explicit
 * authorization. Demo-only safety: the backend refuses close actions on any
 * account that is not a positively identified DEMO account.
 */
export default function LivePositionsPanel() {
  const [snap, setSnap] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [msg, setMsg] = useState(null);
  const [confirmRow, setConfirmRow] = useState(null);      // single-close confirmation
  const [confirmAll, setConfirmAll] = useState(false);      // close-all confirmation
  const [scope, setScope] = useState({ symbol: "" });       // "" = whole account
  const [ackOther, setAckOther] = useState(false);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const d = await api.mt5Positions();
      setSnap(d);
    } catch (e) {
      setErr(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const closeOne = async (ticket) => {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const res = await api.mt5ClosePosition(ticket, true);
      const r = objOrNull(res?.result) || {};
      setResults([r]);
      setMsg(r.closed_verified
        ? `Position ${ticket} CLOSED and verified at the broker (retcode ${r.retcode}, deal ${r.deal ?? "—"}).`
        : `Position ${ticket}: close NOT confirmed — status ${txt(r.status, "UNKNOWN")} (retcode ${r.retcode ?? "—"}). Check the terminal before retrying.`);
      setSnap(res);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false); setConfirmRow(null);
    }
  };

  const closeAll = async () => {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const scopePayload = scope.symbol ? { symbol: scope.symbol } : { all_on_account: true };
      const res = await api.mt5CloseAllPositions(scopePayload, true, ackOther);
      setResults(res?.results || []);
      setMsg(`CLOSE ALL (${scope.symbol || "whole account"}): ${res?.closed_verified_count ?? 0}/${res?.attempted ?? 0} closed and verified at the broker.`);
      setSnap(res);
    } catch (e) {
      const detail = e?.detail || e?.data || {};
      if (detail?.code === "OTHER_MAGIC_AUTHORIZATION_REQUIRED") {
        setErr({ message: `${detail.message} (${safeArr(detail.other_magic_positions).length} position(s) with other magic numbers in scope)` });
        setAckOther(true);
      } else {
        setErr(e);
      }
    } finally {
      setBusy(false); setConfirmAll(false);
    }
  };

  const positions = safeArr(snap?.positions);
  const safety = objOrNull(snap?.account_safety) || {};
  const account = objOrNull(snap?.account) || {};
  const inScope = (p) => !scope.symbol || String(p.symbol || "").toUpperCase() === String(scope.symbol).toUpperCase();

  return (
    <div className="panel" style={{ marginTop: 12 }}>
      <div className="kit-head">
        <div style={{ fontSize: 13, fontWeight: 650 }}>
          Live positions (from the connected MT5 terminal)
          <span className="muted" style={{ fontWeight: 400, marginLeft: 8, fontSize: 11.5 }}>
            {txt(account.login, "no account")} · {txt(account.type || safety.bridge_source, "?")} ·{" "}
            {safety.demo_verified ? "DEMO verified" : "demo NOT verified — close controls disabled"}
          </span>
        </div>
        <div className="kit-cols">
          <button className="btn ghost" onClick={load} disabled={loading}>
            {loading ? "loading…" : "Refresh"}
          </button>
          <button className="btn danger" disabled={busy || !safety.demo_verified}
                  title="close positions for an explicitly selected scope (confirmation shows exactly what will close)"
                  onClick={() => setConfirmAll(true)}>
            CLOSE ALL…
          </button>
        </div>
      </div>

      {err && <div className="kit-inline-err">{err.message || String(err)}</div>}
      {msg && <div className="kit-ok" style={{ marginBottom: 6 }}>{msg}</div>}

      <div className="kit-cols" style={{ marginBottom: 8 }}>
        <label className="muted" style={{ fontSize: 11.5 }}>
          Close-all scope:&nbsp;
          <select className="input" value={scope.symbol} disabled={busy}
                  onChange={(e) => setScope({ symbol: e.target.value })}>
            <option value="">whole connected account (all symbols)</option>
            {[...new Set(positions.map((p) => p.symbol).filter(Boolean))].map((s) => (
              <option key={s} value={s}>{s} only</option>
            ))}
          </select>
        </label>
        <span className="muted" style={{ fontSize: 11.5 }}>
          other-magic positions require explicit authorization — they are never closed silently
        </span>
      </div>

      <div className="table-scroll">
        <table className="table compact">
          <thead>
            <tr>
              <th>ticket</th><th>symbol</th><th>side</th><th>volume</th>
              <th>open price</th><th>current</th><th>SL</th><th>TP</th>
              <th>floating PnL</th><th>opened (UTC)</th><th>magic</th><th>action</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 && (
              <tr><td colSpan={12} className="muted">
                {snap ? "no open positions on the connected account" : NA_TEXT}
              </td></tr>
            )}
            {positions.map((p) => (
              <tr key={p.ticket} style={inScope(p) ? undefined : { opacity: 0.55 }}>
                <td className="mono">{txt(p.ticket, NA_TEXT)}</td>
                <td className="mono">{txt(p.symbol, NA_TEXT)}</td>
                <td className="mono">{txt(p.side, NA_TEXT)}</td>
                <td className="mono">{fmt.num(p.volume, 2)}</td>
                <td className="mono">{fmt.num(p.open_price, 2)}</td>
                <td className="mono">{fmt.num(p.current_price, 2)}</td>
                <td className="mono">{p.sl ? fmt.num(p.sl, 2) : "—"}</td>
                <td className="mono">{p.tp ? fmt.num(p.tp, 2) : "—"}</td>
                <td className="mono" style={{ color: (p.floating_pnl || 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                  {fmt.pnl(p.floating_pnl)}
                </td>
                <td className="mono" style={{ fontSize: 10.5 }}>{txt(p.open_time_iso, "—")}</td>
                <td className="mono" style={{ fontSize: 10.5 }}>{txt(p.magic, "—")}</td>
                <td>
                  <button className="btn danger" disabled={busy || !safety.demo_verified}
                          title="close this position by its broker ticket (verified at the broker)"
                          onClick={() => setConfirmRow(p)}>
                    CLOSE
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {results && results.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 11.5 }}>
          <b>close results (verified per ticket):</b>
          <ul style={{ margin: "4px 0 0 16px" }}>
            {results.map((r) => (
              <li key={r.ticket}>
                ticket {r.ticket}: <b>{txt(r.status, "UNKNOWN")}</b> · retcode {txt(r.retcode, "—")} ·
                broker {r.broker_done ? "DONE" : "not DONE"} · position {r.position_gone ? "gone" : "still open"}
                {r.error ? ` · ${r.error}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}

      {confirmRow && (
        <div className="drawer-backdrop" onClick={() => !busy && setConfirmRow(null)} />
      )}
      {confirmRow && (
        <div className="panel" style={{ position: "fixed", zIndex: 60, top: "20%", left: "50%", transform: "translateX(-50%)", width: 420, background: "#111", border: "1px solid #b45309" }}>
          <h3 style={{ marginTop: 0 }}>Confirm CLOSE at the broker</h3>
          <p style={{ fontSize: 12.5 }}>
            This closes position <b className="mono">{confirmRow.ticket}</b> ({confirmRow.side}{" "}
            {fmt.num(confirmRow.volume, 2)} {confirmRow.symbol}) with the broker's own opposite
            deal. <b>Stopping live testing never closes positions — this action does.</b>
          </p>
          <div className="kit-kv" style={{ fontSize: 11.5 }}>
            <span className="k">Open / current</span>
            <span className="mono">{fmt.num(confirmRow.open_price, 2)} / {fmt.num(confirmRow.current_price, 2)}</span>
          </div>
          <div className="kit-kv" style={{ fontSize: 11.5 }}>
            <span className="k">SL / TP</span>
            <span className="mono">{confirmRow.sl ? fmt.num(confirmRow.sl, 2) : "—"} / {confirmRow.tp ? fmt.num(confirmRow.tp, 2) : "—"}</span>
          </div>
          <div className="kit-kv" style={{ fontSize: 11.5 }}>
            <span className="k">Floating PnL / magic</span>
            <span className="mono">{fmt.pnl(confirmRow.floating_pnl)} / {txt(confirmRow.magic, "—")}</span>
          </div>
          <div className="kit-cols" style={{ marginTop: 10 }}>
            <button className="btn danger" disabled={busy} onClick={() => closeOne(confirmRow.ticket)}>
              {busy ? "closing…" : "CLOSE this position"}
            </button>
            <button className="btn ghost" disabled={busy} onClick={() => setConfirmRow(null)}>cancel</button>
          </div>
        </div>
      )}

      {confirmAll && (
        <div className="drawer-backdrop" onClick={() => !busy && setConfirmAll(false)} />
      )}
      {confirmAll && (
        <div className="panel" style={{ position: "fixed", zIndex: 60, top: "16%", left: "50%", transform: "translateX(-50%)", width: 480, background: "#111", border: "1px solid #b45309" }}>
          <h3 style={{ marginTop: 0 }}>Confirm CLOSE ALL ({scope.symbol || "whole account"})</h3>
          <p style={{ fontSize: 12.5 }}>
            This closes <b>exactly</b> the following {positions.filter(inScope).length} position(s) at
            the broker (each verified afterwards):
          </p>
          <ul style={{ fontSize: 11.5, margin: "4px 0 10px 16px", maxHeight: 140, overflow: "auto" }}>
            {positions.filter(inScope).map((p) => (
              <li key={p.ticket} className="mono">
                {p.ticket} · {p.side} {fmt.num(p.volume, 2)} {p.symbol} · magic {txt(p.magic, "—")} · PnL {fmt.pnl(p.floating_pnl)}
              </li>
            ))}
            {positions.filter(inScope).length === 0 && <li className="muted">nothing matches the scope</li>}
          </ul>
          <label className="muted" style={{ fontSize: 11.5, display: "block" }}>
            <input type="checkbox" checked={ackOther} disabled={busy}
                   onChange={(e) => setAckOther(e.target.checked)} />{" "}
            I authorize closing positions that carry OTHER magic numbers (never done silently)
          </label>
          <div className="kit-cols" style={{ marginTop: 10 }}>
            <button className="btn danger" disabled={busy || positions.filter(inScope).length === 0}
                    onClick={closeAll}>
              {busy ? "closing…" : `CLOSE ALL ${positions.filter(inScope).length} position(s)`}
            </button>
            <button className="btn ghost" disabled={busy} onClick={() => setConfirmAll(false)}>cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
