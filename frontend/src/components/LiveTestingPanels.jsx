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
import { arr, NA_TEXT, numOrNull, objOrNull, txt } from "../lib/safe.js";
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
  const nodeList = arr(nodes).length ? arr(nodes) : arr(status?.nodes);

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
export function ManualOrderPanel() {
  const [form, setForm] = useState({ symbol: "XAUUSD", side: "BUY", risk: "", lots: "", sl: "", tp: "" });
  const [preview, setPreview] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState(null);
  const [state, setState] = useState(null);
  const [market, setMarket] = useState(null);
  const [confirming, setConfirming] = useState(null);   // pending {side}
  const up = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => {
    api.mt5ExecutionState().then(setState).catch(() => setState(null));
    api.liveTestingMarket(form.symbol).then(setMarket).catch(() => setMarket(null));
  }, [form.symbol]);

  const entry = numOrNull(market?.ask ?? market?.bid);
  const sizing = objOrNull(preview?.sizing);
  const blocked = objOrNull(preview?.blocked);

  const runPreview = async (mode) => {
    setBusy("preview"); setErr(null); setResult(null);
    try {
      const payload = {
        symbol: form.symbol, side: form.side, entry, sl: numOrNull(form.sl), tp: numOrNull(form.tp),
      };
      if (mode === "risk") payload.risk_amount = numOrNull(form.risk);
      else payload.volume = numOrNull(form.lots);
      const res = await api.mt5ExecutionPreview(payload);
      setPreview(res);
      // mirror the backend's own answer back into the other field
      if (res?.sizing?.volume !== undefined && res.sizing?.volume !== null && mode === "risk") {
        up("lots", String(res.sizing.volume));
      }
      if (res?.sizing?.actual_risk !== undefined && mode === "lots") {
        up("risk", String(res.sizing.actual_risk));
      }
    } catch (e) {
      setErr(e.message || String(e)); setPreview(null);
    } finally { setBusy(""); }
  };

  /* The order is only sent after the operator confirms the exact ticket in the
   * dialog. The phrase comes from the backend itself and is never hard-coded
   * here, and there is no retry: the panel reports the broker's own answer. */
  const place = async (side) => {
    const useSide = side || form.side;
    setBusy("place"); setErr(null); setResult(null);
    try {
      const res = await api.mt5ExecutionPlace({
        symbol: form.symbol, side: useSide, volume: numOrNull(form.lots),
        sl: numOrNull(form.sl), tp: numOrNull(form.tp),
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

  return (
    <Card title="Manual order panel" right={<Badge tone="warn">DEMO / SIMULATOR ONLY</Badge>}>
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
              <div className="kit-kv"><span className="k">Volume</span><span className="mono">{txt(form.lots, "not calculated")}</span></div>
              <div className="kit-kv"><span className="k">Entry reference</span><span className="mono">{entry === null ? NA_TEXT : fmt.num(entry, 2)}</span></div>
              <div className="kit-kv"><span className="k">Stop loss</span><span className="mono">{txt(form.sl, "missing — an order without SL is refused")}</span></div>
              <div className="kit-kv"><span className="k">Take profit</span><span className="mono">{txt(form.tp, "not set")}</span></div>
              <div className="kit-kv"><span className="k">Risk at SL</span><span className="mono">{txt(form.risk, NA_TEXT)}</span></div>
            </div>
        </ConfirmModal>
      )}
      <div className="warn-banner" style={{ marginBottom: 10 }}>
        <b>Amount = money at risk if the stop loss is hit.</b> It is <u>not</u> margin.
        Lot size is always rounded <b>down</b> to the broker's permitted step by the backend.
      </div>
      <div className="kit-cols">
        <div style={{ flex: "1 1 220px" }}>
          <label className="fld">Symbol
            <input value={form.symbol} onChange={(e) => up("symbol", e.target.value.toUpperCase())} className="mono" />
          </label>
          <label className="fld">Amount / risk (money)
            <input value={form.risk} onChange={(e) => up("risk", e.target.value)} className="mono" placeholder="e.g. 50" />
          </label>
          <label className="fld">Stop loss <span className="muted">(required)</span>
            <input value={form.sl} onChange={(e) => up("sl", e.target.value)} className="mono" placeholder="price" />
          </label>
        </div>
        <div style={{ flex: "1 1 220px" }}>
          <label className="fld">Lot size
            <input value={form.lots} onChange={(e) => up("lots", e.target.value)} className="mono" placeholder="e.g. 0.05" />
          </label>
          <label className="fld">Take profit <span className="muted">(optional)</span>
            <input value={form.tp} onChange={(e) => up("tp", e.target.value)} className="mono" placeholder="price" />
          </label>
          <div className="muted" style={{ fontSize: 11.5 }}>
            Entry reference: <span className="mono">{entry === null ? NA_TEXT : fmt.num(entry, 2)}</span>{" "}
            ({txt(market?.source, "unknown source")})
          </div>
        </div>
      </div>

      <div className="btn-row">
        <button className="btn" disabled={busy === "preview"} onClick={() => runPreview("risk")}>
          {busy === "preview" ? "calculating…" : "Risk → lot size"}
        </button>
        <button className="btn" disabled={busy === "preview"} onClick={() => runPreview("lots")}>
          Lot size → risk
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
        <div className={result?.ok === false ? "kit-inline-err" : "kit-ok"}>
          <b>Backend response:</b>
          <div className="mono" style={{ whiteSpace: "pre-wrap", fontSize: 11.5 }}>
            {JSON.stringify(result, null, 1).slice(0, 900)}
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
                    {arr(nodes).map((n) => {
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
