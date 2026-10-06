import React, { useState, useEffect, useCallback } from "react";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import StructuredError from "../components/StructuredError.jsx";

export default function Mt5Backtest() {
  const { selectedStrategyId, setSelectedStrategyId, shortlist, navigateTab } = useLab() || {};
  const [nodeIdInput, setNodeIdInput] = useState(selectedStrategyId ? String(selectedStrategyId) : "240");
  const [activeStrategy, setActiveStrategy] = useState(null);
  const [historyResults, setHistoryResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState(null);
  const [successMsg, setSuccessMsg] = useState(null);

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

  useEffect(() => {
    const sid = selectedStrategyId || 240;
    setNodeIdInput(String(sid));
    loadStrategy(sid);
    loadHistory(sid);
  }, [selectedStrategyId, loadStrategy, loadHistory]);

  const handleLookup = (e) => {
    e?.preventDefault();
    const cleanId = parseInt(nodeIdInput.replace(/[^0-9]/g, ""), 10);
    if (!cleanId) return;
    loadStrategy(cleanId);
    loadHistory(cleanId);
  };

  const handleRunTest = async () => {
    if (!activeStrategy?.id) return;
    setExecuting(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const payload = {
        symbol: activeStrategy.symbol,
        timeframe: activeStrategy.timeframe,
        initial_deposit: parseFloat(deposit) || 10000.0,
        leverage: parseInt(leverage, 10) || 100,
        spread: parseFloat(spread) || 20.0,
        commission: parseFloat(commission) || 7.0,
        slippage: parseFloat(slippage) || 1.0,
        start_date: startDate,
        end_date: endDate,
      };
      const res = await api.mt5BacktestRun(activeStrategy.id, payload);
      setSuccessMsg(`✓ MT5 Strategy Tester execution completed! Status: ${res.mt5_backtest.status} | Net Profit: ${fmt.currency(res.mt5_backtest.net_profit)}`);
      loadStrategy(activeStrategy.id);
      loadHistory(activeStrategy.id);
    } catch (err) {
      setError(err);
    } finally {
      setExecuting(false);
    }
  };

  return (
    <div className="mt5-backtest-page p-6 max-w-7xl mx-auto space-y-6 animate-in fade-in duration-300">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 bg-slate-900/80 p-4 rounded-xl border border-slate-800 shadow-md">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            <span>⚙️ MT5 STRATEGY TESTER & BACKTEST ENGINE</span>
            <span className="text-xs px-2 py-0.5 rounded font-mono bg-indigo-950 text-indigo-400 border border-indigo-800">
              STAGE 3 PROMOTION
            </span>
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Standardized MT5 Strategy Tester execution with realistic commissions, bid/ask spreads, execution slippage, and persistent SQLite storage.
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
              Target: {activeStrategy ? `Node_${activeStrategy.id} (${activeStrategy.symbol} ${activeStrategy.timeframe})` : "None"}
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

          {/* Action Button */}
          <div className="pt-3 border-t border-slate-800 flex items-center justify-between">
            <div className="text-xs text-slate-400 font-mono">
              Results are permanently persisted into <code className="text-cyan-400">DATA/DATABASE/lab_state.db</code>
            </div>
            <button
              type="button"
              disabled={executing || !activeStrategy}
              onClick={handleRunTest}
              className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-xs rounded-xl shadow-md transition-all disabled:opacity-50 flex items-center gap-2"
            >
              {executing ? (
                <>
                  <span className="animate-spin text-sm">↻</span>
                  <span>Executing MT5 Tester...</span>
                </>
              ) : (
                <>
                  <span>▶</span>
                  <span>RUN MT5 STRATEGY TESTER</span>
                </>
              )}
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
                <span className="text-white font-bold">{activeStrategy.node_id}</span>
              </div>
              <div className="flex justify-between items-center bg-slate-950/60 p-2 rounded border border-slate-800">
                <span className="text-slate-500">Asset & Timeframe:</span>
                <span className="text-cyan-400 font-semibold">{activeStrategy.symbol} · {activeStrategy.timeframe}</span>
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
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="border-b border-slate-800 text-slate-400 uppercase text-[10px]">
                  <th className="py-2.5 px-3">Date</th>
                  <th className="py-2.5 px-3">Node</th>
                  <th className="py-2.5 px-3">Symbol / TF</th>
                  <th className="py-2.5 px-3 text-right">Init Cap</th>
                  <th className="py-2.5 px-3 text-right">Final Cap</th>
                  <th className="py-2.5 px-3 text-right">Net Profit</th>
                  <th className="py-2.5 px-3 text-right">PF</th>
                  <th className="py-2.5 px-3 text-right">Win Rate</th>
                  <th className="py-2.5 px-3 text-right">Trades</th>
                  <th className="py-2.5 px-3 text-right">Max DD</th>
                  <th className="py-2.5 px-3 text-right">Sharpe</th>
                  <th className="py-2.5 px-3">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {historyResults.map((row) => (
                  <tr key={row.id} className="hover:bg-slate-800/40 transition-colors">
                    <td className="py-2 px-3 text-slate-400">{fmt.dt(row.created_at)}</td>
                    <td className="py-2 px-3">
                      <button
                        type="button"
                        onClick={() => loadStrategy(row.strategy_id)}
                        className="text-cyan-400 hover:underline font-bold"
                      >
                        Node_{row.strategy_id}
                      </button>
                    </td>
                    <td className="py-2 px-3 text-slate-300">{row.symbol} · {row.timeframe}</td>
                    <td className="py-2 px-3 text-right text-slate-400">{fmt.currency(row.initial_capital, 0)}</td>
                    <td className="py-2 px-3 text-right text-white font-bold">{fmt.currency(row.final_capital, 0)}</td>
                    <td className={`py-2 px-3 text-right font-bold ${row.net_profit >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {fmt.pnl(row.net_profit)}
                    </td>
                    <td className="py-2 px-3 text-right text-slate-200">{fmt.ratio(row.profit_factor)}</td>
                    <td className="py-2 px-3 text-right text-slate-200">{fmt.pct(row.win_rate)}</td>
                    <td className="py-2 px-3 text-right text-slate-300">{row.trade_count}</td>
                    <td className="py-2 px-3 text-right text-rose-400">{fmt.pct(row.max_drawdown_pct)}</td>
                    <td className="py-2 px-3 text-right text-slate-300">{fmt.num(row.sharpe)}</td>
                    <td className="py-2 px-3">
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
