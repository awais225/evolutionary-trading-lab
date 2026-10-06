import React, { useState, useEffect, useCallback } from "react";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import StructuredError from "../components/StructuredError.jsx";

export default function NodeEconomics() {
  const { selectedStrategyId, setSelectedStrategyId, shortlist, toggleShortlist, navigateTab } = useLab() || {};
  const [nodeIdInput, setNodeIdInput] = useState(selectedStrategyId ? String(selectedStrategyId) : "240");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [actionMsg, setActionMsg] = useState(null);
  const [actionBusy, setActionBusy] = useState("");

  const currentSid = selectedStrategyId || 240;

  const fetchEconomics = useCallback(async (sid) => {
    if (!sid) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.nodeEconomics(sid);
      setData(res);
      if (setSelectedStrategyId) setSelectedStrategyId(sid);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [setSelectedStrategyId]);

  useEffect(() => {
    if (selectedStrategyId) {
      setNodeIdInput(String(selectedStrategyId));
      fetchEconomics(selectedStrategyId);
    } else {
      fetchEconomics(240);
    }
  }, [selectedStrategyId, fetchEconomics]);

  const handleLookup = (e) => {
    e?.preventDefault();
    const cleanId = parseInt(nodeIdInput.replace(/[^0-9]/g, ""), 10);
    if (!cleanId) return;
    fetchEconomics(cleanId);
  };

  const handleToggleStar = async () => {
    if (!data?.id) return;
    try {
      if (toggleShortlist) {
        await toggleShortlist(data.id);
      } else {
        await api.toggleShortlist(data.id);
      }
      fetchEconomics(data.id);
    } catch (err) {
      setError(err);
    }
  };

  const handlePromoteStage = async (newStage) => {
    if (!data?.id) return;
    setActionBusy("stage");
    setActionMsg(null);
    try {
      await api.updatePipelineStage(data.id, newStage, `Advanced via Node Economics`);
      setActionMsg({ ok: true, text: `✓ Strategy pipeline state updated to ${newStage}` });
      fetchEconomics(data.id);
    } catch (err) {
      setError(err);
    } finally {
      setActionBusy("");
    }
  };

  const isShortlisted = shortlist?.includes(data?.id) || data?.shortlisted;
  const g = data?.genome || {};
  const mBt = data?.metrics?.backtest || {};
  const mVal = data?.metrics?.validation || {};
  const mMt5 = data?.metrics?.mt5_backtest || {};
  const mLive = data?.metrics?.live_test || {};
  const mDemo = data?.metrics?.mt5_demo || {};

  return (
    <div className="node-economics-page p-6 max-w-7xl mx-auto space-y-6 animate-in fade-in duration-300">
      {/* Top Header & Strategy Selector */}
      <div className="flex flex-wrap items-center justify-between gap-4 bg-slate-900/80 p-4 rounded-xl border border-slate-800 shadow-md">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
              <span>🧬 NODE ECONOMICS</span>
              <span className="text-xs px-2 py-0.5 rounded font-mono bg-cyan-950 text-cyan-400 border border-cyan-800">
                V4 AUTHORITATIVE GENOME MODEL
              </span>
            </h2>
            {data && (
              <button
                type="button"
                onClick={handleToggleStar}
                className={`p-1.5 rounded-lg border transition-all ${
                  isShortlisted
                    ? "bg-amber-500/20 text-amber-400 border-amber-500/40 hover:bg-amber-500/30"
                    : "bg-slate-800 text-slate-400 border-slate-700 hover:text-amber-400 hover:border-slate-600"
                }`}
                title={isShortlisted ? "Remove from persistent shortlist" : "Add to persistent shortlist (⭐)"}
              >
                {isShortlisted ? "⭐ Shortlisted" : "☆ Add to Shortlist"}
              </button>
            )}
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Authoritative strategy architecture, indicator parameters, exact entry/exit conditions, risk rules, and stage-separated performance.
          </p>
        </div>

        {/* Node Selector Form */}
        <form onSubmit={handleLookup} className="flex items-center gap-2">
          <label className="text-xs font-semibold text-slate-300">Select Node:</label>
          <div className="relative">
            <input
              type="text"
              value={nodeIdInput}
              onChange={(e) => setNodeIdInput(e.target.value)}
              placeholder="e.g. 240"
              className="w-28 px-3 py-1.5 bg-slate-950 border border-slate-700 rounded-lg text-sm font-mono text-cyan-300 focus:outline-none focus:border-cyan-500"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="px-3.5 py-1.5 bg-cyan-600 hover:bg-cyan-500 text-white font-medium text-xs rounded-lg transition-colors shadow disabled:opacity-50"
          >
            {loading ? "Loading..." : "Load Node"}
          </button>
        </form>
      </div>

      {error && <StructuredError error={error} onDismiss={() => setError(null)} />}
      {actionMsg && (
        <div className={`p-3 rounded-lg text-xs font-mono ${actionMsg.ok ? "bg-emerald-950/60 border border-emerald-500/40 text-emerald-300" : "bg-rose-950/60 border border-rose-500/40 text-rose-300"}`}>
          {actionMsg.text}
        </div>
      )}

      {loading && !data && (
        <div className="p-12 text-center text-slate-400 font-mono text-sm animate-pulse">
          Fetching authoritative strategy genome and stage metrics...
        </div>
      )}

      {data && (
        <div className="space-y-6">
          {/* Section 1: IDENTITY & MARKET SPECIFICATION */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Identity Card */}
            <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 shadow-sm">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
                <span className="text-xs uppercase font-bold tracking-wider text-slate-400">
                  Node Identity
                </span>
                <span className={`text-[11px] font-mono font-bold px-2.5 py-0.5 rounded border ${fmt.stageBadge(data.pipeline_stage || data.status)}`}>
                  {data.pipeline_stage || data.status}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-y-3 gap-x-4 text-xs font-mono">
                <div>
                  <span className="text-slate-500 block">Authoritative ID:</span>
                  <span className="text-white font-bold text-sm">Node_{data.id}</span>
                </div>
                <div>
                  <span className="text-slate-500 block">Research Node #:</span>
                  <span className="text-cyan-400 font-semibold">#{data.research_node_num}</span>
                </div>
                <div>
                  <span className="text-slate-500 block">Run ID:</span>
                  <span className="text-slate-300 truncate block" title={data.run_id}>{data.run_id}</span>
                </div>
                <div>
                  <span className="text-slate-500 block">Generation:</span>
                  <span className="text-amber-300 font-semibold">{data.generation}</span>
                </div>
                <div>
                  <span className="text-slate-500 block">Parent Node:</span>
                  <span className="text-slate-300">
                    {data.parent_id ? (
                      <button
                        type="button"
                        onClick={() => fetchEconomics(data.parent_id)}
                        className="text-cyan-400 hover:underline"
                      >
                        Node_{data.parent_id}
                      </button>
                    ) : "Root Gen 0 (None)"}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block">Children Count:</span>
                  <span className="text-slate-300">{data.children?.length || 0} direct descendants</span>
                </div>
                <div>
                  <span className="text-slate-500 block">Created At:</span>
                  <span className="text-slate-400">{fmt.dt(data.created_at)}</span>
                </div>
                <div>
                  <span className="text-slate-500 block">Data Source:</span>
                  <span className="text-slate-300">{data.data_source}</span>
                </div>
              </div>

              {/* Pipeline Advancement Buttons */}
              <div className="mt-5 pt-3 border-t border-slate-800">
                <div className="text-[10px] text-slate-500 mb-2 uppercase font-semibold">
                  Advance Promotion Pipeline (Spec §13):
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {["QUALIFIED", "SHORTLISTED", "MT5_BACKTESTED", "LIVE_TESTING", "MT5_DEMO", "FINAL_CANDIDATE"].map((st) => (
                    <button
                      key={st}
                      type="button"
                      disabled={actionBusy === "stage" || data.pipeline_stage === st}
                      onClick={() => handlePromoteStage(st)}
                      className={`px-2 py-1 rounded text-[10px] font-mono transition-colors ${
                        data.pipeline_stage === st
                          ? "bg-cyan-500/30 text-cyan-300 border border-cyan-400 font-bold"
                          : "bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700"
                      }`}
                    >
                      {st}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Market Specification */}
            <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 shadow-sm">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
                <span className="text-xs uppercase font-bold tracking-wider text-slate-400">
                  Market & Session Architecture
                </span>
                <span className="text-[11px] font-mono text-cyan-400 bg-cyan-950/60 px-2 py-0.5 rounded border border-cyan-800/40">
                  {data.symbol} · {data.timeframe}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-y-3 gap-x-4 text-xs font-mono">
                <div>
                  <span className="text-slate-500 block">Asset Symbol:</span>
                  <span className="text-white font-bold">{data.symbol}</span>
                </div>
                <div>
                  <span className="text-slate-500 block">Primary Timeframe:</span>
                  <span className="text-cyan-300 font-semibold">{data.timeframe}</span>
                </div>
                <div>
                  <span className="text-slate-500 block">Trade Direction:</span>
                  <span className="text-amber-300 uppercase font-bold">{data.direction || "BOTH"}</span>
                </div>
                <div>
                  <span className="text-slate-500 block">Dataset Reference:</span>
                  <span className="text-slate-300 truncate block text-[11px]" title={data.dataset_id}>
                    {data.dataset_id || "Master In-Sample"}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block">Active Sessions:</span>
                  <span className="text-slate-300">
                    {g.sessions && g.sessions.length > 0 ? g.sessions.join(", ") : "All Sessions (24h)"}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block">Active Days:</span>
                  <span className="text-slate-300">
                    {g.days && g.days.length < 5 ? g.days.map(d => ["Mon","Tue","Wed","Thu","Fri"][d]).join(", ") : "Mon - Fri (All Trading Days)"}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block">Regime Filters:</span>
                  <span className="text-slate-300">
                    {g.regime_filters && g.regime_filters.length > 0 ? g.regime_filters.join(", ") : "Unconstrained (All Regimes)"}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block">Complexity Score:</span>
                  <span className="text-slate-300">{data.complexity || 3} indicators / clauses</span>
                </div>
              </div>

              {/* Navigation Action Links */}
              <div className="mt-5 pt-3 border-t border-slate-800 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => navigateTab && navigateTab("mt5_backtest", data.id)}
                  className="px-2.5 py-1 bg-cyan-950/80 hover:bg-cyan-900 border border-cyan-800/60 text-cyan-300 rounded text-[11px] font-mono transition-colors"
                >
                  → Run MT5 Backtest
                </button>
                <button
                  type="button"
                  onClick={() => navigateTab && navigateTab("live_test", data.id)}
                  className="px-2.5 py-1 bg-blue-950/80 hover:bg-blue-900 border border-blue-800/60 text-blue-300 rounded text-[11px] font-mono transition-colors"
                >
                  → Configure Live Test
                </button>
                <button
                  type="button"
                  onClick={() => navigateTab && navigateTab("matrix", data.id)}
                  className="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-[11px] font-mono transition-colors"
                >
                  → Backtest Matrix
                </button>
              </div>
            </div>
          </div>

          {/* Section 2: INDICATORS PRESENT IN GENOME (Spec §6) */}
          <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 shadow-sm">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
              <span className="text-xs uppercase font-bold tracking-wider text-slate-400">
                Active Technical Indicators (Only Indicators Present in Genome)
              </span>
              <span className="text-xs font-mono text-slate-400">
                {data.indicators?.length || 0} Indicators Loaded
              </span>
            </div>

            {(!data.indicators || data.indicators.length === 0) ? (
              <div className="text-slate-500 text-xs font-mono py-2">
                No external indicators referenced in genome (price-action based rules).
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
                {data.indicators.map((ind, idx) => (
                  <div key={idx} className="bg-slate-950/80 border border-slate-800 rounded-lg p-3">
                    <div className="text-cyan-400 font-bold text-xs">{ind.name}</div>
                    <div className="text-slate-200 font-mono text-xs mt-1">{ind.display}</div>
                    <div className="text-slate-500 font-mono text-[10px] mt-1.5 flex justify-between">
                      <span>Spec: {ind.spec}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Section 3: ENTRY & EXIT CONDITIONS */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Entry Conditions */}
            <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 shadow-sm">
              <div className="border-b border-slate-800 pb-3 mb-4">
                <span className="text-xs uppercase font-bold tracking-wider text-emerald-400">
                  Entry Signal Logic (Human-Readable AST)
                </span>
              </div>
              <div className="space-y-4">
                <div>
                  <div className="text-xs font-bold text-emerald-300 mb-1 flex items-center gap-1.5">
                    <span>🟢 LONG ENTRY CONDITIONS:</span>
                  </div>
                  {data.entry_conditions?.long && data.entry_conditions.long.length > 0 ? (
                    <div className="space-y-1.5">
                      {data.entry_conditions.long.map((cond, i) => (
                        <div key={i} className="bg-slate-950/80 border border-emerald-950 rounded p-2 text-xs font-mono text-emerald-200 flex items-start gap-2">
                          <span className="text-slate-500 shrink-0">#{i + 1}</span>
                          <span>{cond}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-slate-500 text-xs font-mono italic">No long entry clause (short-only).</div>
                  )}
                </div>

                <div>
                  <div className="text-xs font-bold text-rose-300 mb-1 flex items-center gap-1.5">
                    <span>🔴 SHORT ENTRY CONDITIONS:</span>
                  </div>
                  {data.entry_conditions?.short && data.entry_conditions.short.length > 0 ? (
                    <div className="space-y-1.5">
                      {data.entry_conditions.short.map((cond, i) => (
                        <div key={i} className="bg-slate-950/80 border border-rose-950 rounded p-2 text-xs font-mono text-rose-200 flex items-start gap-2">
                          <span className="text-slate-500 shrink-0">#{i + 1}</span>
                          <span>{cond}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-slate-500 text-xs font-mono italic">No short entry clause (long-only).</div>
                  )}
                </div>
              </div>
            </div>

            {/* Exit Conditions & Position Management */}
            <div className="space-y-6">
              {/* Exit Conditions */}
              <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 shadow-sm">
                <div className="border-b border-slate-800 pb-3 mb-4">
                  <span className="text-xs uppercase font-bold tracking-wider text-amber-400">
                    Exit Conditions & Risk Orders
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-3 text-xs font-mono">
                  <div className="bg-slate-950/60 p-2.5 rounded border border-slate-800">
                    <span className="text-slate-500 block text-[11px]">Take Profit:</span>
                    <span className="text-emerald-400 font-semibold">{data.exit_conditions?.take_profit || "None"}</span>
                  </div>
                  <div className="bg-slate-950/60 p-2.5 rounded border border-slate-800">
                    <span className="text-slate-500 block text-[11px]">Stop Loss:</span>
                    <span className="text-rose-400 font-semibold">{data.exit_conditions?.stop_loss || "None"}</span>
                  </div>
                  <div className="bg-slate-950/60 p-2.5 rounded border border-slate-800">
                    <span className="text-slate-500 block text-[11px]">Trailing Stop:</span>
                    <span className="text-slate-300">{data.exit_conditions?.trailing_stop || "None"}</span>
                  </div>
                  <div className="bg-slate-950/60 p-2.5 rounded border border-slate-800">
                    <span className="text-slate-500 block text-[11px]">Time Horizon Exit:</span>
                    <span className="text-slate-300">{data.exit_conditions?.time_exit}</span>
                  </div>
                  <div className="col-span-2 bg-slate-950/60 p-2.5 rounded border border-slate-800">
                    <span className="text-slate-500 block text-[11px]">Opposite Signal Exit:</span>
                    <span className="text-slate-300">{data.exit_conditions?.opposite_signal_exit}</span>
                  </div>
                </div>
              </div>

              {/* Position Management */}
              <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 shadow-sm">
                <div className="border-b border-slate-800 pb-3 mb-4">
                  <span className="text-xs uppercase font-bold tracking-wider text-cyan-400">
                    Position Management & Sizing
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-3 text-xs font-mono">
                  <div className="bg-slate-950/60 p-2.5 rounded border border-slate-800">
                    <span className="text-slate-500 block text-[11px]">Risk Per Trade:</span>
                    <span className="text-white font-bold">{data.position_management?.risk_per_trade}</span>
                  </div>
                  <div className="bg-slate-950/60 p-2.5 rounded border border-slate-800">
                    <span className="text-slate-500 block text-[11px]">Max Concurrent Orders:</span>
                    <span className="text-white font-bold">{data.position_management?.max_concurrent_positions}</span>
                  </div>
                  <div className="bg-slate-950/60 p-2.5 rounded border border-slate-800">
                    <span className="text-slate-500 block text-[11px]">Leverage Factor:</span>
                    <span className="text-slate-300">{data.position_management?.leverage}</span>
                  </div>
                  <div className="bg-slate-950/60 p-2.5 rounded border border-slate-800">
                    <span className="text-slate-500 block text-[11px]">Scaling / Pyramiding:</span>
                    <span className="text-slate-300">{data.position_management?.pyramiding}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Section 4: STAGE-SEPARATED PERFORMANCE (Spec §2 & §6) */}
          <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-5 shadow-sm space-y-4">
            <div className="border-b border-slate-800 pb-3 flex items-center justify-between">
              <div>
                <span className="text-xs uppercase font-bold tracking-wider text-white block">
                  Authoritative Stage-Separated Performance (Spec §2)
                </span>
                <span className="text-[11px] text-slate-400">
                  Each testing stage is independently audited and explicitly labeled. Never combined or conflated.
                </span>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
              {/* Stage 1: IN-SAMPLE BACKTEST */}
              <div className="bg-slate-950/90 rounded-xl border border-cyan-900/40 p-4 flex flex-col justify-between">
                <div>
                  <div className="text-[10px] font-bold text-cyan-400 uppercase tracking-wider mb-2">
                    1. In-Sample Backtest
                  </div>
                  <div className="text-2xl font-bold font-mono text-cyan-300">
                    {fmt.ret(data.returns?.backtest_return_pct)}
                  </div>
                  <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                    Net: {fmt.currency(mBt.net_profit)}
                  </div>
                  <div className="mt-3 space-y-1 text-[11px] font-mono text-slate-300">
                    <div className="flex justify-between">
                      <span className="text-slate-500">Profit Factor:</span>
                      <span className="font-semibold">{fmt.ratio(mBt.profit_factor)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Win Rate:</span>
                      <span>{fmt.pct(mBt.win_rate)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Trades:</span>
                      <span>{mBt.trades || 0}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Max DD:</span>
                      <span className="text-rose-400">{fmt.pct(mBt.max_drawdown_pct)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Sharpe:</span>
                      <span>{fmt.num(mBt.sharpe)}</span>
                    </div>
                  </div>
                </div>
                <div className="mt-4 pt-2 border-t border-slate-800/80 text-[10px] text-slate-500 font-mono">
                  Stage: DETAIL_IN_SAMPLE
                </div>
              </div>

              {/* Stage 2: OUT-OF-SAMPLE VALIDATION */}
              <div className="bg-slate-950/90 rounded-xl border border-emerald-900/40 p-4 flex flex-col justify-between">
                <div>
                  <div className="text-[10px] font-bold text-emerald-400 uppercase tracking-wider mb-2">
                    2. OOS Validation
                  </div>
                  <div className="text-2xl font-bold font-mono text-emerald-300">
                    {fmt.ret(data.returns?.validation_oos_return_pct)}
                  </div>
                  <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                    Robustness: {fmt.ratio(data.robustness_score, 3)}
                  </div>
                  <div className="mt-3 space-y-1 text-[11px] font-mono text-slate-300">
                    <div className="flex justify-between">
                      <span className="text-slate-500">OOS PF:</span>
                      <span className="font-semibold">{fmt.ratio(data.profit_factors?.oos)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Validation:</span>
                      <span className={mVal.passed ? "text-emerald-400 font-bold" : "text-slate-400"}>
                        {mVal.passed ? "PASSED" : (data.robustness_score ? "EVALUATED" : "PENDING")}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">OOS Degradation:</span>
                      <span>{fmt.pct(mVal.oos?.degradation)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Walk-Forward:</span>
                      <span>{mVal.walkforward ? "4 Folds" : "–"}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Monte Carlo:</span>
                      <span>{mVal.montecarlo ? "95% Conf" : "–"}</span>
                    </div>
                  </div>
                </div>
                <div className="mt-4 pt-2 border-t border-slate-800/80 text-[10px] text-slate-500 font-mono">
                  Stage: OUT_OF_SAMPLE_BATTERY
                </div>
              </div>

              {/* Stage 3: MT5 STRATEGY TESTER */}
              <div className="bg-slate-950/90 rounded-xl border border-indigo-900/40 p-4 flex flex-col justify-between">
                <div>
                  <div className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider mb-2">
                    3. MT5 Backtest
                  </div>
                  <div className="text-2xl font-bold font-mono text-indigo-300">
                    {data.returns?.mt5_backtest_return_pct != null
                      ? fmt.ret(data.returns.mt5_backtest_return_pct)
                      : "–"}
                  </div>
                  <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                    {mMt5.net_profit != null ? fmt.currency(mMt5.net_profit) : "Not yet tested"}
                  </div>
                  <div className="mt-3 space-y-1 text-[11px] font-mono text-slate-300">
                    <div className="flex justify-between">
                      <span className="text-slate-500">Tester PF:</span>
                      <span className="font-semibold">{fmt.ratio(mMt5.profit_factor)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Win Rate:</span>
                      <span>{fmt.pct(mMt5.win_rate)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Trades:</span>
                      <span>{mMt5.trade_count || 0}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Max DD:</span>
                      <span className="text-rose-400">{fmt.pct(mMt5.max_drawdown_pct)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Status:</span>
                      <span className="text-[10px] truncate max-w-[90px]" title={mMt5.status}>
                        {mMt5.status ? "COMPLETED" : "UNTESTED"}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="mt-4 pt-2 border-t border-slate-800/80 flex items-center justify-between text-[10px] font-mono">
                  <span className="text-slate-500">MT5 TESTER</span>
                  <button
                    type="button"
                    onClick={() => navigateTab && navigateTab("mt5_backtest", data.id)}
                    className="text-cyan-400 hover:underline"
                  >
                    Run Test →
                  </button>
                </div>
              </div>

              {/* Stage 4: LIVE FORWARD TESTING */}
              <div className="bg-slate-950/90 rounded-xl border border-blue-900/40 p-4 flex flex-col justify-between">
                <div>
                  <div className="text-[10px] font-bold text-blue-400 uppercase tracking-wider mb-2">
                    4. Live Testing (No Real $)
                  </div>
                  <div className="text-2xl font-bold font-mono text-blue-300">
                    {data.returns?.live_test_return_pct != null
                      ? fmt.ret(data.returns.live_test_return_pct)
                      : "–"}
                  </div>
                  <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                    P/L: {fmt.currency(mLive.total_pnl || 0)}
                  </div>
                  <div className="mt-3 space-y-1 text-[11px] font-mono text-slate-300">
                    <div className="flex justify-between">
                      <span className="text-slate-500">Status:</span>
                      <span className={mLive.is_active ? "text-emerald-400 font-bold" : "text-slate-400"}>
                        {mLive.status || "IDLE"}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Live Trades:</span>
                      <span>{mLive.total_trades || 0}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Open Pos:</span>
                      <span>{mLive.open_trades?.length || 0}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Win Rate:</span>
                      <span>{fmt.pct(mLive.win_rate)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Real Money:</span>
                      <span className="text-emerald-400 font-bold">$0 (ZERO)</span>
                    </div>
                  </div>
                </div>
                <div className="mt-4 pt-2 border-t border-slate-800/80 flex items-center justify-between text-[10px] font-mono">
                  <span className="text-slate-500">FORWARD LIVE</span>
                  <button
                    type="button"
                    onClick={() => navigateTab && navigateTab("live_test", data.id)}
                    className="text-blue-400 hover:underline"
                  >
                    Configure →
                  </button>
                </div>
              </div>

              {/* Stage 5: MT5 DEMO TRADING */}
              <div className="bg-slate-950/90 rounded-xl border border-purple-900/40 p-4 flex flex-col justify-between">
                <div>
                  <div className="text-[10px] font-bold text-purple-400 uppercase tracking-wider mb-2">
                    5. MT5 Demo Trading
                  </div>
                  <div className="text-2xl font-bold font-mono text-purple-300">
                    {data.returns?.mt5_demo_return_pct != null
                      ? fmt.ret(data.returns.mt5_demo_return_pct)
                      : "–"}
                  </div>
                  <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                    Demo P/L: {fmt.currency(mDemo.total_pnl || 0)}
                  </div>
                  <div className="mt-3 space-y-1 text-[11px] font-mono text-slate-300">
                    <div className="flex justify-between">
                      <span className="text-slate-500">Demo Status:</span>
                      <span className={mDemo.enabled ? "text-purple-400 font-bold" : "text-slate-400"}>
                        {mDemo.status || "STOPPED"}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Demo Trades:</span>
                      <span>{mDemo.total_trades || 0}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Open Orders:</span>
                      <span>{mDemo.open_trades?.length || 0}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Safety Gating:</span>
                      <span className="text-amber-400 font-semibold">CONFIRMED</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Account Type:</span>
                      <span className="text-purple-300">DEMO ONLY</span>
                    </div>
                  </div>
                </div>
                <div className="mt-4 pt-2 border-t border-slate-800/80 flex items-center justify-between text-[10px] font-mono">
                  <span className="text-slate-500">MT5 DEMO</span>
                  <button
                    type="button"
                    onClick={() => navigateTab && navigateTab("mt5_demo", data.id)}
                    className="text-purple-400 hover:underline"
                  >
                    Manage Demo →
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
