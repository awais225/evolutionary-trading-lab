import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { arr, NA_TEXT, objOrNull, txt } from "../lib/safe.js";
import { Badge, Kpi, SectionTitle, useInterval } from "./ui.jsx";

/* V6.5 §3.1 / §8 — ACTIVE TRADES first in the Live Testing tab.
 *
 * Everything shown is broker/ledger fact: floating P/L comes from the terminal,
 * entry reasons from the canonical ledger's recorded explanation, and the SL/TP
 * monetary figures are clearly-labelled ESTIMATES (never guarantees). Rows are
 * expandable so the page stays compact. Status is carried by text + icon as
 * well as colour, never colour alone. */

const fmt2 = (v, suffix = "") =>
  v === null || v === undefined || Number.isNaN(Number(v)) ? NA_TEXT : `${Number(v).toFixed(2)}${suffix}`;

function SideIcon({ side }) {
  return <span aria-hidden="true">{side === "BUY" ? "▲" : side === "SELL" ? "▼" : "•"}</span>;
}

function TradeRow({ t }) {
  const [open, setOpen] = useState(false);
  const er = objOrNull(t.entry_reason) || {};
  const pnl = Number(t.floating_pnl);
  const tone = pnl > 0 ? "pos" : pnl < 0 ? "neg" : undefined;
  return (
    <div className="panel" style={{ marginBottom: 8, padding: "8px 10px" }}>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <Badge tone="mute"><SideIcon side={t.side} /> {txt(t.side, "?")}</Badge>
        <b style={{ fontSize: 13 }}>{txt(t.symbol, NA_TEXT)}</b>
        <span className="mono" style={{ fontSize: 12 }}>
          {txt(t.node_label, NA_TEXT)}
          {t.order_ticket ? ` · ord ${t.order_ticket}` : ""}
          {t.deal_ticket ? ` · deal ${t.deal_ticket}` : ""}
          {t.position_ticket ? ` · pos ${t.position_ticket}` : ""}
        </span>
        <span className="mono" style={{ fontSize: 12 }}>
          entry {fmt2(t.entry_price)} @ {txt(t.entry_time_iso, NA_TEXT)}
        </span>
        <span className="mono" style={{ fontSize: 12 }}>
          now {fmt2(t.current_price)}
        </span>
        <span className="mono" style={{ fontSize: 12, color: tone === "pos" ? "#3fb950" : tone === "neg" ? "#f85149" : undefined }}
              title="floating profit/loss reported by the broker — account currency">
          {fmt2(t.floating_pnl)} {tone === "pos" ? "▲" : tone === "neg" ? "▼" : ""}
        </span>
        <span className="mono muted" style={{ fontSize: 12 }}
              title="floating P/L in pips (instrument's own pip convention)">
          ({fmt2(t.floating_pnl_pips)} pips)
        </span>
        <Badge tone="warn" title="open broker position — text label, not colour alone">● OPEN</Badge>
        <button className="btn ghost" style={{ padding: "2px 8px", fontSize: 11 }}
                onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "▾ details" : "▸ details"}
        </button>
      </div>
      {open && (
        <div className="kit-grid" style={{ fontSize: 11.5, marginTop: 8 }}>
          <div className="kit-kv"><span className="k">Volume</span>
            <span className="mono">{txt(t.volume, NA_TEXT)}</span></div>
          <div className="kit-kv"><span className="k">SL price</span>
            <span className="mono">{fmt2(t.sl)}{t.sl_offset_pips ? ` (default ${fmt2(t.sl_default)}, offset ${t.sl_offset_pips} pips)` : ""}</span></div>
          <div className="kit-kv"><span className="k">TP price</span>
            <span className="mono">{fmt2(t.tp)}{t.tp_offset_pips ? ` (default ${fmt2(t.tp_default)}, offset ${t.tp_offset_pips} pips)` : ""}</span></div>
          <div className="kit-kv"><span className="k">Distance to SL</span>
            <span className="mono">{fmt2(t.distance_to_sl_pips)} pips</span></div>
          <div className="kit-kv"><span className="k">Distance to TP</span>
            <span className="mono">{fmt2(t.distance_to_tp_pips)} pips</span></div>
          <div className="kit-kv"><span className="k">EST. loss at SL</span>
            <span className="mono">{fmt2(t.estimated_pnl_at_sl)} — <i>estimate, not a guarantee</i></span></div>
          <div className="kit-kv"><span className="k">EST. profit at TP</span>
            <span className="mono">{fmt2(t.estimated_pnl_at_tp)} — <i>estimate, not a guarantee</i></span></div>
          <div className="kit-kv"><span className="k">Status</span>
            <span className="mono">{txt(t.status, NA_TEXT)} ({txt(t.explanation_kind || t.data_freshness?.last_ledger_sync_iso, "facts")})</span></div>
          <div className="kit-kv"><span className="k">Magic / comment</span>
            <span className="mono">{txt(t.magic, NA_TEXT)} · {txt(t.comment, NA_TEXT)}</span></div>
          <div className="kit-kv"><span className="k">Data freshness</span>
            <span className="mono">broker: live query · ledger sync: {txt(t.data_freshness?.last_ledger_sync_iso, "never")}</span></div>
          <div className="kit-kv" style={{ gridColumn: "1 / -1" }}>
            <span className="k">Entry reason (recorded at decision time)</span>
            <span className="mono" style={{ whiteSpace: "pre-wrap" }}>
              {er.entry_rule_text || (er.note ? er.note : NA_TEXT)}
              {er.signal_bar_time_utc ? ` · signal bar ${er.signal_bar_time_utc} (${txt(er.timeframe, "?")})` : ""}
              {er.atr && er.atr.value !== null && er.atr.value !== undefined ? ` · ${txt(er.atr.spec, "atr")}=${fmt2(er.atr.value)}` : ""}
              {"\n"}
              <span className="muted">
                [{txt(er.explanation_kind, "recorded facts")}] {txt(er.note, "")}
              </span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export function LiveActiveTrades({ refreshKey = 0 }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const load = useCallback(async () => {
    try {
      const res = await api.liveTestingActiveTrades();
      if (res && res.ok === false) throw new Error(txt(res.error, "active-trades refused"));
      setData(res);
      setErr(null);
    } catch (e) {
      setErr(String((e && e.message) || e));
    }
  }, []);
  useEffect(() => { load(); }, [load, refreshKey]);
  useInterval(load, 15000);
  const trades = arr(data?.active_trades);
  const sync = objOrNull(data?.sync) || {};
  return (
    <>
      <SectionTitle
        hint="Open broker positions managed by this lab, newest evidence first. Floating P/L is live; SL/TP money figures are estimates."
        right={<Badge tone={trades.length ? "real" : "mute"}>{trades.length} open</Badge>}
      >
        Active Trades
      </SectionTitle>
      {err && <div className="panel" style={{ fontSize: 12, color: "#f85149" }}>Active trades unavailable: {err}</div>}
      {!err && trades.length === 0 && (
        <div className="panel muted" style={{ fontSize: 12 }}>
          No active managed trades. {txt(data?.note, "")}
        </div>
      )}
      {trades.map((t) => <TradeRow key={String(t.position_ticket || t.ticket)} t={t} />)}
      {data?.errors && (
        <div className="panel" style={{ fontSize: 11.5, color: "#d29922" }}>
          <b>Sync warnings</b>: {arr(data.errors).join(" · ")}
        </div>
      )}
      <div className="muted" style={{ fontSize: 11 }}>
        Reconciliation: last sync {txt(sync.last_sync_ts ? new Date(sync.last_sync_ts * 1000).toISOString() : null, "never")}
        {" · "}certainty {txt(sync.certainty, "NEVER_SYNCED")}
        {" · "}exits captured {txt(sync.exits_captured, 0)}
        {" · "}recovered {txt(sync.recovered, 0)}
      </div>
    </>
  );
}

/* V6.5 §5.1 — global trade constraints panel (values persist in the existing
 * settings store; unsaved edits are visibly marked; invalid input is refused
 * by the backend and reported, never coerced). */
export function LiveTradeLimitsPanel({ onSaved }) {
  const [limits, setLimits] = useState(null);
  const [draft, setDraft] = useState({});
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);
  const load = useCallback(async () => {
    try {
      const res = await api.liveTestingStatus();
      const l = objOrNull(res?.risk) || {};
      setLimits(l);
      setDraft({
        max_active_trades: l.max_active_trades ?? "",
        max_active_trades_per_node_default: res?.max_active_trades_per_node_default ?? l.max_active_trades_per_node_default ?? 1,
      });
    } catch (e) {
      setErr(String((e && e.message) || e));
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  const dirty = limits && (
    String(draft.max_active_trades) !== String(limits.max_active_trades ?? "") ||
    String(draft.max_active_trades_per_node_default) !== String(limits.max_active_trades_per_node_default ?? 1)
  );
  const save = async () => {
    setMsg(null); setErr(null);
    try {
      const res = await api.liveTestingSettings({
        max_active_trades: Number(draft.max_active_trades),
        max_active_trades_per_node_default: Number(draft.max_active_trades_per_node_default),
      });
      if (res && res.ok === false) throw new Error(txt(res.message || res.error, "refused"));
      setMsg("Saved — effective immediately for the next admission check.");
      await load();
      if (onSaved) onSaved();
    } catch (e) {
      setErr(String((e && e.message) || e));
    }
  };
  return (
    <div className="panel" style={{ marginBottom: 10 }}>
      <div className="kit-head">
        <b>Global trade constraints</b>
        {dirty ? <Badge tone="warn">unsaved changes</Badge> : <Badge tone="mute">saved</Badge>}
      </div>
      <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap", fontSize: 12 }}>
        <label>
          MAX ACTIVE TRADES{" "}
          <input type="number" min={1} max={100} step={1} style={{ width: 70 }}
                 value={draft.max_active_trades ?? ""}
                 onChange={(e) => setDraft({ ...draft, max_active_trades: e.target.value })} />
        </label>
        <label>
          DEFAULT MAX ACTIVE TRADES PER NODE{" "}
          <input type="number" min={1} max={100} step={1} style={{ width: 70 }}
                 value={draft.max_active_trades_per_node_default ?? ""}
                 onChange={(e) => setDraft({ ...draft, max_active_trades_per_node_default: e.target.value })} />
          <span className="muted"> (inherited by nodes showing “Default”)</span>
        </label>
        <button className="btn success" disabled={!dirty} onClick={save}>Save</button>
        {msg && <span style={{ color: "#3fb950" }}>{msg}</span>}
        {err && <span style={{ color: "#f85149" }}>{err}</span>}
      </div>
      <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        Enforced at the execution boundary (global + per node, atomically). Reducing a limit
        never closes existing positions — it blocks new entries until the count drops.
      </div>
    </div>
  );
}
