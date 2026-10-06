import React, { useState, useEffect, useCallback, useRef } from "react";
import { api, fmt } from "../api.js";
import StructuredError from "./StructuredError.jsx";

/**
 * V4.2 — Controlled MT5 DEMO order panel.
 *
 * Proves the real execution pipeline: validation -> explicit confirmation ->
 * MT5 demo market order with real SL/TP -> broker result.
 *
 * Safety behaviour (V4.2 spec §10/§11/§12):
 *  - loading / refreshing this page NEVER places an order (only GET requests
 *    run automatically);
 *  - an order needs the explicit "PLACE DEMO ORDER" action AND the
 *    confirmation dialog AND the backend confirmation token;
 *  - the submit button is disabled while a submission is in flight, and the
 *    client order id is generated once per confirmation, so a double click
 *    cannot place two orders;
 *  - a timeout/unknown result is shown as UNKNOWN with the warning to verify
 *    positions in MT5 — never as a silent success, never auto-retried.
 */
const STATUS_STYLE = {
  POSITION_OPEN: "bg-emerald-950/60 border-emerald-500/50 text-emerald-300",
  PENDING_ORDER: "bg-amber-950/60 border-amber-500/50 text-amber-300",
  EXECUTED_UNCONFIRMED: "bg-amber-950/60 border-amber-500/50 text-amber-300",
  TIMEOUT_UNKNOWN: "bg-orange-950/60 border-orange-500/50 text-orange-300",
  UNKNOWN: "bg-orange-950/60 border-orange-500/50 text-orange-300",
  REJECTED: "bg-rose-950/60 border-rose-500/50 text-rose-300",
};

function newClientOrderId() {
  try {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return "demo-" + crypto.randomUUID();
  } catch {
    /* fall through */
  }
  return "demo-" + Date.now() + "-" + Math.floor(Math.random() * 1e6);
}

function ts(t) {
  if (!t) return "—";
  try {
    return new Date(t > 1e12 ? t : t * 1000).toLocaleString();
  } catch {
    return String(t);
  }
}

export default function DemoOrderPanel() {
  const [state, setState] = useState(null);
  const [nodes, setNodes] = useState([]);
  const [form, setForm] = useState({ symbol: "", side: "buy", volume: "", sl: "", tp: "", strategy_id: "" });
  const [preview, setPreview] = useState(null);
  const [confirming, setConfirming] = useState(null); // { clientOrderId, snapshot }
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [note, setNote] = useState("");
  const clientIdRef = useRef(null);
  const symbolTouched = useRef(false);

  const loadState = useCallback(async () => {
    try {
      const st = await api.mt5ExecutionState();
      setState(st);
      if (!symbolTouched.current && st?.default_symbol) {
        setForm((f) => ({ ...f, symbol: f.symbol || st.default_symbol }));
      }
    } catch (err) {
      setError(err);
    }
  }, []);

  const loadNodes = useCallback(async () => {
    try {
      // /api/research/filter is the existing V4.0 user-scoped endpoint:
      // LEGACY_TEST nodes can never appear here.
      const res = await api.researchFilter({ limit: 50, sort_by: "fitness", sort_desc: true });
      setNodes(res.strategies || []);
    } catch {
      setNodes([]);
    }
  }, []);

  useEffect(() => {
    loadState();
    loadNodes();
    const t = setInterval(loadState, 10000); // GET only — never places an order
    return () => clearInterval(t);
  }, [loadState, loadNodes]);

  const account = state?.account_safety?.account || null;
  const allowed = !!state?.execution_allowed;
  const quote = state?.quote || null;
  const sinfo = state?.symbol || null;
  const marketPrice = quote ? (form.side === "buy" ? quote.ask : quote.bid) : null;

  const numOrNull = (v) => {
    const s = String(v ?? "").trim();
    if (!s) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : NaN;
  };

  const payload = () => ({
    symbol: (form.symbol || "").trim().toUpperCase(),
    side: form.side,
    volume: numOrNull(form.volume),
    sl: numOrNull(form.sl),
    tp: numOrNull(form.tp),
    strategy_id: form.strategy_id === "" ? null : Number(form.strategy_id),
  });

  const handleValidate = async () => {
    setError(null); setResult(null); setNote("");
    try {
      const rep = await api.mt5ExecutionValidate(payload());
      setPreview(rep);
    } catch (err) {
      setError(err);
    }
  };

  const openConfirmation = async () => {
    setError(null); setResult(null);
    try {
      const rep = await api.mt5ExecutionValidate(payload()); // re-validate at confirmation time
      setPreview(rep);
      if (!rep.placement_allowed) return;
      clientIdRef.current = newClientOrderId(); // one id per confirmation
      setConfirming({
        clientOrderId: clientIdRef.current,
        symbol: rep.normalized.symbol, side: rep.normalized.side,
        volume: rep.normalized.volume, price: marketPrice,
        sl: rep.normalized.sl, tp: rep.normalized.tp,
        account, quote,
      });
    } catch (err) {
      setError(err);
    }
  };

  const submit = async () => {
    if (busy) return;                       // ← duplicate-click protection
    const clientOrderId = confirming?.clientOrderId || clientIdRef.current;
    setBusy(true); setError(null); setNote("SUBMITTING TO MT5 — waiting for the broker result…");
    try {
      const res = await api.mt5ExecutionPlace({
        ...payload(), confirm: "PLACE_DEMO_ORDER", client_order_id: clientOrderId,
      });
      setResult(res);
      setNote("");
      loadState();
    } catch (err) {
      setError(err);
      setNote("");
      // a 409 duplicate/in-flight or a blocked order is still an audit entry
      loadState();
    } finally {
      setBusy(false);
      setConfirming(null);
      clientIdRef.current = null;
    }
  };

  return (
    <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-4 shadow-sm space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs uppercase font-bold tracking-wider text-slate-300">
            V4.2 — Manual MT5 Demo Order Execution
          </span>
          <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-purple-500/20 text-purple-300 border border-purple-500/40">
            DEMO ONLY
          </span>
        </div>
        <span className="text-[10px] font-mono text-slate-500">
          manual action only · no automatic trading · no retries
        </span>
      </div>

      {/* account / safety banner (spec §1) */}
      <div className={`p-3 rounded-lg border font-mono text-[11px] space-y-1 ${
        allowed ? "bg-emerald-950/40 border-emerald-500/40 text-emerald-200"
                : "bg-rose-950/40 border-rose-500/40 text-rose-200"}`}>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="font-bold">{allowed ? "✅ MT5 DEMO ACCOUNT VERIFIED" : "⛔ ORDER EXECUTION BLOCKED"}</span>
          <span>bridge: {state?.bridge?.name || "—"} ({state?.bridge?.source || "—"})</span>
          {account && (
            <span>
              account {account.login} @ {account.server} · {account.currency} ·{" "}
              balance {account.balance} · free margin {account.margin_free} · {account.trade_mode_name}
            </span>
          )}
          {state?.kill_switch_engaged != null && (
            <span className="text-slate-400">risk kill switch: {state.kill_switch_engaged ? "ENGAGED (strategy trading)" : "released"}</span>
          )}
        </div>
        {!allowed && <div className="text-rose-300">{state?.blocked_reason}</div>}
        <div className="text-slate-400">demo check code: {state?.blocked_code || "DEMO_VERIFIED"} · MT5 package installed: {String(state?.account_safety?.mt5_package_installed)}</div>
      </div>

      {error && <StructuredError error={error} title="MT5 Execution Error" onDismiss={() => setError(null)} />}
      {note && <div className="p-2.5 rounded-lg text-[11px] font-mono bg-slate-950 border border-slate-700 text-slate-300">{note}</div>}

      {/* order form (spec §6) */}
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3 text-xs font-mono">
        <label className="space-y-1">
          <span className="text-[10px] uppercase text-slate-400 block">Symbol</span>
          <input
            value={form.symbol}
            onChange={(e) => { symbolTouched.current = true; setForm({ ...form, symbol: e.target.value.toUpperCase() }); }}
            className="w-full px-2 py-1.5 bg-slate-950 border border-slate-800 rounded text-slate-200 focus:outline-none focus:border-purple-500"
            placeholder="XAUUSD"
          />
        </label>
        <label className="space-y-1">
          <span className="text-[10px] uppercase text-slate-400 block">Side</span>
          <div className="flex gap-1">
            {["buy", "sell"].map((s) => (
              <button
                key={s} type="button" onClick={() => setForm({ ...form, side: s })}
                className={`flex-1 px-2 py-1.5 rounded border font-bold uppercase transition-colors ${
                  form.side === s
                    ? (s === "buy" ? "bg-emerald-600 border-emerald-500 text-white" : "bg-rose-600 border-rose-500 text-white")
                    : "bg-slate-950 border-slate-800 text-slate-400 hover:text-slate-200"}`}
              >{s}</button>
            ))}
          </div>
        </label>
        <label className="space-y-1">
          <span className="text-[10px] uppercase text-slate-400 block">Volume (lots)</span>
          <input
            value={form.volume}
            onChange={(e) => setForm({ ...form, volume: e.target.value })}
            className="w-full px-2 py-1.5 bg-slate-950 border border-slate-800 rounded text-slate-200 focus:outline-none focus:border-purple-500"
            placeholder={sinfo?.volume_min != null ? String(sinfo.volume_min) : "0.01"}
          />
          {sinfo && (
            <span className="text-[9px] text-slate-500 block">
              min {sinfo.volume_min ?? "?"} / max {sinfo.volume_max ?? "?"} / step {sinfo.volume_step ?? "?"}
            </span>
          )}
        </label>
        <label className="space-y-1">
          <span className="text-[10px] uppercase text-slate-400 block">Market price</span>
          <div className="px-2 py-1.5 bg-slate-950/60 border border-slate-800 rounded text-slate-300">
            {marketPrice != null ? marketPrice : "—"}
          </div>
          <span className="text-[9px] text-slate-500 block">
            {quote ? `bid ${quote.bid} / ask ${quote.ask} ${quote.source ? `(${quote.source})` : ""}` : "no quote"}
          </span>
        </label>
        <label className="space-y-1">
          <span className="text-[10px] uppercase text-slate-400 block">Stop loss</span>
          <input
            value={form.sl}
            onChange={(e) => setForm({ ...form, sl: e.target.value })}
            className="w-full px-2 py-1.5 bg-slate-950 border border-slate-800 rounded text-slate-200 focus:outline-none focus:border-rose-500"
            placeholder={form.side === "buy" ? "< entry" : "> entry"}
          />
          <span className="text-[9px] text-slate-500 block">min distance {sinfo?.stops_level ?? "?"} pts</span>
        </label>
        <label className="space-y-1">
          <span className="text-[10px] uppercase text-slate-400 block">Take profit</span>
          <input
            value={form.tp}
            onChange={(e) => setForm({ ...form, tp: e.target.value })}
            className="w-full px-2 py-1.5 bg-slate-950 border border-slate-800 rounded text-slate-200 focus:outline-none focus:border-emerald-500"
            placeholder={form.side === "buy" ? "> entry" : "< entry"}
          />
        </label>
        <label className="space-y-1 md:col-span-2">
          <span className="text-[10px] uppercase text-slate-400 block">Attach to research node (optional)</span>
          <select
            value={form.strategy_id}
            onChange={(e) => setForm({ ...form, strategy_id: e.target.value })}
            className="w-full px-2 py-1.5 bg-slate-950 border border-slate-800 rounded text-slate-200 focus:outline-none focus:border-purple-500"
          >
            <option value="">— none (manual test) —</option>
            {nodes.map((n) => (
              <option key={n.id} value={n.id}>Node_{String(n.id).padStart(6, "0")} · {n.status}</option>
            ))}
          </select>
          <span className="text-[9px] text-slate-500 block">
            USER_RESEARCH nodes only — legacy nodes are filtered out; a test order never changes node status
          </span>
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button" onClick={handleValidate} disabled={busy}
          className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 font-bold text-xs rounded-lg border border-slate-700 transition-colors disabled:opacity-40"
        >CHECK VALIDATION</button>
        <button
          type="button" onClick={openConfirmation} disabled={busy}
          className="px-4 py-1.5 bg-purple-600 hover:bg-purple-500 text-white font-bold text-xs rounded-lg shadow transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1.5"
        >
          <span>⚡</span><span>{busy ? "SUBMITTING…" : "PLACE DEMO ORDER"}</span>
        </button>
        {!allowed && (
          <span className="text-[10px] font-mono text-rose-300">
            The button is enabled so the block reason is visible — the backend refuses without a verified DEMO account.
          </span>
        )}
      </div>

      {/* validation checklist */}
      {preview && (
        <div className="bg-slate-950/70 border border-slate-800 rounded-lg p-3 space-y-1">
          <div className="text-[10px] uppercase font-bold tracking-wider text-slate-400">
            Local validation — {preview.placement_allowed ? "PASSED (order may be sent)" : "BLOCKED (no order sent)"}
          </div>
          <div className="grid md:grid-cols-2 gap-x-4 gap-y-0.5 font-mono text-[11px]">
            {preview.checks.map((c) => (
              <div key={c.id} className={c.ok ? "text-slate-400" : "text-rose-300"}>
                {c.ok ? "✓" : "✕"} {c.label}{c.detail ? <span className="text-slate-500"> — {c.detail}</span> : null}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* result (spec §14) */}
      {result && (
        <div className={`p-3 rounded-lg border font-mono text-[11px] space-y-1 ${STATUS_STYLE[result.status] || "bg-slate-950 border-slate-700 text-slate-300"}`}>
          <div className="font-bold text-sm">
            {result.status === "POSITION_OPEN" ? "✅ " : result.status === "PENDING_ORDER" ? "🕐 " : result.ok ? "⚠️ " : "⛔ "}
            {result.label}
          </div>
          <div className="grid md:grid-cols-2 gap-x-4">
            <span>ticket: {result.order?.ticket ?? "—"} · deal: {result.order?.deal_ticket ?? "—"} · position: {result.order?.position_ticket ?? "—"}</span>
            <span>retcode: {result.broker?.retcode ?? "—"} · {result.broker?.category}</span>
            <span>symbol: {result.execution?.symbol} · side: {result.execution?.side} · volume: {result.execution?.volume}</span>
            <span>price: {result.execution?.exec_price ?? "—"} (requested {result.execution?.requested_price ?? "—"})</span>
            <span>SL: {result.execution?.sl_broker ?? result.execution?.sl_requested ?? "—"} (requested {result.execution?.sl_requested ?? "—"})</span>
            <span>TP: {result.execution?.tp_broker ?? result.execution?.tp_requested ?? "—"} (requested {result.execution?.tp_requested ?? "—"})</span>
            <span>SL/TP verified on the position: {String(result.execution?.sl_tp_verified)}</span>
            <span>account: {result.account?.login} @ {result.account?.server} ({result.account?.trade_mode_name})</span>
            <span>time: {ts(result.execution?.time)} · duration: {result.duration_ms} ms</span>
            <span>source: {result.source} · client order id: {result.client_order_id}</span>
          </div>
          <div className="text-slate-300">{result.broker?.message}</div>
          {result.broker?.warning && <div className="text-orange-300 font-bold">{result.broker.warning}</div>}
          {result.verification?.note && <div className="text-amber-300">{result.verification.note}</div>}
        </div>
      )}

      {/* recent manual orders */}
      {state?.recent_orders?.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase font-bold tracking-wider text-slate-400">Recent manual demo orders</div>
          <div className="overflow-x-auto">
            <table className="w-full text-[10px] font-mono">
              <thead className="text-slate-500">
                <tr>
                  <th className="text-left px-1">time</th><th className="text-left px-1">symbol</th>
                  <th className="text-left px-1">side</th><th className="text-left px-1">vol</th>
                  <th className="text-left px-1">status</th><th className="text-left px-1">ticket</th>
                  <th className="text-left px-1">retcode</th><th className="text-left px-1">message</th>
                </tr>
              </thead>
              <tbody>
                {state.recent_orders.slice(0, 8).map((o) => (
                  <tr key={o.id} className="border-t border-slate-800/60 text-slate-400">
                    <td className="px-1">{ts(o.ts)}</td>
                    <td className="px-1">{o.symbol || "—"}</td>
                    <td className="px-1">{o.side || "—"}</td>
                    <td className="px-1">{o.volume ?? "—"}</td>
                    <td className={`px-1 ${o.status === "POSITION_OPEN" ? "text-emerald-400" : o.status === "REJECTED" || o.status === "BLOCKED" || o.status === "ERROR" ? "text-rose-400" : "text-amber-400"}`}>{o.status}</td>
                    <td className="px-1">{o.order_ticket || "—"}</td>
                    <td className="px-1">{o.retcode ?? "—"}</td>
                    <td className="px-1 truncate max-w-[320px]" title={o.message}>{o.message || o.error_code || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* confirmation dialog (spec §10) */}
      {confirming && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm">
          <div className="bg-slate-900 border border-purple-500/50 rounded-2xl max-w-lg w-full p-6 shadow-2xl space-y-4 text-xs font-mono">
            <div className="flex items-center gap-2 text-purple-400 font-bold text-sm">
              <span>⚡</span><span>CONFIRM DEMO ORDER</span>
              <span className="ml-auto px-2 py-0.5 rounded text-[10px] bg-purple-500/20 border border-purple-500/40">DEMO ACCOUNT ONLY</span>
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 bg-slate-950 border border-slate-800 rounded-lg p-3">
              <span className="text-slate-500">Account</span>
              <span className="text-slate-200">{confirming.account?.login} @ {confirming.account?.server} · {confirming.account?.trade_mode_name}</span>
              <span className="text-slate-500">Symbol</span><span className="text-slate-200">{confirming.symbol}</span>
              <span className="text-slate-500">Side</span>
              <span className={confirming.side === "buy" ? "text-emerald-400 font-bold" : "text-rose-400 font-bold"}>{String(confirming.side).toUpperCase()}</span>
              <span className="text-slate-500">Volume</span><span className="text-slate-200">{confirming.volume} lots</span>
              <span className="text-slate-500">Estimated / current price</span>
              <span className="text-slate-200">{confirming.price ?? "—"} <span className="text-slate-500">({confirming.side === "buy" ? "ask" : "bid"})</span></span>
              <span className="text-slate-500">Stop loss</span><span className="text-rose-300">{confirming.sl ?? "none"}</span>
              <span className="text-slate-500">Take profit</span><span className="text-emerald-300">{confirming.tp ?? "none"}</span>
              <span className="text-slate-500">Client order id</span><span className="text-slate-400 break-all">{confirming.clientOrderId}</span>
            </div>
            <div className="p-2.5 bg-amber-950/40 border border-amber-600/40 rounded text-[11px] text-amber-200">
              This sends ONE real market order to your MT5 DEMO account with the SL/TP above. It is not
              simulated and it is not retried. Loading or refreshing the page never places an order.
            </div>
            <div className="flex items-center justify-end gap-2">
              <button
                type="button" onClick={() => setConfirming(null)} disabled={busy}
                className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700 disabled:opacity-40"
              >CANCEL</button>
              <button
                type="button" onClick={submit} disabled={busy}
                className="px-4 py-1.5 bg-purple-600 hover:bg-purple-500 text-white font-bold rounded-lg shadow disabled:opacity-40 disabled:cursor-not-allowed"
              >{busy ? "SUBMITTING… ONE REQUEST" : "CONFIRM & SEND DEMO ORDER"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
