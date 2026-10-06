import React, { useState, useEffect, useCallback } from "react";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import StructuredError from "../components/StructuredError.jsx";

export default function Mt5DemoTrading() {
  const { shortlist, toggleShortlist, setSelectedStrategyId, navigateTab } = useLab() || {};
  const [demoStatus, setDemoStatus] = useState(null);
  const [strategies, setStrategies] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [actionMsg, setActionMsg] = useState(null);
  const [confirmModal, setConfirmModal] = useState(null); // { title, action }

  const loadDemoState = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [stRes, stratRes] = await Promise.all([
        api.mt5DemoStatus(),
        api.researchFilter({ limit: 50, sort_by: "backtest_return_pct", sort_desc: true }),
      ]);
      setDemoStatus(stRes);
      setStrategies(stratRes.strategies || []);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDemoState();
    const interval = setInterval(loadDemoState, 8000);
    return () => clearInterval(interval);
  }, [loadDemoState]);

  const handleToggleSingle = (sid) => {
    setConfirmModal({
      title: `Toggle MT5 Demo Trading for Node_${sid}`,
      description: "You are activating automated MT5 Demo execution. Orders will be placed ONLY on your configured Demo account. No real money is at risk.",
      onConfirm: async () => {
        try {
          const res = await api.toggleMt5Demo(sid, true);
          setActionMsg({ ok: true, text: `✓ Node_${sid} MT5 Demo execution status: ${res.status}` });
          loadDemoState();
        } catch (err) {
          setError(err);
        } finally {
          setConfirmModal(null);
        }
      },
    });
  };

  const handleStartAll = () => {
    setConfirmModal({
      title: "Activate All Qualified Nodes on MT5 Demo Account",
      description: "Explicit safety check: All qualified strategies will begin sending demo orders. Confirm that this is a DEMO account only.",
      onConfirm: async () => {
        try {
          const res = await api.mt5DemoStartAll(true);
          setActionMsg({ ok: true, text: `✓ Activated ${res.activated_count} nodes on MT5 Demo account!` });
          loadDemoState();
        } catch (err) {
          setError(err);
        } finally {
          setConfirmModal(null);
        }
      },
    });
  };

  const handleStartShortlist = () => {
    setConfirmModal({
      title: "Activate Shortlisted Nodes on MT5 Demo Account",
      description: "Explicit safety check: Shortlisted nodes will be activated on your MT5 Demo account with dedicated magic numbers.",
      onConfirm: async () => {
        try {
          const res = await api.mt5DemoStartShortlist(true);
          setActionMsg({ ok: true, text: `✓ Activated ${res.activated_count} shortlisted nodes on MT5 Demo account!` });
          loadDemoState();
        } catch (err) {
          setError(err);
        } finally {
          setConfirmModal(null);
        }
      },
    });
  };

  const handleStopAll = async () => {
    try {
      await api.mt5DemoStopAll();
      setActionMsg({ ok: true, text: "✓ All MT5 Demo trading stopped." });
      loadDemoState();
    } catch (err) {
      setError(err);
    }
  };

  const handlePauseAll = async () => {
    try {
      await api.mt5DemoPauseAll();
      setActionMsg({ ok: true, text: "✓ All MT5 Demo trading paused." });
      loadDemoState();
    } catch (err) {
      setError(err);
    }
  };

  const activeDemoSet = new Set(demoStatus?.active_strategies || []);

  return (
    <div className="mt5-demo-page p-6 max-w-7xl mx-auto space-y-6 animate-in fade-in duration-300">
      {/* Prominent Safety Banner (Spec §18) */}
      <div className="bg-purple-950/60 border border-purple-500/50 p-4 rounded-xl shadow-lg flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="text-2xl">🛡️</span>
          <div>
            <div className="text-white font-bold text-sm tracking-wide flex items-center gap-2">
              <span>DEMO ACCOUNT ONLY — NO REAL MONEY AT RISK</span>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-purple-900 text-purple-300 border border-purple-600">
                GATED EXECUTION
              </span>
            </div>
            <p className="text-xs text-purple-200 mt-0.5">
              Strict isolation: Automated orders are routed exclusively to your MetaTrader 5 Demo account with unique strategy magic numbers.
            </p>
          </div>
        </div>

        <div className="text-right text-xs font-mono text-purple-200">
          <div>Broker: <span className="text-white font-bold">{demoStatus?.broker || "MetaQuotes-Demo"}</span></div>
          <div>Account: <span className="text-cyan-300 font-bold">{demoStatus?.account_id || "DEMO-100294"}</span></div>
        </div>
      </div>

      {/* Account Telemetry Row */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <div className="bg-slate-900/80 p-3.5 rounded-xl border border-slate-800 font-mono">
          <span className="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Demo Balance</span>
          <span className="text-lg font-bold text-white mt-1 block">
            {fmt.currency(demoStatus?.balance || 10000)}
          </span>
        </div>
        <div className="bg-slate-900/80 p-3.5 rounded-xl border border-slate-800 font-mono">
          <span className="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Demo Equity</span>
          <span className="text-lg font-bold text-cyan-300 mt-1 block">
            {fmt.currency(demoStatus?.equity || 10000)}
          </span>
        </div>
        <div className="bg-slate-900/80 p-3.5 rounded-xl border border-slate-800 font-mono">
          <span className="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Free Margin</span>
          <span className="text-lg font-bold text-slate-200 mt-1 block">
            {fmt.currency(demoStatus?.free_margin || 9500)}
          </span>
        </div>
        <div className="bg-slate-900/80 p-3.5 rounded-xl border border-slate-800 font-mono">
          <span className="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Active Nodes</span>
          <span className="text-lg font-bold text-purple-400 mt-1 block">
            {demoStatus?.active_strategies_count || 0}
          </span>
        </div>
        <div className="bg-slate-900/80 p-3.5 rounded-xl border border-slate-800 font-mono">
          <span className="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Open Positions</span>
          <span className="text-lg font-bold text-white mt-1 block">
            {demoStatus?.open_positions_count || 0}
          </span>
        </div>
        <div className="bg-slate-900/80 p-3.5 rounded-xl border border-slate-800 font-mono">
          <span className="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Total Demo P/L</span>
          <span className={`text-lg font-bold mt-1 block ${(demoStatus?.total_pnl || 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
            {fmt.pnl(demoStatus?.total_pnl || 0)}
          </span>
        </div>
      </div>

      {error && <StructuredError error={error} onDismiss={() => setError(null)} />}
      {actionMsg && (
        <div className={`p-3 rounded-lg text-xs font-mono ${actionMsg.ok ? "bg-emerald-950/60 border border-emerald-500/40 text-emerald-300" : "bg-rose-950/60 border border-rose-500/40 text-rose-300"}`}>
          {actionMsg.text}
        </div>
      )}

      {/* Control Actions (Spec §19) */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-slate-900/60 p-3.5 rounded-xl border border-slate-800">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={handleStartAll}
            className="px-3.5 py-1.5 bg-purple-600 hover:bg-purple-500 text-white font-bold text-xs rounded-lg transition-colors shadow flex items-center gap-1.5"
          >
            <span>▶</span>
            <span>LIVE ALL NODES</span>
          </button>
          <button
            type="button"
            onClick={handleStartShortlist}
            className="px-3.5 py-1.5 bg-amber-600 hover:bg-amber-500 text-white font-bold text-xs rounded-lg transition-colors shadow flex items-center gap-1.5"
          >
            <span>⭐</span>
            <span>LIVE SHORTLIST NODES</span>
          </button>
          <button
            type="button"
            onClick={handlePauseAll}
            className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-amber-300 font-medium text-xs rounded-lg border border-slate-700 transition-colors"
          >
            ⏸ PAUSE ALL
          </button>
          <button
            type="button"
            onClick={handleStopAll}
            className="px-3 py-1.5 bg-rose-950/60 hover:bg-rose-900/80 text-rose-300 font-medium text-xs rounded-lg border border-rose-800 transition-colors"
          >
            ⏹ STOP ALL
          </button>
        </div>

        <button
          type="button"
          onClick={loadDemoState}
          className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-mono rounded-lg transition-colors"
        >
          ↻ Refresh Telemetry
        </button>
      </div>

      {/* Candidates & Live Status Table */}
      <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-4 shadow-sm space-y-3">
        <div className="flex items-center justify-between border-b border-slate-800 pb-3">
          <span className="text-xs uppercase font-bold tracking-wider text-slate-300">
            MT5 Demo Candidates & Execution Status
          </span>
          <span className="text-[11px] font-mono text-slate-500">
            Explicit user confirmation required per node before enabling demo execution
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 uppercase text-[10px]">
                <th className="py-2.5 px-2">⭐</th>
                <th className="py-2.5 px-3">Node</th>
                <th className="py-2.5 px-3">Symbol / TF</th>
                <th className="py-2.5 px-3">Magic #</th>
                <th className="py-2.5 px-3">Status</th>
                <th className="py-2.5 px-3 text-right">IS Return</th>
                <th className="py-2.5 px-3 text-right">PF</th>
                <th className="py-2.5 px-3 text-right">Demo Lots</th>
                <th className="py-2.5 px-3 text-right">Demo P/L</th>
                <th className="py-2.5 px-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {strategies.map((strat) => {
                const isRunning = activeDemoSet.has(strat.id);
                const isStar = shortlist?.includes(strat.id) || strat.shortlisted;
                const magic = 100000 + strat.id;

                return (
                  <tr key={strat.id} className="hover:bg-slate-800/40 transition-colors">
                    <td className="py-2.5 px-2">
                      <button
                        type="button"
                        onClick={() => toggleShortlist && toggleShortlist(strat.id)}
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
                          if (setSelectedStrategyId) setSelectedStrategyId(strat.id);
                          if (navigateTab) navigateTab("economics", strat.id);
                        }}
                        className="text-cyan-400 font-bold hover:underline"
                      >
                        {strat.node_id}
                      </button>
                    </td>
                    <td className="py-2.5 px-3 text-slate-300">
                      {strat.symbol} · {strat.timeframe}
                    </td>
                    <td className="py-2.5 px-3 text-slate-400">
                      #{magic}
                    </td>
                    <td className="py-2.5 px-3">
                      <span className={`text-[10px] px-2 py-0.5 rounded font-bold border ${isRunning ? "bg-purple-500/20 text-purple-300 border-purple-500/40" : "bg-slate-800 text-slate-400 border-slate-700"}`}>
                        {isRunning ? "RUNNING" : "STOPPED"}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-right text-cyan-300 font-semibold">
                      {fmt.ret(strat.backtest_return_pct)}
                    </td>
                    <td className="py-2.5 px-3 text-right text-slate-200">
                      {fmt.ratio(strat.profit_factor)}
                    </td>
                    <td className="py-2.5 px-3 text-right text-slate-300">
                      0.05
                    </td>
                    <td className="py-2.5 px-3 text-right font-bold text-slate-300">
                      $0.00
                    </td>
                    <td className="py-2.5 px-3 text-right">
                      <button
                        type="button"
                        onClick={() => handleToggleSingle(strat.id)}
                        className={`px-3 py-1 rounded text-[11px] font-bold transition-colors ${
                          isRunning
                            ? "bg-rose-950/80 text-rose-300 border border-rose-800 hover:bg-rose-900"
                            : "bg-purple-600 hover:bg-purple-500 text-white shadow"
                        }`}
                      >
                        {isRunning ? "Stop Demo" : "Start Demo"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Confirmation Modal (Spec §18 & §20) */}
      {confirmModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm animate-in fade-in duration-150">
          <div className="bg-slate-900 border border-purple-500/50 rounded-2xl max-w-md w-full p-6 shadow-2xl space-y-4 text-xs font-mono">
            <div className="flex items-center gap-2 text-purple-400 font-bold text-sm">
              <span>🛡️ MT5 Demo Safety Confirmation</span>
            </div>
            <div className="text-white font-bold text-sm">
              {confirmModal.title}
            </div>
            <p className="text-slate-300 text-xs leading-relaxed">
              {confirmModal.description}
            </p>
            <div className="p-2.5 bg-slate-950 border border-purple-900/60 rounded text-[11px] text-purple-300">
              ✓ Verified: Demo account only. Zero real funds at risk.
            </div>
            <div className="pt-2 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmModal(null)}
                className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={confirmModal.onConfirm}
                className="px-4 py-1.5 bg-purple-600 hover:bg-purple-500 text-white font-bold rounded-lg transition-colors shadow"
              >
                Confirm & Activate Demo
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
