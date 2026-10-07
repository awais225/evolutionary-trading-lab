/* V4.8 — Live Testing panels (§7).
 *
 * Four self-contained blocks used by the Live Testing page:
 *   RiskStrip          global risk %, equity, cash risk, execution sizing,
 *                      per-node risk override + reset (real backend calls)
 *   ManualOrderPanel   manual BUY/SELL with risk<->lot conversion done by the
 *                      backend preview endpoint (never by the browser), SL
 *                      required, lot rounded DOWN by the broker step
 *   LiveMarketPanel    bid/ask/spread, indicator usage, per-node entry
 *                      conditions, "n of m conditions met", bar-close countdown
 *   StageTimeline      activation -> scheduling -> tick -> signal -> risk ->
 *                      order -> position, with heartbeats and verbatim codes
 *
 * Nothing here starts by itself: every loop is an explicit operator action and
 * every number comes from the backend.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api, fmt } from "../api.js";
import { arr, NA_TEXT, numOrNull, objOrNull, rows as safeRows, txt } from "../lib/safe.js";
import { Badge, Card, ConfirmModal, Kpi, SectionTitle, StateBlock, Star, useInterval, parseNodeId } from "./ui.jsx";

/* --------------------------------------------------------------- risk strip */
export function RiskStrip({ nodes, onChanged, lab }) {
  const [status, setStatus] = useState(null);
  const [limits, setLimits] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);
  const [globalPct, setGlobalPct] = useState("");
  const [saving, setSaving] = useState("");
  const [note, setNote] = useState(null);
  const [market, setMarket] = useState(null);

  const load = useCallback(async () => {
    try {
      const [st, mk] = await Promise.all([
        api.liveTestingStatus().catch(() => null),
        api.liveTestingMarket().catch(() => null),
      ]);
      if (st) { setStatus(st); setLimits(objOrNull(st.risk)); setGlobalPct(String(numOrNull(st.risk?.risk_pct_default) ?? "")); }
      if (mk) setMarket(mk);
      if (!st) setErr("Live Testing status is unavailable.");
    } catch (e) { setErr(e.message || String(e)); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const equity = numOrNull(lab?.equity ?? market?.equity);
  const pctNum = numOrNull(globalPct);
  const cashRisk = equity !== null && pctNum !== null ? (equity * pctNum) / 100 : null;
  const min = numOrNull(limits?.risk_pct_min) ?? 0.05;
  const max = numOrNull(limits?.risk_pct_max) ?? 5;

  const saveGlobal = async () => {
    setSaving("global"); setNote(null);
    try {
      const res = await api.liveTestingSettings({ risk_pct_default: pctNum });
      setLimits(objOrNull(res?.live_testing) || limits);
      setNote({ ok: true, text: `Global risk per trade set to ${pctNum}%.` });
      await load(); onChanged?.();
    } catch (e) {
      setNote({ ok: false, text: `Global risk update failed: ${e.message || e}` });
    } finally { setSaving(""); }
  };

  const setNodeRisk = async (sid, value) => {
    setSaving(`n${sid}`); setNote(null);
    try {
      await api.liveTestingNodeConfig(sid, { risk_pct: value });
      setNote({ ok: true, text: `Node #${sid} risk ${value === null ? "reset to the global default" : `set to ${value}%`}.` });
      await load(); onChanged?.();
    } catch (e) {
      setNote({ ok: false, text: `Node #${sid} risk update failed: ${e.message || e}` });
    } finally { setSaving(""); }
  };

  const override = (n) => numOrNull(n?.risk_pct ?? n?.config?.risk_pct);
  const nodeList = safeRows(nodes).length ? safeRows(nodes) : safeRows(status?.nodes);

  return (
    <Card title="Global risk & execution sizing"
          right={<span className="muted" style={{ fontSize: 11.5 }}>
            allowed {min}%–{max}% · change is validated by the backend
          </span>}>
      <StateBlock loading={loading} error={err} onRetry={load}>
        <div className="kit-strip">
          <div className="item">
            <span className="k">Risk per trade %</span>
            <input value={globalPct} onChange={(e) => setGlobalPct(e.target.value)} style={{ width: 90 }} className="mono" />
          </div>
          <div className="item">
            <span className="k">&nbsp;</span>
            <button className="btn primary" disabled={saving === "global" || pctNum === null
              || pctNum < min || pctNum > max} onClick={saveGlobal}>
              {saving === "global" ? "saving…" : "Apply global risk"}
            </button>
          </div>
          <div className="item"><span className="k">Account equity</span>
            <span className="v mono">{equity === null ? NA_TEXT : fmt.currency(equity)}</span></div>
          <div className="item"><span className="k">Cash risk per trade</span>
            <span className="v mono" style={{ color: "var(--amber)" }}>{cashRisk === null ? NA_TEXT : fmt.currency(cashRisk)}</span></div>
          <div className="item"><span className="k">Active default for execution</span>
            <span className="v mono">{txt(limits?.risk_pct_default, NA_TEXT)}%</span></div>
          <div className="item"><span className="k">Max active trades</span>
            <span className="v mono">{txt(limits?.max_active_trades, NA_TEXT)}</span></div>
          <div className="item"><span className="k">Stop loss required</span>
            <span className="v">{limits?.require_sl ? <Badge tone="ok">YES</Badge> : <Badge tone="warn">NO</Badge>}</span></div>
        </div>
        {pctNum !== null && (pctNum < min || pctNum > max) && (
          <div className="kit-inline-err">Risk must be between {min}% and {max}% — the backend rejects anything outside that range.</div>
        )}
        {note && <div className={note.ok ? "kit-ok" : "kit-inline-err"}>{note.text}</div>}

        <SectionTitle hint="A node override is highlighted; Reset puts the node back on the global default.">
          Per-node risk
        </SectionTitle>
        {nodeList.length === 0 ? (
          <div className="muted" style={{ fontSize: 12 }}>
            No nodes are enrolled for live testing yet. Start one below (or start the shortlist) — dead and legacy nodes can never be enrolled.
          </div>
        ) : (
          <div className="kit-scroll">
            <table className="tbl">
              <thead><tr><th>Node</th><th>Status</th><th>Risk %</th><th>Source</th><th>Change</th><th /></tr></thead>
              <tbody>
                {nodeList.map((n) => {
                  const sid = parseNodeId(n?.strategy_id ?? n?.id ?? n?.node_id);
                  const ov = override(n);
                  const custom = ov !== null && ov !== undefined;
                  return (
                    <tr key={sid} className={custom ? "kit-row-hl" : ""}>
                      <td className="mono">#{txt(sid)}</td>
                      <td>{txt(n?.status, "—")}</td>
                      <td className="mono" style={{ color: custom ? "var(--amber)" : undefined }}>
                        {txt(custom ? ov : limits?.risk_pct_default, NA_TEXT)}
                      </td>
                      <td>{custom ? <Badge tone="warn">custom</Badge> : <Badge tone="mute">global default</Badge>}</td>
                      <td>
                        <input style={{ width: 80 }} className="mono" placeholder="%"
                               onKeyDown={(e) => {
                                 if (e.key === "Enter") {
                                   const v = numOrNull(e.target.value);
                                   if (v !== null) setNodeRisk(sid, v);
                                 }
                               }} />
                      </td>
                      <td>
                        <button className="btn" disabled={saving === `n${sid}` || !custom}
                                title={!custom ? "this node has no override — it already uses the global risk" :
                                       saving === `n${sid}` ? "saving the override…" :
                                       "remove the override and use the global risk"}
                                onClick={() => setNodeRisk(sid, null)}>Reset</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </StateBlock>
    </Card>
  );
}

/* -------------------------------------------------------- manual order panel */
export function ManualOrderPanel({ defaultSymbol = "XAUUSD" }) {
  /* V5 §13/§14 — manual MT5 order panel.
   *
   * The browser does no trading maths: every lot size, every money estimate and
   * every price comes back from the backend preview, which uses the broker's own
   * symbol specification. The operator can work in pips (broker independent,
   * default 300) or in exact prices, and can drive the size from money-at-risk or
   * from a lot size — both directions go through the same backend call.
   *
   * The order is sent only after the confirmation dialog, and the panel then
   * reports the broker's own answer: retcode, ticket, fill price, volume, the
   * SL/TP the broker accepted and its message.
   */
  const [form, setForm] = useState({
    symbol: defaultSymbol, side: "BUY", risk: "10", lots: "",
    sl_pips: "300", tp_pips: "600", sl: "", tp: "", entry: "",
  });
  const [preview, setPreview] = useState(null);
  const [market, setMarket] = useState(null);
  const [state, setState] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState(null);
  const [confirming, setConfirming] = useState(null);
  const [sizeMode, setSizeMode] = useState("risk");     // "risk" | "lots"
  // when the backend preview was last computed — makes the automatic refresh
  // (and the optional manual "Recalculate now") observable instead of invisible
  const [previewAt, setPreviewAt] = useState("");
  const up = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  /* §3 — the entry reference is the operator's when they type one, otherwise the
   * live quote for the side (BUY fills at the ask, SELL at the bid). It is never
   * silently 0: when neither exists the preview says why. */
  const quotedEntry = numOrNull(market?.bid) !== null && numOrNull(market?.ask) !== null
    ? (form.side === "BUY" ? numOrNull(market.ask) : numOrNull(market.bid))
    : numOrNull(market?.ask ?? market?.bid);
  const typedEntry = numOrNull(form.entry);
  const entry = typedEntry !== null ? typedEntry : quotedEntry;
  const entrySource = typedEntry !== null ? "entered by the operator"
    : (quotedEntry !== null ? `live ${form.side === "BUY" ? "ask" : "bid"}` : null);
  const levels = objOrNull(preview?.levels);
  const sizing = objOrNull(preview?.sizing);
  const estimate = objOrNull(preview?.estimate);
  const blocked = objOrNull(preview?.blocked);
  const symbolInfo = objOrNull(preview?.symbol_info);
  const defaults = objOrNull(preview?.defaults);

  /* Market + execution state refresh (read-only).
   *
   * V5.1a §13 — the quote is polled, not read once: the entry price the operator
   * sees is the *current* live quote for the side (BUY→ask, SELL→bid), so a moving
   * market is reflected without pressing anything. The preview downstream depends
   * on `entry`, so it re-sizes on the new quote automatically. */
  useEffect(() => {
    let alive = true;
    const tick = () => {
      api.mt5ExecutionState().then((r) => alive && setState(r)).catch(() => alive && setState(null));
      api.liveTestingMarketHeader(form.symbol)
        .then((r) => { if (!alive) return; setMarket((r?.market) || null); })
        .catch(() => alive && setMarket(null));
    };
    tick();
    const t = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(t); };
  }, [form.symbol]);

  /* The preview is the single source of truth for the panel: it is re-run
   * whenever an input changes, so what is displayed is what the backend would
   * size and send at that moment. */
  const runPreview = useCallback(async () => {
    setBusy("preview"); setErr(null);
    try {
      const payload = {
        symbol: form.symbol, side: form.side, entry,
        sl_pips: numOrNull(form.sl_pips), tp_pips: numOrNull(form.tp_pips),
        sl: numOrNull(form.sl), tp: numOrNull(form.tp),
      };
      if (sizeMode === "risk") {
        const risk = numOrNull(form.risk);
        if (risk !== null) payload.risk_amount = risk;
      } else {
        const lots = numOrNull(form.lots);
        if (lots !== null) payload.volume = lots;
      }
      const res = await api.mt5ExecutionPreview(payload);
      setPreview(res);
      setPreviewAt(new Date().toISOString().slice(11, 19) + "Z");
      /* Only the *dependent* field is filled in. Writing the result back into
       * the field the operator is editing would make the value drift away from
       * what they typed (10 -> 9.94 -> 9.88 …) — the input stays theirs. */
      if (sizeMode === "risk") {
        if (res?.sizing?.volume !== undefined && res?.sizing?.volume !== null) {
          up("lots", String(res.sizing.volume));
        }
      } else if (res?.sizing?.actual_risk !== undefined && res?.sizing?.actual_risk !== null) {
        up("risk", String(res.sizing.actual_risk));
      }
    } catch (e) {
      setErr(e.message || String(e)); setPreview(null);
    } finally { setBusy(""); }
  }, [form.symbol, form.side, form.sl_pips, form.tp_pips, form.sl, form.tp, form.risk,
      form.lots, sizeMode, entry]);

  /* §3 — any input change recalculates, immediately (250 ms debounce). The
   * request is sent even when the entry price is not resolvable: the backend then
   * answers with the precise reason (INVALID_ENTRY_PRICE, MT5_…), which is far
   * more useful than an empty panel. */
  useEffect(() => {
    const t = setTimeout(() => { runPreview(); }, 250);
    return () => clearTimeout(t);
  }, [runPreview]);

  const place = async (side) => {
    const useSide = side || form.side;
    setBusy("place"); setErr(null); setResult(null);
    try {
      const res = await api.mt5ExecutionPlace({
        symbol: form.symbol, side: useSide,
        volume: numOrNull(sizing?.volume) ?? numOrNull(form.lots),
        sl: numOrNull(levels?.sl) ?? numOrNull(form.sl),
        tp: numOrNull(levels?.tp) ?? numOrNull(form.tp),
        confirm: state?.confirmation_phrase || "PLACE_DEMO_ORDER",
        risk_amount: numOrNull(form.risk),
        client_order_id: `${form.symbol}-${useSide}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
      });
      setResult(res);
    } catch (e) {
      setErr(e.message || String(e));
    } finally { setBusy(""); setConfirming(null); }
  };

  const executionAllowed = state?.execution_allowed === true;
  const spread = market?.spread !== undefined && market?.spread !== null ? market.spread : null;
  const warnings = arr(levels?.warnings);

  return (
    <Card title="Manual order panel"
          right={
            <div className="row-bar" style={{ margin: 0 }}>
              <Badge tone={String(market?.source || "").toUpperCase() === "MT5" ? "real" : "sim"}>
                {String(market?.source || "").toUpperCase() === "MT5" ? "REAL MT5" : "SIMULATOR"}
              </Badge>
              <Badge tone={executionAllowed ? "ok" : "warn"}>
                {executionAllowed ? "DEMO EXECUTION ALLOWED" : "DEMO ONLY"}
              </Badge>
            </div>
          }>
      {confirming && (
        <ConfirmModal
          open
          tone="danger"
          confirmTone="danger"
          title={`Confirm ${confirming.side} demo order`}
          confirmLabel={`Send ${confirming.side} order`}
          busy={busy === "place"}
          result={err ? { ok: false, error: err } : result}
          dangerText="DEMO ACCOUNT ONLY — this sends ONE market order to the demo terminal. No automatic retry follows."
          onCancel={() => setConfirming(null)}
          onConfirm={() => place(confirming.side)}
        >
          <div>
            <div className="muted" style={{ marginBottom: 6, fontSize: 12 }}>
              Check every field — a submitted order cannot be retried or undone by this panel.
            </div>
            <div className="kit-kv"><span className="k">Account</span>
              <span>{txt(state?.account?.login, "no account")} · <b>{txt(state?.account?.type || state?.account_type, "DEMO ONLY")}</b></span></div>
            <div className="kit-kv"><span className="k">Symbol / side</span><span className="mono">{form.symbol} {confirming.side}</span></div>
            <div className="kit-kv"><span className="k">Volume</span><span className="mono">{txt(sizing?.volume ?? form.lots, "not calculated")}</span></div>
            <div className="kit-kv"><span className="k">Entry reference</span><span className="mono">{entry === null ? NA_TEXT : fmt.num(entry, 2)}</span></div>
            <div className="kit-kv"><span className="k">Stop loss</span>
              <span className="mono">{levels?.sl === undefined || levels?.sl === null
                ? txt(form.sl, "missing — an order without SL is refused")
                : `${fmt.num(levels.sl, 2)} (${txt(levels.sl_pips, NA_TEXT)} pips)`}</span></div>
            <div className="kit-kv"><span className="k">Take profit</span>
              <span className="mono">{levels?.tp === undefined || levels?.tp === null
                ? txt(form.tp, "not set")
                : `${fmt.num(levels.tp, 2)} (${txt(levels.tp_pips, NA_TEXT)} pips)`}</span></div>
            <div className="kit-kv"><span className="k">Estimated loss at SL</span>
              <span className="mono">{txt(estimate?.loss_at_sl, NA_TEXT)}</span></div>
            <div className="kit-kv"><span className="k">Estimated profit at TP</span>
              <span className="mono">{txt(estimate?.profit_at_tp, NA_TEXT)}</span></div>
            <div className="kit-kv"><span className="k">Spread now</span>
              <span className="mono">{spread === null ? NA_TEXT : fmt.num(spread, 2)}</span></div>
          </div>
        </ConfirmModal>
      )}

      <div className="warn-banner" style={{ marginBottom: 10 }}>
        <b>Amount = money at risk if the stop loss is hit.</b> It is <u>not</u> margin. Lot size is
        always rounded <b>down</b> to the broker's permitted step by the backend.
        {defaults && (
          <span className="muted"> Defaults: {txt(defaults.sl_pips, "—")} pip stop · {txt(defaults.risk_amount, "—")} {txt(defaults.symbol, "")} — all editable.</span>
        )}
      </div>

      <div className="kit-cols">
        <div style={{ flex: "1 1 220px" }}>
          <label className="fld">Symbol
            <input value={form.symbol} onChange={(e) => up("symbol", e.target.value.toUpperCase())} className="mono" />
          </label>
          {/* §11 — the side is part of the calculation, not just of the order:
              a BUY is sized from the live ASK, a SELL from the live BID. The
              operator can therefore check both directions here even while the
              send buttons are blocked; sending still requires the gates. */}
          <label className="fld">Side <span className="muted">(BUY sizes from ask, SELL from bid)</span>
            <select value={form.side} onChange={(e) => up("side", e.target.value)} className="mono">
              <option value="BUY">BUY — sized from the live ask</option>
              <option value="SELL">SELL — sized from the live bid</option>
            </select>
          </label>
          <label className="fld">Amount / risk (money)
            <input value={form.risk} onChange={(e) => { setSizeMode("risk"); up("risk", e.target.value); }} className="mono" placeholder="e.g. 10" />
          </label>
          <label className="fld">Stop loss <span className="muted">(pips — required)</span>
            <input value={form.sl_pips} onChange={(e) => { up("sl_pips", e.target.value); up("sl", ""); }} className="mono" placeholder="300" />
          </label>
          <label className="fld">Stop loss <span className="muted">(price, overrides pips)</span>
            <input value={form.sl} onChange={(e) => up("sl", e.target.value)} className="mono" placeholder="optional" />
          </label>
          <label className="fld">Entry <span className="muted">(price, overrides the quote)</span>
            <input value={form.entry} onChange={(e) => up("entry", e.target.value)} className="mono"
                   placeholder={quotedEntry !== null ? `live: ${quotedEntry}` : "no quote — type one"} />
          </label>
        </div>
        <div style={{ flex: "1 1 220px" }}>
          <label className="fld">Lot size
            <input value={form.lots} onChange={(e) => { setSizeMode("lots"); up("lots", e.target.value); }} className="mono" placeholder="e.g. 0.05" />
          </label>
          <label className="fld">Take profit <span className="muted">(pips)</span>
            <input value={form.tp_pips} onChange={(e) => { up("tp_pips", e.target.value); up("tp", ""); }} className="mono" placeholder="600" />
          </label>
          <label className="fld">Take profit <span className="muted">(price, overrides pips)</span>
            <input value={form.tp} onChange={(e) => up("tp", e.target.value)} className="mono" placeholder="optional" />
          </label>
        </div>
        <div style={{ flex: "1 1 200px" }}>
          <div className="kit-kv"><span className="k">Bid</span><span className="mono">{txt(market?.bid, NA_TEXT)}</span></div>
          <div className="kit-kv"><span className="k">Ask</span><span className="mono">{txt(market?.ask, NA_TEXT)}</span></div>
          <div className="kit-kv"><span className="k">Spread</span><span className="mono">{spread === null ? NA_TEXT : fmt.num(spread, 4)}</span></div>
          <div className="kit-kv"><span className="k">Quote time</span><span className="mono">{txt(market?.time, NA_TEXT)}</span></div>
          <div className="kit-kv"><span className="k">Broker min / step</span>
            <span className="mono">{txt(symbolInfo?.volume_min, NA_TEXT)} / {txt(symbolInfo?.volume_step, NA_TEXT)}</span></div>
          <div className="kit-kv"><span className="k">Est. loss / profit</span>
            <span className="mono">{txt(estimate?.loss_at_sl, NA_TEXT)} / {txt(estimate?.profit_at_tp, NA_TEXT)}</span></div>
          <div className="kit-kv"><span className="k">Reward : risk</span><span className="mono">{txt(estimate?.reward_risk, NA_TEXT)}</span></div>
          <div className="kit-kv"><span className="k">Entry used</span>
            <span className="mono">{entry === null ? NA_TEXT : fmt.num(entry, 2)}
              {entrySource ? <span className="muted"> ({entrySource})</span> : null}</span></div>
        </div>
      </div>

      {/* §3 — the broker's own specification, always visible: these are the numbers
       *  the sizing maths uses, so the operator can check them rather than trust us. */}
      <div className="kit-strip" style={{ border: "none", padding: 0, marginTop: 6, flexWrap: "wrap" }}>
        <div className="item"><span className="k">Symbol</span><span className="v mono">{txt(symbolInfo?.symbol, form.symbol)}</span></div>
        <div className="item"><span className="k">Digits</span><span className="v mono">{txt(symbolInfo?.digits, NA_TEXT)}</span></div>
        <div className="item"><span className="k">Point</span><span className="v mono">{txt(symbolInfo?.point, NA_TEXT)}</span></div>
        <div className="item"><span className="k">Tick size</span><span className="v mono">{txt(symbolInfo?.tick_size, NA_TEXT)}</span></div>
        <div className="item"><span className="k">Tick value</span><span className="v mono">{txt(symbolInfo?.tick_value, NA_TEXT)}</span></div>
        <div className="item"><span className="k">Volume min / step / max</span>
          <span className="v mono">{txt(symbolInfo?.volume_min, NA_TEXT)} / {txt(symbolInfo?.volume_step, NA_TEXT)} / {txt(symbolInfo?.volume_max, NA_TEXT)}</span></div>
        <div className="item"><span className="k">Spec source</span>
          <span className="v mono">{txt(symbolInfo?.source, NA_TEXT)}</span></div>
        <div className="item"><span className="k">Margin (per lot)</span>
          <span className="v mono">{txt(symbolInfo?.margin_per_lot ?? symbolInfo?.margin_initial, "not reported by this bridge")}</span></div>
      </div>
      {!symbolInfo && (
        <div className="kit-inline-err" style={{ marginTop: 6, fontSize: 11.5 }}>
          The broker symbol specification is not available yet, so no lot size can be calculated.
          {state?.blocked_reason ? ` (${txt(state.blocked_code, "MT5_UNAVAILABLE")}: ${state.blocked_reason})` : ""}
        </div>
      )}

      <div className="btn-row">
        <button className="btn" disabled={busy === "preview"} onClick={() => runPreview()}
                title="Runs exactly the same backend preview the panel already runs automatically (250 ms after any change).">
          {busy === "preview" ? "calculating…" : "Recalculate now"}
        </button>
        <button className="btn success" disabled={busy === "place" || !executionAllowed}
                onClick={() => { up("side", "BUY"); setConfirming({ side: "BUY" }); }}
                title={executionAllowed ? "Review and confirm the demo order" : txt(state?.blocked_reason, "Demo execution is not allowed")}>
          BUY
        </button>
        <button className="btn danger" disabled={busy === "place" || !executionAllowed}
                onClick={() => { up("side", "SELL"); setConfirming({ side: "SELL" }); }}
                title={executionAllowed ? "Review and confirm the demo order" : txt(state?.blocked_reason, "Demo execution is not allowed")}>
          SELL
        </button>
        <span className="muted" style={{ alignSelf: "center", fontSize: 11.5 }}>
          {previewAt ? `calculated ${previewAt} · ` : ""}
          {executionAllowed ? "Execution path is ALLOWED."
            : `Execution blocked by the backend: ${txt(state?.blocked_code, "MT5_UNAVAILABLE")} — ${txt(state?.blocked_reason, "no real demo terminal is connected")}`}
        </span>
      </div>

      {warnings.length > 0 && (
        <div className="kit-inline-err" style={{ marginTop: 8 }}>
          {warnings.map((w, i) => <div key={i}>• {w}</div>)}
        </div>
      )}

      {preview && (
        <div className="panel" style={{ marginTop: 8 }}>
          <div className="kit-head">
            <div style={{ fontSize: 12.5, fontWeight: 650 }}>
              {preview.mode === "risk_to_lot" ? "Risk → lot result" : "Lot → risk result"}
            </div>
            <Badge tone={preview.ok ? "ok" : "danger"}>{preview.ok ? "CALCULATED BY BACKEND" : txt(blocked?.code, "BLOCKED")}</Badge>
          </div>
          {blocked && (
            <div className="kit-inline-err">
              <b>{txt(blocked.code)}</b> — {txt(blocked.message)}
              {numOrNull(blocked.detail?.volume_min) !== null && (
                <div style={{ marginTop: 4 }}>
                  Entered risk: <span className="mono">{fmt.num(blocked.detail.risk_amount, 2)}</span> ·
                  minimum lot: <span className="mono">{fmt.num(blocked.detail.volume_min, 4)}</span> ·
                  minimum required risk:{" "}
                  <span className="mono">{fmt.num((numOrNull(blocked.detail.volume_min) ?? 0) * (numOrNull(blocked.detail.risk_per_lot) ?? 0), 2)}</span>
                </div>
              )}
            </div>
          )}
          {sizing && (
            <div className="kit-strip" style={{ border: "none", padding: 0 }}>
              <div className="item"><span className="k">Lot size (rounded down)</span><span className="v mono">{fmt.num(sizing.volume, 4)}</span></div>
              <div className="item"><span className="k">Raw lots</span><span className="v mono">{fmt.num(sizing.raw_volume, 6)}</span></div>
              <div className="item"><span className="k">Risk per lot</span><span className="v mono">{fmt.num(sizing.risk_per_lot, 2)}</span></div>
              <div className="item"><span className="k">{sizing.actual_risk !== undefined ? "Monetary risk (actual)" : "Monetary risk"}</span>
                <span className="v mono">{txt(sizing.actual_risk ?? sizing.risk_amount, NA_TEXT)}</span></div>
              <div className="item"><span className="k">Risk distance (SL)</span><span className="v mono">{fmt.num(sizing.stop_distance, 2)}</span></div>
              <div className="item"><span className="k">Reward distance (TP)</span>
                <span className="v mono">{numOrNull(levels?.tp) === null || numOrNull(levels?.entry) === null
                  ? NA_TEXT
                  : fmt.num(Math.abs((numOrNull(levels.tp) ?? 0) - (numOrNull(levels.entry) ?? 0)), 2)}</span></div>
              <div className="item"><span className="k">SL / TP price</span>
                <span className="v mono">{txt(levels?.sl, NA_TEXT)} / {txt(levels?.tp, NA_TEXT)}</span></div>
              <div className="item"><span className="k">SL / TP pips</span>
                <span className="v mono">{txt(levels?.sl_pips, NA_TEXT)} / {txt(levels?.tp_pips, NA_TEXT)}</span></div>
              <div className="item"><span className="k">Pip size</span><span className="v mono">{txt(levels?.pip_size, NA_TEXT)}</span></div>
              <div className="item"><span className="k">Est. loss / profit</span>
                <span className="v mono">{txt(estimate?.loss_at_sl, NA_TEXT)} / {txt(estimate?.profit_at_tp, NA_TEXT)}</span></div>
              <div className="item"><span className="k">Estimate basis</span><span className="v" style={{ fontSize: 11 }}>{txt(estimate?.basis, "broker tick data unavailable")}</span></div>
              <div className="item"><span className="k">Broker min / step</span>
                <span className="v mono">{txt(sizing.volume_min, NA_TEXT)} / {txt(sizing.volume_step, NA_TEXT)}</span></div>
            </div>
          )}
          {sizing && sizing.below_minimum && (
            <div className="kit-inline-err" style={{ marginTop: 6 }}>
              Below the broker minimum: {fmt.num(sizing.volume, 4)} lots. Minimum risk for a legal lot is{" "}
              <b>{fmt.num(sizing.minimum_risk, 2)}</b>.
            </div>
          )}
        </div>
      )}

      {err && <div className="kit-inline-err">{err}</div>}

      {result && (
        <div className={result?.ok === false ? "kit-inline-err" : "kit-ok"} style={{ marginTop: 8 }}>
          <div className="kit-head">
            <b>{txt(result.label, result.ok ? "ORDER RESULT" : "ORDER NOT EXECUTED")}</b>
            <Badge tone={result.ok ? "ok" : "danger"}>{txt(result.status, "UNKNOWN")}</Badge>
          </div>
          <div className="kit-strip" style={{ border: "none", padding: 0 }}>
            <div className="item"><span className="k">Retcode</span><span className="v mono">{txt(result.broker?.retcode ?? result.retcode, NA_TEXT)}</span></div>
            <div className="item"><span className="k">Ticket</span>
              <span className="v mono">{txt(result.order?.ticket ?? result.order_ticket ?? result.ticket, "no ticket returned")}</span></div>
            <div className="item"><span className="k">Fill price</span><span className="v mono">{txt(result.execution?.exec_price, NA_TEXT)}</span></div>
            <div className="item"><span className="k">Volume</span>
              <span className="v mono">{txt(result.execution?.volume ?? result.volume, NA_TEXT)}</span></div>
            <div className="item"><span className="k">SL requested / broker</span>
              <span className="v mono">{txt(result.execution?.sl_requested, NA_TEXT)} / {txt(result.execution?.sl_broker, NA_TEXT)}</span></div>
            <div className="item"><span className="k">TP requested / broker</span>
              <span className="v mono">{txt(result.execution?.tp_requested, NA_TEXT)} / {txt(result.execution?.tp_broker, NA_TEXT)}</span></div>
            <div className="item"><span className="k">SL/TP verified</span><span className="v mono">{txt(result.execution?.sl_tp_verified, NA_TEXT)}</span></div>
          </div>
          <div style={{ marginTop: 4, fontSize: 11.5 }}>
            <b>Broker message:</b> {txt(result.broker?.message ?? result.result_message ?? result.message, "none")}
            {result.broker?.warning ? <div className="muted">{result.broker.warning}</div> : null}
          </div>
        </div>
      )}
    </Card>
  );
}

/* -------------------------------------------------------- live market panel */
export function LiveMarketPanel({ nodes, engineRunning }) {
  /* Read-only per-condition truth table from the engine itself (§7). */
  const [conditions, setConditions] = useState(null);
  const [market, setMarket] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);
  const [events, setEvents] = useState([]);
  const [tick, setTick] = useState(0);

  const load = useCallback(async () => {
    try {
      const [mk, lg, cd] = await Promise.all([
        api.liveTestingMarket().catch((e) => { throw e; }),
        api.liveTestingLog({ limit: 40 }).catch(() => ({ events: [] })),
        api.liveTestingConditions().catch(() => null),
      ]);
      setMarket(mk); setEvents(arr(lg?.events)); setConditions(cd); setErr(null);
    } catch (e) { setErr(e.message || String(e)); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useInterval(() => { load(); setTick((t) => t + 1); }, 2000, true);   // ~2s refresh

  const secondsToBarClose = useMemo(() => {
    const tf = String(nodes?.[0]?.genome?.timeframe || "M15");
    const mins = { M1: 1, M5: 5, M15: 15, M30: 30, H1: 60, H4: 240 }[tf] || 15;
    const ts = numOrNull(market?.ts) || (Date.now() / 1000);
    const period = mins * 60;
    return Math.max(0, Math.round(period - (ts % period)));
  }, [market, nodes, tick]);

  // indicator usage across the enrolled nodes (derived from the genome payload)
  const indicatorUsage = useMemo(() => {
    const counts = {};
    arr(nodes).forEach((n) => {
      arr(n?.genome?.features).forEach((f) => {
        const name = String(f).split(":")[0].toUpperCase();
        counts[name] = (counts[name] || 0) + 1;
      });
    });
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [nodes]);

  // per-node entry conditions + the last evaluated signal for that node
  const signalByNode = useMemo(() => {
    const map = {};
    events.forEach((e) => {
      const stage = String(e?.stage || "");
      if (stage.includes("SIGNAL")) map[e?.node_id] = e;
    });
    return map;
  }, [events]);

  return (
    <div className="grid cols-2">
      <Card title={`Live market — ${txt(market?.symbol, "—")}`}
            right={<span className="muted" style={{ fontSize: 11 }}>
              refreshing every ~2s · {txt(market?.source, "unknown source")}
            </span>}>
        <StateBlock loading={loading} error={err} onRetry={load}>
          <div className="kit-strip" style={{ border: "none", padding: 0 }}>
            <div className="item"><span className="k">Bid</span><span className="v mono">{market?.bid === null || market?.bid === undefined ? NA_TEXT : fmt.num(market.bid, 3)}</span></div>
            <div className="item"><span className="k">Ask</span><span className="v mono">{market?.ask === null || market?.ask === undefined ? NA_TEXT : fmt.num(market.ask, 3)}</span></div>
            <div className="item"><span className="k">Spread</span><span className="v mono">{market?.spread === null || market?.spread === undefined ? NA_TEXT : fmt.num(market.spread, 3)}</span></div>
            <div className="item"><span className="k">Session</span><span className="v">{txt(market?.session_status, "—")}</span></div>
            <div className="item"><span className="k">Tick age</span><span className="v mono">{txt(market?.tick_age_s, "—")}s</span></div>
            <div className="item"><span className="k">Next bar close</span>
              <span className="v mono" style={{ color: "var(--cyan)" }}>~{secondsToBarClose}s</span></div>
          </div>
          {market?.reasons && arr(market.reasons).length > 0 && (
            <div className="kit-inline-err" style={{ marginTop: 8 }}>
              {arr(market.reasons).map((r, i) => <div key={i}>{txt(r)}</div>)}
            </div>
          )}
          <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
            Trading available: {market?.trading_available ? <Badge tone="ok">YES</Badge> : <Badge tone="mute">NO</Badge>}
            {" · "}market open: {market?.market_open ? "yes" : "no"}
          </div>
        </StateBlock>
      </Card>

      <Card title="Active indicators & entry conditions">
        <StateBlock loading={loading} error={err}>
          {indicatorUsage.length === 0 ? (
            <div className="muted" style={{ fontSize: 12 }}>No node is enrolled, so no indicator is active.</div>
          ) : (
            <>
              <div>{indicatorUsage.map(([name, count]) => (
                <span className="kit-chip" key={name}>{name} <b>{count}</b> node{count === 1 ? "" : "s"}</span>
              ))}</div>
              {(conditions?.excluded || []).length > 0 && (
                <div className="kit-inline-err" style={{ marginTop: 6 }}>
                  {(conditions.excluded || []).length} enrolled node(s) can never trade and are not evaluated
                  (for example: {txt(arr(conditions.excluded)[0]?.reason, "not eligible")}).
                </div>
              )}
              <div className="kit-scroll" style={{ marginTop: 8 }}>
                <table className="tbl">
                  <thead><tr><th>Node</th><th>Entry conditions</th><th>Met</th><th>State</th></tr></thead>
                  <tbody>
                    {arr(conditions?.nodes).length > 0 && arr(conditions.nodes).map((c) => (
                      <tr key={`cond-${c.node_id}`}>
                        <td className="mono">#{txt(c.node_id)}</td>
                        <td>
                          {c.available === false ? (
                            <span className="muted">{txt(c.reason, "condition states unavailable")}</span>
                          ) : arr(c.sides).map((sideRow) => (
                            <div key={`${c.node_id}-${sideRow.side}`} style={{ marginBottom: 4 }}>
                              <b className="mono" style={{ fontSize: 11 }}>{sideRow.side}</b>
                              {arr(sideRow.conditions).map((cond, i) => (
                                <div key={i} className="mono" style={{ fontSize: 11 }}>
                                  <span style={{ color: cond.met ? "var(--green)" : "var(--muted)" }}>
                                    {cond.met === true ? "✓" : cond.met === false ? "✕" : "?"}
                                  </span>{" "}
                                  {txt(cond.condition, "unrenderable clause")}
                                  {cond.error ? <span className="muted"> — {cond.error}</span> : null}
                                </div>
                              ))}
                              {sideRow.gate_ok === false && (
                                <div className="muted" style={{ fontSize: 11 }}>
                                  session / day / regime gate is closed on this bar
                                </div>
                              )}
                            </div>
                          ))}
                        </td>
                        <td className="mono">
                          {arr(c.sides).length === 0 ? NA_TEXT
                            : arr(c.sides).map((s2) => `${s2.met_count} of ${s2.total_count}`).join(" / ")}
                        </td>
                        <td>
                          {arr(c.sides).some((s2) => s2.signal)
                            ? <Badge tone="ok">SIGNAL</Badge>
                            : engineRunning ? <Badge tone="mute">evaluated, no signal</Badge>
                              : <Badge tone="mute">idle</Badge>}
                        </td>
                      </tr>
                    ))}
                    {safeRows(nodes).map((n) => {
                      const sid = parseNodeId(n?.strategy_id ?? n?.id);
                      const long = arr(n?.genome?.entry_long ?? n?.genome?.entry_long_conditions);
                      const short = arr(n?.genome?.entry_short ?? n?.genome?.entry_short_conditions);
                      const clauses = [...long.map((c) => `L: ${typeof c === "string" ? c : JSON.stringify(c)}`),
                                       ...short.map((c) => `S: ${typeof c === "string" ? c : JSON.stringify(c)}`)];
                      const sig = signalByNode[sid];
                      if (arr(conditions?.nodes).length > 0) return null;   // the engine table is authoritative
                      return (
                        <tr key={sid}>
                          <td className="mono">#{txt(sid)}</td>
                          <td>
                            {clauses.length === 0 ? <span className="muted">{NA_TEXT}</span>
                              : clauses.slice(0, 4).map((c, i) => <div key={i} className="mono" style={{ fontSize: 11 }}>{c}</div>)}
                          </td>
                          <td className="muted">not reported</td>
                          <td>
                            {sig ? <Badge tone="ok">SIGNAL @ {txt(sig.detail?.bar_time, "closed bar")}</Badge>
                              : engineRunning ? <Badge tone="mute">evaluating…</Badge>
                                : <Badge tone="mute">not evaluated (engine idle)</Badge>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
                Condition states are reported by the running engine per closed bar. The engine evaluates{" "}
                <b>{arr(nodes).length}</b> node(s); {Object.keys(signalByNode).length} produced a signal in the last{" "}
                {arr(events).length} stage events.
              </div>
            </>
          )}
        </StateBlock>
      </Card>
    </div>
  );
}

/* -------------------------------------------------------------- stage timeline */
const TIMELINE = [
  ["ACTIVATION", "Activation"],
  ["SCHEDULING", "Scheduling"],
  ["MARKET VALIDATED", "Tick / Market"],
  ["SIGNAL DETECTED", "Signal"],
  ["RISK CALCULATED", "Risk Check"],
  ["VOLUME CALCULATED", "Sizing"],
  ["ORDER VALIDATED", "Order Placement"],
  ["ORDER SENT", "Order Sent"],
  ["BROKER RESPONSE", "Order Result"],
  ["POSITION VERIFIED", "Position"],
  ["POSITION CLOSED", "Position Closed"],
  ["BLOCKED", "Blocked"],
  ["REJECTED", "Rejected"],
];

export function StageTimeline({ nodes }) {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [filter, setFilter] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await api.liveTestingLog({ limit: 80 });
      setEvents(arr(res?.events)); setErr(null);
    } catch (e) { setErr(e.message || String(e)); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useInterval(load, 2000, true);

  const shown = filter
    ? events.filter((e) => String(e?.stage || "").includes(filter) || String(e?.node_id) === String(parseNodeId(filter)))
    : events;
  const last = events.length ? events[0] : null;
  const seenStages = new Set(events.map((e) => String(e?.stage || "")));

  return (
    <Card title="Stage logging & heartbeat"
          right={<span className="muted" style={{ fontSize: 11 }}>
            {last ? <><span className="kit-live" />last event {txt(last.ts_iso || last.ts, "—")}</>
                  : <><span className="kit-dead" />no events yet — the loop has not run</>}
          </span>}>
      <div className="kit-track" style={{ marginBottom: 8 }}>
        {TIMELINE.map(([stage, label]) => (
          <div key={stage} className={"kit-track-step" + (seenStages.has(stage) ? " done" : "")}
               style={{ cursor: "default", flex: "1 1 92px", minWidth: 92 }}>
            <span className="dot">{seenStages.has(stage) ? "✓" : "·"}</span>
            <span className="lbl" style={{ fontSize: 11 }}>{label}</span>
            <span className="st">{seenStages.has(stage) ? "SEEN" : "—"}</span>
          </div>
        ))}
      </div>
      <input placeholder="filter by stage or node id" value={filter} onChange={(e) => setFilter(e.target.value)}
             style={{ width: 260 }} className="mono" />
      <div className="kit-scroll" style={{ marginTop: 8 }}>
        <StateBlock loading={loading} error={err} onRetry={load} empty={shown.length === 0}
                    emptyHint="No stage events recorded yet — activate Live Testing and start a node to see the loop work.">
          <table className="tbl">
            <thead><tr><th>Time</th><th>Node</th><th>Stage</th><th>Message</th><th>Code</th></tr></thead>
            <tbody>
              {shown.map((e, i) => (
                <tr key={i}>
                  <td className="mono" style={{ fontSize: 11 }}>{txt(e?.ts_iso || e?.ts, "—")}</td>
                  <td className="mono">#{txt(e?.node_id, "—")}</td>
                  <td>{txt(e?.stage, "—")}</td>
                  <td style={{ fontSize: 11.5 }}>{txt(e?.message, "—")}</td>
                  <td className="mono" style={{ fontSize: 11 }}>
                    {txt(e?.detail?.retcode ?? e?.detail?.code ?? e?.code, "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </StateBlock>
      </div>
      <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
        MT5 return codes and simulator warnings are shown exactly as the backend returned them —
        never translated, never rounded.
      </div>
    </Card>
  );
}

/* ------------------------------------------------------ V5 §11 market header */
export function LiveMarketHeader({ symbol, nodeId, onSymbolChange, onNodeChange, nodes = [] }) {
  /* Live MT5 market data header: symbol, bid/ask/spread/price/time, the timeframe
   * and the *selected node's own* indicator values at the last closed bar, with
   * each entry condition evaluated by the engine. Nothing is computed here — the
   * backend resolves the genome's indicators against real bars. */
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(false);
  const [sym, setSym] = useState(symbol || "XAUUSD");
  const [sid, setSid] = useState(nodeId || "");

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const res = await api.liveTestingMarketHeader(sym, sid ? Number(sid) : undefined);
      setData(res);
    } catch (e) { setErr(e); setData(null); } finally { setLoading(false); }
  }, [sym, sid]);

  useEffect(() => { load(); }, [load]);
  useInterval(load, 5000);

  const market = objOrNull(data?.market) || {};
  const node = objOrNull(data?.node) || {};
  const indicators = safeRows(data?.indicators);
  const conditions = safeRows(data?.conditions);
  const reasons = arr(data?.reasons);

  return (
    <Card
      title="Live MT5 market data"
      right={
        <div className="btn-row" style={{ margin: 0 }}>
          <select className="input mono" value={sid} onChange={(e) => {
            setSid(e.target.value);
            if (onNodeChange) onNodeChange(e.target.value);
          }}>
            <option value="">node: none selected</option>
            {safeRows(nodes).map((n) => (
              <option key={n.node_id} value={n.node_id}>
                Node_{n.node_id} · {txt(n.market)} {txt(n.timeframe)}
              </option>
            ))}
          </select>
          <button className="btn ghost" onClick={load} disabled={loading}>
            {loading ? "refreshing…" : "Refresh"}
          </button>
        </div>
      }
    >
      {err && <div className="kit-inline-err">{err.message || String(err)}</div>}
      {data?.available === false && (
        <div className="warn-banner" style={{ marginBottom: 8 }}>
          <b>MT5 market data is not available.</b>{" "}
          {reasons.length ? reasons.join("; ") : txt(data?.reason, "no connection to a real terminal")}
          <span className="muted"> Nothing is estimated or replayed from cached history.</span>
        </div>
      )}
      <div className="kit-strip" style={{ border: "none", padding: 0 }}>
        <div className="item"><span className="k">Symbol</span>
          <span className="v">
            <input className="input mono" style={{ width: 110 }} value={sym}
                   onChange={(e) => { setSym(e.target.value.toUpperCase()); if (onSymbolChange) onSymbolChange(e.target.value.toUpperCase()); }} />
          </span></div>
        <div className="item"><span className="k">Bid</span><span className="v mono">{txt(market.bid, NA_TEXT)}</span></div>
        <div className="item"><span className="k">Ask</span><span className="v mono">{txt(market.ask, NA_TEXT)}</span></div>
        <div className="item"><span className="k">Spread</span><span className="v mono">{txt(market.spread, NA_TEXT)}</span></div>
        <div className="item"><span className="k">Price (mid)</span><span className="v mono">{txt(market.price, NA_TEXT)}</span></div>
        <div className="item"><span className="k">Quote time</span><span className="v mono">{txt(market.time, NA_TEXT)}</span></div>
        <div className="item"><span className="k">Tick age</span><span className="v mono">{txt(market.tick_age_s, NA_TEXT)}{market.tick_age_s !== undefined && market.tick_age_s !== null ? " s" : ""}</span></div>
        <div className="item"><span className="k">Session</span><span className="v">{txt(market.session, NA_TEXT)}</span></div>
        <div className="item"><span className="k">Timeframe</span><span className="v mono">{txt(node.timeframe, NA_TEXT)}</span></div>
        <div className="item"><span className="k">Source</span><span className="v">{txt(market.source, NA_TEXT)}</span></div>
      </div>

      {node.node_id !== undefined && node.node_id !== null && (
        <div style={{ marginTop: 10 }}>
          <SectionTitle>Indicator values for Node_{txt(node.node_id, "?")}</SectionTitle>
          <div className="kit-strip" style={{ border: "none", padding: 0 }}>
            <div className="item"><span className="k">Last closed bar</span>
          <span className="v mono">{data?.indicator_bar_time
            ? new Date(data.indicator_bar_time * 1000).toISOString().slice(0, 16).replace("T", " ")
            : NA_TEXT}</span></div>
            {indicators.map((ind) => (
              <div className="item" key={ind.spec}>
                <span className="k mono">{txt(ind.spec)}</span>
                <span className="v mono">
                  {txt(ind.value, NA_TEXT)}
                  {ind.error ? <span className="muted" style={{ fontSize: 11 }}> ({ind.error})</span> : null}
                </span>
              </div>
            ))}
          </div>
          {indicators.length === 0 && (
            <div className="muted" style={{ fontSize: 11.5 }}>
              This node's genome defines no resolvable indicators — the engine reports that instead of
              inventing values.
            </div>
          )}
          {conditions.map((side) => (
            <div key={txt(side.side)} style={{ marginTop: 8 }}>
              <div className="kit-head">
                <div style={{ fontSize: 12, fontWeight: 650 }}>
                  {txt(side.side)} entry — {txt(side.met_count, "0")} of {txt(side.total_count, "0")} conditions met
                </div>
                <Badge tone={side.signal ? "ok" : "muted"}>
                  {side.signal ? "SIGNAL" : (side.gate_ok ? "waiting" : "session/day/regime filter blocks this side")}
                </Badge>
              </div>
              <table className="table compact">
                <thead><tr><th>Condition</th><th>State</th></tr></thead>
                <tbody>
                  {arr(side.conditions).map((c, i) => (
                    <tr key={i}>
                      <td className="mono">{txt(c.condition)}</td>
                      <td>
                        {c.met === null || c.met === undefined
                          ? <Badge tone="warn">not evaluable</Badge>
                          : <Badge tone={c.met ? "ok" : "muted"}>{c.met ? "MET" : "not met"}</Badge>}
                        {c.error ? <span className="muted" style={{ fontSize: 11 }}> {c.error}</span> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

/* ------------------------------------------- V5 §11/§12 node table + schedule */
// V5.2 §16 — the SAME dialog serves MT5 Demo Trading: pass `loadFn`/`saveFn`
// to point it at another endpoint. The evaluator behind it is identical, so a
// demo schedule is enforced by the very code the Live Testing engine uses.
export function ScheduleDialog({ nodeId, onClose, onSaved, loadFn, saveFn, subtitle }) {
  /* §11 §12 §13 §17 — the fully editable per-node schedule.

   * Every group the engine actually enforces is editable here (days, sessions,
   * regimes, timeframes, the node's own signal conditions, trading windows with
   * an explicit timezone, and the Enabled switch), and every control is a real
   * control: the checkboxes are native checkboxes, "Select all"/"Clear all" act
   * on the whole group, and nothing is a cosmetic label.
   *
   * The values are validated by the SAME backend function the execution engine
   * uses (app.live_testing.schedule), so what this dialog shows is what the
   * engine will do. An invalid combination comes back per field and is shown
   * next to the group that caused it — it is never saved silently.
   */
  const [draft, setDraft] = useState(null);
  const [saved, setSaved] = useState(null);     // last saved config (for Reset)
  const [state, setState] = useState(null);     // engine evaluation + options
  const [err, setErr] = useState(null);
  const [fieldErrs, setFieldErrs] = useState({});
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState(null);

  const optionsOr = (v) => arr(v).map((o) => (o && typeof o === "object"
    ? { value: o.value, label: o.label ?? String(o.value) }
    : { value: o, label: String(o) }));

  const fromServer = useCallback((res) => {
    const c = objOrNull(res?.config) || {};
    const n = objOrNull(c.schedule) || c;      // /nodes rows nest it, the API does not
    const conds = objOrNull(n.conditions) || {};
    const cfgs = n.enabled;
    return {
      days: arr(n.days).length ? arr(n.days).map((d) => Number(d)) : [],
      sessions: arr(n.sessions).map((x) => String(x).toLowerCase()),
      regimes: arr(n.regimes).map((x) => String(x).toLowerCase()),
      timeframes: arr(n.timeframes).map((x) => String(x).toUpperCase()),
      conditions: Object.keys(conds).length ? conds
        : Object.fromEntries(optionsOr(res?.options?.conditions).map((o) => [o.value, true])),
      windows: arr(n.windows).map((w) => ({ start: txt(objOrNull(w)?.start, "00:00"),
                                            end: txt(objOrNull(w)?.end, "23:59") })),
      enabled: cfgs === null || cfgs === undefined ? true : !!cfgs,
      enabled_explicit: cfgs !== null && cfgs !== undefined,
      timezone: txt(n.timezone, "UTC"),
      cooldown_minutes: n.cooldown_minutes ?? "",
      max_trades_per_day: n.max_trades_per_day ?? "",
      spread_limit_points: n.spread_limit_points ?? "",
      max_positions: n.max_positions ?? "",
    };
  }, []);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const res = await (loadFn || api.liveTestingSchedule)(nodeId);
      setState(res);
      const d = fromServer(res);
      setDraft(d);
      setSaved(d);
      setDirty(false);
    } catch (e) { setErr(e); }
  }, [nodeId, fromServer, loadFn]);

  useEffect(() => { load(); }, [load]);

  const upd = (patch) => { setDraft((d) => ({ ...d, ...patch })); setDirty(true); setMsg(null); };
  const toggleIn = (key, value) => upd({
    [key]: arr(draft[key]).includes(value)
      ? arr(draft[key]).filter((x) => x !== value)
      : [...arr(draft[key]), value],
  });

  const save = async () => {
    setBusy(true); setErr(null); setFieldErrs({});
    try {
      const body = {
        days: draft.days, sessions: draft.sessions, regimes: draft.regimes,
        timeframes: draft.timeframes, conditions: draft.conditions,
        windows: draft.windows, enabled: !!draft.enabled,
        timezone: draft.timezone,
        cooldown_minutes: draft.cooldown_minutes === "" ? null : Number(draft.cooldown_minutes),
        max_trades_per_day: draft.max_trades_per_day === "" ? null : Number(draft.max_trades_per_day),
        spread_limit_points: draft.spread_limit_points === "" ? null : Number(draft.spread_limit_points),
        max_positions: draft.max_positions === "" ? null : Number(draft.max_positions),
      };
      const res = await (saveFn || api.saveLiveTestingSchedule)(nodeId, body);
      const d = fromServer(res);
      setDraft(d); setSaved(d); setDirty(false);
      setState({ ...(state || {}), ...objOrNull(res?.evaluation), config: res?.config,
                 options: res?.options || state?.options, description: res?.description });
      /* the demo endpoints return the evaluation instead of embedding it */
      setMsg(`Saved — the engine now enforces: ${txt(res?.description, "saved")}`);
      if (onSaved) onSaved();
    } catch (e) {
      const det = objOrNull(e?.detail) || objOrNull(e?.body) || {};
      const errs = arr(det.errors);
      if (errs.length) {
        const map = {};
        errs.forEach((x) => { map[txt(objOrNull(x)?.field, "?")] = txt(objOrNull(x)?.error, ""); });
        setFieldErrs(map);
      }
      setErr(e);
    } finally { setBusy(false); }
  };

  const reset = () => {
    if (!saved) return;
    setDraft({ ...saved }); setFieldErrs({}); setMsg("Changes discarded — the stored schedule is unchanged.");
    setDirty(false);
  };

  const dayNames = optionsOr(state?.options?.days).length
    ? optionsOr(state?.options?.days)
    : [0, 1, 2, 3, 4, 5, 6].map((i) => ({ value: i, label: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][i] }));
  const sessionOpts = optionsOr(state?.options?.sessions);
  const regimeOpts = optionsOr(state?.options?.regimes);
  const tfOpts = optionsOr(state?.options?.timeframes);
  const condOpts = optionsOr(state?.options?.conditions);
  const tzOpts = arr(state?.options?.timezones).map(String);
  const nodeTf = txt(state?.options?.node_timeframe, "");

  const group = (label, key, opts, valueOf = (o) => o.value, labelOf = (o) => o.label, hint = null) => (
    <div className="kit-col" style={{ marginBottom: 10 }}>
      <div className="row-bar">
        <div className="muted" style={{ fontSize: 11.5, fontWeight: 600 }}>{label}</div>
        <div className="spacer" />
        <button type="button" className="btn ghost" style={{ padding: "1px 6px", fontSize: 11 }}
                onClick={() => upd({ [key]: arr(draft?.[key]).length === arr(opts).length ? [] : arr(opts).map(valueOf) })}>
          {arr(draft?.[key]).length === arr(opts).length && arr(opts).length ? "Clear all" : "Select all"}
        </button>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
        {arr(opts).map((o) => {
          const v = valueOf(o);
          const on = arr(draft?.[key]).includes(v);
          return (
            <label key={String(v)} className="chk" style={{ display: "inline-flex", gap: 5, alignItems: "center", fontSize: 12 }}>
              <input type="checkbox" checked={on} onChange={() => toggleIn(key, v)} />
              <span>{labelOf(o)}{key === "timeframes" && v === nodeTf ? " (node)" : ""}</span>
            </label>
          );
        })}
      </div>
      {hint && <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>{hint}</div>}
      {fieldErrs[key] && <div className="kit-inline-err" style={{ fontSize: 11.5 }}>{fieldErrs[key]}</div>}
    </div>
  );

  // the engine returns the rule trace as a list of {rule, ok, detail}
  // `state` can legitimately be null (first paint, or a failed GET) — read the
  // node block through one guarded accessor so no render path dereferences null.
  const nodeMeta = objOrNull(state?.node) || {};
  const nodeTitle = nodeMeta.research_node_num != null
    ? `Node_${nodeMeta.research_node_num}` : `node ${nodeId}`;

  const ruleRows = Array.isArray(state?.rules)
    ? state.rules
    : arr(state?.rules && typeof state.rules === "object"
        ? Object.entries(state.rules).map(([k, v]) => ({ rule: k, ok: !!objOrNull(v)?.ok, detail: objOrNull(v)?.detail ?? v }))
        : []);
  const overallAllowed = state?.allowed;

  return (
    <ConfirmModal open title={`Schedule — ${nodeTitle}`}
                  confirmLabel={busy ? "Saving…" : (dirty ? "Save & enforce *" : "Save & enforce")}
                  busy={busy} onCancel={onClose} onConfirm={save}
                  result={err ? { ok: false, error: err.message || String(err) } : null}>
      {err && (
        <div className="kit-inline-err">
          {txt(objOrNull(err)?.message, "") || err.message || String(err)}
          {Object.keys(fieldErrs).length > 0 && (
            <ul style={{ margin: "4px 0 0 16px" }}>
              {Object.entries(fieldErrs).map(([f, e]) => <li key={f}><b>{f}</b>: {e}</li>)}
            </ul>
          )}
        </div>
      )}
      {msg && <div className="kit-inline-ok" style={{ marginBottom: 8 }}>{msg}</div>}

      {state && (
        <div className="kit-strip" style={{ border: "none", padding: 0, marginBottom: 8, flexWrap: "wrap" }}>
          <div className="item"><span className="k">Node</span>
            <span className="v mono">{nodeTitle}{nodeMeta.run_id ? ` · ${nodeMeta.run_id}` : ""}</span></div>
          <div className="item"><span className="k">Symbol / TF</span>
            <span className="v mono">{txt(nodeMeta.symbol, NA_TEXT)} {txt(nodeMeta.timeframe, "")}</span></div>
          <div className="item"><span className="k">Node status</span>
            <span className="v mono">{txt(nodeMeta.status, NA_TEXT)}</span></div>
          <div className="item"><span className="k">Engine clock</span>
            <span className="v mono">{txt(state?.local_time, NA_TEXT)} {txt(state?.timezone, "")}</span></div>
          <div className="item"><span className="k">Engine verdict</span>
            <span className="v">
              <Badge tone={overallAllowed ? "ok" : "warn"}>{overallAllowed ? "may trade now" : "BLOCKED"}</Badge>
            </span></div>
        </div>
      )}
      {state?.reason && <div className="kit-inline-err" style={{ marginBottom: 8 }}>{state.reason}</div>}

      {draft && (
        <>
          <label className="fld" style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
            <input type="checkbox" checked={!!draft.enabled} onChange={(e) => upd({ enabled: e.target.checked })} />
            <span><b>Schedule enabled</b> — when off, the engine blocks every order from this node with
              {" "}<span className="mono">SCHEDULE_BLOCKED</span> (it is not the same as removing the restrictions).</span>
          </label>
          {fieldErrs.enabled && <div className="kit-inline-err" style={{ fontSize: 11.5 }}>{fieldErrs.enabled}</div>}

          {group("Active days", "days", dayNames)}
          {group("Sessions", "sessions", sessionOpts)}
          {group("Market regimes (values defined by the research engine)", "regimes", regimeOpts,
                 (o) => o.value, (o) => o.label,
                 arr(draft.regimes).length
                   ? `Only trades while this node's current regime is one of the ${arr(draft.regimes).length} selected value(s); the engine checks this rule before every order.`
                   : "No regime restriction — an empty selection means the engine does not filter by regime (it never blocks every trade).")}
          {group("Timeframes", "timeframes", tfOpts)}
          {group("Signal conditions (from this node's own genome)", "conditions", condOpts,
                 (o) => o.value, (o) => `${o.label}`)}

          <div className="kit-col" style={{ marginBottom: 10 }}>
            <div className="row-bar">
              <div className="muted" style={{ fontSize: 11.5, fontWeight: 600 }}>Trading windows</div>
              <div className="spacer" />
              <button type="button" className="btn ghost" style={{ padding: "1px 6px", fontSize: 11 }}
                      onClick={() => upd({ windows: [...arr(draft.windows), { start: "00:00", end: "23:59" }] })}>
                Add window
              </button>
              <button type="button" className="btn ghost" style={{ padding: "1px 6px", fontSize: 11 }}
                      disabled={!arr(draft.windows).length}
                      onClick={() => upd({ windows: [] })}>Clear all</button>
            </div>
            {arr(draft.windows).length === 0 && (
              <div className="muted" style={{ fontSize: 11.5 }}>
                No window restriction — the days/sessions above still apply.
              </div>
            )}
            {arr(draft.windows).map((w, i) => (
              <div key={`w${i}`} style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 4 }}>
                <input className="input mono" style={{ width: 80 }} value={w.start}
                       onChange={(e) => upd({ windows: arr(draft.windows).map((x, j) => j === i ? { ...x, start: e.target.value } : x) })} />
                <span className="muted">→</span>
                <input className="input mono" style={{ width: 80 }} value={w.end}
                       onChange={(e) => upd({ windows: arr(draft.windows).map((x, j) => j === i ? { ...x, end: e.target.value } : x) })} />
                <span className="muted" style={{ fontSize: 11 }}>{txt(draft.timezone, "UTC")}</span>
                <button type="button" className="btn ghost" style={{ padding: "1px 6px", fontSize: 11 }}
                        onClick={() => upd({ windows: arr(draft.windows).filter((_, j) => j !== i) })}>remove</button>
              </div>
            ))}
            <div className="muted" style={{ fontSize: 11 }}>
              start &lt; end = a window inside one day · start &gt; end = an overnight window that crosses
              midnight (17:00 → 08:00, labelled "(overnight)") · identical times are rejected as impossible.
            </div>
            {fieldErrs.windows && <div className="kit-inline-err" style={{ fontSize: 11.5 }}>{fieldErrs.windows}</div>}
          </div>

          <div className="kit-cols">
            <div style={{ flex: "1 1 160px" }}>
              <label className="fld">Timezone (windows + days)
                <select className="input" value={draft.timezone}
                        onChange={(e) => upd({ timezone: e.target.value })}>
                  {(tzOpts.includes(draft.timezone) ? tzOpts : [draft.timezone, ...tzOpts]).map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select></label>
              {fieldErrs.timezone && <div className="kit-inline-err" style={{ fontSize: 11.5 }}>{fieldErrs.timezone}</div>}
            </div>
            <div style={{ flex: "1 1 160px" }}>
              <label className="fld">Cooldown (minutes)
                <input className="input mono" value={draft.cooldown_minutes}
                       onChange={(e) => upd({ cooldown_minutes: e.target.value })} /></label>
              <label className="fld">Max trades / day
                <input className="input mono" value={draft.max_trades_per_day}
                       onChange={(e) => upd({ max_trades_per_day: e.target.value })} /></label>
            </div>
            <div style={{ flex: "1 1 160px" }}>
              <label className="fld">Spread limit (points)
                <input className="input mono" value={draft.spread_limit_points}
                       onChange={(e) => upd({ spread_limit_points: e.target.value })} /></label>
              <label className="fld">Max open positions
                <input className="input mono" value={draft.max_positions}
                       onChange={(e) => upd({ max_positions: e.target.value })} /></label>
            </div>
          </div>

          <div className="kit-summary" style={{ marginTop: 8, padding: "6px 8px", background: "rgba(148,163,184,0.08)", borderRadius: 6 }}>
            <div className="muted" style={{ fontSize: 11 }}>Summary{dirty ? " (unsaved changes)" : " (stored)"}</div>
            <div style={{ fontSize: 12.5 }}>
              {txt(state?.description, "—")}
            </div>
            <div className="muted" style={{ fontSize: 11 }}>
              days: {arr(draft.days).length ? arr(draft.days).map((d) => dayNames.find((x) => Number(x.value) === Number(d))?.label?.slice(0, 3) || d).join("/") : "no restriction"}
              {" · "}sessions: {arr(draft.sessions).length ? arr(draft.sessions).join("+") : "no restriction"}
              {" · "}regimes: {arr(draft.regimes).length ? arr(draft.regimes).join("+") : "no restriction"}
              {" · "}timeframes: {arr(draft.timeframes).length ? arr(draft.timeframes).join("+") : "no restriction"}
              {" · "}conditions: {Object.entries(objOrNull(draft.conditions) || {}).filter(([, v]) => v).map(([k]) => k).join("+") || "none"}
              {" · "}windows: {arr(draft.windows).length ? arr(draft.windows).map((w) => `${w.start}–${w.end}`).join(", ") : "none"}
              {" · "}enabled: {draft.enabled ? "yes" : "NO"}
            </div>
          </div>
        </>
      )}

      {ruleRows.length > 0 && (
        <table className="table compact" style={{ marginTop: 8 }}>
          <thead><tr><th>Rule</th><th>State</th><th>Detail</th></tr></thead>
          <tbody>
            {ruleRows.map((r) => (
              <tr key={r.rule}>
                <td className="mono">{r.rule}</td>
                <td><Badge tone={r.ok ? "ok" : "warn"}>{r.ok ? "ok" : "blocking"}</Badge></td>
                <td className="muted" style={{ fontSize: 11.5 }}>{txt(r.detail)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="row-bar" style={{ marginTop: 8 }}>
        <div className="muted" style={{ fontSize: 11.5 }}>
          {txt(state?.description, "")} — the live engine and the deep backtest evaluate exactly these rules
          (one evaluator: app.live_testing.schedule).
        </div>
        <div className="spacer" />
        <button type="button" className="btn ghost" disabled={!draft || !dirty} onClick={reset}>Reset changes</button>
      </div>
    </ConfirmModal>
  );
}
