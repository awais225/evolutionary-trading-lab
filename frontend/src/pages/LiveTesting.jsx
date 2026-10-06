import React, { useState, useEffect, useCallback } from "react";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import StructuredError from "../components/StructuredError.jsx";
import LiveTestingControl from "../components/LiveTestingControl.jsx";

export default function LiveTesting() {
  const { shortlist, toggleShortlist, setSelectedStrategyId, navigateTab } = useLab() || {};
  const [strategies, setStrategies] = useState([]);
  const [statusSummary, setStatusSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [filterShortlistOnly, setFilterShortlistOnly] = useState(false);
  const [filterActiveOnly, setFilterActiveOnly] = useState(false);
  const [search, setSearch] = useState("");
  const [error, setError] = useState(null);
  const [actionMsg, setActionMsg] = useState(null);

  // Schedule modal state
  const [scheduleModalOpen, setScheduleModalOpen] = useState(false);
  const [activeConfigSid, setActiveConfigSid] = useState(null);
  const [scheduleConfig, setScheduleConfig] = useState({
    timeframes: ["M15"],
    days: ["Mon", "Tue", "Wed", "Thu", "Fri"],
    sessions: ["london", "newyork"],
    start_time: "00:00",
    end_time: "23:59",
    timezone: "UTC",
    lot_size: 0.1,
    risk_pct: 1.0,
  });

  const loadLiveState = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [stRes, stratRes] = await Promise.all([
        api.liveTestStatus(),
        api.researchFilter({ limit: 100, sort_by: "backtest_return_pct", sort_desc: true }),
      ]);
      setStatusSummary(stRes);
      setStrategies(stratRes.strategies || []);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadLiveState();
    const interval = setInterval(loadLiveState, 10000);
    return () => clearInterval(interval);
  }, [loadLiveState]);

  // Bulk actions
  const handleStartAll = async () => {
    try {
      const res = await api.liveTestStartAll();
      setActionMsg({ ok: true, text: `✓ Activated ${res.activated_count} strategies in forward live testing!` });
      loadLiveState();
    } catch (err) {
      setError(err);
    }
  };

  const handleStartShortlist = async () => {
    try {
      const res = await api.liveTestStartShortlist();
      setActionMsg({ ok: true, text: `✓ Activated ${res.activated_count} shortlisted strategies in forward live testing!` });
      loadLiveState();
    } catch (err) {
      setError(err);
    }
  };

  const handleStopAll = async () => {
    try {
      await api.liveTestStopAll();
      setActionMsg({ ok: true, text: "✓ All live test strategies stopped." });
      loadLiveState();
    } catch (err) {
      setError(err);
    }
  };

  const handlePauseAll = async () => {
    try {
      await api.liveTestPauseAll();
      setActionMsg({ ok: true, text: "✓ All live test strategies paused." });
      loadLiveState();
    } catch (err) {
      setError(err);
    }
  };

  const handleResumeAll = async () => {
    try {
      await api.liveTestResumeAll();
      setActionMsg({ ok: true, text: "✓ All active live test strategies resumed." });
      loadLiveState();
    } catch (err) {
      setError(err);
    }
  };

  // Single toggle
  const handleToggleStrategy = async (sid) => {
    try {
      const res = await api.toggleLiveTest(sid);
      setActionMsg({ ok: true, text: `✓ Strategy Node_${sid} live testing status: ${res.status}` });
      loadLiveState();
    } catch (err) {
      setError(err);
    }
  };

  // Schedule modal open
  const handleOpenSchedule = async (sid) => {
    setActiveConfigSid(sid);
    try {
      const res = await api.liveTestConfig(sid);
      if (res?.config) {
        setScheduleConfig(res.config);
      }
      setScheduleModalOpen(true);
    } catch (err) {
      setError(err);
    }
  };

  const handleSaveSchedule = async () => {
    if (!activeConfigSid) return;
    try {
      await api.saveLiveTestConfig(activeConfigSid, scheduleConfig);
      setActionMsg({ ok: true, text: `✓ Schedule rules saved for Node_${activeConfigSid} without genome modification!` });
      setScheduleModalOpen(false);
      loadLiveState();
    } catch (err) {
      setError(err);
    }
  };

  const activeIdsSet = new Set(statusSummary?.active_strategy_ids || []);

  const filteredStrategies = strategies.filter((s) => {
    if (filterShortlistOnly && !shortlist?.includes(s.id) && !s.shortlisted) return false;
    if (filterActiveOnly && !activeIdsSet.has(s.id)) return false;
    if (search) {
      const q = search.toLowerCase();
      const match =
        String(s.id).includes(q) ||
        s.node_id?.toLowerCase().includes(q) ||
        s.symbol?.toLowerCase().includes(q) ||
        s.timeframe?.toLowerCase().includes(q);
      if (!match) return false;
    }
    return true;
  });

  return (
    <div className="live-testing-page p-6 max-w-7xl mx-auto space-y-6 animate-in fade-in duration-300">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 bg-slate-900/80 p-4 rounded-xl border border-slate-800 shadow-md">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            <span>📡 LIVE TESTING ENGINE (FORWARD TEST)</span>
            <span className="text-xs px-2 py-0.5 rounded font-mono bg-blue-950 text-blue-400 border border-blue-800">
              ZERO REAL MONEY · REAL-TIME MARKET DATA
            </span>
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Real-time forward market simulation. Per-node schedule and session controls without genome mutation.
          </p>
        </div>

        {/* Global Live Testing Telemetry */}
        <div className="flex items-center gap-4 text-xs font-mono">
          <div className="bg-slate-950 px-3 py-1.5 rounded-lg border border-slate-800">
            <span className="text-slate-500 block text-[10px]">Active Nodes</span>
            <span className="text-cyan-400 font-bold">{statusSummary?.active_strategies || 0}</span>
          </div>
          <div className="bg-slate-950 px-3 py-1.5 rounded-lg border border-slate-800">
            <span className="text-slate-500 block text-[10px]">Open Pos</span>
            <span className="text-white font-bold">{statusSummary?.open_positions || 0}</span>
          </div>
          <div className="bg-slate-950 px-3 py-1.5 rounded-lg border border-slate-800">
            <span className="text-slate-500 block text-[10px]">Today P/L</span>
            <span className={(statusSummary?.today_pnl || 0) >= 0 ? "text-emerald-400 font-bold" : "text-rose-400 font-bold"}>
              {fmt.currency(statusSummary?.today_pnl || 0)}
            </span>
          </div>
          <div className="bg-slate-950 px-3 py-1.5 rounded-lg border border-slate-800">
            <span className="text-slate-500 block text-[10px]">Total Live P/L</span>
            <span className={(statusSummary?.total_pnl || 0) >= 0 ? "text-emerald-400 font-bold" : "text-rose-400 font-bold"}>
              {fmt.currency(statusSummary?.total_pnl || 0)}
            </span>
          </div>
        </div>
      </div>

      {error && <StructuredError error={error} onDismiss={() => setError(null)} />}
      {actionMsg && (
        <div className={`p-3 rounded-lg text-xs font-mono ${actionMsg.ok ? "bg-emerald-950/60 border border-emerald-500/40 text-emerald-300" : "bg-rose-950/60 border border-rose-500/40 text-rose-300"}`}>
          {actionMsg.text}
        </div>
      )}

      {/* V4.3 — controlled demo execution: mode, safety, risk, market, counter, stage log */}
      <LiveTestingControl />

      {/* Action Toolbar (Spec §12) */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-slate-900/60 p-3 rounded-xl border border-slate-800">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={handleStartAll}
            className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white font-bold text-xs rounded-lg transition-colors shadow"
          >
            ▶ LIVE TEST ALL
          </button>
          <button
            type="button"
            onClick={handleStartShortlist}
            className="px-3 py-1.5 bg-amber-600 hover:bg-amber-500 text-white font-bold text-xs rounded-lg transition-colors shadow flex items-center gap-1"
          >
            <span>⭐</span>
            <span>LIVE TEST SHORTLIST</span>
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
            onClick={handleResumeAll}
            className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-emerald-300 font-medium text-xs rounded-lg border border-slate-700 transition-colors"
          >
            ↻ RESUME ALL
          </button>
          <button
            type="button"
            onClick={handleStopAll}
            className="px-3 py-1.5 bg-rose-950/60 hover:bg-rose-900/80 text-rose-300 font-medium text-xs rounded-lg border border-rose-800 transition-colors"
          >
            ⏹ STOP ALL
          </button>
        </div>

        {/* Filters & Search */}
        <div className="flex items-center gap-3 text-xs font-mono">
          <label className="flex items-center gap-1.5 text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={filterShortlistOnly}
              onChange={(e) => setFilterShortlistOnly(e.target.checked)}
              className="rounded bg-slate-950 border-slate-700 text-amber-500 focus:ring-0"
            />
            <span>Shortlist Only (⭐)</span>
          </label>
          <label className="flex items-center gap-1.5 text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={filterActiveOnly}
              onChange={(e) => setFilterActiveOnly(e.target.checked)}
              className="rounded bg-slate-950 border-slate-700 text-blue-500 focus:ring-0"
            />
            <span>Active Only</span>
          </label>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search node or symbol..."
            className="px-2.5 py-1 bg-slate-950 border border-slate-800 rounded text-slate-200 text-xs w-44 focus:outline-none focus:border-blue-500"
          />
          <button
            type="button"
            onClick={loadLiveState}
            className="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs transition-colors"
            title="Refresh"
          >
            ↻
          </button>
        </div>
      </div>

      {/* Strategies List View (Spec §14) */}
      <div className="bg-slate-900/80 rounded-xl border border-slate-800 p-4 shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-3">
          <span className="text-xs uppercase font-bold tracking-wider text-slate-300">
            Live Testing Candidates ({filteredStrategies.length})
          </span>
          <span className="text-[11px] font-mono text-slate-500">
            Click "Schedule" to adjust active days and sessions without genome mutation
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 uppercase text-[10px]">
                <th className="py-2 px-2">⭐</th>
                <th className="py-2 px-3">Node</th>
                <th className="py-2 px-3">Status</th>
                <th className="py-2 px-3">Market</th>
                <th className="py-2 px-3 text-right">IS Return</th>
                <th className="py-2 px-3 text-right">PF</th>
                <th className="py-2 px-3 text-right">Today P/L</th>
                <th className="py-2 px-3 text-right">Total Live P/L</th>
                <th className="py-2 px-3 text-right">Trades</th>
                <th className="py-2 px-3">Schedule / Days</th>
                <th className="py-2 px-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {filteredStrategies.map((strat) => {
                const isActive = activeIdsSet.has(strat.id);
                const isStar = shortlist?.includes(strat.id) || strat.shortlisted;
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
                    <td className="py-2.5 px-3">
                      <span className={`text-[10px] px-2 py-0.5 rounded font-bold border ${isActive ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/40" : "bg-slate-800 text-slate-400 border-slate-700"}`}>
                        {isActive ? "RUNNING" : "IDLE"}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-slate-300">
                      {strat.symbol} · {strat.timeframe}
                    </td>
                    <td className="py-2.5 px-3 text-right text-cyan-300 font-semibold">
                      {fmt.ret(strat.backtest_return_pct)}
                    </td>
                    <td className="py-2.5 px-3 text-right text-slate-200">
                      {fmt.ratio(strat.profit_factor)}
                    </td>
                    <td className="py-2.5 px-3 text-right text-slate-300">
                      $0.00
                    </td>
                    <td className="py-2.5 px-3 text-right font-bold text-slate-300">
                      $0.00
                    </td>
                    <td className="py-2.5 px-3 text-right text-slate-400">
                      0
                    </td>
                    <td className="py-2.5 px-3 text-[11px] text-slate-400">
                      Mon-Fri · 24h
                    </td>
                    <td className="py-2.5 px-3 text-right space-x-1.5">
                      <button
                        type="button"
                        onClick={() => handleOpenSchedule(strat.id)}
                        className="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-[11px] transition-colors"
                      >
                        Schedule
                      </button>
                      <button
                        type="button"
                        onClick={() => handleToggleStrategy(strat.id)}
                        className={`px-2.5 py-1 rounded text-[11px] font-bold transition-colors ${
                          isActive
                            ? "bg-rose-950/80 text-rose-300 border border-rose-800 hover:bg-rose-900"
                            : "bg-blue-600 text-white hover:bg-blue-500 shadow"
                        }`}
                      >
                        {isActive ? "Stop" : "Live Test"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Schedule Controls Modal (Spec §15) */}
      {scheduleModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm animate-in fade-in duration-150">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl max-w-lg w-full p-6 shadow-2xl space-y-5 text-xs font-mono">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div>
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  <span>🗓️ Node Schedule & Filter Controls</span>
                  <span className="text-xs text-cyan-400">Node_{activeConfigSid}</span>
                </h3>
                <p className="text-[11px] text-slate-400 mt-0.5">
                  Does NOT modify the underlying strategy genome. Controls when live test orders can open.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setScheduleModalOpen(false)}
                className="text-slate-400 hover:text-white text-lg p-1"
              >
                ✕
              </button>
            </div>

            {/* Timeframe Checkboxes */}
            <div className="space-y-1.5">
              <label className="text-slate-300 font-bold block">Execution Timeframes:</label>
              <div className="flex flex-wrap gap-2">
                {["M1", "M5", "M15", "M30", "H1", "H4", "D1"].map((tf) => {
                  const checked = scheduleConfig.timeframes?.includes(tf);
                  return (
                    <label key={tf} className={`px-2.5 py-1 rounded border cursor-pointer ${checked ? "bg-cyan-950/80 border-cyan-500 text-cyan-300 font-bold" : "bg-slate-950 border-slate-800 text-slate-400"}`}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => {
                          const cur = new Set(scheduleConfig.timeframes || []);
                          if (cur.has(tf)) cur.delete(tf);
                          else cur.add(tf);
                          setScheduleConfig({ ...scheduleConfig, timeframes: Array.from(cur) });
                        }}
                        className="sr-only"
                      />
                      <span>{tf}</span>
                    </label>
                  );
                })}
              </div>
            </div>

            {/* Days of Week Checkboxes (Spec §15: Unchecked day must not open trades) */}
            <div className="space-y-1.5">
              <label className="text-slate-300 font-bold block">Trading Days of Week:</label>
              <div className="flex flex-wrap gap-2">
                {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => {
                  const checked = scheduleConfig.days?.includes(day);
                  return (
                    <label key={day} className={`px-2.5 py-1 rounded border cursor-pointer ${checked ? "bg-emerald-950/80 border-emerald-500 text-emerald-300 font-bold" : "bg-slate-950 border-slate-800 text-slate-500"}`}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => {
                          const cur = new Set(scheduleConfig.days || []);
                          if (cur.has(day)) cur.delete(day);
                          else cur.add(day);
                          setScheduleConfig({ ...scheduleConfig, days: Array.from(cur) });
                        }}
                        className="sr-only"
                      />
                      <span>{day}</span>
                    </label>
                  );
                })}
              </div>
              <div className="text-[10px] text-slate-500">
                Unchecked days are strictly blocked from generating new live-test trade entries.
              </div>
            </div>

            {/* Sessions Checkboxes */}
            <div className="space-y-1.5">
              <label className="text-slate-300 font-bold block">Trading Sessions:</label>
              <div className="flex flex-wrap gap-2">
                {[
                  ["asia", "Asia (Tokyo)"],
                  ["london", "London"],
                  ["newyork", "New York"],
                  ["london_ny_overlap", "London/NY Overlap"]
                ].map(([sid, label]) => {
                  const checked = scheduleConfig.sessions?.includes(sid);
                  return (
                    <label key={sid} className={`px-2.5 py-1 rounded border cursor-pointer ${checked ? "bg-indigo-950/80 border-indigo-500 text-indigo-300 font-bold" : "bg-slate-950 border-slate-800 text-slate-500"}`}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => {
                          const cur = new Set(scheduleConfig.sessions || []);
                          if (cur.has(sid)) cur.delete(sid);
                          else cur.add(sid);
                          setScheduleConfig({ ...scheduleConfig, sessions: Array.from(cur) });
                        }}
                        className="sr-only"
                      />
                      <span>{label}</span>
                    </label>
                  );
                })}
              </div>
            </div>

            {/* Custom Time Window */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-slate-400 block mb-1">Start Time (UTC):</label>
                <input
                  type="time"
                  value={scheduleConfig.start_time || "00:00"}
                  onChange={(e) => setScheduleConfig({ ...scheduleConfig, start_time: e.target.value })}
                  className="w-full px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded text-slate-200"
                />
              </div>
              <div>
                <label className="text-slate-400 block mb-1">End Time (UTC):</label>
                <input
                  type="time"
                  value={scheduleConfig.end_time || "23:59"}
                  onChange={(e) => setScheduleConfig({ ...scheduleConfig, end_time: e.target.value })}
                  className="w-full px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded text-slate-200"
                />
              </div>
            </div>

            {/* Risk & Lot Size */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-slate-400 block mb-1">Lot Size:</label>
                <input
                  type="number"
                  step="0.01"
                  value={scheduleConfig.lot_size || 0.1}
                  onChange={(e) => setScheduleConfig({ ...scheduleConfig, lot_size: parseFloat(e.target.value) || 0.1 })}
                  className="w-full px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded text-slate-200"
                />
              </div>
              <div>
                <label className="text-slate-400 block mb-1">Risk % Per Trade:</label>
                <input
                  type="number"
                  step="0.1"
                  value={scheduleConfig.risk_pct || 1.0}
                  onChange={(e) => setScheduleConfig({ ...scheduleConfig, risk_pct: parseFloat(e.target.value) || 1.0 })}
                  className="w-full px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded text-slate-200"
                />
              </div>
            </div>

            {/* Modal Buttons */}
            <div className="pt-3 border-t border-slate-800 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setScheduleModalOpen(false)}
                className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSaveSchedule}
                className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-lg transition-colors shadow"
              >
                Save Schedule Rules
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
