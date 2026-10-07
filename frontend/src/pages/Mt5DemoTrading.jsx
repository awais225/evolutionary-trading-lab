import React, { useState, useEffect, useCallback } from "react";
import { api, fmt } from "../api.js";
import DemoAccountSafety from "../components/DemoAccountSafety.jsx";
import { NA_TEXT, numOrNull, rows as safeRows, txt } from "../lib/safe.js";
import { useLab } from "../App.jsx";
import StructuredError from "../components/StructuredError.jsx";
import DemoOrderPanel from "../components/DemoOrderPanel.jsx";
import Mt5AccountSelector from "../components/Mt5AccountSelector.jsx";
import { ManualOrderPanel } from "../components/LiveTestingPanels.jsx";

export default function Mt5DemoTrading() {
  const { shortlist, toggleShortlist, setSelectedStrategyId, navigateTab } = useLab() || {};
  const [demoStatus, setDemoStatus] = useState(null);
  const [strategies, setStrategies] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [actionMsg, setActionMsg] = useState(null);
  const [confirmModal, setConfirmModal] = useState(null); // { title, action }
  // V5 §20 — the node list is searchable/sortable/filterable, and every value it
  // shows comes from the backend (a missing value is "n/a", never a placeholder).
  const [nodesTable, setNodesTable] = useState(null);
  const [demoSearch, setDemoSearch] = useState("");
  const [demoSortKey, setDemoSortKey] = useState("is_return_pct");
  const [demoSortDesc, setDemoSortDesc] = useState(true);
  const [demoStarred, setDemoStarred] = useState(false);
  const [demoStatusFilter, setDemoStatusFilter] = useState("");
  const [demoDetail, setDemoDetail] = useState(null);

  const loadDemoState = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [stRes, stratRes, table] = await Promise.all([
        api.mt5DemoStatus(),
        api.researchFilter({ limit: 50, sort_by: "backtest_return_pct", sort_desc: true }),
        api.liveTestingNodesTable({ limit: 200 }),
      ]);
      setDemoStatus(stRes);
      setStrategies(stratRes.strategies || []);
      setNodesTable(table);
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

  /* V5 §20 — client-side search / sort / filter over the backend's own rows. */
  const demoRows = (() => {
    let rows = safeRows(nodesTable?.nodes).map((r) => ({ ...r, isRunning: activeDemoSet.has(r.node_id) }))
      .filter((r) => !Number.isNaN(Number(r.node_id)));
    if (demoStarred) rows = rows.filter((r) => r.starred);
    if (demoStatusFilter) {
      rows = rows.filter((r) => String(r.v5_status || r.status || "").toUpperCase() === demoStatusFilter);
    }
    if (demoSearch) {
      const q = demoSearch.toLowerCase();
      rows = rows.filter((r) => `node_${r.node_id} ${r.market || ""} ${r.timeframe || ""}`
        .toLowerCase().includes(q));
    }
    const key = demoSortKey;
    rows.sort((a, b) => {
      const av = a[key]; const bv = b[key];
      const an = av === null || av === undefined; const bn = bv === null || bv === undefined;
      if (an && bn) return 0;
      if (an) return 1;                     // rows without a value sort last, always
      if (bn) return -1;
      if (typeof av === "number" && typeof bv === "number") return demoSortDesc ? bv - av : av - bv;
      return demoSortDesc
        ? String(bv).localeCompare(String(av))
        : String(av).localeCompare(String(bv));
    });
    return rows;
  })();

  return (
    <div className="mt5-demo-page p-6 max-w-7xl mx-auto space-y-6 animate-in fade-in duration-300">
      {/* Prominent Safety Banner (Spec §18) */}
      <div className="bg-purple-950/60 border border-purple-500/50 p-4 rounded-xl shadow-lg flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="status-pill" title="demo account only">DEMO</span>
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
          <div>Broker: <span className="text-white font-bold">{txt(demoStatus?.broker, NA_TEXT)}</span></div>
          <div>Account: <span className="text-cyan-300 font-bold">{txt(demoStatus?.account_id, NA_TEXT)}</span></div>
        </div>
      </div>

      {/* Account Telemetry Row */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <div className="bg-slate-900/80 p-3.5 rounded-xl border border-slate-800 font-mono">
          <span className="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Demo Balance</span>
          <span className="text-lg font-bold text-white mt-1 block">
            {numOrNull(demoStatus?.balance) === null ? NA_TEXT : fmt.currency(demoStatus.balance)}
          </span>
        </div>
        <div className="bg-slate-900/80 p-3.5 rounded-xl border border-slate-800 font-mono">
          <span className="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Demo Equity</span>
          <span className="text-lg font-bold text-cyan-300 mt-1 block">
            {numOrNull(demoStatus?.equity) === null ? NA_TEXT : fmt.currency(demoStatus.equity)}
          </span>
        </div>
        <div className="bg-slate-900/80 p-3.5 rounded-xl border border-slate-800 font-mono">
          <span className="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Free Margin</span>
          <span className="text-lg font-bold text-slate-200 mt-1 block">
            {numOrNull(demoStatus?.free_margin) === null ? NA_TEXT : fmt.currency(demoStatus.free_margin)}
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
            {numOrNull(demoStatus?.total_pnl) === null ? NA_TEXT : fmt.pnl(demoStatus.total_pnl)}
          </span>
        </div>
      </div>

      {/* V4.8 — account type / connection / safety state */}
      <DemoAccountSafety />

      {/* V5.1a-next §B — which terminal/account the backend is using, and the
       * explicit switch. Read-only until the operator selects; a REAL account is
       * shown as REAL and stays refused. */}
      <Mt5AccountSelector />

      {/* V5.1a-next §7/§15 — the manual trade calculator. It opens with $10 of
       * money at risk, a 300-pip stop, the current live entry price and the lot
       * size computed from the broker's own specification — the same component
       * and the same single implementation the Live Testing page uses (nothing
       * was redesigned). The V4.2 panel below keeps its own attach-to-strategy
       * order flow. */}
      <ManualOrderPanel defaultSymbol="XAUUSD" />

      {/* V4.2 — manual, explicitly-confirmed demo order execution */}
      <DemoOrderPanel />

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
            PAUSE ALL
          </button>
          <button
            type="button"
            onClick={handleStopAll}
            className="px-3 py-1.5 bg-rose-950/60 hover:bg-rose-900/80 text-rose-300 font-medium text-xs rounded-lg border border-rose-800 transition-colors"
          >
            STOP ALL
          </button>
        </div>

        <button
          type="button"
          onClick={loadDemoState}
          className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-mono rounded-lg transition-colors"
        >
          Refresh Telemetry
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

        {/* V5 §20 — search / sort / filter / starred / details */}
        <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono">
          <input
            className="bg-slate-950 border border-slate-800 rounded px-2 py-1 text-slate-200"
            placeholder="search node / symbol / tf"
            value={demoSearch}
            onChange={(e) => setDemoSearch(e.target.value)}
          />
          <select className="bg-slate-950 border border-slate-800 rounded px-2 py-1 text-slate-200"
                  value={demoStatusFilter} onChange={(e) => setDemoStatusFilter(e.target.value)}>
            <option value="">any status</option>
            {["VALID", "LIVE_ELIGIBLE", "LIVE_TESTING", "LIVE_COMPLETED", "MT5_DEMO"].map((x) => (
              <option key={x} value={x}>{x}</option>))}
          </select>
          <select className="bg-slate-950 border border-slate-800 rounded px-2 py-1 text-slate-200"
                  value={demoSortKey} onChange={(e) => setDemoSortKey(e.target.value)}>
            <option value="is_return_pct">sort: IS return</option>
            <option value="profit_factor">sort: profit factor</option>
            <option value="total_live_pnl">sort: live P/L</option>
            <option value="today_pnl">sort: today P/L</option>
            <option value="live_trades">sort: live trades</option>
            <option value="risk_pct">sort: risk</option>
            <option value="node_id">sort: node</option>
          </select>
          <label className="text-slate-400">
            <input type="checkbox" checked={demoSortDesc} onChange={(e) => setDemoSortDesc(e.target.checked)} /> desc
          </label>
          <label className="text-slate-400">
            <input type="checkbox" checked={demoStarred} onChange={(e) => setDemoStarred(e.target.checked)} /> starred only
          </label>
          <span className="text-slate-500">{demoRows.length} of {(nodesTable?.nodes || []).length} node(s)</span>
        </div>

        <div className="overflow-x-auto">
          <table className="table w-full text-left text-xs font-mono">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 uppercase text-[10px]">
                <th className="">⭐</th>
                <th className="">Node</th>
                <th className="">Symbol / TF</th>
                <th className="">Magic #</th>
                <th className="">Status</th>
                <th className="text-right">IS Return</th>
                <th className="text-right">PF</th>
                <th className="text-right">Risk / trade</th>
                <th className="text-right">Live P/L</th>
                <th className="text-right">Today P/L</th>
                <th className="text-right">Details</th>
                <th className="text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {demoRows.map((strat) => {
                const isRunning = strat.isRunning;
                const isStar = strat.starred;
                const magic = Number.isFinite(Number(strat.node_id))
                  ? 100000 + Number(strat.node_id) : null;

                return (
                  <tr key={strat.node_id} className="hover:bg-slate-800/40 transition-colors">
                    <td className="">
                      <button
                        type="button"
                        onClick={() => toggleShortlist && toggleShortlist(strat.node_id)}
                        className={`text-sm ${isStar ? "text-amber-400" : "text-slate-600 hover:text-amber-300"}`}
                        title={isStar ? "Shortlisted" : "Add to shortlist"}
                      >
                        {isStar ? "⭐" : "☆"}
                      </button>
                    </td>
                    <td className="">
                      <button
                        type="button"
                        onClick={() => setDemoDetail(strat)}
                        className="text-cyan-400 font-bold hover:underline"
                      >
                        Node_{txt(strat.node_id)}
                      </button>
                    </td>
                    <td className="text-slate-300">
                      {txt(strat.market, NA_TEXT)} · {txt(strat.timeframe, NA_TEXT)}
                    </td>
                    <td className="text-slate-400">
                      {magic === null ? NA_TEXT : `#${magic}`}
                    </td>
                    <td className="">
                      <span className={`text-[10px] px-2 py-0.5 rounded font-bold border ${isRunning ? "bg-purple-500/20 text-purple-300 border-purple-500/40" : "bg-slate-800 text-slate-400 border-slate-700"}`}
                            title={txt(strat.v5_status || strat.status, "")}>
                        {isRunning ? "RUNNING" : txt(strat.v5_status || strat.status, "STOPPED")}
                      </span>
                    </td>
                    <td className="text-right text-cyan-300 font-semibold">
                      {strat.is_return_pct === null || strat.is_return_pct === undefined
                        ? NA_TEXT : `${fmt.num(strat.is_return_pct, 2)} %`}
                    </td>
                    <td className="text-right text-slate-200">
                      {strat.profit_factor === null || strat.profit_factor === undefined
                        ? NA_TEXT : fmt.num(strat.profit_factor, 3)}
                    </td>
                    <td className="text-right text-slate-300">
                      {strat.risk_pct === null || strat.risk_pct === undefined
                        ? NA_TEXT : `${strat.risk_pct} %`}
                      <span className="text-slate-600"> {strat.risk_source === "CUSTOM" ? "(node)" : "(global)"}</span>
                    </td>
                    <td className="text-right font-bold text-slate-300">
                      {strat.total_live_pnl === null || strat.total_live_pnl === undefined
                        ? NA_TEXT : fmt.pnl(strat.total_live_pnl)}
                    </td>
                    <td className="text-right text-slate-300">
                      {strat.today_pnl === null || strat.today_pnl === undefined
                        ? NA_TEXT : fmt.pnl(strat.today_pnl)}
                    </td>
                    <td className="text-right">
                      <button type="button" className="text-slate-300 hover:text-white underline"
                              onClick={() => setDemoDetail(strat)}>details</button>
                    </td>
                    <td className="text-right">
                      <button
                        type="button"
                        onClick={() => handleToggleSingle(strat.node_id)}
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

        {demoDetail && (
          <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-[11px] font-mono">
            <div className="flex items-center justify-between mb-2">
              <span className="text-cyan-300 font-bold">Node_{demoDetail.node_id} — details</span>
              <button type="button" className="text-slate-400 hover:text-white"
                      onClick={() => setDemoDetail(null)}>close</button>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <span>Market: <b>{txt(demoDetail.market, NA_TEXT)}</b></span>
              <span>Timeframe: <b>{txt(demoDetail.timeframe, NA_TEXT)}</b></span>
              <span>Direction: <b>{txt(demoDetail.direction, NA_TEXT)}</b></span>
              <span>Status: <b>{txt(demoDetail.v5_status || demoDetail.status, NA_TEXT)}</b></span>
              <span>IS return: <b>{demoDetail.is_return_pct === null || demoDetail.is_return_pct === undefined ? NA_TEXT : `${demoDetail.is_return_pct} %`}</b></span>
              <span>Profit factor: <b>{txt(demoDetail.profit_factor, NA_TEXT)}</b></span>
              <span>IS trades: <b>{txt(demoDetail.trades_is, NA_TEXT)}</b></span>
              <span>Effective risk: <b>{txt(demoDetail.risk_pct, NA_TEXT)} % ({txt(demoDetail.risk_source)})</b></span>
              <span>Live trades: <b>{txt(demoDetail.live_trades, "0")}</b></span>
              <span>Live closed: <b>{txt(demoDetail.live_closed, "0")}</b></span>
              <span>Live win rate: <b>{demoDetail.live_win_rate === null || demoDetail.live_win_rate === undefined ? NA_TEXT : `${demoDetail.live_win_rate} %`}</b></span>
              <span>Live P/L: <b>{txt(demoDetail.total_live_pnl, NA_TEXT)}</b></span>
              <span>Magic: <b>{Number.isFinite(Number(demoDetail.node_id))
                ? `#${100000 + Number(demoDetail.node_id)}` : NA_TEXT}</b></span>
              <span>Schedule: <b>{demoDetail.schedule?.start_time
                ? `${demoDetail.schedule.start_time}–${demoDetail.schedule.end_time} ${txt(demoDetail.schedule.timezone, "")}`
                : "no window set"}</b></span>
              <span>Schedule active: <b>{demoDetail.schedule?.active ? "yes" : "no"}</b></span>
            </div>
            <div className="flex gap-2 mt-2">
              <button type="button" className="underline text-cyan-300"
                      onClick={() => {
                        if (setSelectedStrategyId) setSelectedStrategyId(demoDetail.node_id);
                        if (navigateTab) navigateTab("StrategyLab", demoDetail.node_id);
                      }}>
                open node (genome, Trading Info, backtests)
              </button>
            </div>
            <div className="text-slate-500 mt-2">
              All values above come from the backend: research metrics from the node's evaluated
              backtest, live values from its recorded demo/live-test trades. n/a means the value
              does not exist yet — it is never shown as 0.
            </div>
          </div>
        )}
      </div>

      {/* Confirmation Modal (Spec §18 & §20) */}
      {confirmModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm animate-in fade-in duration-150">
          <div className="bg-slate-900 border border-purple-500/50 rounded-2xl max-w-md w-full p-6 shadow-2xl space-y-4 text-xs font-mono">
            <div className="flex items-center gap-2 text-purple-400 font-bold text-sm">
              <span>MT5 Demo Safety Confirmation</span>
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
