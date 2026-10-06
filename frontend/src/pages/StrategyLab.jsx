import React, { useEffect, useState, useCallback } from "react";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import { Pill, SignedNum, Spinner } from "../components/common.jsx";
import StructuredError from "../components/StructuredError.jsx";

export default function StrategyLab() {
  const { shortlist, toggleShortlist, setSelectedStrategyId, navigateTab } = useLab() || {};

  // Comprehensive Filters (Spec §5)
  const [filters, setFilters] = useState({
    min_return_pct: "",
    max_drawdown_pct: "",
    min_profit_factor: "",
    min_win_rate: "",
    min_trades: "",
    min_sharpe: "",
    min_sortino: "",
    min_robustness_score: "",
    min_oos_return_pct: "",
    min_oos_profit_factor: "",
    symbol: "",
    timeframe: "",
    status_category: "ALL", // ALL | QUALIFIED | ALIVE | DEAD
    status: "",
    generation: "",
    search: "",
  });

  const [shortlistOnly, setShortlistOnly] = useState(false);
  const [sortCol, setSortCol] = useState("backtest_return_pct");
  const [sortDir, setSortDir] = useState("desc");
  const [activeQuickFilter, setActiveQuickFilter] = useState("NONE");

  // State
  const [strategies, setStrategies] = useState([]);
  const [totalEvaluated, setTotalEvaluated] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchStrategies = useCallback(async (overrideFilters = null, overrideSort = null, overrideDir = null) => {
    setLoading(true);
    setError(null);
    try {
      const f = overrideFilters || filters;
      const sCol = overrideSort || sortCol;
      const sDir = overrideDir !== null ? overrideDir : sortDir;

      const payload = {
        limit: 300,
        offset: 0,
        sort_by: sCol,
        sort_desc: sDir === "desc",
        shortlist_only: shortlistOnly || undefined,
        search: f.search?.trim() || undefined,
        symbol: f.symbol || undefined,
        timeframe: f.timeframe || undefined,
        status_category: f.status_category !== "ALL" ? f.status_category : undefined,
        status: f.status || undefined,
        generation: f.generation !== "" ? parseInt(f.generation, 10) : undefined,
        min_trades: f.min_trades !== "" ? parseInt(f.min_trades, 10) : undefined,
        min_return_pct: f.min_return_pct !== "" ? parseFloat(f.min_return_pct) : undefined,
        max_drawdown_pct: f.max_drawdown_pct !== "" ? parseFloat(f.max_drawdown_pct) : undefined,
        min_win_rate: f.min_win_rate !== "" ? parseFloat(f.min_win_rate) : undefined,
        min_profit_factor: f.min_profit_factor !== "" ? parseFloat(f.min_profit_factor) : undefined,
        min_sharpe: f.min_sharpe !== "" ? parseFloat(f.min_sharpe) : undefined,
        min_sortino: f.min_sortino !== "" ? parseFloat(f.min_sortino) : undefined,
        min_robustness_score: f.min_robustness_score !== "" ? parseFloat(f.min_robustness_score) : undefined,
        min_oos_return_pct: f.min_oos_return_pct !== "" ? parseFloat(f.min_oos_return_pct) : undefined,
        min_oos_profit_factor: f.min_oos_profit_factor !== "" ? parseFloat(f.min_oos_profit_factor) : undefined,
      };

      const res = await api.researchFilter(payload);
      setStrategies(res.strategies || []);
      setTotalEvaluated(res.total_evaluated || 0);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [filters, sortCol, sortDir, shortlistOnly]);

  useEffect(() => {
    fetchStrategies();
  }, [fetchStrategies]);

  // Quick Filter Handler (Spec §5)
  const applyQuickFilter = (type) => {
    setActiveQuickFilter(type);
    let newF = { ...filters };
    let newSort = "backtest_return_pct";
    let newDir = "desc";

    if (type === "QUALIFIED") {
      newF.status_category = "QUALIFIED";
      setShortlistOnly(false);
    } else if (type === "SHORTLISTED") {
      setShortlistOnly(true);
      newF.status_category = "ALL";
    } else if (type === "BEST_RETURN") {
      newF.status_category = "ALL";
      newSort = "backtest_return_pct";
      newDir = "desc";
    } else if (type === "BEST_PF") {
      newF.status_category = "ALL";
      newSort = "profit_factor";
      newDir = "desc";
    } else if (type === "LOWEST_DD") {
      newF.status_category = "ALL";
      newSort = "max_drawdown_pct";
      newDir = "asc";
    } else if (type === "BEST_ROBUSTNESS") {
      newF.status_category = "ALL";
      newSort = "robustness_score";
      newDir = "desc";
    } else if (type === "BEST_OOS") {
      newF.status_category = "ALL";
      newSort = "validation_oos_return_pct";
      newDir = "desc";
    } else if (type === "RESET") {
      newF = {
        min_return_pct: "",
        max_drawdown_pct: "",
        min_profit_factor: "",
        min_win_rate: "",
        min_trades: "",
        min_sharpe: "",
        min_sortino: "",
        min_robustness_score: "",
        min_oos_return_pct: "",
        min_oos_profit_factor: "",
        symbol: "",
        timeframe: "",
        status_category: "ALL",
        status: "",
        generation: "",
        search: "",
      };
      setShortlistOnly(false);
      newSort = "backtest_return_pct";
      newDir = "desc";
    }

    setFilters(newF);
    setSortCol(newSort);
    setSortDir(newDir);
    fetchStrategies(newF, newSort, newDir);
  };

  const handleSort = (col) => {
    if (sortCol === col) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortCol(col);
      setSortDir("desc");
    }
  };

  const sortArrow = (col) => {
    if (sortCol !== col) return "";
    return sortDir === "asc" ? " ▲" : " ▼";
  };

  return (
    <div className="strategy-lab-page p-6 max-w-7xl mx-auto space-y-6 animate-in fade-in duration-300">
      {/* Top Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 bg-slate-900/80 p-4 rounded-xl border border-slate-800 shadow-md">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            <span>🔬 STRATEGY LABORATORY</span>
            <span className="text-xs px-2 py-0.5 rounded font-mono bg-cyan-950 text-cyan-400 border border-cyan-800">
              PRIMARY SELECTION WORKSPACE
            </span>
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Examine and filter across all {totalEvaluated} research candidates. Prioritize authoritative detail backtests and out-of-sample validation.
          </p>
        </div>

        <div className="flex items-center gap-2 text-xs font-mono">
          <input
            type="text"
            value={filters.search}
            onChange={(e) => setFilters({ ...filters, search: e.target.value })}
            onKeyDown={(e) => { if (e.key === "Enter") fetchStrategies(); }}
            placeholder="Search node, symbol, TF..."
            className="px-3 py-1.5 bg-slate-950 border border-slate-700 rounded-lg text-white font-mono w-56 focus:outline-none focus:border-cyan-500"
          />
          <button
            type="button"
            onClick={() => fetchStrategies()}
            className="px-3 py-1.5 bg-cyan-600 hover:bg-cyan-500 text-white font-bold rounded-lg transition-colors shadow"
          >
            Filter
          </button>
        </div>
      </div>

      {error && <StructuredError error={error} onDismiss={() => setError(null)} />}

      {/* QUICK FILTERS BAR (Spec §5) */}
      <div className="bg-slate-900/60 p-3.5 rounded-xl border border-slate-800 space-y-2.5">
        <div className="flex items-center justify-between text-xs">
          <span className="text-[10px] uppercase font-bold tracking-wider text-slate-400">
            Quick Filters (Spec §5):
          </span>
          <span className="text-slate-500 font-mono text-[11px]">
            {strategies.length} matching candidates
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => applyQuickFilter("QUALIFIED")}
            className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold transition-all border ${
              activeQuickFilter === "QUALIFIED"
                ? "bg-emerald-600 text-white border-emerald-500 shadow-md"
                : "bg-slate-950 text-slate-300 border-slate-800 hover:border-slate-700"
            }`}
          >
            QUALIFIED
          </button>
          <button
            type="button"
            onClick={() => applyQuickFilter("SHORTLISTED")}
            className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold transition-all border flex items-center gap-1 ${
              activeQuickFilter === "SHORTLISTED"
                ? "bg-amber-600 text-white border-amber-500 shadow-md"
                : "bg-slate-950 text-slate-300 border-slate-800 hover:border-slate-700"
            }`}
          >
            <span>⭐</span>
            <span>SHORTLISTED</span>
          </button>
          <button
            type="button"
            onClick={() => applyQuickFilter("BEST_RETURN")}
            className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold transition-all border ${
              activeQuickFilter === "BEST_RETURN"
                ? "bg-cyan-600 text-white border-cyan-500 shadow-md"
                : "bg-slate-950 text-slate-300 border-slate-800 hover:border-slate-700"
            }`}
          >
            BEST RETURN
          </button>
          <button
            type="button"
            onClick={() => applyQuickFilter("BEST_PF")}
            className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold transition-all border ${
              activeQuickFilter === "BEST_PF"
                ? "bg-cyan-600 text-white border-cyan-500 shadow-md"
                : "bg-slate-950 text-slate-300 border-slate-800 hover:border-slate-700"
            }`}
          >
            BEST PF
          </button>
          <button
            type="button"
            onClick={() => applyQuickFilter("LOWEST_DD")}
            className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold transition-all border ${
              activeQuickFilter === "LOWEST_DD"
                ? "bg-cyan-600 text-white border-cyan-500 shadow-md"
                : "bg-slate-950 text-slate-300 border-slate-800 hover:border-slate-700"
            }`}
          >
            LOWEST DD
          </button>
          <button
            type="button"
            onClick={() => applyQuickFilter("BEST_ROBUSTNESS")}
            className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold transition-all border ${
              activeQuickFilter === "BEST_ROBUSTNESS"
                ? "bg-indigo-600 text-white border-indigo-500 shadow-md"
                : "bg-slate-950 text-slate-300 border-slate-800 hover:border-slate-700"
            }`}
          >
            BEST ROBUSTNESS
          </button>
          <button
            type="button"
            onClick={() => applyQuickFilter("BEST_OOS")}
            className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold transition-all border ${
              activeQuickFilter === "BEST_OOS"
                ? "bg-indigo-600 text-white border-indigo-500 shadow-md"
                : "bg-slate-950 text-slate-300 border-slate-800 hover:border-slate-700"
            }`}
          >
            BEST OOS
          </button>
          <button
            type="button"
            onClick={() => applyQuickFilter("RESET")}
            className="px-2.5 py-1.5 rounded-lg text-xs font-mono text-slate-400 hover:text-white transition-colors ml-auto"
          >
            Reset Filters
          </button>
        </div>
      </div>

      {/* STRATEGIES SELECTION TABLE */}
      <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-4 shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 uppercase text-[10px]">
                <th className="py-2.5 px-2">⭐</th>
                <th onClick={() => handleSort("id")} className="py-2.5 px-3 cursor-pointer hover:text-white">
                  Node ID{sortArrow("id")}
                </th>
                <th onClick={() => handleSort("status")} className="py-2.5 px-3 cursor-pointer hover:text-white">
                  Status{sortArrow("status")}
                </th>
                <th onClick={() => handleSort("generation")} className="py-2.5 px-3 cursor-pointer hover:text-white">
                  Gen{sortArrow("generation")}
                </th>
                <th onClick={() => handleSort("symbol")} className="py-2.5 px-3 cursor-pointer hover:text-white">
                  Symbol/TF{sortArrow("symbol")}
                </th>
                <th onClick={() => handleSort("trades")} className="py-2.5 px-3 cursor-pointer hover:text-white text-right">
                  Trades{sortArrow("trades")}
                </th>
                <th onClick={() => handleSort("backtest_return_pct")} className="py-2.5 px-3 cursor-pointer hover:text-white text-right text-cyan-400 font-bold">
                  BACKTEST RETURN (IS){sortArrow("backtest_return_pct")}
                </th>
                <th onClick={() => handleSort("validation_oos_return_pct")} className="py-2.5 px-3 cursor-pointer hover:text-white text-right text-emerald-400 font-bold">
                  OOS RETURN{sortArrow("validation_oos_return_pct")}
                </th>
                <th onClick={() => handleSort("profit_factor")} className="py-2.5 px-3 cursor-pointer hover:text-white text-right">
                  PF{sortArrow("profit_factor")}
                </th>
                <th onClick={() => handleSort("max_drawdown_pct")} className="py-2.5 px-3 cursor-pointer hover:text-white text-right">
                  Max DD{sortArrow("max_drawdown_pct")}
                </th>
                <th onClick={() => handleSort("robustness_score")} className="py-2.5 px-3 cursor-pointer hover:text-white text-right">
                  Robustness{sortArrow("robustness_score")}
                </th>
                <th onClick={() => handleSort("sharpe")} className="py-2.5 px-3 cursor-pointer hover:text-white text-right">
                  Sharpe{sortArrow("sharpe")}
                </th>
                <th className="py-2.5 px-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {strategies.map((s) => {
                const isStar = shortlist?.includes(s.id) || s.shortlisted;
                return (
                  <tr key={s.id} className="hover:bg-slate-800/40 transition-colors">
                    <td className="py-2.5 px-2">
                      <button
                        type="button"
                        onClick={() => toggleShortlist && toggleShortlist(s.id)}
                        className={`text-sm ${isStar ? "text-amber-400" : "text-slate-600 hover:text-amber-300"}`}
                        title={isStar ? "Shortlisted" : "Add to shortlist"}
                      >
                        {isStar ? "⭐" : "☆"}
                      </button>
                    </td>
                    <td className="py-2.5 px-3">
                      <button
                        type="button"
                        onClick={() => {
                          if (setSelectedStrategyId) setSelectedStrategyId(s.id);
                          if (navigateTab) navigateTab("economics", s.id);
                        }}
                        className="text-cyan-400 font-bold hover:underline"
                      >
                        {s.node_id}
                      </button>
                    </td>
                    <td className="py-2.5 px-3">
                      <Pill status={s.status} />
                    </td>
                    <td className="py-2.5 px-3 text-slate-300">
                      G{s.generation}
                    </td>
                    <td className="py-2.5 px-3 text-slate-300">
                      {s.symbol} {s.timeframe}
                    </td>
                    <td className="py-2.5 px-3 text-right text-slate-300">
                      {s.trades}
                    </td>
                    <td className="py-2.5 px-3 text-right text-cyan-300 font-bold">
                      {fmt.ret(s.backtest_return_pct)}
                    </td>
                    <td className="py-2.5 px-3 text-right text-emerald-300 font-bold">
                      {s.validation_oos_return_pct != null ? fmt.ret(s.validation_oos_return_pct) : "–"}
                    </td>
                    <td className="py-2.5 px-3 text-right text-slate-200 font-semibold">
                      {fmt.ratio(s.profit_factor)}
                    </td>
                    <td className="py-2.5 px-3 text-right text-rose-400">
                      {fmt.pct(s.max_drawdown_pct)}
                    </td>
                    <td className="py-2.5 px-3 text-right text-indigo-300">
                      {s.robustness_score != null ? fmt.ratio(s.robustness_score, 3) : "–"}
                    </td>
                    <td className="py-2.5 px-3 text-right text-slate-300">
                      {fmt.num(s.sharpe)}
                    </td>
                    <td className="py-2.5 px-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => {
                            if (setSelectedStrategyId) setSelectedStrategyId(s.id);
                            if (navigateTab) navigateTab("economics", s.id);
                          }}
                          className="px-2 py-1 bg-cyan-950/80 hover:bg-cyan-900 border border-cyan-800 text-cyan-300 rounded text-[10px] transition-colors"
                          title="View in Node Economics"
                        >
                          ECONOMICS
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            if (setSelectedStrategyId) setSelectedStrategyId(s.id);
                            if (navigateTab) navigateTab("mt5_backtest", s.id);
                          }}
                          className="px-2 py-1 bg-indigo-950/80 hover:bg-indigo-900 border border-indigo-800 text-indigo-300 rounded text-[10px] transition-colors"
                          title="Send to MT5 Backtest"
                        >
                          MT5 BT
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            if (setSelectedStrategyId) setSelectedStrategyId(s.id);
                            if (navigateTab) navigateTab("live_test", s.id);
                          }}
                          className="px-2 py-1 bg-blue-950/80 hover:bg-blue-900 border border-blue-800 text-blue-300 rounded text-[10px] transition-colors"
                          title="Send to Live Testing"
                        >
                          LIVE
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {strategies.length === 0 && (
                <tr>
                  <td colSpan={13} className="py-12 text-center text-slate-500 font-mono">
                    {loading ? "Loading research candidates..." : "No strategies match the current selection filters."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
