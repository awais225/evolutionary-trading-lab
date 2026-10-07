import React, { useCallback, useEffect, useState } from "react";
import { api, fmt } from "../api.js";
import { Badge } from "./ui.jsx";

/**
 * V4.3 — Controlled Live Testing control surface (demo only).
 *
 * Safety semantics implemented here (mirrors the backend):
 *   • Live Testing always loads INACTIVE; this panel only *displays* that state.
 *   • Activation requires the confirmation panel (MT5 account, DEMO status,
 *     symbol scope, node scope, risk, max active trades, market state + warning).
 *   • STOP LIVE TESTING blocks new orders and never closes existing positions;
 *     "CLOSE EXISTING POSITIONS" is explicitly not available in V4.3.
 *   • The trade counter comes from MT5 positions/orders, not from this screen.
 *
 * V5.1a §5 — the surface now separates the three operational states the operator
 * must be able to read at a glance, using the dashboard's own kit:
 *
 *   testing state   ACTIVE / INACTIVE
 *   MT5 state       REAL MT5 / SIMULATOR / UNAVAILABLE   (never SIMULATOR as real)
 *   demo safety     VERIFIED / NOT VERIFIED / BLOCKED
 *   actions         Activate · Stop · Verify with MT5
 *
 * Every value this panel showed before is still shown, unchanged and coming from
 * the same payloads; only the presentation moved onto the shared V5 components.
 */

const STAGE_STYLE = (stage, status) => {
  const s = String(stage || "").toUpperCase();
  const st = String(status || "").toUpperCase();
  if (st.includes("BLOCK") || s.includes("BLOCK")) return "text-amber-300 border-amber-500/40 bg-amber-950/40";
  if (st.includes("REJECT")) return "text-rose-300 border-rose-500/40 bg-rose-950/40";
  if (st.includes("UNKNOWN")) return "text-fuchsia-300 border-fuchsia-500/40 bg-fuchsia-950/40";
  if (st.includes("LIMIT")) return "text-orange-300 border-orange-500/40 bg-orange-950/40";
  if (s.includes("POSITION VERIFIED") || st.includes("POSITION_OPEN")) return "text-emerald-300 border-emerald-500/40 bg-emerald-950/40";
  if (s.includes("BROKER")) return "text-cyan-300 border-cyan-500/40 bg-cyan-950/40";
  return "text-slate-300 border-slate-700 bg-slate-900/60";
};

/** One label/value tile in the kit's own idiom (`.kit-strip .item`). */
function KV({ label, value, tone = "", title }) {
  return (
    <div className="item" title={title}>
      <span className="k">{label}</span>
      <span className={"v " + tone}>{value}</span>
    </div>
  );
}

export default function LiveTestingControl() {
  const [status, setStatus] = useState(null);
  const [market, setMarket] = useState(null);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [confirmPanel, setConfirmPanel] = useState(null);
  const [armed, setArmed] = useState(false);
  const [riskInput, setRiskInput] = useState("1.0");
  const [maxTradesInput, setMaxTradesInput] = useState("1");

  const load = useCallback(async () => {
    try {
      const [st, mk, log] = await Promise.all([
        api.liveTestingStatus(),
        api.liveTestingMarket(),
        api.liveTestingLog({ limit: 25 }),
      ]);
      setStatus(st);
      setMarket(mk);
      setEvents(log.events || []);
      setError(null);
      if (st?.risk?.risk_pct_default != null) setRiskInput(String(st.risk.risk_pct_default));
      if (st?.max_active_trades != null) setMaxTradesInput(String(st.max_active_trades));
    } catch (err) {
      setError(err?.formattedMessage || err?.message || String(err));
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  const openConfirmation = async () => {
    setBusy(true);
    setArmed(false);
    try {
      const panel = await api.liveTestingConfirmation();
      setConfirmPanel(panel);
      setShowConfirm(true);
      setMsg(null);
    } catch (err) {
      setError(err?.formattedMessage || err?.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  const doActivate = async () => {
    setBusy(true);
    try {
      const res = await api.liveTestingActivate({
        armed_by: "dashboard",
        risk_pct_default: parseFloat(riskInput),
        max_active_trades: parseInt(maxTradesInput, 10),
      });
      if (res?.ok) {
        setMsg({
          ok: true,
          text: `LIVE TESTING: ACTIVE — ${res.activation?.node_count || 0} node(s), risk ${res.activation?.risk_pct_default}%, max ${res.activation?.max_active_trades} active trade(s). Orders still require every safety gate.`,
        });
        setShowConfirm(false);
      } else {
        setError(res?.error || res?.code || "activation refused");
      }
      await load();
    } catch (err) {
      setError(err?.formattedMessage || err?.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  const doDeactivate = async () => {
    setBusy(true);
    try {
      const res = await api.liveTestingDeactivate("operator stopped live testing");
      setMsg({
        ok: true,
        text: `LIVE TESTING: INACTIVE — new orders blocked. Existing positions: ${res?.positions_closed || 0} closed (no automatic liquidation in V4.3).`,
      });
      await load();
    } catch (err) {
      setError(err?.formattedMessage || err?.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  const doSaveLimits = async () => {
    setBusy(true);
    try {
      await api.liveTestingSettings({
        risk_pct_default: parseFloat(riskInput),
        max_active_trades: parseInt(maxTradesInput, 10),
      });
      setMsg({ ok: true, text: `Limits saved: risk ${riskInput}% / max ${maxTradesInput} active trade(s).` });
      await load();
    } catch (err) {
      setError(err?.formattedMessage || err?.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  const doReconcile = async () => {
    setBusy(true);
    try {
      const res = await api.liveTestingReconcile();
      setMsg({ ok: true, text: `MT5 verification: ${res?.reconcile?.verified || 0} verified, ${res?.reconcile?.closed || 0} closed, ${res?.reconcile?.unknown || 0} unknown.` });
      await load();
    } catch (err) {
      setError(err?.formattedMessage || err?.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  const active = !!status?.active;
  const activity = status?.activity || {};
  const limitReached = status?.limit?.reached === true;      // None = broker state unknown
  const limitUnknown = status?.limit?.reached === null || status?.limit?.unknown === true;
  const risk = status?.risk || {};
  const connected = !!market?.connected;
  const safety = status?.account_status || confirmPanel?.account_status || {};

  /* ---- §5 operational states, derived from the payloads (never assumed) ---- */
  const source = String(market?.source || "").toUpperCase();
  const isRealMt5 = source === "MT5";
  const mt5State = isRealMt5 ? (connected ? "REAL MT5" : "UNAVAILABLE")
    : source === "SIMULATOR" ? "SIMULATOR" : "UNAVAILABLE";
  const mt5Tone = isRealMt5 && connected ? "real" : source === "SIMULATOR" ? "sim" : "bad";
  const mt5Detail = isRealMt5
    ? `${market?.connection || "–"} · real terminal`
    : source === "SIMULATOR"
      ? "simulated quotes · no broker execution"
      : (market?.reasons || [])[0] || "no market bridge";

  const safetyVerified = !!safety?.demo_verified;
  const safetyState = safetyVerified ? "VERIFIED" : (safety?.blocked_code ? "BLOCKED" : "NOT VERIFIED");
  const safetyTone = safetyVerified ? "ok" : (safety?.blocked_code ? "bad" : "warn");

  return (
    <div className="space-y-3">
      {/* ------- operational state + actions ------- */}
      <div className="panel">
        <div className="kit-head">
          <div className="kit-section-title" style={{ margin: 0 }}>Live testing control</div>
          <div className="muted" style={{ fontSize: 11.5 }}>
            Orders only ever go to a connected, positively identified DEMO terminal.
          </div>
        </div>

        <div className="kit-strip" style={{ marginBottom: 10 }}>
          <div className="item">
            <span className="k">Testing state</span>
            <span className="v">
              <Badge tone={active ? "ok" : "mute"}>{active ? "ACTIVE" : "INACTIVE"}</Badge>
            </span>
          </div>
          <div className="item" title={mt5Detail}>
            <span className="k">MT5 state</span>
            <span className="v"><Badge tone={mt5Tone}>{mt5State}</Badge></span>
          </div>
          <div className="item" title={safety?.blocked_reason || "demo account verification"}>
            <span className="k">Demo safety</span>
            <span className="v"><Badge tone={safetyTone}>{safetyState}</Badge></span>
          </div>
          <div className="item" style={{ flex: 1, minWidth: 220 }}>
            <span className="k">Detail</span>
            <span className="v" style={{ fontSize: 11.5, fontWeight: 500 }}>
              {mt5Detail}
              {safety?.blocked_code ? ` · ${safety.blocked_code}` : ""}
              {status?.previously_active && !active ? " · previously active" : ""}
              {status?.last_cycle_error ? " · engine error" : ""}
            </span>
          </div>
        </div>

        <div className="btn-row">
          <button type="button" className="btn success" onClick={openConfirmation} disabled={busy || active}
            title="Review the safety panel, then activate the live engine for the enrolled nodes.">
            ACTIVATE LIVE TESTING
          </button>
          <button type="button" className="btn danger" onClick={doDeactivate} disabled={busy || !active}
            title="Stops new demo orders immediately. Existing positions are NOT closed.">
            STOP LIVE TESTING
          </button>
          <button type="button" className="btn" onClick={doReconcile} disabled={busy}
            title="Verify recorded live-test trades against MT5 positions/orders">
            VERIFY WITH MT5
          </button>
        </div>

        <div className="kit-strip" style={{ border: "none", padding: 0 }}>
          <KV label="Active live test trades"
            value={`${activity.active_total ?? "–"} / ${status?.max_active_trades ?? "–"}`}
            tone={limitReached ? "text-orange-300" : (activity.active_total ? "text-emerald-300" : "")}
            title="Counted from MT5 positions + pending orders with the live-test magic range" />
          <KV label="Positions / orders" value={`${activity.positions ?? "–"} / ${activity.orders ?? "–"}`}
            title="MT5/broker state is authoritative — a local audit row is not an open position" />
          <KV label="Counted from" value={activity.counted ? (activity.source || "MT5") : "unavailable"}
            tone={activity.counted ? "" : "text-amber-300"} />
          <KV label="Risk per trade" value={`${risk.risk_pct_default ?? "–"}% (max ${risk.risk_pct_max ?? "–"}%)`} />
          <KV label="Eligible nodes" value={status?.node_count ?? "–"} />
          <KV label="Excluded nodes" value={status?.excluded_count ?? "–"}
            tone={(status?.excluded_count || 0) > 0 ? "text-amber-300" : ""}
            title="LEGACY_TEST / dead / killed / retired / unconfigured nodes are never live candidates" />
          <KV label="Blocked / unknown records" value={`${status?.records?.blocked ?? 0} / ${status?.records?.unknown ?? 0}`} />
          <KV label="Last engine cycle" value={status?.last_cycle_iso ? new Date(status.last_cycle_iso).toLocaleTimeString() : "–"}
            title={status?.last_noop_reason || ""} />
        </div>

        {limitUnknown && (
          <div className="kit-state warn" style={{ marginTop: 8 }}>
            ACTIVE LIVE TEST TRADES UNKNOWN — the MT5 position/order list could not be read
            ({status?.limit?.reason || "broker state unavailable"}). No new orders will be sent while
            the broker state is unknown.
          </div>
        )}
        {limitReached && (
          <div className="kit-state warn" style={{ marginTop: 8 }}>
            TRADE LIMIT REACHED — no new orders will be sent while {activity.active_total} live-test trade(s) are active
            (limit {status?.max_active_trades}). Existing trades are never closed or replaced automatically.
          </div>
        )}
        {!connected && (
          <div className="kit-state error" style={{ marginTop: 8 }}>
            MT5 DISCONNECTED — live trading unavailable, no new trades. Existing positions are untouched and Live Testing stays
            INACTIVE after reconnect until you activate it again.
          </div>
        )}

        {/* limits editor */}
        <div className="row-bar" style={{ marginTop: 10, alignItems: "flex-end" }}>
          <label className="field" style={{ margin: 0, width: 140 }}>
            <span>Risk per trade (%)</span>
            <input type="number" step="0.1" min="0.1" className="input" value={riskInput}
              onChange={(e) => setRiskInput(e.target.value)} />
          </label>
          <label className="field" style={{ margin: 0, width: 140 }}>
            <span>Max active trades</span>
            <input type="number" step="1" min="1" className="input" value={maxTradesInput}
              onChange={(e) => setMaxTradesInput(e.target.value)} />
          </label>
          <button type="button" className="btn sm" onClick={doSaveLimits} disabled={busy}>Save limits</button>
          <span className="muted" style={{ fontSize: 11.5, alignSelf: "center" }}>
            Per-node overrides are set in each node's schedule ("Risk % Per Trade").
          </span>
        </div>
      </div>

      {error && (
        <div className="kit-state error">
          {error}
          <button type="button" className="btn subtle xs" style={{ marginLeft: 8 }} onClick={() => setError(null)}>dismiss</button>
        </div>
      )}
      {msg && (
        <div className={msg.ok ? "kit-ok" : "kit-state error"}>
          {msg.text}
          <button type="button" className="btn subtle xs" style={{ marginLeft: 8 }} onClick={() => setMsg(null)}>dismiss</button>
        </div>
      )}

      {/* ------- live market panel + stage log ------- */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <div className="panel">
          <div className="kit-head">
            <div className="kit-section-title" style={{ margin: 0 }}>Live market state (MT5)</div>
            <Badge tone={market?.trading_available ? "ok" : "warn"}>
              {market?.trading_available ? "trading available" : "trading blocked"}
            </Badge>
          </div>
          <div className="kit-strip" style={{ border: "none", padding: 0 }}>
            <KV label="Symbol" value={market?.symbol || "–"} />
            <KV label="Bid" value={fmt.num(market?.bid, 5)} />
            <KV label="Ask" value={fmt.num(market?.ask, 5)} />
            <KV label="Spread" value={fmt.num(market?.spread, 5)} />
            <KV label="Tick age" value={market?.tick_age_s != null ? `${fmt.num(market.tick_age_s, 1)}s` : "–"}
              tone={market?.data_fresh ? "text-emerald-300" : "text-amber-300"} />
            <KV label="Session" value={market?.session_status || "–"} />
            <KV label="Tick time" value={market?.tick_time ? new Date(market.tick_time).toLocaleTimeString() : "–"} />
            <KV label="Point / digits" value={`${market?.point ?? "–"} / ${market?.digits ?? "–"}`} />
            <KV label="Tradable" value={market?.symbol_tradable == null ? "–" : (market.symbol_tradable ? "yes" : "no")}
              tone={market?.symbol_tradable ? "text-emerald-300" : "text-amber-300"} />
          </div>
          {!!(market?.reasons || []).length && (
            <ul style={{ margin: "8px 0 0 16px" }}>
              {market.reasons.map((r, i) => (
                <li key={i} style={{ fontSize: 11.5, color: "var(--amber)" }}>{r}</li>
              ))}
            </ul>
          )}
        </div>

        <div className="panel">
          <div className="kit-head">
            <div className="kit-section-title" style={{ margin: 0 }}>Live test stage log</div>
            <span className="muted" style={{ fontSize: 10.5 }}>
              SIGNAL → RISK → VOLUME → MARKET → VALIDATED → SENT → BROKER → VERIFIED
            </span>
          </div>
          <div className="kit-scroll" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {events.length === 0 && (
              <div className="muted" style={{ fontSize: 11.5 }}>
                No live-test events yet. Events appear when the engine is ACTIVE and an eligible node
                produces a signal (or is blocked before an order).
              </div>
            )}
            {events.map((e) => (
              <div key={e.id ?? `${e.ts}-${e.stage}`} className={`px-2 py-1 rounded border text-[11px] font-mono ${STAGE_STYLE(e.stage, e.status)}`}>
                <span className="text-slate-500">{e.ts_iso ? new Date(e.ts_iso).toLocaleTimeString() : fmt.ts(e.ts)}</span>
                <span className="mx-1.5 font-bold">{e.stage}</span>
                {e.status && <span className="text-slate-400">[{e.status}]</span>}
                <div className="text-slate-200 whitespace-normal break-words">{e.message}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ------- activation confirmation panel (spec §5) ------- */}
      {showConfirm && (
        <div className="modal-backdrop">
          <div className="modal">
            <div className="kit-head" style={{ borderBottom: "1px solid var(--border)", paddingBottom: 10 }}>
              <div>
                <div className="modal-title">Activate Live Testing (MT5 DEMO only)</div>
                <div className="muted" style={{ fontSize: 11.5, marginTop: 3 }}>
                  Review every item below. Activation lets the engine place <b>demo</b> market orders when a node's
                  signal passes all safety gates. It never closes positions and never retries.
                </div>
              </div>
            </div>

            <div className="kit-strip" style={{ border: "none", padding: 0, marginTop: 10 }}>
              <KV label="MT5 account" value={confirmPanel?.mt5_account ? `${confirmPanel.mt5_account.login} @ ${confirmPanel.mt5_account.server}` : "not available"}
                tone={confirmPanel?.account_status?.demo_verified ? "text-emerald-300" : "text-rose-300"} />
              <KV label="Account type" value={confirmPanel?.account_status?.demo_verified ? "DEMO (verified)" : `${confirmPanel?.account_status?.blocked_code || "NOT VERIFIED"}`}
                tone={confirmPanel?.account_status?.demo_verified ? "text-emerald-300" : "text-rose-300"} />
              <KV label="Equity" value={fmt.currency(confirmPanel?.mt5_account?.equity)} />
              <KV label="Bridge / connection" value={`${confirmPanel?.account_status?.bridge_source || "?"} / ${confirmPanel?.account_status?.connected ? "connected" : "disconnected"}`}
                tone={confirmPanel?.account_status?.connected ? "" : "text-rose-300"} />
              <KV label="Symbol scope" value={(confirmPanel?.symbols || []).join(", ") || "no eligible nodes"} />
              <KV label="Node scope" value={`${confirmPanel?.node_count ?? 0} node(s)`} />
              <KV label="Risk setting" value={`${confirmPanel?.risk_pct_default ?? "–"}% default (max ${confirmPanel?.risk_pct_max ?? "–"}%)`} />
              <KV label="Max active trades" value={confirmPanel?.max_active_trades ?? "–"} />
              <KV label="Market" value={confirmPanel?.market?.trading_available ? `available (${confirmPanel.market.symbol})` : "blocked"}
                tone={confirmPanel?.market?.trading_available ? "text-emerald-300" : "text-amber-300"} />
              <KV label="Execution currently allowed" value={confirmPanel?.execution_allowed ? "yes" : "no"}
                tone={confirmPanel?.execution_allowed ? "text-emerald-300" : "text-amber-300"} />
            </div>

            {(confirmPanel?.nodes || []).length > 0 && (
              <div style={{ background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 8,
                            padding: 8, maxHeight: 130, overflowY: "auto", marginTop: 10 }}>
                {confirmPanel.nodes.map((n) => (
                  <div key={n.node_id} className="kit-kv">
                    <span className="k">Node_{n.node_id} · {n.symbol} · {n.timeframe}</span>
                    <span className={n.risk_pct_source === "node_override" ? "text-amber-300" : "muted"}>
                      {n.risk_pct}% ({n.risk_pct_source})
                    </span>
                  </div>
                ))}
              </div>
            )}

            <div className="kit-state warn" style={{ marginTop: 10 }}>
              <b>Warning:</b> {confirmPanel?.warning || "Demo orders can be placed on the MT5 DEMO account when Live Testing is active."}
              {confirmPanel?.account_status?.blocked_reason && (
                <div style={{ marginTop: 4, color: "var(--red)" }}>Current blocker: {confirmPanel.account_status.blocked_reason}</div>
              )}
            </div>

            <label className="chk row-bar" style={{ marginTop: 10, fontSize: 11.5 }}>
              <input type="checkbox" checked={armed} onChange={(e) => setArmed(e.target.checked)} />
              <span>I understand this enables controlled demo-order execution for the selected nodes and that
                STOP LIVE TESTING only blocks new orders.</span>
            </label>

            <div className="btn-row" style={{ justifyContent: "flex-end", marginTop: 10, marginBottom: 0 }}>
              <button type="button" className="btn" onClick={() => setShowConfirm(false)}>Cancel</button>
              <button type="button" className="btn success" onClick={doActivate} disabled={!armed || busy}>
                CONFIRM — ACTIVATE LIVE TESTING
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
