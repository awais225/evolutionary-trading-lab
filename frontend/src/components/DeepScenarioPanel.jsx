// V6.6 §11–13 — scenario inspector: all of a node's DeepTest scenarios,
// preferred-scenario selection (explicit, never silently replaced), side-by-side
// comparison that names config/coverage differences, full analytics, and the
// searchable/sortable/paginated trade-level detail.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Badge, StateBlock } from "./ui";

function f2(v) { return v == null ? "N/A" : (typeof v === "number" ? v.toFixed(2) : String(v)); }
function f4(v) { return v == null ? "N/A" : (typeof v === "number" ? v.toFixed(4) : String(v)); }
function when(ts) { return ts ? new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19) + "Z" : "—"; }

const SUMMARY_ROWS = [
  ["Initial capital", (s) => f2(s.initial_capital)],
  ["Final equity", (s) => f2(s.final_equity)],
  ["Net profit", (s) => f2(s.net_profit)],
  ["Gross profit / loss", (s) => `${f2(s.gross_profit)} / ${f2(s.gross_loss)}`],
  ["Total costs", (s) => f2(s.total_costs)],
  ["Return %", (s) => (s.net_return_pct == null ? "N/A" : `${(s.net_return_pct * 100).toFixed(3)}%`)],
  ["Max drawdown %", (s) => (s.max_drawdown_pct == null ? "N/A" : `${(s.max_drawdown_pct * 100).toFixed(3)}%`)],
  ["Sharpe", (s) => (s.sharpe == null ? "N/A" : Number(s.sharpe).toFixed(3))],
  ["Profit factor", (s) => (s.profit_factor == null ? "N/A" : Number(s.profit_factor).toFixed(3))],
  ["Trades", (s) => s.trades ?? "N/A"],
  ["Wins / losses", (s) => `${s.winning_trades ?? "N/A"} / ${s.losing_trades ?? "N/A"}`],
  ["Win rate", (s) => (s.win_rate == null ? "N/A" : `${(s.win_rate * 100).toFixed(2)}%`)],
  ["Avg win / avg loss", (s) => `${f4(s.avg_win)} / ${f4(s.avg_loss)}`],
  ["Largest win / loss", (s) => `${f2(s.largest_win)} / ${f2(s.largest_loss)}`],
  ["Expectancy / trade", (s) => f4(s.expectancy_per_trade)],
  ["Long net P&L", (s) => `${f2(s.long?.net_pnl)} (${s.long?.trades ?? 0} trades)`],
  ["Short net P&L", (s) => `${f2(s.short?.net_pnl)} (${s.short?.trades ?? 0} trades)`],
  ["Avg hold / max hold", (s) => {
    const h = s.holding || {};
    return `${h.avg_minutes == null ? "N/A" : h.avg_minutes + " min"} / ${h.max_seconds == null ? "N/A" : h.max_seconds + " s"}`;
  }],
  ["Max consecutive W / L", (s) => `${s.max_consecutive_wins ?? "N/A"} / ${s.max_consecutive_losses ?? "N/A"}`],
];

const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const SESSION_KEYS = ["asia", "london", "newyork", "off"];

// §6.2 — OR-style independent exclusion dimensions over the SAVED trades:
// a trade is excluded when its entry day-of-week is excluded OR its entry
// session is. Timestamps = actual entry timestamp (UTC); day/session come
// from the framework fields recorded with each trade.
function isKeptTrade(t, days, sessions) {
  const dd = t.dow == null ? null : String(DAY_KEYS[Number(t.dow) % 7]);
  const sess = t.session ? String(t.session).toLowerCase() : null;
  if (dd && days.includes(dd)) return false;
  if (sess && sessions.includes(sess)) return false;
  return true;
}

export default function DeepScenarioPanel({ nodeId, node = null, scenarios: scenariosProp = null, onChanged,
                                            initialRunId = null, initialCompare = null }) {
  const [ownScenarios, setOwnScenarios] = useState(null);
  const scenarios = scenariosProp || ownScenarios || [];
  const reload = useCallback(async () => {
    try {
      const res = await api.deeptestScenarios({ node_id: nodeId });
      setOwnScenarios(res.scenarios || []);
    } catch { setOwnScenarios([]); }
  }, [nodeId]);
  useEffect(() => { if (!scenariosProp) reload(); }, [scenariosProp, reload]);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [trades, setTrades] = useState(null);
  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState("trade_no");
  const [sortDir, setSortDir] = useState(1);
  const [search, setSearch] = useState("");
  const [compareSel, setCompareSel] = useState([]);
  const [compare, setCompare] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  // V6.8 §6 — performance EXCLUSION filters: checkbox state drives the
  // filtered trade set which drives EVERY displayed statistic (recalculated
  // from the saved trades, never scaled from the original summary).
  const [flt, setFltState] = useState({ days: [], sessions: [] });
  const filtByRun = useRef({});          // per-scenario filter state (restore on switch)
  const filtSeq = useRef(0);             // latest-request-wins for filtered fetches
  const [filt, setFilt] = useState(null);
  const [filtBusy, setFiltBusy] = useState(false);
  const setFlt = (next) => {
    setFltState(next);
    if (selected) filtByRun.current[selected] = next;
  };
  const exclDays = flt.days;
  const exclSessions = flt.sessions;
  const filtersActive = flt.days.length > 0 || flt.sessions.length > 0;

  const loadFiltered = useCallback(async (runId, days, sessions) => {
    if (!runId) return;
    const seq = ++filtSeq.current;
    setFiltBusy(true);
    try {
      const res = await api.deeptestFilteredStats(runId, {
        exclude_days: days, exclude_sessions: sessions,
      });
      // §6.4 — an older async response can never overwrite the current results
      if (seq === filtSeq.current) setFilt(res);
    } catch { if (seq === filtSeq.current) setFilt(null); }
    finally { if (seq === filtSeq.current) setFiltBusy(false); }
  }, []);

  const loadDetail = useCallback(async (runId) => {
    setError(null);
    try {
      const res = await api.deeptestRunGet(runId);
      setDetail(res.scenario);
      // the complete saved trade list — the filter recalculation and the
      // visible list must both cover every trade of the scenario
      const tr = await api.deeptestRunTrades(runId, { limit: 10000, offset: 0 });
      setTrades(tr);
      setPage(0);
    } catch (e) {
      setError(e);
    }
  }, []);

  // filters recalculating never refetches the scenario itself — only the
  // derived analysis of its saved trades.
  // switching scenarios restores THAT scenario's own filter state (never a
  // leak of another scenario's exclusions) — §6.4
  useEffect(() => {
    setFltState(filtByRun.current[selected] || { days: [], sessions: [] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  useEffect(() => {
    if (selected) loadFiltered(selected, flt.days, flt.sessions);
  }, [selected, flt, loadFiltered]);

  useEffect(() => {
    if (scenarios.length && !selected) {
      setSelected(scenarios[0].run_id);
      loadDetail(scenarios[0].run_id);
    }
    if (!scenarios.length) { setSelected(null); setDetail(null); setTrades(null); }
  }, [scenarios, selected, loadDetail]);

  const prefer = async (runId, explicit = true) => {
    setBusy(true); setError(null);
    try {
      const res = await api.deeptestPrefer({ node_id: nodeId, run_id: runId, explicit });
      if (!res.ok) setError(res.error || "could not set the preferred scenario");
      if (onChanged) onChanged();
    } catch (e) { setError(e); } finally { setBusy(false); }
  };

  const runCompareIds = async (ids) => {
    setBusy(true); setError(null);
    try {
      const res = await api.deeptestCompare(ids);
      setCompare(res.ok ? res : null);
      if (!res.ok) setError(res.error || "comparison refused");
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const runCompare = () => runCompareIds(compareSel);

  // §5.2 — a comparison opened from the DEEPTESTED table (initialCompare)
  // runs automatically once the scenarios are known; a per-scenario INSPECT
  // (initialRunId) selects exactly that scenario.
  const initialRef = useRef({ run: initialRunId, cmp: initialCompare });
  useEffect(() => {
    if (!scenarios.length) return;
    const { run, cmp } = initialRef.current;
    if (run && scenarios.some((x) => x.run_id === run) && selected !== run) {
      setSelected(run);
      loadDetail(run);
    }
    if (cmp && cmp.length === 2 && !compare) {
      setCompareSel(cmp);
      runCompareIds(cmp);
    }
    initialRef.current = { run: null, cmp: null };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenarios]);

  const tradeRows = (trades?.trades || []).filter((t) => {
    // §6.2 — exclusion semantics: keep only trades matching NO active exclusion
    if (!isKeptTrade(t, flt.days, flt.sessions)) return false;
    if (!search.trim()) return true;
    const q = search.trim().toLowerCase();
    return JSON.stringify(t).toLowerCase().includes(q);
  });
  const sorted = [...tradeRows].sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;          // nulls always last
    if (bv == null) return -1;
    if (av < bv) return -sortDir;
    if (av > bv) return sortDir;
    return 0;
  });
  const pageSize = 10;
  const pageRows = sorted.slice(page * pageSize, page * pageSize + pageSize);

  const sel = detail || (scenarios.find((s) => s.run_id === selected) || null);
  // §6 — when filters are active EVERY displayed statistic comes from the
  // filtered trades' own recomputation (same accounting), never from the
  // original summary scaled or left in place. Unfiltered = the scenario's own
  // saved analytics, exactly as stored.
  const origSummary = (sel?.analytics || {}).summary || {};
  const summary = (filtersActive && filt?.filtered && !filt.filtered.error)
    ? (filt.filtered.summary || {})
    : origSummary;
  const breakdowns = (sel?.analytics || {}).breakdowns || {};
  const unavail = (sel?.analytics || {}).unavailable || [];
  const blocks = (filtersActive && filt?.filtered) ? filt.filtered
    : (filt?.original || {});
  const shownTradeCounts = filt?.trade_counts || null;

  return (
    <div className="panel" style={{ marginTop: 8 }}>
      <b style={{ fontSize: 12.5 }}>Scenarios — Node #{nodeId}</b>
      <div style={{ fontSize: 11, opacity: 0.8, marginTop: 2 }}>
        every scenario is an immutable snapshot (config, data fingerprint, coverage, assumptions,
        metrics, trades). Preferred = latest valid initially; an explicit choice is never silently
        replaced; failed/cancelled runs never displace a valid one.
      </div>
      {error && <div className="kit-inline-err">{String(error.message || error)}</div>}
      {!scenarios.length && (
        <StateBlock empty emptyText="no DeepTest scenarios yet — pick a period and press DEEPTEST" />
      )}
      {!!scenarios.length && (
        <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap", alignItems: "center" }}>
          <select value={selected || ""} onChange={(e) => { setSelected(e.target.value); loadDetail(e.target.value); }}>
            {scenarios.map((s) => (
              <option key={s.run_id} value={s.run_id}>
                {s.run_id} · {s.duration_key || "?"} · {s.status}
                {s.coverage_status === "PARTIAL" ? " · PARTIAL COVERAGE" : ""}
                {s.preferred_run_id === s.run_id ? " · PREFERRED" : ""} · {when(s.finished_at)}
              </option>
            ))}
          </select>
          <button disabled={busy} onClick={() => prefer(selected, true)}>Set preferred</button>
          <label style={{ fontSize: 11 }}>compare:</label>
          {scenarios.slice(0, 6).map((s) => (
            <label key={s.run_id} style={{ fontSize: 11 }}>
              <input type="checkbox"
                     checked={compareSel.includes(s.run_id)}
                     onChange={(e) => setCompareSel(e.target.checked
                       ? [...compareSel, s.run_id]
                       : compareSel.filter((x) => x !== s.run_id))} />{" "}
              {s.run_id}
            </label>
          ))}
          <button disabled={busy || compareSel.length < 2} onClick={runCompare}>
            Compare ({compareSel.length})
          </button>
        </div>
      )}
      {sel && (
        <div style={{ marginTop: 8, fontSize: 11.5 }}>
          <div className="kit-strip" style={{ border: "none", padding: 0, flexWrap: "wrap" }}>
            <div className="item"><span className="k">Status</span><span className="v mono">{sel.status}</span></div>
            <div className="item"><span className="k">Coverage</span>
              <span className="v mono">
                {sel.coverage_status || "—"}
                {sel.coverage_status === "PARTIAL" ? " (PARTIAL COVERAGE — never labelled complete)" : ""}
              </span></div>
            <div className="item"><span className="k">Requested range</span>
              <span className="v mono">
                {when((sel.provenance?.requested_period || {}).start_ts ?? sel.requested_start)} →{" "}
                {when((sel.provenance?.requested_period || {}).end_ts ?? sel.requested_end)}
              </span></div>
            <div className="item"><span className="k">Actual tested range</span>
              <span className="v mono">
                {when((sel.provenance?.actual_tested_range || {}).start_ts ?? sel.actual_start)} →{" "}
                {when((sel.provenance?.actual_tested_range || {}).end_ts ?? sel.actual_end)}{" "}
                ({(sel.provenance?.actual_tested_range || {}).bars ?? sel.bars_used ?? "—"} bars)
              </span></div>
            <div className="item"><span className="k">Dataset fingerprint</span>
              <span className="v mono">{String(sel.dataset_fingerprint || "—").slice(0, 18)}</span></div>
            <div className="item"><span className="k">Gaps (listed, never filled)</span>
              <span className="v mono">{(sel.gaps || []).length}</span></div>
            <div className="item"><span className="k">Started / finished</span>
              <span className="v mono">{when(sel.started_at)} / {when(sel.finished_at)}</span></div>
            <div className="item"><span className="k">Engine versions</span>
              <span className="v mono">{JSON.stringify(sel.engine_versions || {})}</span></div>
            <div className="item"><span className="k">Scenario id</span>
              <span className="v mono">{sel.run_id}{sel.signature ? ` · sig ${String(sel.signature).slice(0, 12)}` : ""}</span></div>
            <div className="item"><span className="k">Strategy fingerprints</span>
              <span className="v mono">genome {String(sel.genome_hash || "—").slice(0, 12)} · config {String(sel.config_hash || "—").slice(0, 12)}</span></div>
            {node && (
              <>
                <div className="item"><span className="k">Live layer</span>
                  <span className="v mono" title="live-testing records only — never copied from generation or DeepTest metrics">
                    {(node.live && (node.live.trades || node.live.closed || node.live.total_pnl != null))
                      ? `state ${node.live?.status || node.lifecycle || "?"} · ${node.live?.trades ?? 0} trades · P&L ${node.live?.total_pnl ?? "N/A"}`
                      : "NO LIVE DATA"}
                  </span></div>
                <div className="item"><span className="k">Node fitness / complexity</span>
                  <span className="v mono">{node.fitness == null ? "N/A" : Number(node.fitness).toFixed(4)}
                    {" / "}{node.signal_logic ? "declared" : "N/A"}</span></div>
                <div className="item"><span className="k">Species / mutation</span>
                  <span className="v mono">{node.family || "N/A"}{node.innovation ? ` · ${node.innovation}` : ""}</span></div>
                <div className="item"><span className="k">Generation metrics (source record)</span>
                  <span className="v mono">
                    ret {node.generation_metrics?.return_pct == null ? "N/A"
                      : (node.generation_metrics.return_pct * 100).toFixed(3) + "%"}
                    {" "}· PF {node.generation_metrics?.profit_factor ?? "N/A"}
                    {" "}· {node.generation_metrics?.trades ?? "N/A"} trades
                  </span></div>
              </>
            )}
            {sel.error && (
              <div className="item"><span className="k">Error</span><span className="v mono">{sel.error}</span></div>
            )}
          </div>
          {sel.status === "PARTIAL" && (
            <Badge tone="warn">PARTIAL COVERAGE — results valid only for the actual tested range</Badge>
          )}

          <div style={{ display: "flex", gap: 16, marginTop: 8, flexWrap: "wrap" }}>
            <div style={{ minWidth: 320 }}>
              <b>Metrics</b>
              {filtersActive && <Badge tone="warn">FILTERED — recomputed from the kept trades</Badge>}
              <table style={{ width: "100%", fontSize: 11 }}>
                <tbody>
                  {SUMMARY_ROWS.map(([label, fn]) => (
                    <tr key={label}>
                      <td style={{ opacity: 0.8 }}>{label}</td>
                      <td className="mono" style={{ textAlign: "right" }}>
                        {summary.initial_capital === undefined && label !== "Trades" ? "N/A" : (() => {
                          try { return String(fn(summary)); } catch { return "N/A"; }
                        })()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {unavail.length > 0 && (
                <div style={{ fontSize: 10.5, opacity: 0.8, marginTop: 4 }}>
                  N/A (with reason): {unavail.map((u) => `${u.field} — ${u.reason}`).join("; ")}
                </div>
              )}
            </div>
            <div style={{ minWidth: 260 }}>
              <b>Time breakdowns</b>
              <div style={{ fontSize: 11 }}>
                <div style={{ opacity: 0.8, marginTop: 4 }}>By day of week</div>
                {Object.entries(breakdowns.by_day_of_week || {}).map(([d, b]) => (
                  <div key={d} className="mono" style={{ display: "flex", justifyContent: "space-between" }}>
                    <span>{d}</span><span>{b.trades} trades · {f2(b.net_pnl)} · {b.win_rate == null ? "N/A" : (b.win_rate * 100).toFixed(0) + "%"}</span>
                  </div>
                ))}
                <div style={{ opacity: 0.8, marginTop: 6 }}>By session</div>
                {Object.entries(breakdowns.by_session || {}).map(([sname, b]) => (
                  <div key={sname} className="mono" style={{ display: "flex", justifyContent: "space-between" }}>
                    <span>{sname}</span><span>{b.trades} trades · {f2(b.net_pnl)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* V6.7 §9/§10 — monthly PnL + exit reasons + interactive EXCLUSION
              filters, all recalculated from this scenario's saved trades. */}
          <div style={{ display: "flex", gap: 16, marginTop: 10, flexWrap: "wrap" }}>
            <div style={{ minWidth: 260 }}>
              <b>Monthly PnL <span style={{ fontWeight: 400, opacity: 0.75 }}>(reconciles with the shown trades{filtersActive ? " · FILTERED" : ""})</span></b>
              <div style={{ fontSize: 11, marginTop: 4 }}>
                {Object.entries(blocks.monthly_pnl || {}).map(([m, v]) => (
                  <div key={m} className="mono" style={{ display: "flex", justifyContent: "space-between" }}>
                    <span>{m}</span>
                    <span className={v >= 0 ? "pos" : "neg"}>{f2(v)}</span>
                  </div>
                ))}
                {!Object.keys(blocks.monthly_pnl || {}).length && (
                  <span className="muted">{filtBusy ? "computing…" : (filtersActive ? "no trades match the filters" : "no saved trades")}</span>
                )}
              </div>
            </div>
            <div style={{ minWidth: 260 }}>
              <b>Exit reasons <span style={{ fontWeight: 400, opacity: 0.75 }}>(count + net PnL, from the shown trades{filtersActive ? " · FILTERED" : ""})</span></b>
              <div style={{ fontSize: 11, marginTop: 4 }}>
                {Object.entries(blocks.exit_reasons || {}).map(([r, v]) => (
                  <div key={r} className="mono" style={{ display: "flex", justifyContent: "space-between" }}>
                    <span>{r}</span><span>{v.count} × <span className={v.net_pnl >= 0 ? "pos" : "neg"}>{f2(v.net_pnl)}</span></span>
                  </div>
                ))}
                {!Object.keys(blocks.exit_reasons || {}).length && (
                  <span className="muted">{filtBusy ? "computing…" : (filtersActive ? "no trades match the filters" : "no saved trades")}</span>
                )}
              </div>
            </div>
            <div style={{ flex: "1 1 380px", minWidth: 380 }}>
              <b>
                Performance filters
                {(exclDays.length + exclSessions.length) > 0 && (
                  <Badge tone="warn">{exclDays.length + exclSessions.length} active</Badge>
                )}
              </b>
              <div style={{ fontSize: 10.5, opacity: 0.8, marginTop: 2 }}>
                exclude days / sessions (OR: excluded day OR excluded session removes the trade).
                Recalculated immediately from saved trades; never creates a scenario, never alters
                stored trades, never touches live.
              </div>
              <div style={{ display: "flex", gap: 10, marginTop: 6, flexWrap: "wrap" }}>
                <div>
                  <div style={{ opacity: 0.8, fontSize: 10.5 }}>exclude day-of-week</div>
                  {DAY_KEYS.map((d) => (
                    <label key={d} style={{ fontSize: 11, marginRight: 6 }}>
                      <input type="checkbox" checked={exclDays.includes(d)}
                             onChange={(e) => setFlt({
                               days: e.target.checked ? [...exclDays, d] : exclDays.filter((x) => x !== d),
                               sessions: exclSessions,
                             })} /> {d}
                    </label>
                  ))}
                </div>
                <div>
                  <div style={{ opacity: 0.8, fontSize: 10.5 }}>exclude session</div>
                  {SESSION_KEYS.map((sx) => (
                    <label key={sx} style={{ fontSize: 11, marginRight: 6 }}>
                      <input type="checkbox" checked={exclSessions.includes(sx)}
                             onChange={(e) => setFlt({
                               days: exclDays,
                               sessions: e.target.checked ? [...exclSessions, sx] : exclSessions.filter((x) => x !== sx),
                             })} /> {sx}
                    </label>
                  ))}
                </div>
                <button className="btn" style={{ fontSize: 10.5, alignSelf: "flex-start" }}
                        title="restores the scenario's original unfiltered metrics and trade list"
                        onClick={() => { setFlt({ days: [], sessions: [] }); if (selected) filtByRun.current[selected] = { days: [], sessions: [] }; }}>
                  Reset filters
                </button>
              </div>
              {!!filt && (
                <div style={{ fontSize: 11, marginTop: 6 }}>
                  <div className="mono" style={{ opacity: 0.85 }}>
                    trades: {filt.trade_counts?.kept ?? tradeRows.length} of {filt.trade_counts?.original ?? (trades?.total ?? "—")} kept
                    ({filt.trade_counts?.excluded ?? 0} excluded)
                    {filtBusy ? " · recalculating…" : ""}
                  </div>
                  <div style={{ display: "flex", gap: 14, marginTop: 4, flexWrap: "wrap" }}>
                    <div>
                      <div style={{ opacity: 0.8, fontSize: 10.5 }}>original</div>
                      <div className="mono">
                        net {f2(filt.original?.summary?.net_profit)} · ret{" "}
                        {filt.original?.summary?.net_return_pct == null ? "N/A"
                          : (filt.original.summary.net_return_pct * 100).toFixed(3) + "%"}
                        {" "}· PF {f2(filt.original?.summary?.profit_factor)}
                        {" "}· trades {filt.original?.summary?.trades ?? filt.trade_counts?.original ?? (trades?.total ?? 0)}
                      </div>
                    </div>
                    <div>
                      <div style={{ opacity: 0.8, fontSize: 10.5 }}>filtered</div>
                      <div className="mono">
                        net {f2(filt.filtered?.summary?.net_profit)} · ret{" "}
                        {filt.filtered?.summary?.net_return_pct == null ? "N/A"
                          : (filt.filtered.summary.net_return_pct * 100).toFixed(3) + "%"}
                        {" "}· PF {f2(filt.filtered?.summary?.profit_factor)}
                        {" "}· trades {filt.filtered?.summary?.trades ?? filt.trade_counts?.kept ?? tradeRows.length}
                      </div>
                    </div>
                  </div>
                  <div style={{ opacity: 0.75, fontSize: 10, marginTop: 4 }}>
                    {filt.disclosure}
                  </div>
                </div>
              )}
            </div>
          </div>

          {!!(sel.provenance?.assumptions || sel.provisional) && (
            <details style={{ marginTop: 8, fontSize: 11 }}>
              <summary>assumptions &amp; provisional flags (saved with this scenario)</summary>
              <pre className="mono" style={{ maxHeight: 200, overflow: "auto", fontSize: 10.5 }}>
                {JSON.stringify({
                  ...(sel.provenance?.assumptions || {}),
                  provisional: sel.provenance?.provisional ?? null,
                }, null, 2)}
              </pre>
            </details>
          )}

          <div style={{ marginTop: 10 }}>
            <b>Trades ({shownTradeCounts
              ? `${shownTradeCounts.kept}${filtersActive ? ` of ${shownTradeCounts.original}` : ""}`
              : (trades?.total ?? "—")})</b>
            {filtersActive && <Badge tone="warn">FILTERED</Badge>}
            <div style={{ display: "flex", gap: 8, alignItems: "center", margin: "6px 0", flexWrap: "wrap" }}>
              <input placeholder="search trades" value={search}
                     onChange={(e) => { setSearch(e.target.value); setPage(0); }} style={{ width: 180 }} />
              <span style={{ fontSize: 10.5, opacity: 0.75 }}>sort:</span>
              {["trade_no", "entry_ts", "exit_ts", "net_pnl", "hold_seconds"].map((k) => (
                <button key={k} style={{ fontSize: 10.5 }}
                        onClick={() => { if (sortKey === k) setSortDir(-sortDir); else { setSortKey(k); setSortDir(1); } }}>
                  {k}{sortKey === k ? (sortDir === 1 ? " ▲" : " ▼") : ""}
                </button>
              ))}
            </div>
            <div className="scroll-y table-scroll" style={{ maxHeight: 320 }}>
              <table style={{ width: "100%", fontSize: 10.5 }} className="tbl mono">
                <thead>
                  <tr>
                    {["#", "side", "entry", "exit", "volume",
                      "SL", "TP", "exit reason", "hold",
                      "net P&L", "session"].map((h) => (
                      <th key={h} style={{ position: "sticky", top: 0, background: "var(--bg-box, #1a2332)", zIndex: 1 }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((t) => (
                    <tr key={t.id ?? t.trade_no}>
                      <td>{t.trade_no}</td>
                      <td>{t.side}</td>
                      <td>{when(t.entry_ts)} @ {f2(t.entry_price)}</td>
                      <td>{when(t.exit_ts)} @ {f2(t.exit_price)}</td>
                      <td>{t.volume ?? "N/A"}</td>
                      <td>{t.sl == null ? "N/A" : t.sl}</td>
                      <td>{t.tp == null ? "N/A" : t.tp}</td>
                      <td>{t.exit_reason || "N/A"}</td>
                      <td>{t.hold_seconds == null ? "N/A" : `${Math.round(t.hold_seconds)}s`}</td>
                      <td>{f2(t.net_pnl)}</td>
                      <td>{t.session || "N/A"}{t.dow != null ? ` · d${t.dow}` : ""}</td>
                    </tr>
                  ))}
                  {!pageRows.length && (
                    <tr><td colSpan={11} style={{ opacity: 0.7 }}>no trades match</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 6, fontSize: 11, alignItems: "center" }}>
              <button disabled={page === 0} onClick={() => setPage(page - 1)}>prev</button>
              <span>page {page + 1} / {Math.max(1, Math.ceil(sorted.length / pageSize))}</span>
              <button disabled={(page + 1) * pageSize >= sorted.length} onClick={() => setPage(page + 1)}>next</button>
              <span style={{ opacity: 0.7 }}>{sorted.length} row(s)</span>
            </div>
          </div>
        </div>
      )}

      {compare && (
        <div style={{ marginTop: 12, fontSize: 11 }} className="panel">
          <b>Scenario comparison — side by side</b>
          <div className="muted" style={{ marginTop: 2 }}>
            each scenario keeps its OWN saved results; trades and statistics are never combined
          </div>
          {(compare.warnings || [compare.warning]).filter(Boolean).map((w) => (
            <div key={w} className="kit-inline-err" style={{ marginTop: 4 }}>{w}</div>
          ))}
          <div style={{ display: "flex", gap: 12, marginTop: 8, flexWrap: "wrap" }}>
            {(compare.scenarios || []).map((sc, i) => (
              <div key={sc.run_id} style={{ flex: "1 1 280px", border: "1px solid var(--border)", borderRadius: 6, padding: 8 }}>
                <b>{i === 0 ? "Scenario A" : "Scenario B"}</b>
                <div className="muted mono" style={{ fontSize: 9.5 }}>{sc.run_id}</div>
                <div className="mono" style={{ fontSize: 10.5, marginTop: 4 }}>
                  status {sc.status} · coverage {sc.coverage_status || "—"} · bars used {sc.bars_used ?? "—"}
                  <br />requested {when((sc.requested || {}).start_ts)} → {when((sc.requested || {}).end_ts)}
                  <br />actual {when((sc.actual_tested_range || {}).start_ts)} → {when((sc.actual_tested_range || {}).end_ts)}
                  <br />capital {sc.total_capital ?? "—"} · risk {sc.risk_pct ?? "—"}%
                  <br />genome {String(sc.genome_hash || "—").slice(0, 10)} · config {String(sc.config_hash || "—").slice(0, 10)}
                  <br />data fp {String(sc.dataset_fingerprint || "—").slice(0, 14)}
                </div>
                <button className="btn" style={{ fontSize: 10, marginTop: 6 }}
                        onClick={() => { setSelected(sc.run_id); loadDetail(sc.run_id); setCompare(null); setCompareSel([]); }}>
                  Inspect this scenario
                </button>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 8 }}>
            <b>metrics side by side (left = A, right = B; Δ = A − B)</b>
            <table className="tbl mono" style={{ width: "100%", fontSize: 10.5, marginTop: 4 }}>
              <thead>
                <tr><th>metric</th><th>A</th><th>B</th><th>Δ</th></tr>
              </thead>
              <tbody>
                {(compare.metrics || []).map((r) => (
                  <tr key={r.metric}>
                    <td>{r.metric}{r.unavailable_reason ? <span className="muted" title={r.unavailable_reason}> (N/A)</span> : null}</td>
                    <td className="mono">{r.left == null ? "N/A" : String(r.left)}</td>
                    <td className="mono">{r.right == null ? "N/A" : String(r.right)}</td>
                    <td className="mono">{r.delta == null
                      ? <span className="muted">—</span>
                      : <span className={r.delta >= 0 ? "pos" : "neg"}>{r.delta >= 0 ? "+" : ""}{Number(r.delta).toFixed(4)}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ display: "flex", gap: 16, marginTop: 8, flexWrap: "wrap" }}>
            {["left", "right"].map((side) => {
              const b = ((compare.monthly_pnl || {})[side]) || {};
              return (
                <div key={side} style={{ flex: "1 1 280px" }}>
                  <b>Monthly PnL &amp; exits — {side === "left" ? "Scenario A" : "Scenario B"}</b>
                  <div className="mono" style={{ fontSize: 10.5, marginTop: 4 }}>
                    {Object.entries(b.monthly_pnl || {}).map(([m, v]) => (
                      <div key={m} style={{ display: "flex", justifyContent: "space-between" }}>
                        <span>{m}</span><span className={v >= 0 ? "pos" : "neg"}>{f2(v)}</span>
                      </div>
                    ))}
                    {!Object.keys(b.monthly_pnl || {}).length ? <span className="muted">no monthly data</span> : null}
                    <div style={{ marginTop: 6 }}>
                      {Object.entries(b.exit_reasons || {}).map(([r, v]) => (
                        <div key={r} style={{ display: "flex", justifyContent: "space-between" }}>
                          <span>{r}</span><span>{v.count} × {f2(v.net_pnl)}</span>
                        </div>
                      ))}
                    </div>
                    <div className="muted" style={{ marginTop: 4 }}>trades in scenario: {b.trade_count ?? "—"}</div>
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{ marginTop: 8 }}>
            <b>config &amp; coverage differences</b>
            {(compare.config_and_coverage_differences || []).length === 0 && (
              <div style={{ opacity: 0.8 }}>none among the compared fields</div>
            )}
            <table style={{ width: "100%", fontSize: 10.5 }} className="tbl mono">
              <tbody>
                {(compare.config_and_coverage_differences || []).map((d) => (
                  <tr key={d.field}>
                    <td>{d.field}</td>
                    <td>{JSON.stringify(d.left)}</td>
                    <td>{JSON.stringify(d.right)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
