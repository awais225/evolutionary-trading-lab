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
    sl_pips: "300", tp_pips: "600", sl: "", tp: "",
  });
  const [preview, setPreview] = useState(null);
  const [market, setMarket] = useState(null);
  const [state, setState] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState(null);
  const [confirming, setConfirming] = useState(null);
  const [sizeMode, setSizeMode] = useState("risk");     // "risk" | "lots"
  const up = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const entry = numOrNull(market?.bid) !== null && numOrNull(market?.ask) !== null
    ? (form.side === "BUY" ? numOrNull(market.ask) : numOrNull(market.bid))
    : numOrNull(market?.ask ?? market?.bid);
  const levels = objOrNull(preview?.levels);
  const sizing = objOrNull(preview?.sizing);
  const estimate = objOrNull(preview?.estimate);
  const blocked = objOrNull(preview?.blocked);
  const symbolInfo = objOrNull(preview?.symbol_info);
  const defaults = objOrNull(preview?.defaults);

  /* Market + execution state refresh (read-only). */
  useEffect(() => {
    let alive = true;
    api.mt5ExecutionState().then((r) => alive && setState(r)).catch(() => alive && setState(null));
    api.liveTestingMarketHeader(form.symbol)
      .then((r) => { if (!alive) return; setMarket((r?.market) || null); })
      .catch(() => alive && setMarket(null));
    return () => { alive = false; };
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
      if (res?.sizing?.volume !== undefined && res?.sizing?.volume !== null) {
        up("lots", String(res.sizing.volume));
      }
      if (res?.sizing?.actual_risk !== undefined && res?.sizing?.actual_risk !== null) {
        up("risk", String(res.sizing.actual_risk));
      }
    } catch (e) {
      setErr(e.message || String(e)); setPreview(null);
    } finally { setBusy(""); }
  }, [form.symbol, form.side, form.sl_pips, form.tp_pips, form.sl, form.tp, form.risk,
      form.lots, sizeMode, entry]);

  useEffect(() => {
    if (entry === null) return undefined;
    const t = setTimeout(() => { runPreview(); }, 250);
    return () => clearTimeout(t);
  }, [runPreview, entry]);

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
          right={<Badge tone={executionAllowed ? "ok" : "warn"}>
            {executionAllowed ? "DEMO EXECUTION ALLOWED" : "DEMO ONLY"}
          </Badge>}>
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
          <label className="fld">Amount / risk (money)
            <input value={form.risk} onChange={(e) => { setSizeMode("risk"); up("risk", e.target.value); }} className="mono" placeholder="e.g. 10" />
          </label>
          <label className="fld">Stop loss <span className="muted">(pips — required)</span>
            <input value={form.sl_pips} onChange={(e) => { up("sl_pips", e.target.value); up("sl", ""); }} className="mono" placeholder="300" />
          </label>
          <label className="fld">Stop loss <span className="muted">(price, overrides pips)</span>
            <input value={form.sl} onChange={(e) => up("sl", e.target.value)} className="mono" placeholder="optional" />
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
        </div>
      </div>

      <div className="btn-row">
        <button className="btn" disabled={busy === "preview"} onClick={() => runPreview()}>
          {busy === "preview" ? "calculating…" : "Recalculate (backend)"}
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
              <div className="item"><span className="k">{sizing.actual_risk !== undefined ? "Actual risk" : "Cash risk"}</span>
                <span className="v mono">{txt(sizing.actual_risk ?? sizing.risk_amount, NA_TEXT)}</span></div>
              <div className="item"><span className="k">Stop distance</span><span className="v mono">{fmt.num(sizing.stop_distance, 2)}</span></div>
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
export function LiveNodeTable({ onOpenNode, onToggleStar, globalRisk, onRiskChanged }) {
  /* One combined table: research metrics (IS return, PF) + live state (status,
   * today's and total live P/L, trades, effective risk and where it comes from)
   * + schedule + the START/STOP action that really enrols/un-enrols the node.
   *
   * Search, status/timeframe filters, sorting and paging are server-side; the
   * row is exactly what the backend reports, including "null" for a metric that
   * does not exist yet (never a fabricated 0). */
  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [msg, setMsg] = useState(null);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [timeframe, setTimeframe] = useState("");
  const [starredOnly, setStarredOnly] = useState(false);
  const [sortBy, setSortBy] = useState("node_id");
  const [sortDesc, setSortDesc] = useState(true);
  const [limit, setLimit] = useState(25);
  const [offset, setOffset] = useState(0);
  const [busyId, setBusyId] = useState(null);
  const [scheduleFor, setScheduleFor] = useState(null);
  const [riskDraft, setRiskDraft] = useState("");

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const res = await api.liveTestingNodesTable({
        search, status, timeframe, starred_only: starredOnly || undefined,
        sort_by: sortBy, sort_desc: sortDesc, limit, offset,
      });
      setRows(safeRows(res?.nodes));
      setTotal(res?.total ?? 0);
      setMeta(res);
    } catch (e) { setErr(e); } finally { setLoading(false); }
  }, [search, status, timeframe, starredOnly, sortBy, sortDesc, limit, offset]);

  useEffect(() => { load(); }, [load]);
  useInterval(load, 15000);
  useEffect(() => { setRiskDraft(globalRisk === undefined || globalRisk === null ? "" : String(globalRisk)); }, [globalRisk]);

  const act = async (row, action) => {
    setBusyId(row.node_id); setMsg(null); setErr(null);
    try {
      const res = action === "start"
        ? await api.liveTestingStartNode(row.node_id)
        : await api.liveTestingStopNode(row.node_id);
      setMsg(`Node_${row.node_id}: ${action === "start" ? "STARTED" : "STOPPED"} — ${txt(res?.note, "")}`);
      await load();
      if (onRiskChanged) onRiskChanged();
    } catch (e) {
      setErr(e);
    } finally { setBusyId(null); }
  };

  const saveGlobalRisk = async () => {
    setErr(null);
    try {
      await api.liveTestingSetRisk(Number(riskDraft));
      setMsg(`Global default risk set to ${riskDraft} %`);
      await load();
      if (onRiskChanged) onRiskChanged();
    } catch (e) { setErr(e); }
  };

  const sortBtn = (key, label) => (
    <button className="btn ghost" style={{ padding: "2px 6px", fontSize: 11 }}
            onClick={() => { setSortBy(key); setSortDesc(sortBy === key ? !sortDesc : true); }}>
      {label}{sortBy === key ? (sortDesc ? " ▼" : " ▲") : ""}
    </button>
  );

  return (
    <Card
      title="Live test nodes"
      right={<Badge tone="mute">{total} node(s) · page {Math.floor(offset / limit) + 1}</Badge>}
    >
      <div className="btn-row" style={{ marginBottom: 8, flexWrap: "wrap" }}>
        <input className="input" style={{ width: 160 }} placeholder="search id / market / tf"
               value={search} onChange={(e) => { setOffset(0); setSearch(e.target.value); }} />
        <select className="input" value={status} onChange={(e) => { setOffset(0); setStatus(e.target.value); }}>
          <option value="">any status</option>
          <option value="VALID">Valid</option>
          <option value="LIVE_ELIGIBLE">Live eligible</option>
          <option value="LIVE_TESTING">Live testing</option>
          <option value="LIVE_COMPLETED">Live completed</option>
          <option value="MT5_DEMO">MT5 demo</option>
        </select>
        <select className="input" value={timeframe} onChange={(e) => { setOffset(0); setTimeframe(e.target.value); }}>
          <option value="">any timeframe</option>
          {["M1", "M5", "M15", "M30", "H1"].map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <label className="muted" style={{ fontSize: 12 }}>
          <input type="checkbox" checked={starredOnly}
                 onChange={(e) => { setOffset(0); setStarredOnly(e.target.checked); }} /> starred only
        </label>
        <span className="muted" style={{ fontSize: 12 }}>
          Global default risk:&nbsp;
          <input className="input mono" style={{ width: 62 }} value={riskDraft}
                 onChange={(e) => setRiskDraft(e.target.value)} /> %
          <button className="btn ghost" style={{ marginLeft: 4 }}
                  onClick={saveGlobalRisk} disabled={riskDraft === ""}>set</button>
        </span>
        <button className="btn ghost" onClick={load} disabled={loading}>
          {loading ? "loading…" : "Reload"}
        </button>
      </div>

      {err && <div className="kit-inline-err">{err.message || String(err)}</div>}
      {msg && <div className="kit-ok" style={{ marginBottom: 6 }}>{msg}</div>}

      <div style={{ overflowX: "auto" }}>
        <table className="table compact">
          <thead>
            <tr>
              <th>★</th>
              <th>{sortBtn("node_id", "ID")}</th>
              <th>Status</th>
              <th>Market</th>
              <th>TF</th>
              <th>{sortBtn("is_return_pct", "IS return")}</th>
              <th>{sortBtn("profit_factor", "PF")}</th>
              <th>{sortBtn("today_pnl", "Today P/L")}</th>
              <th>{sortBtn("total_live_pnl", "Total live P/L")}</th>
              <th>{sortBtn("live_trades", "Trades")}</th>
              <th>Risk</th>
              <th>Schedule</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.node_id}>
                <td>
                  <Star on={r.starred} title={r.starred ? "On the shortlist" : "Add to shortlist"}
                        onClick={() => onToggleStar && onToggleStar(r.node_id)} />
                </td>
                <td className="mono">
                  <a href="#" onClick={(e) => { e.preventDefault(); onOpenNode && onOpenNode(r.node_id); }}>
                    Node_{r.node_id}
                  </a>
                </td>
                <td><Badge tone={r.is_active ? "ok" : "mute"}>{txt(r.v5_status || r.status, "—")}</Badge></td>
                <td className="mono">{txt(r.market, NA_TEXT)}</td>
                <td className="mono">{txt(r.timeframe, NA_TEXT)}</td>
                <td className="mono">{r.is_return_pct === null || r.is_return_pct === undefined ? NA_TEXT : `${fmt.num(r.is_return_pct, 2)} %`}</td>
                <td className="mono">{txt(r.profit_factor, NA_TEXT)}</td>
                <td className="mono">{r.today_pnl === null || r.today_pnl === undefined ? NA_TEXT : fmt.num(r.today_pnl, 2)}</td>
                <td className="mono">{r.total_live_pnl === null || r.total_live_pnl === undefined ? NA_TEXT : fmt.num(r.total_live_pnl, 2)}</td>
                <td className="mono">{txt(r.live_closed, "0")} / {txt(r.live_trades, "0")}</td>
                <td className="mono">
                  {txt(r.risk_pct, NA_TEXT)} %{" "}
                  <span className="muted" style={{ fontSize: 10.5 }}>
                    {r.risk_source === "CUSTOM" ? "(node override)" : "(global)"}
                  </span>
                </td>
                <td style={{ fontSize: 11 }}>
                  <button className="btn ghost" style={{ padding: "2px 6px" }}
                          onClick={() => setScheduleFor(r.node_id)}>
                    {r.schedule?.active ? "active" : "edit"}
                  </button>
                </td>
                <td>
                  {r.is_active
                    ? <button className="btn danger" disabled={busyId === r.node_id}
                              onClick={() => act(r, "stop")}>
                        {busyId === r.node_id ? "…" : "STOP"}
                      </button>
                    : <button className="btn success" disabled={busyId === r.node_id}
                              onClick={() => act(r, "start")}>
                        {busyId === r.node_id ? "…" : "START"}
                      </button>}
                </td>
              </tr>
            ))}
            {rows.length === 0 && !loading && (
              <tr><td colSpan={13} className="muted">No node matches the current filters.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="btn-row" style={{ marginTop: 8 }}>
        <button className="btn ghost" disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - limit))}>« prev</button>
        <button className="btn ghost" disabled={offset + limit >= total}
                onClick={() => setOffset(offset + limit)}>next »</button>
        <select className="input" value={limit}
                onChange={(e) => { setOffset(0); setLimit(Number(e.target.value)); }}>
          {[10, 25, 50, 100].map((n) => <option key={n} value={n}>{n} / page</option>)}
        </select>
        <span className="muted" style={{ fontSize: 11.5 }}>
          {txt(meta?.note, "")}
        </span>
      </div>

      {scheduleFor && (
        <ScheduleDialog
          nodeId={scheduleFor}
          onClose={() => setScheduleFor(null)}
          onSaved={async () => { setMsg(`Node_${scheduleFor}: schedule saved and enforced by the engine`); await load(); }}
        />
      )}
    </Card>
  );
}

export function ScheduleDialog({ nodeId, onClose, onSaved }) {
  /* §11 — the functional schedule popup. Saving writes the values the engine
   * reads before every order; the dialog then shows the engine's own evaluation
   * (allowed / blocked, with the reason and the full rule trace). */
  const [cfg, setCfg] = useState(null);
  const [state, setState] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const res = await api.liveTestingSchedule(nodeId);
      setState(res);
      const s = objOrNull(res?.schedule) || {};
      setCfg({
        days: arr(s.days).map((d) => Number(d)),
        sessions: arr(s.sessions),
        start_time: txt(s.start_time, "00:00"),
        end_time: txt(s.end_time, "23:59"),
        timezone: txt(s.timezone, "UTC"),
        cooldown_minutes: s.cooldown_minutes ?? "",
        max_trades_per_day: s.max_trades_per_day ?? "",
        spread_limit_points: s.spread_limit_points ?? "",
        max_positions: s.max_positions ?? "",
      });
    } catch (e) { setErr(e); }
  }, [nodeId]);

  useEffect(() => { load(); }, [load]);
  useInterval(load, 20000);

  const dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const sessions = ["asia", "london", "newyork", "london_ny_overlap", "custom"];
  const toggle = (list, value) => (list.includes(value)
    ? list.filter((x) => x !== value) : [...list, value]);

  const save = async () => {
    setBusy(true); setErr(null);
    try {
      await api.saveLiveTestingSchedule(nodeId, {
        days: cfg.days, sessions: cfg.sessions, start_time: cfg.start_time,
        end_time: cfg.end_time, timezone: cfg.timezone,
        cooldown_minutes: cfg.cooldown_minutes === "" ? null : Number(cfg.cooldown_minutes),
        max_trades_per_day: cfg.max_trades_per_day === "" ? null : Number(cfg.max_trades_per_day),
        spread_limit_points: cfg.spread_limit_points === "" ? null : Number(cfg.spread_limit_points),
        max_positions: cfg.max_positions === "" ? null : Number(cfg.max_positions),
      });
      await load();
      if (onSaved) onSaved();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };

  const rules = arr(state?.rules);

  return (
    <ConfirmModal open title={`Schedule — Node_${nodeId}`} confirmLabel="Save & enforce"
                  busy={busy} onCancel={onClose} onConfirm={save}
                  result={err ? { ok: false, error: err.message || String(err) } : null}>
      {err && <div className="kit-inline-err">{err.message || String(err)}</div>}
      {state && (
        <div className="kit-strip" style={{ border: "none", padding: 0, marginBottom: 8 }}>
          <div className="item"><span className="k">Now (engine clock)</span>
            <span className="v mono">{txt(state.local_time, NA_TEXT)}</span></div>
          <div className="item"><span className="k">Timezone</span>
            <span className="v mono">{txt(state.timezone, NA_TEXT)}</span></div>
          <div className="item"><span className="k">Trades today</span>
            <span className="v mono">{txt(state.trades_today, "0")}</span></div>
          <div className="item"><span className="k">Engine verdict</span>
            <span className="v">
              <Badge tone={state.allowed ? "ok" : "warn"}>
                {state.allowed ? "may trade now" : "BLOCKED"}
              </Badge>
            </span></div>
        </div>
      )}
      {state?.reason && <div className="kit-inline-err" style={{ marginBottom: 8 }}>{state.reason}</div>}

      {cfg && (
        <div className="kit-cols">
          <div style={{ flex: "1 1 200px" }}>
            <div className="muted" style={{ fontSize: 11.5 }}>Active days</div>
            <div className="btn-row" style={{ flexWrap: "wrap" }}>
              {dayNames.map((d, i) => (
                <button key={d} className={cfg.days.includes(i) ? "btn" : "btn ghost"}
                        style={{ padding: "2px 8px" }}
                        onClick={() => setCfg({ ...cfg, days: toggle(cfg.days, i) })}>{d}</button>
              ))}
            </div>
            <div className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>Sessions (UTC)</div>
            <div className="btn-row" style={{ flexWrap: "wrap" }}>
              {sessions.map((sName) => (
                <button key={sName} className={cfg.sessions.includes(sName) ? "btn" : "btn ghost"}
                        style={{ padding: "2px 8px" }}
                        onClick={() => setCfg({ ...cfg, sessions: toggle(cfg.sessions, sName) })}>
                  {sName.replace(/_/g, " ")}
                </button>
              ))}
            </div>
          </div>
          <div style={{ flex: "1 1 200px" }}>
            <label className="fld">Start time
              <input className="input mono" value={cfg.start_time}
                     onChange={(e) => setCfg({ ...cfg, start_time: e.target.value })} placeholder="00:00" /></label>
            <label className="fld">End time
              <input className="input mono" value={cfg.end_time}
                     onChange={(e) => setCfg({ ...cfg, end_time: e.target.value })} placeholder="23:59" /></label>
            <label className="fld">Timezone
              <input className="input mono" value={cfg.timezone}
                     onChange={(e) => setCfg({ ...cfg, timezone: e.target.value })} /></label>
          </div>
          <div style={{ flex: "1 1 200px" }}>
            <label className="fld">Cooldown (minutes)
              <input className="input mono" value={cfg.cooldown_minutes}
                     onChange={(e) => setCfg({ ...cfg, cooldown_minutes: e.target.value })} /></label>
            <label className="fld">Max trades / day
              <input className="input mono" value={cfg.max_trades_per_day}
                     onChange={(e) => setCfg({ ...cfg, max_trades_per_day: e.target.value })} /></label>
            <label className="fld">Spread limit (points)
              <input className="input mono" value={cfg.spread_limit_points}
                     onChange={(e) => setCfg({ ...cfg, spread_limit_points: e.target.value })} /></label>
            <label className="fld">Max open positions
              <input className="input mono" value={cfg.max_positions}
                     onChange={(e) => setCfg({ ...cfg, max_positions: e.target.value })} /></label>
          </div>
        </div>
      )}

      {rules.length > 0 && (
        <table className="table compact" style={{ marginTop: 8 }}>
          <thead><tr><th>Rule</th><th>State</th><th>Detail</th></tr></thead>
          <tbody>
            {rules.map((r) => (
              <tr key={r.rule}>
                <td className="mono">{r.rule}</td>
                <td><Badge tone={r.ok ? "ok" : "warn"}>{r.ok ? "ok" : "blocking"}</Badge></td>
                <td className="muted" style={{ fontSize: 11.5 }}>{txt(r.detail)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
        {txt(state?.description, "")} — the live engine evaluates exactly these rules before every order.
      </div>
    </ConfirmModal>
  );
}
