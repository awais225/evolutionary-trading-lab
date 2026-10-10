/* V5.1a §8 §9 §10 — the Live Testing node table (V6.5.1: the unified control surface).
 *
 * One table, fed by the authoritative qualified-node index (`GET /api/nodes`)
 * instead of the legacy `nodes-table` projection. The index is the same payload
 * Deep Backtest consumes, so the two pages can never disagree about which nodes
 * exist, what bucket they are in, or what their research metrics are.
 *
 * V6.5.1 §2-§6 — this table is the ONE place node-level controls live:
 *
 *  * a genuine START/STOP lifecycle control: STOPPED shows START, STARTING and
 *    STOPPING are truthful pending states, RUNNING shows STOP — and the state
 *    is ALWAYS the backend's (worker state + lifecycle from `GET /api/nodes`),
 *    restored on refresh/navigation, with stale polling responses discarded.
 *  * inline Risk editing in the Risk cell (Mode A % of capital / Mode B
 *    constant money) — validated, saved through the node-config API, with
 *    saving/error states; the visible value updates from the saved result.
 *  * inline MAX ACTIVE TRADES, SL offset and TP offset cells in the same row.
 *  * the Schedule cell opens the structured schedule editor (unchanged).
 *
 * Every metric comes from the row as the backend recorded it; a missing value
 * renders `N/A`, never `0`. START/STOP calls the real lifecycle endpoints and
 * then re-reads the index, so the row state after the click is the backend's
 * state, never an optimistic guess.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, fmt } from "../api.js";
import { Badge, Card, useInterval } from "./ui.jsx";
import { arr, NA_TEXT, numOrNull, objOrNull, rows as safeRows, txt } from "../lib/safe.js";
import { ScheduleDialog } from "./LiveTestingPanels.jsx";

/* §10 — the filters the index supports, in the order an operator scans them.
 * "qualified" is the default: qualified / alive / eligible, by construction of
 * the backend classifier (a node is only ever in one bucket). */
const FILTERS = [
  { value: "qualified", label: "Qualified / Alive / Eligible" },
  { value: "all", label: "All" },
  { value: "alive", label: "Alive (incl. qualified)" },
  { value: "eligible", label: "Eligible (incl. qualified)" },
  { value: "failed", label: "Failed" },
  { value: "excluded", label: "Excluded" },
  { value: "blocked", label: "Blocked" },
  { value: "unknown", label: "Unknown" },
];

/* §10 — sortable columns. The key is what the backend sorts on; each alias is
 * resolved server-side against the row, then `metrics`, then `robustness`. */
const SORTS = [
  { value: "node_id", label: "Node" },
  { value: "fitness", label: "Fitness" },
  { value: "return", label: "Return %" },
  { value: "profit_factor", label: "Profit factor" },
  { value: "max_drawdown_pct", label: "Max DD %" },
  { value: "win_rate", label: "Win rate" },
  { value: "trades", label: "Trades" },
  { value: "robustness", label: "Robustness" },
  { value: "generation", label: "Generation" },
];

const BUCKET_TONE = {
  qualified: "ok", alive: "sim", failed: "danger", blocked: "warn",
  excluded: "mute", unknown: "warn",
};

const LIFECYCLE_TONE = {
  RUNNING: "ok", STARTING: "warn", STOPPING: "warn", STOPPED: "mute",
  IDLE: "mute", COMPLETED: "sim", ERROR: "danger", UNKNOWN: "warn",
};

function n2(v, suffix = "") {
  const n = Number(v);
  if (v === null || v === undefined || !Number.isFinite(n)) return NA_TEXT;
  return `${fmt.num(n, 2)}${suffix}`;
}

function n0(v) {
  const n = Number(v);
  if (v === null || v === undefined || !Number.isFinite(n)) return NA_TEXT;
  return String(Math.round(n));
}

function pct(v) {
  const n = Number(v);
  if (v === null || v === undefined || !Number.isFinite(n)) return NA_TEXT;
  // research metrics are fractions (0.0061 -> 0.61 %), live P/L is currency
  return `${fmt.num(n * 100, 2)} %`;
}

/* A run id / experiment id is a string, but a node may also carry a null run id
 * (a freshly created node before the run is stamped). Never print "null". */
function experimentId(exp) {
  const e = objOrNull(exp);
  return txt(e?.run_id, "no experiment yet");
}

/* ------------------------------------------------------------------ *
 * V6.5.1 §3 — inline Risk cell (Mode A % of capital / Mode B constant
 * money). Click the cell to edit; Save/Esc; saving + error states; the
 * displayed value always comes from the backend's saved result.
 * ------------------------------------------------------------------ */
function RiskCell({ row, onSaved }) {
  const risk = objOrNull(row.risk) || {};
  const rmode = objOrNull(row.risk_mode) || {};
  const mode = String(rmode.mode || "PERCENT").toUpperCase();
  const basis = String(rmode.capital_basis || "EQUITY").toUpperCase();
  const [editing, setEditing] = useState(false);
  const [draftMode, setDraftMode] = useState(mode);
  const [draftValue, setDraftValue] = useState("");
  const [draftBasis, setDraftBasis] = useState(basis);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);

  const effective = mode === "AMOUNT"
    ? { text: `${n2(rmode.risk_amount)} ${txt(objOrNull(rmode)?.currency, "")}`.trim(), tag: "money" }
    : { text: n2(risk.pct, " %"), tag: basis === "BALANCE" ? "% of balance" : "% of equity" };

  const open = () => {
    setDraftMode(mode);
    setDraftBasis(basis);
    setDraftValue(String(mode === "AMOUNT"
      ? (rmode.risk_amount ?? "")
      : (risk.override_pct ?? risk.pct ?? "")));
    setErr(null);
    setEditing(true);
  };

  const cancel = () => { setEditing(false); setErr(null); };

  const save = async () => {
    const v = numOrNull(draftValue);
    if (v === null || v <= 0) {
      setErr(`Risk value must be a positive number (got "${draftValue}")`);
      return;
    }
    setSaving(true); setErr(null);
    try {
      const body = draftMode === "AMOUNT"
        ? { risk_mode: "AMOUNT", risk_amount: v, risk_pct: null }
        : { risk_mode: "PERCENT", risk_pct: v, risk_capital_basis: draftBasis, risk_amount: null };
      const res = await api.liveTestingNodeConfig(row.node_id, body);
      setEditing(false);
      if (onSaved) onSaved(res);
    } catch (e) {
      // the value that failed is never shown as saved
      setErr(String(e?.message || e));
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <button className="btn ghost" style={{ padding: "2px 6px", fontSize: 11, textAlign: "left" }}
              title={`Risk: ${effective.text} (${effective.tag}, ${risk.source === "CUSTOM" ? "node override" : "global default"}). Click to edit this node's risk mode and value.`}
              onClick={open}>
        <span className="mono">{effective.text}</span>{" "}
        <span className="muted" style={{ fontSize: 10 }}>
          {effective.tag} ({risk.source === "CUSTOM" ? "node" : "global"}) ✎
        </span>
      </button>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 150 }}
         onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); cancel(); } }}>
      <select className="input" style={{ fontSize: 11, padding: "1px 4px" }} value={draftMode}
              disabled={saving}
              onChange={(e) => setDraftMode(e.target.value)}>
        <option value="PERCENT">% of capital</option>
        <option value="AMOUNT">Constant money</option>
      </select>
      {draftMode === "PERCENT" ? (
        <div style={{ display: "flex", gap: 3 }}>
          <input className="input mono" type="number" step="0.01" min="0.01" style={{ width: 62, fontSize: 11 }}
                 value={draftValue} disabled={saving} autoFocus
                 onChange={(e) => setDraftValue(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") cancel(); }} />
          <select className="input" style={{ fontSize: 10, padding: "1px 2px" }} value={draftBasis}
                  disabled={saving} title="capital basis for the percentage"
                  onChange={(e) => setDraftBasis(e.target.value)}>
            <option value="EQUITY">equity</option>
            <option value="BALANCE">balance</option>
          </select>
        </div>
      ) : (
        <input className="input mono" type="number" step="0.01" min="0.01" style={{ width: 100, fontSize: 11 }}
               value={draftValue} disabled={saving} autoFocus
               title="money at risk if the stop is hit (account currency)"
               onChange={(e) => setDraftValue(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") cancel(); }} />
      )}
      <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
        <button className="btn success" style={{ padding: "1px 8px", fontSize: 11 }} disabled={saving}
                onClick={save}>{saving ? "saving…" : "Save"}</button>
        <button className="btn ghost" style={{ padding: "1px 6px", fontSize: 11 }} disabled={saving}
                onClick={cancel}>Esc</button>
      </div>
      {err && <div className="kit-inline-err" style={{ fontSize: 10.5 }}>{err}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * V6.5.1 §5 — one numeric inline cell (max trades / SL offset / TP
 * offset). Commit on Save or Enter; Esc cancels; the visible value is
 * only replaced by the backend's saved result.
 * ------------------------------------------------------------------ */
function NumCell({ row, field, label, min, max, step = 1, width = 58, title, format, allowNull = true, onSaved }) {
  const raw = row[field];
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(raw === null || raw === undefined ? "" : String(raw));
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);
  const display = format ? format(raw) : (raw === null || raw === undefined ? "Default" : String(raw));

  const save = async () => {
    const t = draft.trim();
    let v = null;
    if (t !== "") {
      v = Number(t);
      if (!Number.isFinite(v) || (min !== undefined && v < min) || (max !== undefined && v > max)) {
        setErr(`${label} must be a number${min !== undefined ? ` ≥ ${min}` : ""}${max !== undefined ? ` ≤ ${max}` : ""} (got "${draft}")`);
        return;
      }
    } else if (!allowNull) {
      setErr(`${label} must not be empty`);
      return;
    }
    setSaving(true); setErr(null);
    try {
      const res = await api.liveTestingNodeConfig(row.node_id, { [field]: v });
      setEditing(false);
      if (onSaved) onSaved(res);
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <button className="btn ghost mono" style={{ padding: "2px 6px", fontSize: 11 }}
              title={`${title} — click to edit`}
              onClick={() => { setDraft(raw === null || raw === undefined ? "" : String(raw)); setErr(null); setEditing(true); }}>
        {display} ✎
      </button>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}
         onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setEditing(false); } }}>
      <input className="input mono" type="number" step={step} style={{ width, fontSize: 11 }}
             value={draft} disabled={saving} autoFocus
             onChange={(e) => setDraft(e.target.value)}
             onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }} />
      <div style={{ display: "flex", gap: 3 }}>
        <button className="btn success" style={{ padding: "0 6px", fontSize: 10 }} disabled={saving}
                onClick={save}>{saving ? "…" : "OK"}</button>
        <button className="btn ghost" style={{ padding: "0 5px", fontSize: 10 }} disabled={saving}
                onClick={() => setEditing(false)}>Esc</button>
      </div>
      {err && <div className="kit-inline-err" style={{ fontSize: 10 }}>{err}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * V6.5.1 §2 — the lifecycle control. Rendered ONLY from backend state
 * (`row.lifecycle` / `row.worker`) plus the explicit local pending state
 * taken when a request is in flight. Never a frontend-only boolean.
 * ------------------------------------------------------------------ */
function LifecycleAction({ row, pending, busy, onAct }) {
  const worker = objOrNull(row.worker) || {};
  const lc = String(row.lifecycle || "").toUpperCase();
  const state = String(worker.state || "").toUpperCase();
  const pend = pending;

  if (pend === "STARTING" || state === "STARTING") {
    return <button className="btn" disabled title="the worker is starting — wait for the backend to confirm RUNNING">STARTING…</button>;
  }
  if (pend === "STOPPING" || state === "STOPPING") {
    return <button className="btn" disabled title="stop requested — the in-flight cycle finishes first, then the final state is confirmed">STOPPING…</button>;
  }
  if (lc === "RUNNING") {
    return (
      <button className="btn danger" disabled={busy} onClick={() => onAct("stop")}
              title={`STOP this node only (worker ${state || "RUNNING"}). Stopping never closes its open positions; other nodes keep running.`}>
        STOP
      </button>
    );
  }
  if (lc === "UNKNOWN") {
    // The backend cannot confirm the true state (e.g. a restart left the node
    // enrolled with no live worker). Never claim RUNNING or STOPPED: offer the
    // two idempotent operations that establish the truth.
    return (
      <span style={{ display: "inline-flex", gap: 4 }}>
        <button className="btn success" disabled={busy} onClick={() => onAct("start")}
                title="state UNKNOWN (enrolled but no live worker) — START is idempotent and establishes the true state">START</button>
        <button className="btn ghost" disabled={busy} onClick={() => onAct("stop")}
                title="state UNKNOWN — STOP is idempotent and establishes the true state">STOP</button>
      </span>
    );
  }
  return (
    <button className="btn success" disabled={busy} onClick={() => onAct("start")}
            title={lc === "ERROR"
              ? `the last worker failed${worker.last_error ? ` (${worker.last_error})` : ""} — START creates a fresh worker`
              : "start this node's live-testing worker (backend confirms before the button flips)"}>
      {busy ? "…" : "START"}
    </button>
  );
}

export function LiveNodeTable({ onOpenNode, onToggleStar, globalRisk, onRiskChanged }) {
  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [msg, setMsg] = useState(null);

  const [filter, setFilter] = useState("qualified");
  const [search, setSearch] = useState("");
  const [timeframe, setTimeframe] = useState("");
  const [starredOnly, setStarredOnly] = useState(false);
  const [sortBy, setSortBy] = useState("fitness");
  const [sortDesc, setSortDesc] = useState(true);
  /* V5.2.3 §10 — the operator asked for ONE table, not pages: every matching
   * node is fetched (limit=0 = all rows) and rendered in one scrollable table. */
  const [allRows, setAllRows] = useState(0);
  const [busyId, setBusyId] = useState(null);
  /* V6.5.1 §2 — explicit per-node pending lifecycle while a START/STOP request
   * is in flight (prevents duplicate requests AND shows STARTING/STOPPING). */
  const [pending, setPending] = useState({});
  const [scheduleFor, setScheduleFor] = useState(null);
  const [riskDraft, setRiskDraft] = useState("");
  const [detail, setDetail] = useState(null);
  /* §7 — when the index is empty the page must say WHY, with the authoritative
   * numbers, instead of showing a blank table that looks broken. */
  const [pop, setPop] = useState(null);
  const [popErr, setPopErr] = useState(null);
  /* V6.5.1 §2 — stale-response guard: a polling response that lost the race to
   * a newer request is discarded instead of overwriting newer state. */
  const loadSeq = useRef(0);

  const load = useCallback(async (opts = {}) => {
    const seq = ++loadSeq.current;
    setLoading(true);
    // V6.5.1 §2 — a state-restore reload after a FAILED action must not wipe
    // the error the operator needs to see; only read errors set themselves.
    if (!opts.silent) setErr(null);
    try {
      // V5.3 §2 — unset filters are OMITTED (never sent as the string "undefined",
      // which the API rejects with 422 and which left this table empty).
      const res = await api.nodes({
        filter, search: search || undefined, timeframe: timeframe || undefined,
        ...(starredOnly ? { starred_only: true } : {}),
        sort_by: sortBy, sort_desc: sortDesc,
        limit: 0,
      });
      if (seq !== loadSeq.current) return;           // superseded by a newer load
      if (res && res.ok === false) throw new Error(txt(res.error, "the node index refused the filter"));
      setRows(safeRows(res?.nodes));
      setTotal(res?.total ?? 0);
      setAllRows(safeRows(res?.nodes).length);
      setMeta(res);
    } catch (e) {
      if (seq !== loadSeq.current) return;
      setErr(e);
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [filter, search, timeframe, starredOnly, sortBy, sortDesc]);

  useEffect(() => { load({ silent: true }); }, [load]);

  // V6.5.1 §2 — poll the authoritative state so the lifecycle badge and the
  // START/STOP control keep telling the truth after navigation, after a backend
  // state change, and while this page is open. Silent: a poll never wipes an
  // action error the operator has not acknowledged.
  useInterval(() => load({ silent: true }), 8000);

  // §7 — the population authority is fetched whenever the table is empty (or the
  // index failed), so the explanation always carries real backend numbers.
  useEffect(() => {
    if (loading || (safeRows(rows).length > 0 && !err)) return undefined;
    let live = true;
    api.nodePopulations()
      .then((d) => {
        if (!live) return;
        if (d && d.ok === false) { setPopErr(txt(d.error, "the population endpoint refused")); return; }
        setPop(d || null);
        setPopErr(null);
      })
      .catch((e) => { if (live) setPopErr(String((e && e.message) || e)); });
    return () => { live = false; };
  }, [loading, rows, err]);
  useEffect(() => {
    setRiskDraft(globalRisk === undefined || globalRisk === null ? "" : String(globalRisk));
  }, [globalRisk]);

  const act = async (row, action) => {
    if (busyId !== null) return;                 // duplicate-click guard
    const id = row.node_id;
    setBusyId(id); setMsg(null); setErr(null);
    // V6.5.1 §2 — immediately show the truthful pending state for THIS node
    setPending((p) => ({ ...p, [id]: action === "start" ? "STARTING" : "STOPPING" }));
    try {
      // V6.4 §5/§6 — START submits the canonical node id AND the selected
      // schedule (what this row is displaying is what gets sent and enforced).
      const sched = objOrNull(row?.schedule) || null;
      const schedulePayload = sched && (sched.days || sched.sessions || sched.timeframes
        || sched.windows || sched.timezone)
        ? {
            days: sched.days ?? null,
            sessions: sched.sessions ?? null,
            timeframes: sched.timeframes ?? null,
            windows: sched.windows ?? null,
            timezone: sched.timezone || "UTC",
            enabled: sched.enabled ?? null,
          }
        : null;
      const res = action === "start"
        ? await api.liveTestingStartNode(row.node_id, schedulePayload)
        : await api.liveTestingStopNode(row.node_id);
      // V6 — the button only ever reflects BACKEND-confirmed worker state. The
      // UI never flips START -> STOP on its own: load() below re-reads the table
      // (worker state included) after the backend has confirmed the outcome.
      const worker = objOrNull(res?.worker) || {};
      const confirmed = action === "start"
        ? (res?.worker_running === true || worker.running === true)
        : (res?.worker_stopped === true || worker.running === false);
      const schedNote = res?.schedule_source ? ` — schedule: ${res.schedule_source} (${txt(res?.schedule_timezone, "UTC")})` : "";
      setMsg(`${txt(row.node_label, `Node #${row.node_id}`)}${row.research_label ? ` · ${row.research_label}` : ""}: ${
        action === "start"
          ? (confirmed ? "STARTED — live-testing worker running (backend-confirmed)"
                       : "START requested — worker NOT confirmed running")
          : (confirmed ? "STOPPED — live-testing worker stopped (backend-confirmed)"
                       : "STOP requested — worker NOT confirmed stopped")
      }${schedNote} — ${txt(res?.note, "")}`);
    } catch (e) {
      // SCHEDULE_REQUIRED (409): no provenance exists — require a deliberate
      // selection instead of an invented default; open the editor for it.
      const detail = e?.detail || e?.data || {};
      if (detail?.code === "SCHEDULE_REQUIRED") {
        setErr({ message: `${detail.message || "a deliberate schedule selection is required"} — open the node's Schedule to select days/sessions/timeframes.` });
        setScheduleFor(row.node_id);
      } else {
        setErr(e);
      }
    } finally {
      // restore the truth from the backend whatever happened (silently: a
      // FAILED action keeps its error visible while the state is restored)
      try { await load({ silent: true }); } catch { /* the error above is already reported */ }
      setPending((p) => { const n = { ...p }; delete n[id]; return n; });
      setBusyId(null);
      if (onRiskChanged) onRiskChanged();
    }
  };

  // V6.5.1 §3-§5 — a settings cell was saved (risk / limits / offsets): show
  // the authoritative result and re-read the row from the backend.
  const onCellSaved = async (res) => {
    const cfg = objOrNull(res?.config) || {};
    setMsg(`Node #${txt(res?.strategy_id, "?")}: settings saved — ` +
           `risk mode ${txt(cfg.risk_mode, "PERCENT")}` +
           (cfg.risk_amount !== null && cfg.risk_amount !== undefined ? `, risk ${cfg.risk_amount}` : "") +
           (cfg.risk_pct !== null && cfg.risk_pct !== undefined ? `, risk % ${cfg.risk_pct}` : "") +
           (cfg.max_positions !== null && cfg.max_positions !== undefined ? `, max active trades ${cfg.max_positions}` : ", max active trades Default") +
           ` · SL ${cfg.sl_offset_pips ?? 0} / TP ${cfg.tp_offset_pips ?? 0} pips — other nodes unchanged.`);
    await load();
    if (onRiskChanged) onRiskChanged();
  };

  const stopAll = async () => {
    setBusyId("__all__"); setMsg(null); setErr(null);
    try {
      const res = await api.liveTestingStopAll("operator STOP ALL");
      setMsg(`STOP ALL: ${txt(res?.stopped, 0)} worker(s) stopped and confirmed, ${txt(res?.unenrolled_nodes, 0)} node(s) un-enrolled — positions_touched: false (open broker positions remain until closed explicitly).`);
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

  const counts = objOrNull(meta?.counts) || {};
  // the composite filters (alive / eligible / all) have no single bucket, so the
  // label count comes from the server's own filter_totals
  const filterTotals = objOrNull(meta?.filter_totals) || {};
  const exp = objOrNull(meta?.experiment) || {};
  const range = arr(exp.node_number_range);
  const isPostReset = exp.is_empty === true && total === 0;
  // V5.3 §3 — the boundary counts the API measured for THIS request
  const bc = (meta && meta.boundary_counts) || null;

  return (
    <Card
      title="Live test nodes"
      right={<Badge tone="mute">
        {loading ? "loading…" : `${total} node(s) · all rows in one table`}
        {allRows && total > allRows ? ` · ${allRows} rendered` : ""}
      </Badge>}
    >
      {/* V5.3 §3 — the hop counts. If this table is ever empty while Overview shows
        * eligible nodes, the line below names the hop that lost them: the
        * authoritative population, what this exact query matched, and what the
        * page actually rendered. */}
      {bc && (
        <div className="muted" style={{ fontSize: 11.5, marginBottom: 4 }}>
          authority <b className="mono">{txt(bc.population_live_eligible)}</b> live-eligible
          {" · "}query matched <b className="mono">{txt(bc.live_testing_query_result_count)}</b>
          {" · "}returned <b className="mono">{txt(bc.serialized_node_count)}</b>
          {" · "}rendered <b className="mono">{arr(rows).length}</b>
          {bc.filter_applied ? <> · filter <b className="mono">{txt(bc.filter_applied)}</b></> : null}
          {bc.rows_withheld ? " · rows withheld by a limit" : ""}
        </div>
      )}

      {/* §27 — which experiment these node numbers belong to, on the table itself. */}
      <div className="muted" style={{ fontSize: 11.5, marginBottom: 6 }}>
        Experiment <b className="mono">{experimentId(exp)}</b>
        {exp.population !== undefined ? <> · population <b className="mono">{txt(exp.population)}</b></> : null}
        {range.length === 2 ? <> · node numbers <b className="mono">{txt(range[0])}–{txt(range[1])}</b></> : null}
        {" · "}numbers are local to this experiment
      </div>

      <div className="btn-row" style={{ marginBottom: 6, flexWrap: "wrap" }}>
        <select className="input" value={filter}
                onChange={(e) => { setFilter(e.target.value); }}
                title="Which nodes to list (default: qualified / alive / eligible)">
          {FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}{filterTotals[f.value] !== undefined ? ` (${filterTotals[f.value]})` : ""}
            </option>
          ))}
        </select>
        <input className="input" style={{ width: 150 }} placeholder="search id / market / tf"
               value={search} onChange={(e) => { setSearch(e.target.value); }} />
        <select className="input" value={timeframe}
                onChange={(e) => { setTimeframe(e.target.value); }}>
          <option value="">any timeframe</option>
          {["M1", "M5", "M15", "M30", "H1", "H4", "D1"].map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <label className="muted" style={{ fontSize: 12 }}>
          <input type="checkbox" checked={starredOnly}
                 onChange={(e) => { setStarredOnly(e.target.checked); }} /> starred only
        </label>
        <span className="muted" style={{ fontSize: 12 }}>sort</span>
        <select className="input" value={sortBy}
                onChange={(e) => { setSortBy(e.target.value); }}>
          {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
        <button className="btn ghost" style={{ padding: "2px 8px" }}
                onClick={() => setSortDesc(!sortDesc)}>{sortDesc ? "desc ▼" : "asc ▲"}</button>
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
        <button className="btn danger" onClick={stopAll} disabled={busyId !== null}
                title="cancel EVERY node's live-testing worker (positions are never touched)">
          {busyId === "__all__" ? "stopping…" : "STOP ALL"}
        </button>
      </div>

      {err && <div className="kit-inline-err">{err.message || String(err)}</div>}
      {msg && <div className="kit-ok" style={{ marginBottom: 6 }}>{msg}</div>}

      <div className="table-scroll">
        <table className="table compact">
          <thead>
            <tr>
              <th>★</th>
              <th>{sortBtn("node_id", "Node")}</th>
              <th>Bucket</th>
              <th>Gen</th>
              <th>Market</th>
              <th>TF</th>
              <th>{sortBtn("fitness", "Fitness")}</th>
              <th>{sortBtn("return", "IS return")}</th>
              <th>{sortBtn("profit_factor", "PF")}</th>
              <th>{sortBtn("max_drawdown_pct", "Max DD")}</th>
              <th>{sortBtn("win_rate", "Win")}</th>
              <th>{sortBtn("trades", "Trades")}</th>
              <th>{sortBtn("robustness", "Robust")}</th>
              <th title="V6.5.1 §3 — risk mode + value, editable inline in this cell">Risk ✎</th>
              <th title="V6.5.1 §5.2 — this node's max active trades (Default = inherited)">Max trades ✎</th>
              <th title="V6.5.1 §9 — experimental SL offset in pips (0 = the strategy's own level)">SL off ✎</th>
              <th title="V6.5.1 §9 — experimental TP offset in pips (0 = the strategy's own level)">TP off ✎</th>
              <th>Schedule</th>
              <th title="V6.5.1 §2 — the backend's lifecycle state for this node">Lifecycle</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const m = objOrNull(r.metrics) || {};
              const rob = objOrNull(r.robustness) || {};
              const live = objOrNull(r.live) || {};
              const sched = objOrNull(r.schedule) || {};
              const bucket = txt(r.bucket, "unknown");
              const bcov = objOrNull(r.backtest_coverage) || {};
              const label = txt(r.node_label, `Node #${r.node_id}`);
              const rlabel = txt(r.research_label, "");
              const worker = objOrNull(r.worker) || {};
              const lc = String(r.lifecycle || "").toUpperCase() || "UNKNOWN";
              return (
                <React.Fragment key={r.node_id}>
                  <tr>
                    <td>
                      <button className="btn ghost" style={{ padding: 0, lineHeight: 1 }}
                              title={r.starred ? "On the shortlist" : "Add to shortlist"}
                              onClick={() => onToggleStar && onToggleStar(r.node_id)}>
                        {r.starred ? "★" : "☆"}
                      </button>
                    </td>
                    <td className="mono">
                      <a href="#" title={`canonical node id ${r.node_id}${rlabel ? ` (${rlabel} — study-local display number)` : ""}`}
                         onClick={(e) => {
                           e.preventDefault();
                           if (onOpenNode) onOpenNode(r.node_id);
                           setDetail(detail === r.node_id ? null : r.node_id);
                         }}>{label}{rlabel ? <span className="muted" style={{ fontSize: 10 }}> · {rlabel}</span> : null}</a>
                    </td>
                    <td>
                      <Badge tone={BUCKET_TONE[bucket] || "mute"}>{txt(r.bucket_label, bucket)}</Badge>
                    </td>
                    <td className="mono">{txt(r.generation, NA_TEXT)}</td>
                    <td className="mono">{txt(r.symbol, NA_TEXT)}</td>
                    <td className="mono">{txt(r.timeframe, NA_TEXT)}</td>
                    <td className="mono">{n2(r.fitness)}</td>
                    <td className="mono">{pct(m.return_pct)}</td>
                    <td className="mono">{n2(m.profit_factor)}</td>
                    <td className="mono">{pct(m.max_drawdown_pct)}</td>
                    <td className="mono">{pct(m.win_rate)}</td>
                    <td className="mono">{n0(m.trades)}</td>
                    <td className="mono">{n2(rob.score)}</td>
                    <td><RiskCell row={r} onSaved={onCellSaved} /></td>
                    <td>
                      <NumCell row={r} field="max_active_trades" label="Max active trades"
                               min={1} max={100} step={1} width={52}
                               title={`max active trades (effective ${txt(r.max_active_trades_effective, 1)}${r.max_active_trades_source === "CUSTOM" ? ", explicit override" : ", inherited default"})`}
                               allowNull
                               format={(v) => (v === null || v === undefined
                                 ? `Default (${txt(r.max_active_trades_default, 1)})` : String(v))}
                               onSaved={onCellSaved} />
                    </td>
                    <td>
                      <NumCell row={r} field="sl_offset_pips" label="SL offset"
                               min={-100000} max={100000} step={0.1} width={52}
                               title="experimental SL offset, pips (0 = strategy's own level; + = farther from entry)"
                               allowNull
                               format={(v) => (v === null || v === undefined ? "0" : String(v))}
                               onSaved={onCellSaved} />
                    </td>
                    <td>
                      <NumCell row={r} field="tp_offset_pips" label="TP offset"
                               min={-100000} max={100000} step={0.1} width={52}
                               title="experimental TP offset, pips (0 = strategy's own level; + = closer to entry)"
                               allowNull
                               format={(v) => (v === null || v === undefined ? "0" : String(v))}
                               onSaved={onCellSaved} />
                    </td>
                    <td style={{ fontSize: 11 }}>
                      <button className="btn ghost" style={{ padding: "2px 6px" }}
                              title={txt(sched.description, "no schedule saved — the product default applies")}
                              onClick={() => setScheduleFor(r.node_id)}>
                        {sched.configured ? (sched.enabled === false ? "disabled" : "edit") : "default"}
                      </button>
                      <span className="muted" style={{ fontSize: 10 }}> {txt(sched.timezone, "UTC")}</span>
                    </td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {/* V6.5.1 §2 — backend lifecycle, with the live-trade counts */}
                      <Badge tone={LIFECYCLE_TONE[lc] || "mute"}
                             title={`worker state: ${txt(worker.state, "none")}${worker.last_error ? ` — ${worker.last_error}` : ""}`}>
                        {lc}
                      </Badge>
                      <span className="muted"> · {n0(live.closed)}/{n0(live.trades)}</span>
                    </td>
                    <td>
                      <LifecycleAction row={r} pending={pending[r.node_id]}
                                       busy={busyId === r.node_id}
                                       onAct={(a) => act(r, a)} />
                    </td>
                  </tr>
                  {detail === r.node_id && (
                    <tr>
                      <td colSpan={20} style={{ background: "rgba(255,255,255,0.02)" }}>
                        {/* §9 — the node's actual research record. Nothing here is
                            recalculated in the browser: it is the row the API served. */}
                        <div className="kit-grid" style={{ fontSize: 11.5 }}>
                          <div className="kit-kv"><span className="k">Node (canonical id / research #)</span>
                            <span className="mono">{label}{rlabel ? ` · ${rlabel}` : ""}</span></div>
                          <div className="kit-kv"><span className="k">Experiment</span>
                            <span className="mono">{txt(r.experiment, experimentId(exp))}</span></div>
                          <div className="kit-kv"><span className="k">Generation / parent</span>
                            <span className="mono">{txt(r.generation, NA_TEXT)} · #{txt(r.parent_id, NA_TEXT)}</span></div>
                          <div className="kit-kv"><span className="k">Innovation</span>
                            <span className="mono">{txt(r.innovation, NA_TEXT)}</span></div>
                          <div className="kit-kv"><span className="k">Symbol / TF / direction</span>
                            <span className="mono">{txt(r.symbol, NA_TEXT)} · {txt(r.timeframe, NA_TEXT)} · {txt(r.direction, NA_TEXT)}</span></div>
                          <div className="kit-kv"><span className="k">Qualification</span>
                            <span className="mono">{txt(r.qualification, NA_TEXT)}</span></div>
                          <div className="kit-kv"><span className="k">Survival evidence</span>
                            <span className="mono">{txt(r.survival_evidence, NA_TEXT)}</span></div>
                          <div className="kit-kv"><span className="k">Bucket reason</span>
                            <span className="mono">{txt(r.bucket_reason, NA_TEXT)}</span></div>
                          <div className="kit-kv"><span className="k">Net profit / expectancy / avg trade</span>
                            <span className="mono">{n2(m.net_profit)} · {n2(m.expectancy)} · {n2(m.avg_trade)}</span></div>
                          <div className="kit-kv"><span className="k">Backtest data</span>
                            <span className="mono">
                              {txt(bcov.source, NA_TEXT)} · {txt(bcov.bars, NA_TEXT)} bars
                              {bcov.start || bcov.end ? ` · ${txt(bcov.start, "?")} → ${txt(bcov.end, "?")}` : ""}
                            </span></div>
                          <div className="kit-kv"><span className="k">Schedule</span>
                            <span className="mono">{txt(sched.description, "no schedule saved — product default (Mon–Fri, all sessions)")}</span></div>
                          <div className="kit-kv"><span className="k">Risk</span>
                            <span className="mono">{n2((objOrNull(r.risk) || {}).pct, " %")} ({txt((objOrNull(r.risk) || {}).source, NA_TEXT)}{r.risk_mode ? ` · mode ${txt(r.risk_mode.mode, "PERCENT")}` : ""})</span></div>
                          <div className="kit-kv"><span className="k">Live state</span>
                            <span className="mono">{txt(live.status, "IDLE")} · {n0(live.trades)} trade(s), {n0(live.closed)} closed, P/L {n2(live.total_pnl)} (today {n2(live.today_pnl)})</span></div>
                          <div className="kit-kv"><span className="k">Open position</span>
                            <span className="mono">{r.position_open ? txt(objOrNull(r.position)?.ticket ?? r.position, "open") : "none"}</span></div>
                          <div className="kit-kv"><span className="k">Max active trades</span>
                            <span className="mono">
                              {r.max_active_trades_source === "CUSTOM"
                                ? `override ${txt(r.max_active_trades, NA_TEXT)}`
                                : `Default (${txt(r.max_active_trades_default, 1)})`}
                              {" — effective "}{txt(r.max_active_trades_effective, 1)}
                              {r.offsets_active ? " · offsets active" : ""}
                            </span></div>
                          <div className="kit-kv"><span className="k">Experimental SL/TP offsets (pips)</span>
                            <span className="mono">
                              SL {txt(r.sl_offset_pips ?? 0)} · TP {txt(r.tp_offset_pips ?? 0)}
                              <span className="muted"> — 0 = strategy's own levels; +SL = farther from entry; +TP = closer to entry (experimental). Editable in the row.</span>
                            </span></div>
                          <div className="kit-kv"><span className="k">Worker</span>
                            <span className="mono">
                              {txt(worker.state, "IDLE")}
                              {worker.task_id ? ` · task ${txt(worker.task_id)}` : ""}
                              {worker.cycles !== undefined ? ` · ${txt(worker.cycles)} cycle(s)` : ""}
                              {worker.last_error ? ` · last error: ${txt(worker.last_error)}` : ""}
                            </span></div>
                          <div className="kit-kv"><span className="k">Signal logic</span>
                            <span className="mono">{txt(objOrNull(r.signal_logic)?.entry_long, NA_TEXT)}</span></div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
            {rows.length === 0 && !loading && (
              <tr>
                <td colSpan={20}>
                  {/* §7 — an explicit, truthful empty state: the authoritative
                      population numbers, the eligibility reason and the next step.
                      Never a blank table, never an invented node. */}
                  <div style={{ border: "1px solid var(--border, #2a2f3a)", borderRadius: 8,
                                padding: 10, background: "rgba(255,255,255,0.02)" }}>
                    <div style={{ fontWeight: 700, marginBottom: 4 }}>
                      {err
                        ? "THE NODE INDEX COULD NOT BE READ"
                        : (filter === "qualified" ? "NO LIVE-TESTING-ELIGIBLE NODE YET"
                                                  : `NO NODE IN THE "${filter}" FILTER`)}
                    </div>
                    <div className="muted" style={{ fontSize: 11.5, marginBottom: 6 }}>
                      {err
                        ? "The node index returned an error, so no row is shown. This is a read failure, not an empty population."
                        : isPostReset
                          ? "This experiment has no nodes yet — a FROM SCRATCH reset cleared the previous population. Start the research loop, or switch to the All filter to inspect excluded/legacy records."
                          : `No node matches the "${filter}" filter with the current search / timeframe / starred setting.`}
                    </div>
                    {popErr && <div className="kit-inline-err">node populations unavailable — {popErr}</div>}
                    {pop && pop.state && (
                      <div className="kit-strip" style={{ marginBottom: 6 }}>
                        {["TOTAL", "ALIVE", "QUALIFIED", "FINAL_TESTING_ELIGIBLE",
                          "DEEP_TESTING_ELIGIBLE", "LIVE_TESTING_ELIGIBLE",
                          "LIVE_TESTING_ACTIVE"].map((k) => (
                          <div className="item" key={k}>
                            <span className="k">{k.replace(/_/g, " ")}</span>
                            <span className="v mono">
                              {pop.state[k] === undefined || pop.state[k] === null
                                ? "–" : Number(pop.state[k]).toLocaleString()}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                    {pop && pop.state && (
                      <div className="muted" style={{ fontSize: 11.5 }}>
                        Every value above is the backend's own classification
                        (<span className="mono">{txt(pop.authority, "populations.py")}</span>).
                        A node becomes live-testing eligible by being QUALIFIED; dead, failed,
                        blocked, legacy and infrastructure-blocked nodes are never offered here.
                        {Number(pop.state.QUALIFIED || 0) > 0 && total === 0
                          ? " The index reports 0 rows while the population authority reports qualified nodes — press Reload; if it persists this is a defect, not an empty population."
                          : ""}
                      </div>
                    )}
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* V5.2.3 §10 — no pager: the table above holds every matching node and
        * scrolls inside its own frame (header pinned). */}
      <div className="btn-row" style={{ marginTop: 8 }}>
        <button className="btn ghost" onClick={load} disabled={loading}>
          {loading ? "loading…" : "reload"}
        </button>
        <span className="muted" style={{ fontSize: 11.5 }}>
          {total} {filter} node(s) · all {allRows} row(s) in the table above · one scrollable page
          {txt(meta?.note, "") ? ` · ${txt(meta?.note, "")}` : ""}
        </span>
      </div>

      {scheduleFor && (
        <ScheduleDialog
          nodeId={scheduleFor}
          onClose={() => setScheduleFor(null)}
          onSaved={async () => {
            setMsg(`Node_${scheduleFor}: schedule saved and enforced by the engine`);
            await load();
          }}
        />
      )}
    </Card>
  );
}

export default LiveNodeTable;
