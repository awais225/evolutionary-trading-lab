import React, { useCallback, useEffect, useState } from "react";
import { api, fmt } from "../api.js";

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

function KV({ label, value, tone = "text-slate-200", title }) {
  return (
    <div className="bg-slate-950 px-2.5 py-1.5 rounded-lg border border-slate-800 min-w-[112px]" title={title}>
      <span className="text-slate-500 block text-[10px] uppercase tracking-wide">{label}</span>
      <span className={`font-bold text-[11px] ${tone}`}>{value}</span>
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
  const modeTone = active
    ? "bg-emerald-950/60 border-emerald-500/50 text-emerald-300"
    : "bg-slate-950 border-slate-700 text-slate-300";

  return (
    <div className="space-y-3">
      {/* ------- mode + safety + counter ------- */}
      <div className="bg-slate-900/70 rounded-xl border border-slate-800 p-3 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className={`px-3 py-1.5 rounded-lg border font-bold text-xs tracking-wider ${modeTone}`}>
              LIVE TESTING: {active ? "ACTIVE" : "INACTIVE"}
            </span>
            <span className={`px-2.5 py-1 rounded-lg border text-[11px] font-mono ${connected ? "border-cyan-700 text-cyan-300 bg-cyan-950/40" : "border-rose-700 text-rose-300 bg-rose-950/40"}`}>
              MT5: {market?.connection || "–"} ({market?.source || "?"})
            </span>
            <span className={`px-2.5 py-1 rounded-lg border text-[11px] font-mono ${safety?.demo_verified ? "border-emerald-700 text-emerald-300 bg-emerald-950/40" : "border-amber-700 text-amber-300 bg-amber-950/40"}`}
              title={safety?.blocked_reason || "demo account verification"}>
              {safety?.demo_verified ? "DEMO VERIFIED" : `DEMO SAFETY: ${safety?.blocked_code || "NOT VERIFIED"}`}
            </span>
            {status?.previously_active && !active && (
              <span className="px-2.5 py-1 rounded-lg border border-slate-700 text-[11px] font-mono text-slate-400"
                title="The engine was activated earlier in this process but is not active now.">
                previously active
              </span>
            )}
            {status?.last_cycle_error && (
              <span className="px-2.5 py-1 rounded-lg border border-rose-800 text-[11px] font-mono text-rose-300">engine error</span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={openConfirmation} disabled={busy || active}
              className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 disabled:cursor-not-allowed text-white font-bold text-xs rounded-lg transition-colors shadow">
              ▶ ACTIVATE LIVE TESTING
            </button>
            <button type="button" onClick={doDeactivate} disabled={busy || !active}
              className="px-3 py-1.5 bg-rose-700 hover:bg-rose-600 disabled:opacity-40 disabled:cursor-not-allowed text-white font-bold text-xs rounded-lg transition-colors shadow"
              title="Stops new demo orders immediately. Existing positions are NOT closed.">
              ⏹ STOP LIVE TESTING
            </button>
            <button type="button" onClick={doReconcile} disabled={busy}
              className="px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg border border-slate-700 transition-colors"
              title="Verify recorded live-test trades against MT5 positions/orders">
              ↻ VERIFY WITH MT5
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <KV label="Active live test trades"
            value={`${activity.active_total ?? "–"} / ${status?.max_active_trades ?? "–"}`}
            tone={limitReached ? "text-orange-300" : (activity.active_total ? "text-emerald-300" : "text-slate-200")}
            title="Counted from MT5 positions + pending orders with the live-test magic range" />
          <KV label="Positions / orders" value={`${activity.positions ?? "–"} / ${activity.orders ?? "–"}`}
            title="MT5/broker state is authoritative — a local audit row is not an open position" />
          <KV label="Counted from" value={activity.counted ? (activity.source || "MT5") : "unavailable"}
            tone={activity.counted ? "text-slate-200" : "text-amber-300"} />
          <KV label="Risk per trade" value={`${risk.risk_pct_default ?? "–"}% (max ${risk.risk_pct_max ?? "–"}%)`} />
          <KV label="Eligible nodes" value={status?.node_count ?? "–"} />
          <KV label="Excluded nodes" value={status?.excluded_count ?? "–"}
            tone={(status?.excluded_count || 0) > 0 ? "text-amber-300" : "text-slate-200"}
            title="LEGACY_TEST / dead / killed / retired / unconfigured nodes are never live candidates" />
          <KV label="Blocked / unknown records" value={`${status?.records?.blocked ?? 0} / ${status?.records?.unknown ?? 0}`} />
          <KV label="Last engine cycle" value={status?.last_cycle_iso ? new Date(status.last_cycle_iso).toLocaleTimeString() : "–"}
            title={status?.last_noop_reason || ""} />
        </div>

        {limitUnknown && (
          <div className="px-3 py-2 rounded-lg border border-amber-700/60 bg-amber-950/30 text-amber-200 text-[11px] font-mono">
            ACTIVE LIVE TEST TRADES UNKNOWN — the MT5 position/order list could not be read
            ({status?.limit?.reason || "broker state unavailable"}). No new orders will be sent while
            the broker state is unknown.
          </div>
        )}
        {limitReached && (
          <div className="px-3 py-2 rounded-lg border border-orange-600/50 bg-orange-950/30 text-orange-200 text-[11px] font-mono">
            TRADE LIMIT REACHED — no new orders will be sent while {activity.active_total} live-test trade(s) are active
            (limit {status?.max_active_trades}). Existing trades are never closed or replaced automatically.
          </div>
        )}
        {!connected && (
          <div className="px-3 py-2 rounded-lg border border-rose-700/60 bg-rose-950/30 text-rose-200 text-[11px] font-mono">
            MT5 DISCONNECTED — live trading unavailable, no new trades. Existing positions are untouched and Live Testing stays
            INACTIVE after reconnect until you activate it again.
          </div>
        )}

        {/* limits editor */}
        <div className="flex flex-wrap items-end gap-3 text-[11px] font-mono">
          <label className="flex flex-col gap-1">
            <span className="text-slate-400">Risk per trade (%)</span>
            <input type="number" step="0.1" min="0.1" value={riskInput} onChange={(e) => setRiskInput(e.target.value)}
              className="w-24 px-2 py-1 bg-slate-950 border border-slate-800 rounded text-slate-200" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-slate-400">Max active trades</span>
            <input type="number" step="1" min="1" value={maxTradesInput} onChange={(e) => setMaxTradesInput(e.target.value)}
              className="w-24 px-2 py-1 bg-slate-950 border border-slate-800 rounded text-slate-200" />
          </label>
          <button type="button" onClick={doSaveLimits} disabled={busy}
            className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg border border-slate-700 transition-colors">
            Save limits
          </button>
          <span className="text-slate-500">Per-node overrides are set in each node's schedule ("Risk % Per Trade").</span>
        </div>
      </div>

      {error && (
        <div className="px-3 py-2 rounded-lg border border-rose-700/60 bg-rose-950/40 text-rose-200 text-[11px] font-mono">
          {error}
          <button type="button" className="ml-2 text-rose-300 underline" onClick={() => setError(null)}>dismiss</button>
        </div>
      )}
      {msg && (
        <div className={`px-3 py-2 rounded-lg border text-[11px] font-mono ${msg.ok ? "border-emerald-600/50 bg-emerald-950/30 text-emerald-200" : "border-rose-600/50 bg-rose-950/30 text-rose-200"}`}>
          {msg.text}
          <button type="button" className="ml-2 underline" onClick={() => setMsg(null)}>dismiss</button>
        </div>
      )}

      {/* ------- live market panel + stage log ------- */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <div className="bg-slate-900/70 rounded-xl border border-slate-800 p-3">
          <div className="flex items-center justify-between border-b border-slate-800 pb-2 mb-2">
            <span className="text-xs uppercase font-bold tracking-wider text-slate-300">Live market state (MT5)</span>
            <span className={`text-[10px] font-mono ${market?.trading_available ? "text-emerald-400" : "text-amber-400"}`}>
              {market?.trading_available ? "trading available" : "trading blocked"}
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
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
            <ul className="mt-2 space-y-1">
              {market.reasons.map((r, i) => (
                <li key={i} className="text-[11px] font-mono text-amber-300">• {r}</li>
              ))}
            </ul>
          )}
        </div>

        <div className="bg-slate-900/70 rounded-xl border border-slate-800 p-3">
          <div className="flex items-center justify-between border-b border-slate-800 pb-2 mb-2">
            <span className="text-xs uppercase font-bold tracking-wider text-slate-300">Live test stage log</span>
            <span className="text-[10px] font-mono text-slate-500">
              SIGNAL → RISK → VOLUME → MARKET → VALIDATED → SENT → BROKER → VERIFIED
            </span>
          </div>
          <div className="max-h-72 overflow-y-auto space-y-1">
            {events.length === 0 && (
              <div className="text-[11px] font-mono text-slate-500">
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm">
          <div className="bg-slate-900 border border-emerald-800/60 rounded-2xl max-w-2xl w-full p-6 shadow-2xl space-y-4 text-xs font-mono">
            <div className="border-b border-slate-800 pb-3">
              <h3 className="text-base font-bold text-white">Activate Live Testing (MT5 DEMO only)</h3>
              <p className="text-[11px] text-slate-400 mt-1">
                Review every item below. Activation lets the engine place <b>demo</b> market orders when a node's
                signal passes all safety gates. It never closes positions and never retries.
              </p>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <KV label="MT5 account" value={confirmPanel?.mt5_account ? `${confirmPanel.mt5_account.login} @ ${confirmPanel.mt5_account.server}` : "not available"}
                tone={confirmPanel?.account_status?.demo_verified ? "text-emerald-300" : "text-rose-300"} />
              <KV label="Account type" value={confirmPanel?.account_status?.demo_verified ? "DEMO (verified)" : `${confirmPanel?.account_status?.blocked_code || "NOT VERIFIED"}`}
                tone={confirmPanel?.account_status?.demo_verified ? "text-emerald-300" : "text-rose-300"} />
              <KV label="Equity" value={fmt.currency(confirmPanel?.mt5_account?.equity)} />
              <KV label="Bridge / connection" value={`${confirmPanel?.account_status?.bridge_source || "?"} / ${confirmPanel?.account_status?.connected ? "connected" : "disconnected"}`}
                tone={confirmPanel?.account_status?.connected ? "text-slate-200" : "text-rose-300"} />
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
              <div className="bg-slate-950 border border-slate-800 rounded-lg p-2 max-h-32 overflow-y-auto">
                {confirmPanel.nodes.map((n) => (
                  <div key={n.node_id} className="flex items-center justify-between text-[11px]">
                    <span className="text-slate-300">Node_{n.node_id} · {n.symbol} · {n.timeframe}</span>
                    <span className={n.risk_pct_source === "node_override" ? "text-amber-300" : "text-slate-400"}>
                      {n.risk_pct}% ({n.risk_pct_source})
                    </span>
                  </div>
                ))}
              </div>
            )}

            <div className="px-3 py-2 rounded-lg border border-amber-600/50 bg-amber-950/30 text-amber-200 text-[11px]">
              ⚠ {confirmPanel?.warning || "Demo orders can be placed on the MT5 DEMO account when Live Testing is active."}
              {confirmPanel?.account_status?.blocked_reason && (
                <div className="mt-1 text-rose-300">Current blocker: {confirmPanel.account_status.blocked_reason}</div>
              )}
            </div>

            <label className="flex items-center gap-2 text-[11px] text-slate-300">
              <input type="checkbox" checked={armed} onChange={(e) => setArmed(e.target.checked)} />
              I understand this enables controlled demo-order execution for the selected nodes and that
              STOP LIVE TESTING only blocks new orders.
            </label>

            <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
              <button type="button" onClick={() => setShowConfirm(false)}
                className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg">Cancel</button>
              <button type="button" onClick={doActivate} disabled={!armed || busy}
                className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 disabled:cursor-not-allowed text-white font-bold rounded-lg">
                CONFIRM — ACTIVATE LIVE TESTING
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
