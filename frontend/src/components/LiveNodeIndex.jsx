/* V5.1a §8 §9 §10 — the Live Testing node table.

 * One table, fed by the authoritative qualified-node index (`GET /api/nodes`)
 * instead of the legacy `nodes-table` projection. The index is the same payload
 * Deep Backtest consumes, so the two pages can never disagree about which nodes
 * exist, what bucket they are in, or what their research metrics are.
 *
 * What this file is careful about:
 *
 *  * the DEFAULT filter is `qualified` (Qualified / Alive / Eligible). A failed or
 *    historical node is never in the default view — it is reachable through an
 *    explicit filter, and the filter bar shows the server's own counts so the
 *    operator can see how many nodes each filter would show.
 *  * every metric comes from the row as the backend recorded it; a missing value
 *    renders `N/A`, never `0`.
 *  * the node label is the experiment-local number (`Node_7`), and the experiment
 *    id is printed above the table — a node number from a previous experiment can
 *    never be mistaken for one of these.
 *  * START/STOP calls the real enrolment endpoint and then re-reads the index, so
 *    the row state after the click is the backend's state, not an optimistic guess.
 *  * sorting is server-side and is offered on exactly the metric set §10 lists.
 */
import React, { useCallback, useEffect, useState } from "react";
import { api, fmt } from "../api.js";
import { Badge, Card } from "./ui.jsx";
import { arr, NA_TEXT, objOrNull, rows as safeRows, txt } from "../lib/safe.js";
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
  const [limit, setLimit] = useState(25);
  const [offset, setOffset] = useState(0);
  const [busyId, setBusyId] = useState(null);
  const [scheduleFor, setScheduleFor] = useState(null);
  const [riskDraft, setRiskDraft] = useState("");
  const [detail, setDetail] = useState(null);
  /* §7 — when the index is empty the page must say WHY, with the authoritative
   * numbers, instead of showing a blank table that looks broken. */
  const [pop, setPop] = useState(null);
  const [popErr, setPopErr] = useState(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const res = await api.nodes({
        filter, search: search || undefined, timeframe: timeframe || undefined,
        starred_only: starredOnly || undefined, sort_by: sortBy, sort_desc: sortDesc,
        limit, offset,
      });
      if (res && res.ok === false) throw new Error(txt(res.error, "the node index refused the filter"));
      setRows(safeRows(res?.nodes));
      setTotal(res?.total ?? 0);
      setMeta(res);
    } catch (e) { setErr(e); } finally { setLoading(false); }
  }, [filter, search, timeframe, starredOnly, sortBy, sortDesc, limit, offset]);

  useEffect(() => { load(); }, [load]);

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
    setBusyId(row.node_id); setMsg(null); setErr(null);
    try {
      const res = action === "start"
        ? await api.liveTestingStartNode(row.node_id)
        : await api.liveTestingStopNode(row.node_id);
      setMsg(`${txt(row.node_label, `Node_${row.node_id}`)}: ${action === "start" ? "STARTED" : "STOPPED"} — ${txt(res?.note, "")}`);
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
            onClick={() => { setOffset(0); setSortBy(key); setSortDesc(sortBy === key ? !sortDesc : true); }}>
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

  return (
    <Card
      title="Live test nodes"
      right={<Badge tone="mute">{total} node(s) · page {Math.floor(offset / limit) + 1}</Badge>}
    >
      {/* §27 — which experiment these node numbers belong to, on the table itself. */}
      <div className="muted" style={{ fontSize: 11.5, marginBottom: 6 }}>
        Experiment <b className="mono">{experimentId(exp)}</b>
        {exp.population !== undefined ? <> · population <b className="mono">{txt(exp.population)}</b></> : null}
        {range.length === 2 ? <> · node numbers <b className="mono">{txt(range[0])}–{txt(range[1])}</b></> : null}
        {" · "}numbers are local to this experiment
      </div>

      <div className="btn-row" style={{ marginBottom: 6, flexWrap: "wrap" }}>
        <select className="input" value={filter}
                onChange={(e) => { setOffset(0); setFilter(e.target.value); }}
                title="Which nodes to list (default: qualified / alive / eligible)">
          {FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}{filterTotals[f.value] !== undefined ? ` (${filterTotals[f.value]})` : ""}
            </option>
          ))}
        </select>
        <input className="input" style={{ width: 150 }} placeholder="search id / market / tf"
               value={search} onChange={(e) => { setOffset(0); setSearch(e.target.value); }} />
        <select className="input" value={timeframe}
                onChange={(e) => { setOffset(0); setTimeframe(e.target.value); }}>
          <option value="">any timeframe</option>
          {["M1", "M5", "M15", "M30", "H1", "H4", "D1"].map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <label className="muted" style={{ fontSize: 12 }}>
          <input type="checkbox" checked={starredOnly}
                 onChange={(e) => { setOffset(0); setStarredOnly(e.target.checked); }} /> starred only
        </label>
        <span className="muted" style={{ fontSize: 12 }}>sort</span>
        <select className="input" value={sortBy}
                onChange={(e) => { setOffset(0); setSortBy(e.target.value); }}>
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
      </div>

      {err && <div className="kit-inline-err">{err.message || String(err)}</div>}
      {msg && <div className="kit-ok" style={{ marginBottom: 6 }}>{msg}</div>}

      <div style={{ overflowX: "auto" }}>
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
              <th>Risk</th>
              <th>Schedule</th>
              <th>Live</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const m = objOrNull(r.metrics) || {};
              const rob = objOrNull(r.robustness) || {};
              const live = objOrNull(r.live) || {};
              const sched = objOrNull(r.schedule) || {};
              const risk = objOrNull(r.risk) || {};
              const bucket = txt(r.bucket, "unknown");
              const bcov = objOrNull(r.backtest_coverage) || {};
              const label = txt(r.node_label, `Node_${r.node_id}`);
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
                      <a href="#" title={`internal row id ${r.node_id}`}
                         onClick={(e) => {
                           e.preventDefault();
                           if (onOpenNode) onOpenNode(r.node_id);
                           setDetail(detail === r.node_id ? null : r.node_id);
                         }}>{label}</a>
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
                    <td className="mono">
                      {n2(risk.pct, " %")}{" "}
                      <span className="muted" style={{ fontSize: 10.5 }}>
                        {risk.source === "CUSTOM" ? "(node)" : "(global)"}
                      </span>
                    </td>
                    <td style={{ fontSize: 11 }}>
                      <button className="btn ghost" style={{ padding: "2px 6px" }}
                              title={txt(sched.description, "no schedule saved — the product default applies")}
                              onClick={() => setScheduleFor(r.node_id)}>
                        {sched.configured ? (sched.enabled === false ? "disabled" : "edit") : "default"}
                      </button>
                    </td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {txt(live.status, "IDLE")}
                      <span className="muted"> · {n0(live.closed)}/{n0(live.trades)}</span>
                    </td>
                    <td>
                      {live.status === "ACTIVE" || r.is_active
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
                  {detail === r.node_id && (
                    <tr>
                      <td colSpan={17} style={{ background: "rgba(255,255,255,0.02)" }}>
                        {/* §9 — the node's actual research record. Nothing here is
                            recalculated in the browser: it is the row the API served. */}
                        <div className="kit-grid" style={{ fontSize: 11.5 }}>
                          <div className="kit-kv"><span className="k">Node / row id</span>
                            <span className="mono">{label} · #{txt(r.node_id)}</span></div>
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
                            <span className="mono">{n2(risk.pct, " %")} ({txt(risk.source, NA_TEXT)}{risk.override_pct !== null && risk.override_pct !== undefined ? `, override ${n2(risk.override_pct, " %")}` : ""})</span></div>
                          <div className="kit-kv"><span className="k">Live state</span>
                            <span className="mono">{txt(live.status, "IDLE")} · {n0(live.trades)} trade(s), {n0(live.closed)} closed, P/L {n2(live.total_pnl)} (today {n2(live.today_pnl)})</span></div>
                          <div className="kit-kv"><span className="k">Open position</span>
                            <span className="mono">{r.position_open ? txt(objOrNull(r.position)?.ticket ?? r.position, "open") : "none"}</span></div>
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
                <td colSpan={17}>
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
          {txt(meta?.note, "")}{loading ? " loading…" : ""}
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
