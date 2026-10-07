import React, { useState, useEffect, useCallback } from "react";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import StructuredError from "../components/StructuredError.jsx";
import HistoricalBacktestPanel from "../components/HistoricalBacktestPanel.jsx";
import { SimulatorBanner, SourceChip, Badge } from "../components/ui.jsx";
import { NA_TEXT, numOrNull, rows as safeRows, txt } from "../lib/safe.js";
import HistoricalRunResults from "../components/HistoricalRunResults.jsx";

export default function Mt5Backtest() {
  const { selectedStrategyId, setSelectedStrategyId, shortlist, navigateTab } = useLab() || {};
  const [nodeIdInput, setNodeIdInput] = useState(selectedStrategyId ? String(selectedStrategyId) : "240");
  const [activeStrategy, setActiveStrategy] = useState(null);
  const [historyResults, setHistoryResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [successMsg, setSuccessMsg] = useState(null);
  // V4.6 — MT5 historical backtest runs (the honest path)
  const [histRunId, setHistRunId] = useState(null);
  const [histRuns, setHistRuns] = useState([]);
  // V5 §19 — real MT5 historical-data availability and queue limits
  const [caps, setCaps] = useState(null);
  const [capsErr, setCapsErr] = useState(null);
  const [histVersion, setHistVersion] = useState(0);
  // V5 §19 — pick the node to test from the research population (search/sort/filter/star)
  const [picker, setPicker] = useState(null);
  const [pickerErr, setPickerErr] = useState(null);
  const [pickerLoading, setPickerLoading] = useState(false);
  const [pSearch, setPSearch] = useState("");
  const [pStatus, setPStatus] = useState("");
  const [pSort, setPSort] = useState("return");
  const [pDir, setPDir] = useState("desc");
  const [pStarred, setPStarred] = useState(false);
  const [pPage, setPPage] = useState(0);
  const PICKER_PAGE_SIZE = 10;
  const [pSel, setPSel] = useState(null);

  // V5 §19 — the node's own symbol/timeframe: availability, integrity, reuse, fetch
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState(null);
  const [syncErr, setSyncErr] = useState(null);

  // Configuration state
  const [deposit, setDeposit] = useState("10000");
  const [leverage, setLeverage] = useState("100");
  const [spread, setSpread] = useState("20");
  const [commission, setCommission] = useState("7.0");
  const [slippage, setSlippage] = useState("1.0");
  const [startDate, setStartDate] = useState("2026-01-01");
  const [endDate, setEndDate] = useState("2026-10-01");

  const loadStrategy = useCallback(async (sid) => {
    if (!sid) return;
    setLoading(true);
    setError(null);
    try {
      const s = await api.authoritativeStrategy(sid);
      setActiveStrategy(s);
      if (setSelectedStrategyId) setSelectedStrategyId(sid);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [setSelectedStrategyId]);

  const loadHistory = useCallback(async (sid = null) => {
    try {
      const res = await api.mt5BacktestResults(sid);
      setHistoryResults(res.results || []);
    } catch (err) {
      console.error("Failed to load MT5 history:", err);
    }
  }, []);

  const loadHistoricalRuns = useCallback(async (sid) => {
    if (!sid) return;
    try {
      const res = await api.mt5HistoricalRuns({ strategy_id: sid, limit: 25 });
      const runs = res.runs || [];
      setHistRuns(runs);
      setHistRunId((prev) => (prev && runs.some((r) => r.run_id === prev) ? prev : (runs[0]?.run_id || null)));
    } catch (err) {
      setHistRuns([]);
    }
  }, []);

  const loadCaps = useCallback(async (refresh = false) => {
    setCapsErr(null);
    try {
      setCaps(await api.mt5HistoricalCapabilities(refresh === true));
    } catch (e) {
      setCapsErr(e);
    }
  }, []);

  /* §19 — fetch/extend the stored dataset for the selected node's own symbol and
   * timeframe. This calls the lab's real data engine (`POST /api/data/sync`),
   * which reuses stored data when it is already up to date and otherwise asks the
   * *active bridge*. Its verbatim answer is shown — including `no_data_from_bridge`
   * (reported as DATA_UNAVAILABLE, never as a strategy failure) and including the
   * fact that a simulator bridge writes simulated bars, which are labelled as such
   * and are never presented as MT5 data. */
  const fetchNodeData = useCallback(async (symbol, timeframe) => {
    if (!symbol || !timeframe || syncing) return;
    setSyncing(true); setSyncErr(null); setSyncResult(null);
    try {
      const res = await api.dataSync({ symbol, timeframe, force: false });
      setSyncResult(res);
      await loadCaps(true);
    } catch (e) {
      setSyncErr(e);
    } finally {
      setSyncing(false);
    }
  }, [syncing, loadCaps]);

  useEffect(() => { loadCaps(); }, [loadCaps]);

  useEffect(() => {
    const sid = selectedStrategyId || 240;
    setNodeIdInput(String(sid));
    loadStrategy(sid);
    loadHistory(sid);
    loadHistoricalRuns(sid);
  }, [selectedStrategyId, loadStrategy, loadHistory, loadHistoricalRuns, histVersion]);

  // §19 — the selected node's own symbol/timeframe and what the lab actually has for it
  const nodeSymbol = String(activeStrategy?.symbol || "").toUpperCase() || null;
  const nodeTimeframe = String(activeStrategy?.timeframe || "").toUpperCase() || null;
  const nodeDatasets = nodeSymbol && nodeTimeframe
    ? (caps?.datasets || []).filter((d) => String(d.symbol || "").toUpperCase() === nodeSymbol
        && String(d.timeframe || "").toUpperCase() === nodeTimeframe && d.eligible)
    : [];
  const nodeRejections = nodeSymbol && nodeTimeframe
    ? (caps?.rejected_datasets || []).filter((d) => String(d.symbol || "").toUpperCase() === nodeSymbol
        && String(d.timeframe || "").toUpperCase() === nodeTimeframe)
    : [];

  const loadPicker = useCallback(async (over = {}) => {
    const q = { search: pSearch, status: pStatus, sort: pSort, dir: pDir,
                shortlist_only: pStarred, limit: PICKER_PAGE_SIZE, offset: (over.page ?? pPage) * PICKER_PAGE_SIZE };
    if (!q.search) delete q.search;
    if (!q.status) delete q.status;
    if (!q.shortlist_only) delete q.shortlist_only;
    setPickerLoading(true); setPickerErr(null);
    try {
      setPicker(await api.researchStrategies(q));
    } catch (e) {
      setPickerErr(e);
    } finally {
      setPickerLoading(false);
    }
  }, [pSearch, pStatus, pSort, pDir, pStarred, pPage]);

  useEffect(() => { loadPicker(); }, [loadPicker]);

  const toggleStar = useCallback(async (id) => {
    try {
      await api.toggleShortlist(id);
      await loadPicker();
    } catch (e) {
      setPickerErr(e);
    }
  }, [loadPicker]);

  const handleLookup = (e) => {
    e?.preventDefault();
    const cleanId = parseInt(nodeIdInput.replace(/[^0-9]/g, ""), 10);
    if (!cleanId) return;
    loadStrategy(cleanId);
    loadHistory(cleanId);
  };

  return (
    <div className="mt5-backtest-page p-6 max-w-7xl mx-auto space-y-6 animate-in fade-in duration-300">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 bg-slate-900/80 p-4 rounded-xl border border-slate-800 shadow-md">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            <span>MT5 STRATEGY TESTER &amp; BACKTEST ENGINE</span>
            <span className="text-xs px-2 py-0.5 rounded font-mono bg-indigo-950 text-indigo-400 border border-indigo-800">
              V4.0 LEGACY RECORDS
            </span>
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            The section below lists <b>V4.0 stored records</b> (read-only). New historical backtests are executed by the
            V4.6 engine above, which stores real engine results, equity, trades and provenance — the V4.0 record table
            predates that engine and its rows were not produced by it.
          </p>
        </div>

        {/* Node Selector Form */}
        <form onSubmit={handleLookup} className="flex items-center gap-2">
          <label className="text-xs font-semibold text-slate-300">Strategy Node:</label>
          <input
            type="text"
            value={nodeIdInput}
            onChange={(e) => setNodeIdInput(e.target.value)}
            placeholder="e.g. 240"
            className="w-28 px-3 py-1.5 bg-slate-950 border border-slate-700 rounded-lg text-sm font-mono text-cyan-300 focus:outline-none focus:border-cyan-500"
          />
          <button
            type="submit"
            disabled={loading}
            className="px-3.5 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white font-medium text-xs rounded-lg transition-colors shadow disabled:opacity-50"
          >
            {loading ? "Loading..." : "Select"}
          </button>
        </form>
      </div>

      {/* V4.8 — the environment this page actually runs in, stated up front */}
      <SimulatorBanner strict source={histRuns?.[0]?.data?.source || "SIMULATOR"}
        detail="REAL MT5 TRADING UNAVAILABLE — SIMULATOR MODE ACTIVE. Historical runs execute over the stored MT5 bars with the lab's own engine: this is not a MetaTrader Strategy Tester session and never a live order." />
      <div className="kit-strip" style={{ marginBottom: 10 }}>
        <div className="item"><span className="k">Run mode</span>
          <span className="v">{(() => {
            const src = String(histRuns?.[0]?.data?.source || histRuns?.[0]?.source || "SIMULATOR").toUpperCase();
            const real = src.includes("MT5") && !src.includes("SIM");
            return real ? <Badge tone="real">REAL MT5 DATA</Badge> : <Badge tone="sim">SIMULATOR</Badge>;
          })()}</span></div>
        <div className="item"><span className="k">Node carried into this page</span>
          <span className="v mono">{(() => {
            const id = numOrNull(activeStrategy?.id) ?? numOrNull(activeStrategy?.node_id) ?? numOrNull(selectedStrategyId);
            return id === null ? NA_TEXT : `#${id}`;
          })()}</span></div>
        <div className="item"><span className="k">Saved runs</span><span className="v mono">{histRuns.length}</span></div>
        <div className="item"><span className="k">Orders placed</span><span className="v"><Badge tone="ok">NEVER</Badge></span></div>
      </div>

      {/* V5 §19 — which node to test: real research rows, search/sort/filter/star/details */}
      <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-4 shadow-sm space-y-2">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 pb-2">
          <span className="text-xs uppercase font-bold tracking-wider text-slate-300">
            Choose a node to backtest
          </span>
          <Badge tone="mute">{txt(picker?.total, "0")} node(s) in scope</Badge>
          <span style={{ flex: 1 }} />
          <input value={pSearch} onChange={(e) => { setPSearch(e.target.value); setPPage(0); }}
                 placeholder="search id / symbol / run…"
                 className="px-2 py-1 bg-slate-950 border border-slate-700 rounded-lg text-[11px] font-mono text-cyan-300 w-44" />
          <select value={pStatus} onChange={(e) => { setPStatus(e.target.value); setPPage(0); }}
                  className="px-2 py-1 bg-slate-950 border border-slate-700 rounded-lg text-[11px] font-mono text-slate-300">
            <option value="">any status</option>
            {["SURVIVED", "QUALIFIED", "FAILED", "KILLED"].map((st) => <option key={st} value={st}>{st}</option>)}
          </select>
          <select value={pSort} onChange={(e) => setPSort(e.target.value)}
                  className="px-2 py-1 bg-slate-950 border border-slate-700 rounded-lg text-[11px] font-mono text-slate-300">
            {[["return", "IS return"], ["profit_factor", "profit factor"], ["trades", "trades"],
              ["sharpe", "Sharpe"], ["drawdown", "max drawdown"], ["win_rate", "win rate"],
              ["id", "id"], ["updated", "last tested"]].map(([k, lbl]) => (
              <option key={k} value={k}>{lbl}</option>
            ))}
          </select>
          <button type="button" onClick={() => setPDir(pDir === "desc" ? "asc" : "desc")}
                  className="text-[11px] font-mono px-2 py-0.5 rounded border border-slate-700 text-slate-300">
            {pDir === "desc" ? "▼ desc" : "▲ asc"}
          </button>
          <label className="text-[11px] font-mono text-slate-300 flex items-center gap-1">
            <input type="checkbox" checked={pStarred} onChange={(e) => { setPStarred(e.target.checked); setPPage(0); }} />
            starred only
          </label>
          <button type="button" onClick={() => loadPicker()}
                  className="text-[11px] font-mono text-slate-300 hover:text-white underline">
            {pickerLoading ? "loading…" : "reload"}
          </button>
        </div>

        {pickerErr && <div className="text-[11px] font-mono text-rose-300">{pickerErr.message || String(pickerErr)}</div>}
        <div className="overflow-x-auto">
          <table className="table w-full text-left text-[11px] font-mono">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 uppercase text-[10px]">
                <th className="">★</th><th className="">ID</th>
                <th className="">Status</th><th className="">Symbol</th>
                <th className="">TF</th><th className="text-right">IS return</th>
                <th className="text-right">PF</th><th className="text-right">Trades</th>
                <th className="text-right">Max DD</th><th className="text-right">Sharpe</th>
                <th className="">Last tested</th><th className="">Action</th>
              </tr>
            </thead>
            <tbody>
              {safeRows(picker?.nodes).map((n) => {
                const r = n.research || {};
                const sid = numOrNull(n.id);
                const lastTested = numOrNull(n.updated_at);
                return (
                  <tr key={sid ?? n.node_id} className={"border-b border-slate-800/60 " + (pSel === sid ? "bg-slate-800/50" : "")}>
                    <td className="">
                      <button type="button" title={n.shortlisted ? "remove from shortlist" : "add to shortlist"}
                              onClick={() => toggleStar(sid)} className="text-[13px] leading-none">
                        {n.shortlisted ? "⭐" : "☆"}
                      </button>
                    </td>
                    <td className="text-cyan-300">{txt(n.node_id, sid ?? NA_TEXT)}</td>
                    <td className="">
                      <Badge tone={String(n.status).toUpperCase() === "FAILED" ? "bad"
                        : String(n.status).toUpperCase() === "QUALIFIED" ? "ok" : "mute"}>
                        {txt(n.status, NA_TEXT)}
                      </Badge>
                      {!n.research_eligible && (
                        <span className="ml-1 text-[10px] text-amber-300" title="excluded from research scope">excluded</span>
                      )}
                    </td>
                    <td className="">{txt(n.symbol, NA_TEXT)}</td>
                    <td className="">{txt(n.timeframe, NA_TEXT)}</td>
                    <td className="text-right">{r.return_pct === undefined || r.return_pct === null
                      ? NA_TEXT : `${(r.return_pct * 100).toFixed(2)} %`}</td>
                    <td className="text-right">{txt(r.profit_factor, NA_TEXT)}</td>
                    <td className="text-right">{txt(r.trades, NA_TEXT)}</td>
                    <td className="text-right">{r.max_drawdown_pct === undefined || r.max_drawdown_pct === null
                      ? NA_TEXT : `${(r.max_drawdown_pct * 100).toFixed(1)} %`}</td>
                    <td className="text-right">{txt(r.sharpe, NA_TEXT)}</td>
                    <td className="text-slate-400">
                      {lastTested === null ? NA_TEXT : new Date(lastTested * 1000).toISOString().slice(0, 16).replace("T", " ")}
                    </td>
                    <td className="">
                      <div className="flex items-center gap-2">
                        <button type="button" onClick={() => { setPSel(sid); setNodeIdInput(String(sid)); loadStrategy(sid); loadHistory(sid); loadHistoricalRuns(sid); }}
                                className="px-2 py-0.5 rounded border border-indigo-700 text-indigo-300 hover:bg-indigo-950">
                          backtest this
                        </button>
                        {setSelectedStrategyId && navigateTab && (
                          <button type="button"
                                  onClick={() => { setSelectedStrategyId(sid); navigateTab("StrategyLab", sid); }}
                                  className="text-slate-400 hover:text-white underline">
                            details
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
              {safeRows(picker?.nodes).length === 0 && (
                <tr><td colSpan={12} className="py-2 px-2 text-slate-500">
                  {pickerLoading ? "loading research nodes…"
                    : "no node matches the current search/filter — nothing is hidden silently, the filters are shown above"}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center gap-2 text-[11px] font-mono text-slate-400">
          <button type="button" disabled={pPage <= 0 || pickerLoading}
                  onClick={() => { const pg = pPage - 1; setPPage(pg); loadPicker({ page: pg }); }}
                  className="px-2 py-0.5 rounded border border-slate-700 disabled:opacity-40">prev</button>
          <span>page {txt(numOrNull(picker?.offset) === null ? null : Math.floor(picker.offset / PICKER_PAGE_SIZE) + 1, "1")}
            {" "}of {txt(picker?.pages, "1")}</span>
          <button type="button" disabled={pickerLoading || (numOrNull(picker?.pages) ?? 1) <= pPage + 1}
                  onClick={() => { const pg = pPage + 1; setPPage(pg); loadPicker({ page: pg }); }}
                  className="px-2 py-0.5 rounded border border-slate-700 disabled:opacity-40">next</button>
          <span className="text-slate-500">showing {txt(picker?.returned, "0")} of {txt(picker?.total, "0")}
            {" "}(legacy rows excluded: {txt(picker?.legacy_excluded_total, "0")})</span>
        </div>
      </div>

      {/* ======================= V4.6 — HISTORICAL MT5 BACKTEST ======================= */}
      <div className="space-y-4">
        {/* V5 §19 — MT5 historical data actually available for backtesting */}
        <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-4 shadow-sm space-y-2">
          <div className="flex items-center justify-between border-b border-slate-800 pb-2">
            <span className="text-xs uppercase font-bold tracking-wider text-slate-300">
              MT5 historical data (what a backtest can actually use)
            </span>
            <button type="button" onClick={loadCaps}
                    className="text-[11px] font-mono text-slate-300 hover:text-white underline">
              refresh
            </button>
          </div>
          {capsErr && <div className="text-[11px] font-mono text-rose-300">{capsErr.message || String(capsErr)}</div>}
          {!caps && !capsErr && <div className="text-[11px] font-mono text-slate-500">loading availability…</div>}
          {caps && (
            <>
              <div className="flex flex-wrap gap-3 text-[11px] font-mono text-slate-300">
                <span>Bridge: <b>{txt(caps.bridge?.active_bridge, NA_TEXT)}</b>{" "}
                  ({caps.bridge?.is_simulated ? "simulated — no terminal attached" : "real terminal"})</span>
                <span>MT5 package installed: <b>{String(Boolean(caps.bridge?.mt5_package_installed))}</b></span>
                <span>Queue: max {txt(caps.limits?.max_queue, NA_TEXT)}, active {txt(caps.limits?.max_active, NA_TEXT)}
                  {caps.limits?.one_at_a_time ? " (one at a time)" : ""}</span>
                <span>Minimum bars: <b>{txt(caps.limits?.min_bars, NA_TEXT)}</b></span>
              </div>
              <div className="text-[11px] font-mono text-slate-500">
                {txt(caps.bridge?.note, "")}
              </div>
              <div className="overflow-x-auto">
                <table className="table w-full text-left text-[11px] font-mono">
                  <thead>
                    <tr className="border-b border-slate-800 text-slate-400 uppercase text-[10px]">
                      <th className="">Symbol</th>
                      <th className="">Timeframes with stored data</th>
                      <th className="">Datasets</th>
                      <th className="">Rejected / unusable</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(caps.datasets_by_symbol || {}).map(([sym, tfs]) => {
                      const shown = Array.from(new Set(tfs.map((t) => t.toUpperCase())));
                      return (
                        <tr key={sym} className="border-b border-slate-800/60">
                          <td className="text-cyan-300">{sym}</td>
                          <td className="">{shown.join(", ") || NA_TEXT}</td>
                          <td className="">{tfs.length}</td>
                          <td className="">
                            {(caps.rejected_datasets || []).filter((d) => (d.symbol || "").toUpperCase() === sym).length}
                          </td>
                        </tr>
                      );
                    })}
                    {Object.keys(caps.datasets_by_symbol || {}).length === 0 && (
                      <tr><td colSpan={4} className="py-2 px-2 text-slate-500">
                        No stored historical dataset is available for backtesting. A run started now would be
                        reported as DATA_UNAVAILABLE — not as a strategy failure.
                      </td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              <div className="text-[11px] font-mono text-slate-400">
                Run states: QUEUED → RUNNING → COMPLETED, or CANCELLED / FAILED / DATA_UNAVAILABLE /
                BACKTEST_ERROR. A run that never gets data is never reported as a strategy failure.
              </div>
            </>
          )}
        </div>

        {/* V5 §19 — the selected node's own data: availability, integrity, reuse, fetch */}
        {caps && (
          <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-4 shadow-sm space-y-2">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <span className="text-xs uppercase font-bold tracking-wider text-slate-300">
                Data for this node{' '}
                <span className="font-mono normal-case text-cyan-300">
                  {nodeSymbol ? `${nodeSymbol} ${nodeTimeframe}` : NA_TEXT}
                </span>
              </span>
              <div className="flex items-center gap-2">
                <button type="button" onClick={() => loadCaps(true)}
                        className="text-[11px] font-mono text-slate-300 hover:text-white underline">
                  re-check availability
                </button>
                <button type="button" disabled={!nodeSymbol || !nodeTimeframe || syncing}
                        onClick={() => fetchNodeData(nodeSymbol, nodeTimeframe)}
                        className="text-[11px] font-mono px-2 py-0.5 rounded border border-cyan-700 text-cyan-300 hover:bg-cyan-950 disabled:opacity-40">
                  {syncing ? "fetching…" : "fetch / extend stored data"}
                </button>
              </div>
            </div>

            {!nodeSymbol || !nodeTimeframe ? (
              <div className="text-[11px] font-mono text-slate-500">
                this node has no symbol/timeframe recorded — the lab cannot choose a dataset for it, and
                says so instead of guessing one.
              </div>
            ) : nodeDatasets.length > 0 ? (
              <div className="space-y-1">
                {nodeDatasets.map((d) => (
                  <div key={`${d.dataset_id}-${d.source}`} className="flex flex-wrap gap-3 text-[11px] font-mono text-slate-300">
                    <span>{d.source === "MT5" ? <Badge tone="real">MT5 DATA</Badge> : <Badge tone="sim">{txt(d.source, "SIMULATED")} DATA</Badge>}</span>
                    <span>dataset <b>{txt(d.dataset_id, NA_TEXT)}</b></span>
                    <span>{fmt.num(d.bars, 0)} bars</span>
                    <span>{String(d.start || "").slice(0, 10)} → {String(d.end || "").slice(0, 10)}</span>
                    <span>min {txt(d.min_bars, NA_TEXT)} bars {d.bars >= (d.min_bars ?? 0) ? "(range long enough)" : "(TOO SHORT — DATA_UNAVAILABLE)"}</span>
                    {d.broker ? <span>{d.broker}{d.server ? ` / ${d.server}` : ""}</span> : null}
                    <span>fingerprint <b>{txt(d.fingerprint, NA_TEXT)}</b></span>
                    <span>integrity: <b className={d.eligible ? "text-emerald-400" : "text-rose-400"}>{txt(d.eligibility_reason, d.eligible ? "eligible" : "not eligible")}</b></span>
                    <span className="text-slate-500">reused in place — nothing is re-downloaded while this dataset satisfies the run</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-[11px] font-mono text-amber-300">
                <b>DATA_UNAVAILABLE</b> — no stored {nodeSymbol} {nodeTimeframe} dataset can be used for a backtest.
                A run started now is reported as DATA_UNAVAILABLE, never as a strategy failure.
              </div>
            )}

            {nodeRejections.length > 0 && (
              <div className="text-[11px] font-mono text-rose-300">
                rejected for {nodeSymbol} {nodeTimeframe}:
                {nodeRejections.map((r, i) => (
                  <span key={`${r.dataset_id}-${i}`}> {txt(r.dataset_id, "?")} ({txt(r.source, "?")}) — {txt(r.reason, "no reason recorded")}</span>
                ))}
              </div>
            )}

            {caps.bridge?.is_simulated && (
              <div className="text-[11px] font-mono text-amber-300">
                the active bridge is <b>{txt(caps.bridge?.active_bridge, NA_TEXT)}</b>, not a real MT5 terminal:
                a fetch writes <b>simulated</b> bars and they stay labelled SIMULATED. Real MT5 history needs a
                terminal attached — the lab never relabels simulated bars as MT5 data.
              </div>
            )}
            {syncErr && <div className="text-[11px] font-mono text-rose-300">fetch failed: {txt(syncErr.message || syncErr, NA_TEXT)}</div>}
            {syncResult && (
              <div className="text-[11px] font-mono text-slate-300">
                engine answer: <b>{txt(syncResult.action || syncResult.fetch_mode || syncResult.status, NA_TEXT)}</b>
                {syncResult.new_bars != null ? <> · {fmt.num(syncResult.new_bars, 0)} new bars</> : null}
                {syncResult.total_bars != null ? <> · {fmt.num(syncResult.total_bars, 0)} stored</> : null}
                {syncResult.dataset_id ? <> · dataset {txt(syncResult.dataset_id)}</> : null}
                {String(syncResult.status) === "no_data_from_bridge"
                  ? <span className="text-amber-300"> — the bridge returned no bars: DATA_UNAVAILABLE (not a strategy failure)</span>
                  : null}
              </div>
            )}
          </div>
        )}



        <HistoricalBacktestPanel
          strategyId={activeStrategy?.id || selectedStrategyId || 240}
          strategyLabel={activeStrategy ? `Node_${txt(activeStrategy.id ?? activeStrategy.node_id, "?")} (${txt(activeStrategy.symbol, "?")} ${txt(activeStrategy.timeframe, "?")})` : ""}
          compact
          onStarted={() => setHistVersion((v) => v + 1)}
        />
        {histRuns.length > 0 && (
          <div className="bg-slate-900/80 p-4 rounded-xl border border-slate-800">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs uppercase font-bold tracking-wider text-slate-300">
                Saved historical runs for this node
              </span>
              <select value={histRunId || ""} onChange={(e) => setHistRunId(e.target.value)}
                      className="px-2 py-1 bg-slate-950 border border-slate-700 rounded-lg text-xs font-mono text-cyan-300">
                {histRuns.map((r) => (
                  <option key={r.run_id} value={r.run_id}>
                    {r.run_id} — {r.status} — {String(r.period?.start || "").slice(0, 10)}→{String(r.period?.end || "").slice(0, 10)}
                  </option>
                ))}
              </select>
              <span className="text-[11px] text-slate-400 font-mono">
                {histRuns.length} run(s) — each one is stored separately and never overwrites another
              </span>
            </div>
          </div>
        )}
        {histRunId && <HistoricalRunResults runId={histRunId} onClose={() => setHistRunId(null)} />}
      </div>

      {error && <StructuredError error={error} onDismiss={() => setError(null)} />}
      {successMsg && (
        <div className="p-3 bg-emerald-950/60 border border-emerald-500/40 text-emerald-300 rounded-lg text-xs font-mono">
          {successMsg}
        </div>
      )}

      {/* Main Grid: Parameters & Node Preview */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left 2 Cols: Tester Setup */}
        <div className="lg:col-span-2 bg-slate-900/80 rounded-xl border border-slate-800 p-5 shadow-sm space-y-5">
          <div className="border-b border-slate-800 pb-3 flex items-center justify-between">
            <span className="text-xs uppercase font-bold tracking-wider text-slate-300">
              Tester Environment & Execution Parameters
            </span>
            <span className="text-[11px] font-mono text-slate-400">
              Target: {activeStrategy ? `Node_${txt(activeStrategy.id ?? activeStrategy.node_id, "?")} (${txt(activeStrategy.symbol, "?")} ${txt(activeStrategy.timeframe, "?")})` : "None"}
            </span>
          </div>

          {/* Bridge Status Notice */}
          <div className="p-3.5 bg-indigo-950/30 border border-indigo-900/40 rounded-lg text-xs font-mono text-indigo-200">
            <div className="flex items-center gap-2 text-indigo-300 font-bold mb-1">
              <span>ℹ️ MT5 Bridge & Tester Architecture (Spec §36)</span>
            </div>
            <p className="text-[11px] text-slate-300 leading-relaxed">
              When executed on Windows hosts with native MetaTrader 5 installed, tests execute directly against the MT5 Strategy Tester terminal.
              On Linux / cloud sandbox environments, tests execute via the High-Fidelity Research Simulator with identical MT5 spread, slippage, and roundturn commission models.
            </p>
          </div>

          {/* Inputs Grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs font-mono">
            <div>
              <label className="text-slate-400 block mb-1">Initial Deposit ($):</label>
              <input
                type="number"
                value={deposit}
                onChange={(e) => setDeposit(e.target.value)}
                className="w-full px-3 py-1.5 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono focus:border-indigo-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="text-slate-400 block mb-1">Leverage Ratio:</label>
              <select
                value={leverage}
                onChange={(e) => setLeverage(e.target.value)}
                className="w-full px-3 py-1.5 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono focus:border-indigo-500 focus:outline-none"
              >
                <option value="30">1:30</option>
                <option value="50">1:50</option>
                <option value="100">1:100 (Standard)</option>
                <option value="200">1:200</option>
                <option value="500">1:500</option>
              </select>
            </div>
            <div>
              <label className="text-slate-400 block mb-1">Spread (points):</label>
              <input
                type="number"
                step="0.5"
                value={spread}
                onChange={(e) => setSpread(e.target.value)}
                className="w-full px-3 py-1.5 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono focus:border-indigo-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="text-slate-400 block mb-1">Commission ($ per round lot):</label>
              <input
                type="number"
                step="0.5"
                value={commission}
                onChange={(e) => setCommission(e.target.value)}
                className="w-full px-3 py-1.5 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono focus:border-indigo-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="text-slate-400 block mb-1">Execution Slippage (points):</label>
              <input
                type="number"
                step="0.1"
                value={slippage}
                onChange={(e) => setSlippage(e.target.value)}
                className="w-full px-3 py-1.5 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono focus:border-indigo-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="text-slate-400 block mb-1">Test Date Range:</label>
              <div className="flex items-center gap-1.5">
                <input
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  className="w-1/2 px-2 py-1 bg-slate-950 border border-slate-800 rounded text-slate-300 font-mono text-[11px]"
                />
                <span className="text-slate-500">to</span>
                <input
                  type="date"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                  className="w-1/2 px-2 py-1 bg-slate-950 border border-slate-800 rounded text-slate-300 font-mono text-[11px]"
                />
              </div>
            </div>
          </div>

          {/* Action Button — the V4.0 fabricated-result path is disabled.
              V4.6 deliberately does not re-enable it: it produced numbers that were
              not the output of a backtest engine. The endpoint itself is untouched,
              and historical backtests now run through the V4.6 panel above. */}
          <div className="pt-3 border-t border-slate-800 flex items-center justify-between">
            <div className="text-xs text-slate-400 font-mono">
              V4.0 record creation is disabled — use the <b>HISTORICAL MT5 BACKTEST</b> panel above, which stores
              real engine results, equity, trades and provenance.
            </div>
            <button
              type="button"
              disabled
              title="disabled in V4.6: this legacy path built its numbers from stored research metrics rather than from a backtest engine"
              className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-xs rounded-xl shadow-md transition-all disabled:opacity-50 flex items-center gap-2"
            >
              <>
                  
                  <span>RUN MT5 STRATEGY TESTER (DISABLED IN V4.6)</span>
                </>
            </button>
          </div>
        </div>

        {/* Right 1 Col: Strategy Summary Card */}
        <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 shadow-sm space-y-4">
          <div className="border-b border-slate-800 pb-3 flex items-center justify-between">
            <span className="text-xs uppercase font-bold tracking-wider text-slate-400">
              Strategy Under Test
            </span>
            {activeStrategy && (
              <span className={`text-[10px] font-mono px-2 py-0.5 rounded border ${fmt.stageBadge(activeStrategy.pipeline_stage)}`}>
                {activeStrategy.pipeline_stage}
              </span>
            )}
          </div>

          {activeStrategy ? (
            <div className="space-y-3 text-xs font-mono">
              <div className="flex justify-between items-center bg-slate-950/60 p-2 rounded border border-slate-800">
                <span className="text-slate-500">Node Identifier:</span>
                <span className="text-white font-bold">{txt(activeStrategy.node_id ?? activeStrategy.id, "—")}</span>
              </div>
              <div className="flex justify-between items-center bg-slate-950/60 p-2 rounded border border-slate-800">
                <span className="text-slate-500">Asset & Timeframe:</span>
                <span className="text-cyan-400 font-semibold">{txt(activeStrategy.symbol, "—")} · {txt(activeStrategy.timeframe, "—")}</span>
              </div>
              <div className="flex justify-between items-center bg-slate-950/60 p-2 rounded border border-slate-800">
                <span className="text-slate-500">In-Sample Return:</span>
                <span className="text-cyan-300 font-bold">{fmt.ret(activeStrategy.returns?.backtest_return_pct)}</span>
              </div>
              <div className="flex justify-between items-center bg-slate-950/60 p-2 rounded border border-slate-800">
                <span className="text-slate-500">In-Sample PF:</span>
                <span className="text-white">{fmt.ratio(activeStrategy.profit_factors?.in_sample)}</span>
              </div>
              <div className="flex justify-between items-center bg-slate-950/60 p-2 rounded border border-slate-800">
                <span className="text-slate-500">OOS Robustness:</span>
                <span className="text-emerald-400">{fmt.ratio(activeStrategy.robustness_score, 3)}</span>
              </div>

              <div className="pt-2">
                <span className="text-[11px] text-slate-500 uppercase font-semibold block mb-1">
                  Active Indicators ({activeStrategy.indicators?.length || 0}):
                </span>
                <div className="space-y-1">
                  {activeStrategy.indicators?.slice(0, 3).map((ind, i) => (
                    <div key={i} className="text-[10px] text-slate-300 bg-slate-950 px-2 py-1 rounded truncate">
                      • {ind.display}
                    </div>
                  ))}
                </div>
              </div>

              <div className="pt-2">
                <button
                  type="button"
                  onClick={() => navigateTab && navigateTab("economics", activeStrategy.id)}
                  className="w-full py-1.5 bg-slate-800 hover:bg-slate-700 text-cyan-300 rounded text-center text-xs transition-colors"
                >
                  View Full Genome in Node Economics →
                </button>
              </div>
            </div>
          ) : (
            <div className="text-slate-500 text-xs font-mono py-8 text-center">
              No strategy selected. Enter a Node ID above.
            </div>
          )}
        </div>
      </div>

      {/* Section 2: PERSISTENT MT5 BACKTEST RESULTS */}
      <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 shadow-sm space-y-4">
        <div className="border-b border-slate-800 pb-3 flex items-center justify-between">
          <div>
            <span className="text-xs uppercase font-bold tracking-wider text-slate-300 block">
              Persistent MT5 Strategy Tester Results History
            </span>
            <span className="text-[11px] text-slate-400">
              Audit log of completed MT5 runs across all strategy nodes.
            </span>
          </div>
          <button
            type="button"
            onClick={() => loadHistory(null)}
            className="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs font-mono transition-colors"
          >
            Show All Nodes ({historyResults.length})
          </button>
        </div>

        {historyResults.length === 0 ? (
          <div className="text-slate-500 text-xs font-mono py-8 text-center">
            No MT5 Strategy Tester executions recorded yet. Click "RUN MT5 STRATEGY TESTER" above.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table w-full text-left text-xs font-mono">
              <thead>
                <tr className="border-b border-slate-800 text-slate-400 uppercase text-[10px]">
                  <th className="">Date</th>
                  <th className="">Node</th>
                  <th className="">Symbol / TF</th>
                  <th className="text-right">Init Cap</th>
                  <th className="text-right">Final Cap</th>
                  <th className="text-right">Net Profit</th>
                  <th className="text-right">PF</th>
                  <th className="text-right">Win Rate</th>
                  <th className="text-right">Trades</th>
                  <th className="text-right">Max DD</th>
                  <th className="text-right">Sharpe</th>
                  <th className="">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {historyResults.map((row) => (
                  <tr key={row.id} className="hover:bg-slate-800/40 transition-colors">
                    <td className="text-slate-400">{fmt.dt(row.created_at)}</td>
                    <td className="">
                      <button
                        type="button"
                        onClick={() => loadStrategy(row.strategy_id)}
                        className="text-cyan-400 hover:underline font-bold"
                      >
                        Node_{row.strategy_id}
                      </button>
                    </td>
                    <td className="text-slate-300">{row.symbol} · {row.timeframe}</td>
                    <td className="text-right text-slate-400">{fmt.currency(row.initial_capital, 0)}</td>
                    <td className="text-right text-white font-bold">{fmt.currency(row.final_capital, 0)}</td>
                    <td className={`py-2 px-3 text-right font-bold ${row.net_profit >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {fmt.pnl(row.net_profit)}
                    </td>
                    <td className="text-right text-slate-200">{fmt.ratio(row.profit_factor)}</td>
                    <td className="text-right text-slate-200">{fmt.pct(row.win_rate)}</td>
                    <td className="text-right text-slate-300">{row.trade_count}</td>
                    <td className="text-right text-rose-400">{fmt.pct(row.max_drawdown_pct)}</td>
                    <td className="text-right text-slate-300">{fmt.num(row.sharpe)}</td>
                    <td className="">
                      <span className="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                        {row.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
