import React, { useEffect, useState, useMemo, useRef } from "react";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import { Badge, Kpi } from "../components/ui.jsx";
import { NA_TEXT, numOrNull, objOrNull, txt as stxt } from "../lib/safe.js";
import { Pill, SignedNum, ErrorNote } from "../components/common.jsx";
import StructuredError from "../components/StructuredError.jsx";

export default function FinalTesting() {
  const { openStrategy, shortlist, toggleShortlist, setSelectedStrategyId, navigateTab } = useLab() || {};

  // Search & Query State
  const [searchInput, setSearchInput] = useState("");
  const [exactNodeInput, setExactNodeInput] = useState("");

  // Post-Evaluation Filters State (V4: default min_trade_duration_minutes empty so all strategies are visible)
  const [filters, setFilters] = useState({
    min_trades: 5,
    min_trade_duration_minutes: "",
    max_trade_duration_minutes: "",
    min_return_pct: "",
    max_drawdown_pct: 35.0,
    min_win_rate: 0.35,
    min_profit_factor: 1.0,
    min_sharpe: "",
    min_sortino: "",
    min_net_profit: "",
    max_loss: "",
    min_avg_trade: "",
    min_expectancy: "",
    max_consecutive_losses: "",
    min_robustness_score: "",
    min_oos_return_pct: "",
    status_category: "ALL",
    status: "",
    symbol: "",
    timeframe: "",
    direction: "both",
    generation: "",
  });

  // Table Data & Loading
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [resultsData, setResultsData] = useState(null);
  const [pageLimit, setPageLimit] = useState(250);   // V4.8 server-side paging
  const [pageOffset, setPageOffset] = useState(0);
  const [sortCol, setSortCol] = useState("total_return_pct");
  const [sortDir, setSortDir] = useState("desc");

  // Selection & Shortlist State
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [savedShortlist, setSavedShortlist] = useState([]);
  const [shortlistOnly, setShortlistOnly] = useState(false);

  // Active Detail Drawer / Modal State
  const [activeNodeId, setActiveNodeId] = useState(null);
  const [nodeDetail, setNodeDetail] = useState(null);
  const [detailTab, setDetailTab] = useState("results"); // 'results' | 'trades' | 'genome' | 'lineage'
  const [nodeTrades, setNodeTrades] = useState([]);
  const [actionBusy, setActionBusy] = useState("");
  const [actionMsg, setActionMsg] = useState(null);
  const [actionError, setActionError] = useState(null);

  // Comparison Modal State
  const [showComparison, setShowComparison] = useState(false);

  // Load Saved Shortlist on Mount
  const loadSavedShortlist = async () => {
    try {
      const res = await api.savedShortlist();
      setSavedShortlist(res.shortlist || []);
    } catch (e) {
      console.warn("Failed to load shortlist:", e);
    }
  };

  useEffect(() => {
    loadSavedShortlist();
  }, []);

  // Fetch Filtered Strategies (Zero-recomputation)
  /* V4.8 — any new query starts from page 1; paging only moves the window. */
  const fetchStrategies = async (customFilters = null, resetPage = true) => {
    const offsetToUse = resetPage ? 0 : pageOffset;
    if (resetPage && pageOffset !== 0) setPageOffset(0);
    setLoading(true);
    setError(null);
    try {
      const p = customFilters || filters;
      const payload = {
        limit: pageLimit,
        offset: offsetToUse,
        search: searchInput.trim() || undefined,
        node_id: exactNodeInput ? parseInt(exactNodeInput, 10) : undefined,
        status_category: p.status_category !== "ALL" ? p.status_category : undefined,
        status: p.status || undefined,
        symbol: p.symbol || undefined,
        timeframe: p.timeframe || undefined,
        direction: p.direction !== "both" ? p.direction : undefined,
        generation: p.generation !== "" ? parseInt(p.generation, 10) : undefined,
        min_trades: p.min_trades !== "" ? parseInt(p.min_trades, 10) : undefined,
        min_trade_duration_minutes: p.min_trade_duration_minutes !== "" ? parseFloat(p.min_trade_duration_minutes) : undefined,
        max_trade_duration_minutes: p.max_trade_duration_minutes !== "" ? parseFloat(p.max_trade_duration_minutes) : undefined,
        min_return_pct: p.min_return_pct !== "" ? parseFloat(p.min_return_pct) : undefined,
        max_drawdown_pct: p.max_drawdown_pct !== "" ? parseFloat(p.max_drawdown_pct) : undefined,
        min_win_rate: p.min_win_rate !== "" ? parseFloat(p.min_win_rate) : undefined,
        min_profit_factor: p.min_profit_factor !== "" ? parseFloat(p.min_profit_factor) : undefined,
        min_sharpe: p.min_sharpe !== "" ? parseFloat(p.min_sharpe) : undefined,
        min_sortino: p.min_sortino !== "" ? parseFloat(p.min_sortino) : undefined,
        min_net_profit: p.min_net_profit !== "" ? parseFloat(p.min_net_profit) : undefined,
        max_loss: p.max_loss !== "" ? parseFloat(p.max_loss) : undefined,
        min_avg_trade: p.min_avg_trade !== "" ? parseFloat(p.min_avg_trade) : undefined,
        min_expectancy: p.min_expectancy !== "" ? parseFloat(p.min_expectancy) : undefined,
        max_consecutive_losses: p.max_consecutive_losses !== "" ? parseInt(p.max_consecutive_losses, 10) : undefined,
        min_robustness_score: p.min_robustness_score !== "" ? parseFloat(p.min_robustness_score) : undefined,
        min_oos_return_pct: p.min_oos_return_pct !== "" ? parseFloat(p.min_oos_return_pct) : undefined,
        sort_by: sortCol,
        sort_desc: sortDir === "desc",
      };

      const res = await api.researchFilter(payload);
      setResultsData(res);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStrategies();
  }, [shortlistOnly]);

  /* V4.8 — moving the page refetches that window from the server; the guard
   * prevents a second identical request when a filter change resets to page 1. */
  const lastOffsetRef = useRef(-1);
  useEffect(() => {
    if (lastOffsetRef.current === pageOffset) return;
    lastOffsetRef.current = pageOffset;
    fetchStrategies(null, false);
  }, [pageOffset]);

  /* V4.8 — population counters for the KPI row come from the authoritative
   * facets endpoint, not from a recount of the page held in the browser. */
  const [facets, setFacets] = useState(null);
  useEffect(() => { api.researchFacets().then(setFacets).catch(() => setFacets(null)); }, []);

  // Open Node Detail Drawer
  const handleOpenDetail = async (sid) => {
    setActiveNodeId(sid);
    setNodeDetail(null);
    setNodeTrades([]);
    setActionMsg(null);
    setActionError(null);
    if (setSelectedStrategyId) setSelectedStrategyId(sid);
    try {
      const d = await api.strategy(sid);
      setNodeDetail(d);
      const trRes = await api.strategyTrades(sid);
      setNodeTrades(trRes.trades || []);
    } catch (e) {
      console.warn("Failed to load node detail:", e);
    }
  };

  // Toggle Shortlist for a single node
  const handleToggleShortlist = async (sid, e) => {
    if (e) e.stopPropagation();
    try {
      if (toggleShortlist) {
        await toggleShortlist(sid);
      } else {
        const res = await api.toggleShortlist(sid);
        setSavedShortlist(res.shortlist || []);
      }
      fetchStrategies();
    } catch (err) {
      console.warn("Toggle shortlist failed:", err);
    }
  };

  // Toggle Selection Checkbox for multi-actions / comparison
  const handleToggleSelect = (sid, e) => {
    if (e) e.stopPropagation();
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(sid)) next.delete(sid);
      else next.add(sid);
      return next;
    });
  };

  // Select / Deselect All
  const handleSelectAll = () => {
    if (!resultsData?.strategies) return;
    if (selectedIds.size === resultsData.strategies.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(resultsData.strategies.map((s) => s.id)));
    }
  };

  // Add all selected to shortlist
  const handleAddSelectedToShortlist = async () => {
    for (const sid of selectedIds) {
      if (!savedShortlist.includes(sid)) {
        await api.toggleShortlist(sid);
      }
    }
    await loadSavedShortlist();
    fetchStrategies();
  };

  // Rerun Backtest Action
  const handleRerunBacktest = async (sid) => {
    setActionBusy("backtest");
    setActionMsg(null);
    setActionError(null);
    try {
      const res = await api.strategyRerunBacktest(sid);
      setActionMsg({ ok: true, text: `✓ Backtest completed: PF ${res.metrics?.profit_factor?.toFixed(2)}, Return ${res.metrics?.total_return_pct?.toFixed(1)}%` });
      handleOpenDetail(sid);
      fetchStrategies();
    } catch (err) {
      setActionError(err);
    } finally {
      setActionBusy("");
    }
  };

  // Run Validation Action
  const handleRunValidation = async (sid) => {
    setActionBusy("validate");
    setActionMsg(null);
    setActionError(null);
    try {
      const res = await api.strategyRunValidation(sid);
      setActionMsg({ ok: true, text: `✓ Validation finished: ${res.status} (Score: ${res.validation?.robustness_score?.toFixed(2)})` });
      handleOpenDetail(sid);
      fetchStrategies();
    } catch (err) {
      setActionError(err);
    } finally {
      setActionBusy("");
    }
  };

  // Export Results JSON
  const handleExportResults = (stratObj = null) => {
    const dataToExport = stratObj || (activeNodeId ? nodeDetail : resultsData?.strategies);
    if (!dataToExport) return;
    const blob = new Blob([JSON.stringify(dataToExport, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `final_testing_${stratObj ? `node_${stratObj.id}` : "export"}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Sorting
  const handleSort = (col) => {
    if (sortCol === col) {
      setSortDir((prev) => (prev === "desc" ? "asc" : "desc"));
    } else {
      setSortCol(col);
      setSortDir("desc");
    }
  };

  const displayedStrategies = useMemo(() => {
    if (!resultsData?.strategies) return [];
    let list = [...resultsData.strategies];
    if (shortlistOnly) {
      const sSet = new Set(savedShortlist);
      list = list.filter((s) => sSet.has(s.id));
    }
    list.sort((a, b) => {
      let va = a[sortCol];
      let vb = b[sortCol];
      if (va == null) va = -Infinity;
      if (vb == null) vb = -Infinity;
      return sortDir === "desc" ? (vb > va ? 1 : vb < va ? -1 : 0) : (va > vb ? 1 : va < vb ? -1 : 0);
    });
    return list;
  }, [resultsData, shortlistOnly, savedShortlist, sortCol, sortDir]);

  // Selected Nodes for Comparison
  const selectedNodesList = useMemo(() => {
    if (!resultsData?.strategies) return [];
    return resultsData.strategies.filter((s) => selectedIds.has(s.id));
  }, [resultsData, selectedIds]);

  const sortArrow = (col) => (sortCol !== col ? "" : sortDir === "desc" ? " ↓" : " ↑");

  return (
    <div className="final-testing-page">
      {/* Page Header */}
      <div className="flex justify-between items-center" style={{ marginBottom: "0.75rem", flexWrap: "wrap", gap: "10px" }}>
        <div>
          <h2 className="page-title" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span>🔬 FINAL TESTING &amp; RESEARCH SHORTLIST</span>
            <span className="pill pos" style={{ fontSize: "0.7rem", fontWeight: 700 }}>
              ZERO RECOMPUTATION
            </span>
          </h2>
          <div className="page-sub" style={{ marginBottom: 0 }}>
            Inspect complete persisted results, apply multi-metric filters, review individual trade tickets, and manage research shortlists.
          </div>
        </div>

        {/* V4.8 — population KPIs, from /api/research/facets (server-side counts) */}
        <div className="kit-kpi-row" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {(() => {
            const pop = objOrNull(facets?.population);
            return pop ? (
              <>
                <Kpi label="Total logged" value={numOrNull(pop.total)?.toLocaleString?.() ?? NA_TEXT} sub="USER_RESEARCH" />
                <Kpi label="Alive" value={numOrNull(pop.alive)?.toLocaleString?.() ?? NA_TEXT} tone="pos" />
                <Kpi label="Dead" value={numOrNull(pop.dead)?.toLocaleString?.() ?? NA_TEXT} sub="shown here by design" />
                <Kpi label="Qualified" value={numOrNull(pop.qualified)?.toLocaleString?.() ?? NA_TEXT} tone="pos" />
                <Kpi label="Matching filter" value={numOrNull(resultsData?.total_matching)?.toLocaleString?.() ?? NA_TEXT}
                     sub={`of ${numOrNull(resultsData?.total_evaluated)?.toLocaleString?.() ?? NA_TEXT} evaluated`} />
              </>
            ) : <Kpi label="Population counters" value={NA_TEXT} sub="facets unavailable" />;
          })()}
        </div>

        {/* Global Shortlist Status Badge & Actions */}
        <div className="flex items-center gap-2">
          <button
            className={`btn btn-sm ${shortlistOnly ? "primary" : ""}`}
            onClick={() => setShortlistOnly(!shortlistOnly)}
            style={{ fontWeight: 600 }}
          >
            ★ RESEARCH SHORTLIST ({savedShortlist.length})
          </button>
          {selectedIds.size >= 2 && (
            <button
              className="btn btn-sm primary"
              style={{ background: "#0ea5e9", borderColor: "#0284c7" }}
              onClick={() => setShowComparison(true)}
            >
              ⚖ COMPARE SELECTED ({selectedIds.size})
            </button>
          )}
          {selectedIds.size > 0 && (
            <button className="btn btn-sm" onClick={handleAddSelectedToShortlist}>
              ★ +SHORTLIST ({selectedIds.size})
            </button>
          )}
          <button className="btn btn-sm" onClick={() => handleExportResults(null)}>
            📥 EXPORT RESULTS
          </button>
        </div>
      </div>

      {error && <StructuredError error={error} onDismiss={() => setError(null)} />}

      {/* 1. TOP POWER SEARCH BAR (User Spec §15) */}
      <div
        className="panel"
        style={{
          marginBottom: 12,
          padding: "10px 14px",
          background: "linear-gradient(180deg, #162032 0%, #0f172a 100%)",
          border: "1px solid var(--border)",
        }}
      >
        <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
          {/* Exact Node ID Search */}
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ fontSize: "0.85rem", fontWeight: 700, color: "var(--fg)" }}>
              Search Node ID:
            </span>
            <input
              type="number"
              placeholder="e.g. 371"
              value={exactNodeInput}
              onChange={(e) => setExactNodeInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") fetchStrategies(); }}
              style={{
                width: "90px",
                padding: "6px 8px",
                fontSize: "0.88rem",
                background: "var(--bg-box)",
                border: "1px solid var(--border)",
                borderRadius: "4px",
                color: "var(--fg)",
                fontFamily: "monospace",
              }}
            />
          </div>

          {/* Full-Text Query Search */}
          <div style={{ flex: 1, minWidth: "220px", display: "flex", alignItems: "center", gap: "6px" }}>
            <input
              type="text"
              placeholder="Search symbol, timeframe, status, parent, origin, reason..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") fetchStrategies(); }}
              style={{
                width: "100%",
                padding: "6px 10px",
                fontSize: "0.88rem",
                background: "var(--bg-box)",
                border: "1px solid var(--border)",
                borderRadius: "4px",
                color: "var(--fg)",
              }}
            />
          </div>

          <button
            className="btn primary"
            style={{ padding: "6px 14px", fontSize: "0.85rem", fontWeight: 700 }}
            onClick={() => fetchStrategies()}
            disabled={loading}
          >
            {loading ? "SEARCHING..." : "🔍 SEARCH"}
          </button>

          {(searchInput || exactNodeInput) && (
            <button
              className="btn"
              style={{ padding: "6px 10px", fontSize: "0.85rem" }}
              onClick={() => {
                setSearchInput("");
                setExactNodeInput("");
                fetchStrategies({ ...filters });
              }}
            >
              CLEAR
            </button>
          )}
        </div>
      </div>

      {/* 2. POST-EVALUATION FILTERS PANEL (User Spec §16, §17, §18) */}
      <div
        className="panel"
        style={{
          marginBottom: 12,
          padding: "12px 14px",
          background: "rgba(15, 23, 42, 0.7)",
          border: "1px solid var(--border)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <div className="flex items-center gap-2">
            <span style={{ fontSize: "0.85rem", fontWeight: 700, letterSpacing: "0.5px" }}>
              POST-EVALUATION FILTERS (PERSISTED METRICS)
            </span>
            <span className="muted" style={{ fontSize: "0.75rem" }}>
              Filtered across {resultsData?.total_evaluated ?? 0} evaluated nodes · {displayedStrategies.length} matching
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              className="btn btn-xs"
              onClick={() => {
                const def = {
                  min_trades: 5,
                  min_trade_duration_minutes: 2.0,
                  max_trade_duration_minutes: "",
                  min_return_pct: "",
                  max_drawdown_pct: 35.0,
                  min_win_rate: 0.35,
                  min_profit_factor: 1.0,
                  min_sharpe: "",
                  min_sortino: "",
                  min_net_profit: "",
                  max_loss: "",
                  min_avg_trade: "",
                  min_expectancy: "",
                  max_consecutive_losses: "",
                  status_category: "ALL",
                  status: "",
                  symbol: "",
                  timeframe: "",
                  direction: "both",
                  generation: "",
                };
                setFilters(def);
                fetchStrategies(def);
              }}
            >
              RESET FILTERS
            </button>
            <button
              className="btn btn-xs primary"
              onClick={() => fetchStrategies()}
              disabled={loading}
            >
              {loading ? "APPLYING..." : "APPLY FILTERS"}
            </button>
          </div>
        </div>

        {/* Filter Inputs Grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
            gap: "8px",
            fontSize: "0.8rem",
          }}
        >
          <div>
            <div className="muted" style={{ fontSize: "0.7rem", marginBottom: 2 }}>STATUS CATEGORY</div>
            <select
              value={filters.status_category}
              onChange={(e) => setFilters({ ...filters, status_category: e.target.value })}
              style={inputStyle}
            >
              <option value="ALL">ALL STATUSES</option>
              <option value="QUALIFIED">QUALIFIED &amp; PAPER</option>
              <option value="ALIVE">ALIVE (IN FLIGHT)</option>
              <option value="DEAD">DEAD (KILLED / FAILED)</option>
            </select>
          </div>

          <div>
            <div className="muted" style={{ fontSize: "0.7rem", marginBottom: 2 }}>MIN TRADES</div>
            <input
              type="number"
              min="0"
              value={filters.min_trades}
              onChange={(e) => setFilters({ ...filters, min_trades: e.target.value })}
              style={inputStyle}
            />
          </div>

          <div>
            <div className="muted" style={{ fontSize: "0.7rem", marginBottom: 2, color: "var(--accent)" }}>
              MIN TRADE TIME (MIN)
            </div>
            <input
              type="number"
              step="0.5"
              min="0"
              placeholder="2.0"
              value={filters.min_trade_duration_minutes}
              onChange={(e) => setFilters({ ...filters, min_trade_duration_minutes: e.target.value })}
              style={{ ...inputStyle, borderColor: "var(--accent)" }}
            />
          </div>

          <div>
            <div className="muted" style={{ fontSize: "0.7rem", marginBottom: 2 }}>MAX TRADE TIME (MIN)</div>
            <input
              type="number"
              step="1"
              min="0"
              placeholder="Any"
              value={filters.max_trade_duration_minutes}
              onChange={(e) => setFilters({ ...filters, max_trade_duration_minutes: e.target.value })}
              style={inputStyle}
            />
          </div>

          <div>
            <div className="muted" style={{ fontSize: "0.7rem", marginBottom: 2 }}>MIN RETURN (%)</div>
            <input
              type="number"
              step="1"
              placeholder="0"
              value={filters.min_return_pct}
              onChange={(e) => setFilters({ ...filters, min_return_pct: e.target.value })}
              style={inputStyle}
            />
          </div>

          <div>
            <div className="muted" style={{ fontSize: "0.7rem", marginBottom: 2 }}>MAX DRAWDOWN (%)</div>
            <input
              type="number"
              step="1"
              value={filters.max_drawdown_pct}
              onChange={(e) => setFilters({ ...filters, max_drawdown_pct: e.target.value })}
              style={inputStyle}
            />
          </div>

          <div>
            <div className="muted" style={{ fontSize: "0.7rem", marginBottom: 2 }}>MIN PROFIT FACTOR</div>
            <input
              type="number"
              step="0.1"
              value={filters.min_profit_factor}
              onChange={(e) => setFilters({ ...filters, min_profit_factor: e.target.value })}
              style={inputStyle}
            />
          </div>

          <div>
            <div className="muted" style={{ fontSize: "0.7rem", marginBottom: 2 }}>MIN SHARPE RATIO</div>
            <input
              type="number"
              step="0.1"
              placeholder="0.0"
              value={filters.min_sharpe}
              onChange={(e) => setFilters({ ...filters, min_sharpe: e.target.value })}
              style={inputStyle}
            />
          </div>

          <div>
            <div className="muted" style={{ fontSize: "0.7rem", marginBottom: 2 }}>MIN WIN RATE (%)</div>
            <input
              type="number"
              step="0.05"
              value={filters.min_win_rate}
              onChange={(e) => setFilters({ ...filters, min_win_rate: e.target.value })}
              style={inputStyle}
            />
          </div>

          <div>
            <div className="muted" style={{ fontSize: "0.7rem", marginBottom: 2 }}>MIN NET PROFIT ($)</div>
            <input
              type="number"
              step="10"
              placeholder="0"
              value={filters.min_net_profit}
              onChange={(e) => setFilters({ ...filters, min_net_profit: e.target.value })}
              style={inputStyle}
            />
          </div>

          <div>
            <div className="muted" style={{ fontSize: "0.7rem", marginBottom: 2 }}>MAX CONSEC LOSSES</div>
            <input
              type="number"
              step="1"
              placeholder="Any"
              value={filters.max_consecutive_losses}
              onChange={(e) => setFilters({ ...filters, max_consecutive_losses: e.target.value })}
              style={inputStyle}
            />
          </div>

          <div>
            <div className="muted" style={{ fontSize: "0.7rem", marginBottom: 2 }}>DIRECTION</div>
            <select
              value={filters.direction}
              onChange={(e) => setFilters({ ...filters, direction: e.target.value })}
              style={inputStyle}
            >
              <option value="both">BOTH</option>
              <option value="buy">LONG ONLY</option>
              <option value="sell">SHORT ONLY</option>
            </select>
          </div>
        </div>
      </div>

      {/* 3. STRATEGIES RESULTS TABLE */}
      <div className="panel" style={{ padding: "8px 12px", marginBottom: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8, fontSize: "0.8rem" }}>
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={displayedStrategies.length > 0 && selectedIds.size === displayedStrategies.length}
              onChange={handleSelectAll}
              title="Select / Deselect All Matching"
            />
            <span className="mono font-bold">
              {displayedStrategies.length} Strategies Listed
            </span>
            {selectedIds.size > 0 && (
              <span className="pill" style={{ background: "rgba(59, 130, 246, 0.2)", color: "#60a5fa" }}>
                {selectedIds.size} Selected
              </span>
            )}
          </div>
          <div className="muted">
            Click any row to open the complete Node Detail &amp; Trade History view
          </div>
        </div>

        <div className="scroll-x" style={{ maxHeight: "calc(100vh - 430px)", overflowY: "auto" }}>
          <table className="tbl" style={{ width: "100%", fontSize: "0.82rem" }}>
            <thead>
              <tr>
                <th style={{ width: "30px" }}></th>
                <th style={{ width: "35px" }}>★</th>
                <th onClick={() => handleSort("id")} style={{ cursor: "pointer" }}>ID{sortArrow("id")}</th>
                <th onClick={() => handleSort("status")} style={{ cursor: "pointer" }}>Status{sortArrow("status")}</th>
                <th style={{ minWidth: 190 }}>Dead / outcome reason</th>
                <th onClick={() => handleSort("generation")} style={{ cursor: "pointer" }}>Gen{sortArrow("generation")}</th>
                <th onClick={() => handleSort("symbol")} style={{ cursor: "pointer" }}>Symbol/TF{sortArrow("symbol")}</th>
                <th onClick={() => handleSort("trades")} style={{ cursor: "pointer" }}>Trades{sortArrow("trades")}</th>
                <th onClick={() => handleSort("min_trade_duration_seconds")} style={{ cursor: "pointer" }}>Min Time{sortArrow("min_trade_duration_seconds")}</th>
                <th onClick={() => handleSort("avg_trade_duration_seconds")} style={{ cursor: "pointer" }}>Avg Time{sortArrow("avg_trade_duration_seconds")}</th>
                <th onClick={() => handleSort("total_return_pct")} style={{ cursor: "pointer" }}>BACKTEST RETURN (IS){sortArrow("total_return_pct")}</th>
                <th onClick={() => handleSort("validation_oos_return_pct")} style={{ cursor: "pointer" }}>OOS RETURN{sortArrow("validation_oos_return_pct")}</th>
                <th onClick={() => handleSort("robustness_score")} style={{ cursor: "pointer" }}>ROBUSTNESS{sortArrow("robustness_score")}</th>
                <th onClick={() => handleSort("net_profit")} style={{ cursor: "pointer" }}>Net Profit{sortArrow("net_profit")}</th>
                <th onClick={() => handleSort("max_drawdown_pct")} style={{ cursor: "pointer" }}>Max DD %{sortArrow("max_drawdown_pct")}</th>
                <th onClick={() => handleSort("profit_factor")} style={{ cursor: "pointer" }}>PF{sortArrow("profit_factor")}</th>
                <th onClick={() => handleSort("sharpe")} style={{ cursor: "pointer" }}>Sharpe{sortArrow("sharpe")}</th>
                <th onClick={() => handleSort("win_rate")} style={{ cursor: "pointer" }}>Win Rate{sortArrow("win_rate")}</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {displayedStrategies.map((s) => {
                const isSelected = selectedIds.has(s.id);
                const isShortlisted = shortlist?.includes(s.id) || s.shortlisted;
                return (
                  <tr
                    key={s.id}
                    onClick={() => handleOpenDetail(s.id)}
                    style={{
                      cursor: "pointer",
                      background: isSelected ? "rgba(59, 130, 246, 0.1)" : "transparent",
                    }}
                  >
                    <td onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={(e) => handleToggleSelect(s.id, e)}
                      />
                    </td>
                    <td onClick={(e) => handleToggleShortlist(s.id, e)}>
                      <span style={{ color: isShortlisted ? "#facc15" : "rgba(255,255,255,0.2)", fontSize: "14px", cursor: "pointer" }}>
                        ★
                      </span>
                    </td>
                    <td className="mono font-bold">#{s.id}</td>
                    <td><Pill status={s.status} /></td>
                    <td className="muted" style={{ fontSize: "0.72rem", maxWidth: 240 }}>
                      {s.dead_reason || s.survival_reason
                        ? (s.dead_reason || s.survival_reason)
                        : <span title="The engine recorded no reason for this node">reason not recorded by the engine</span>}
                    </td>
                    <td className="mono">G{s.generation}</td>
                    <td>{s.symbol} {s.timeframe}</td>
                    <td className="mono font-bold">{s.trades}</td>
                    <td className="mono">
                      {s.min_trade_duration_seconds > 0 ? `${(s.min_trade_duration_seconds / 60).toFixed(1)}m` : "–"}
                    </td>
                    <td className="mono">
                      {s.avg_trade_duration_seconds > 0 ? `${(s.avg_trade_duration_seconds / 60).toFixed(1)}m` : "–"}
                    </td>
                    <td><SignedNum v={s.total_return_pct} pct /></td>
                    <td>{s.validation_oos_return_pct != null ? <SignedNum v={s.validation_oos_return_pct} pct /> : <span className="muted">–</span>}</td>
                    <td className="mono">{s.robustness_score != null ? fmt.ratio(s.robustness_score, 3) : "–"}</td>
                    <td className="mono font-bold">
                      {s.net_profit != null ? `$${s.net_profit.toFixed(1)}` : "–"}
                    </td>
                    <td className="mono">{fmt.pct(s.max_drawdown_pct)}</td>
                    <td className="mono font-bold">{fmt.num(s.profit_factor, 2)}</td>
                    <td className="mono">{s.sharpe != null ? s.sharpe.toFixed(2) : "–"}</td>
                    <td className="mono">{fmt.pct(s.win_rate)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center gap-1">
                        <button
                          className="btn btn-xs"
                          onClick={() => handleOpenDetail(s.id)}
                          title="Inspect full performance and trade tickets"
                        >
                          DETAIL
                        </button>
                        <button
                          className="btn btn-xs"
                          onClick={(e) => handleToggleShortlist(s.id, e)}
                          title={isShortlisted ? "Remove from Shortlist" : "Add to Shortlist"}
                        >
                          {isShortlisted ? "UNSTAR" : "STAR"}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {displayedStrategies.length === 0 && (
                <tr>
                  <td colSpan={17} className="muted" style={{ textAlign: "center", padding: "24px" }}>
                    {loading ? "Loading matching strategies..." : "No strategies match the current filters."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* V4.8 — server-side pagination: the browser never holds the whole population */}
        <div className="kit-pager" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
          <button className="btn btn-xs" disabled={loading || pageOffset <= 0}
                  title={loading ? "waiting for the current page" : pageOffset <= 0 ? "already on the first page" : "previous page"}
                  onClick={() => setPageOffset(Math.max(0, pageOffset - pageLimit))}>‹ prev</button>
          <span className="muted" style={{ fontSize: 11.5 }}>
            showing {(resultsData?.total_matching ?? 0) === 0 ? 0 : pageOffset + 1}–{Math.min(pageOffset + pageLimit, resultsData?.total_matching ?? 0)}
            {" "}of {(resultsData?.total_matching ?? 0).toLocaleString()}
            {" "}· page {Math.floor(pageOffset / pageLimit) + 1} of {Math.max(1, Math.ceil((resultsData?.total_matching ?? 0) / pageLimit))}
          </span>
          <button className="btn btn-xs" disabled={loading || pageOffset + pageLimit >= (resultsData?.total_matching ?? 0)}
                  title={loading ? "waiting for the current page" : pageOffset + pageLimit >= (resultsData?.total_matching ?? 0) ? "no more matching nodes" : "next page"}
                  onClick={() => setPageOffset(pageOffset + pageLimit)}>next ›</button>
          <label className="fld" style={{ display: "flex", alignItems: "center", gap: 6, margin: 0 }}>
            <span className="muted" style={{ fontSize: 11.5 }}>rows per page</span>
            <select value={String(pageLimit)} style={{ width: 80 }}
                    onChange={(e) => { setPageLimit(parseInt(e.target.value, 10)); setPageOffset(0); }}>
              {[50, 100, 250, 500].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          {shortlistOnly && <Badge tone="info">shortlist only</Badge>}
        </div>

      </div>

      {/* 4. NODE DETAIL DRAWER / MODAL (User Spec §19, §20) */}
      {activeNodeId && nodeDetail && (
        <div
          className="modal-backdrop"
          onClick={() => setActiveNodeId(null)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.7)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
        >
          <div
            className="modal-content"
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: "8px",
              padding: "1.2rem",
              width: "820px",
              maxWidth: "95vw",
              maxHeight: "92vh",
              display: "flex",
              flexDirection: "column",
            }}
          >
            {/* Header */}
            <div className="flex justify-between items-center" style={{ borderBottom: "1px solid var(--border)", paddingBottom: "10px", marginBottom: "12px" }}>
              <div className="flex items-center gap-3">
                <h3 style={{ margin: 0, fontSize: "1.1rem" }}>
                  Node #{nodeDetail.id} ({nodeDetail.symbol} {nodeDetail.timeframe})
                </h3>
                <Pill status={nodeDetail.status} />
                <span className="mono muted" style={{ fontSize: "0.8rem" }}>
                  Gen {nodeDetail.generation} · Parent #{nodeDetail.parent_id || "None"}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  className="btn btn-xs"
                  onClick={() => handleToggleShortlist(nodeDetail.id)}
                >
                  {savedShortlist.includes(nodeDetail.id) ? "★ SHORTLISTED" : "☆ ADD TO SHORTLIST"}
                </button>
                <button className="btn btn-xs btn-subtle" onClick={() => setActiveNodeId(null)}>✕</button>
              </div>
            </div>

            {actionMsg && (
              <div
                style={{
                  padding: "8px 12px",
                  borderRadius: "4px",
                  marginBottom: "10px",
                  fontSize: "0.85rem",
                  background: actionMsg.ok ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)",
                  border: `1px solid ${actionMsg.ok ? "var(--green)" : "var(--red)"}`,
                }}
              >
                {actionMsg.text}
              </div>
            )}

            {actionError && (
              <StructuredError error={actionError} onDismiss={() => setActionError(null)} />
            )}

            {/* Action Buttons Toolbar (User Spec §20) */}
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "12px" }}>
              <button
                className={`btn btn-xs ${detailTab === "results" ? "primary" : ""}`}
                onClick={() => setDetailTab("results")}
              >
                📊 VIEW RESULTS
              </button>
              <button
                className={`btn btn-xs ${detailTab === "trades" ? "primary" : ""}`}
                onClick={() => setDetailTab("trades")}
              >
                📜 VIEW TRADES ({nodeTrades.length})
              </button>
              <button
                className={`btn btn-xs ${detailTab === "genome" ? "primary" : ""}`}
                onClick={() => setDetailTab("genome")}
              >
                🧬 VIEW GENOME
              </button>
              <button
                className={`btn btn-xs ${detailTab === "lineage" ? "primary" : ""}`}
                onClick={() => setDetailTab("lineage")}
              >
                🌿 VIEW ANCESTRY &amp; CHILDREN
              </button>
              <button
                className="btn btn-xs"
                style={{ marginLeft: "auto", background: "#334155" }}
                disabled={actionBusy === "backtest"}
                onClick={() => handleRerunBacktest(nodeDetail.id)}
              >
                {actionBusy === "backtest" ? "BACKTESTING..." : "🔄 RERUN BACKTEST"}
              </button>
              <button
                className="btn btn-xs"
                style={{ background: "#047857" }}
                disabled={actionBusy === "validate"}
                onClick={() => handleRunValidation(nodeDetail.id)}
              >
                {actionBusy === "validate" ? "VALIDATING..." : "⚡ RUN VALIDATION"}
              </button>
              <button className="btn btn-xs" onClick={() => handleExportResults(nodeDetail)}>
                📥 EXPORT
              </button>
            </div>

            {/* Tab 1: Performance & Results (User Spec §19) */}
            {detailTab === "results" && (
              <div className="scroll-y" style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "10px" }}>
                {/* 9 Metrics Overview */}
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: "8px" }}>
                  <MetricCard label="NET RETURN" value={`${(nodeDetail.latest_backtest?.metrics?.total_return_pct ?? 0).toFixed(2)}%`} isPos={(nodeDetail.latest_backtest?.metrics?.total_return_pct ?? 0) >= 0} />
                  <MetricCard label="NET PROFIT" value={`$${(nodeDetail.latest_backtest?.metrics?.net_profit ?? 0).toFixed(2)}`} isPos={(nodeDetail.latest_backtest?.metrics?.net_profit ?? 0) >= 0} />
                  <MetricCard label="MAX DRAWDOWN" value={`${(nodeDetail.latest_backtest?.metrics?.max_drawdown_pct ?? 0).toFixed(2)}%`} isNeg={true} />
                  <MetricCard label="PROFIT FACTOR" value={(nodeDetail.latest_backtest?.metrics?.profit_factor ?? 0).toFixed(2)} isPos={(nodeDetail.latest_backtest?.metrics?.profit_factor ?? 0) >= 1.0} />
                  <MetricCard label="SHARPE RATIO" value={(nodeDetail.latest_backtest?.metrics?.sharpe ?? 0).toFixed(2)} />
                  <MetricCard label="WIN RATE" value={`${((nodeDetail.latest_backtest?.metrics?.win_rate ?? 0) * 100).toFixed(1)}%`} />
                  <MetricCard label="TOTAL TRADES" value={nodeDetail.latest_backtest?.metrics?.trades ?? 0} />
                  <MetricCard label="AVG TRADE DURATION" value={nodeDetail.latest_backtest?.metrics?.avg_trade_duration_seconds ? `${(nodeDetail.latest_backtest.metrics.avg_trade_duration_seconds / 60).toFixed(1)}m` : "–"} />
                  <MetricCard label="MIN TRADE DURATION" value={nodeDetail.latest_backtest?.metrics?.min_trade_duration_seconds ? `${(nodeDetail.latest_backtest.metrics.min_trade_duration_seconds / 60).toFixed(1)}m` : "–"} />
                </div>

                {/* Additional Risk & Expectancy Metrics */}
                <div style={{ background: "rgba(0,0,0,0.3)", padding: "10px", borderRadius: "6px", fontSize: "0.85rem" }}>
                  <div className="font-bold" style={{ marginBottom: "6px" }}>Risk &amp; Execution Profile:</div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "6px" }}>
                    <div>Expectancy: <b>${(nodeDetail.latest_backtest?.metrics?.expectancy ?? 0).toFixed(2)}</b></div>
                    <div>Average Trade: <b>${(nodeDetail.latest_backtest?.metrics?.avg_trade ?? 0).toFixed(2)}</b></div>
                    <div>Sortino Ratio: <b>{(nodeDetail.latest_backtest?.metrics?.sortino ?? 0).toFixed(2)}</b></div>
                    <div>Gross Profit: <b>${(nodeDetail.latest_backtest?.metrics?.gross_profit ?? 0).toFixed(2)}</b></div>
                    <div>Gross Loss: <b>${(nodeDetail.latest_backtest?.metrics?.gross_loss ?? 0).toFixed(2)}</b></div>
                    <div>Avg Hold Bars: <b>{(nodeDetail.latest_backtest?.metrics?.avg_hold_bars ?? 0).toFixed(1)} bars</b></div>
                  </div>
                </div>

                {/* Validation Score if available */}
                {nodeDetail.validation && (
                  <div style={{ background: "rgba(16, 185, 129, 0.08)", border: "1px solid rgba(16, 185, 129, 0.3)", padding: "10px", borderRadius: "6px", fontSize: "0.85rem" }}>
                    <div className="font-bold" style={{ color: "var(--green)", marginBottom: "4px" }}>
                      Validation Battery Verdict: {nodeDetail.validation.passed ? "PASSED (QUALIFIED)" : "FAILED"}
                    </div>
                    <div>Robustness Score: <b>{nodeDetail.validation.robustness_score?.toFixed(2)}</b></div>
                    <div>Created: <b>{new Date(nodeDetail.validation.created_at * 1000).toLocaleString()}</b></div>
                  </div>
                )}
              </div>
            )}

            {/* Tab 2: Individual Trade Records (User Spec §19) */}
            {detailTab === "trades" && (
              <div className="scroll-y" style={{ flex: 1, overflowY: "auto" }}>
                <div style={{ fontSize: "0.85rem", marginBottom: "8px" }} className="muted">
                  Showing {nodeTrades.length} recorded trade tickets:
                </div>
                <table className="tbl" style={{ width: "100%", fontSize: "0.8rem" }}>
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Direction</th>
                      <th>Entry Time</th>
                      <th>Exit Time</th>
                      <th>Duration</th>
                      <th>Entry Price</th>
                      <th>Exit Price</th>
                      <th>P/L ($)</th>
                      <th>P/L (%)</th>
                      <th>Exit Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {nodeTrades.map((t) => (
                      <tr key={t.trade_num}>
                        <td className="mono">{t.trade_num}</td>
                        <td className="font-bold" style={{ color: t.side === "BUY" ? "var(--green)" : "var(--accent)" }}>
                          {t.side}
                        </td>
                        <td className="mono" style={{ fontSize: "0.75rem" }}>{t.entry_ts ? new Date(t.entry_ts * 1000).toLocaleString() : "–"}</td>
                        <td className="mono" style={{ fontSize: "0.75rem" }}>{t.exit_ts ? new Date(t.exit_ts * 1000).toLocaleString() : "–"}</td>
                        <td className="mono font-bold">{t.duration_minutes}m</td>
                        <td className="mono">{t.entry_price?.toFixed(3)}</td>
                        <td className="mono">{t.exit_price?.toFixed(3)}</td>
                        <td className="mono font-bold" style={{ color: t.pnl >= 0 ? "var(--green)" : "var(--red)" }}>
                          {t.pnl >= 0 ? `+$${t.pnl.toFixed(2)}` : `-$${Math.abs(t.pnl).toFixed(2)}`}
                        </td>
                        <td className="mono" style={{ color: t.pnl_pct >= 0 ? "var(--green)" : "var(--red)" }}>
                          {t.pnl_pct >= 0 ? `+${t.pnl_pct.toFixed(2)}%` : `${t.pnl_pct.toFixed(2)}%`}
                        </td>
                        <td className="mono" style={{ fontSize: "0.75rem" }}>{t.exit_reason}</td>
                      </tr>
                    ))}
                    {nodeTrades.length === 0 && (
                      <tr>
                        <td colSpan={10} className="muted" style={{ textAlign: "center", padding: "20px" }}>
                          No trade tickets recorded for this backtest run.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}

            {/* Tab 3: Genome View */}
            {detailTab === "genome" && (
              <div className="scroll-y" style={{ flex: 1, overflowY: "auto" }}>
                <pre
                  className="mono"
                  style={{
                    padding: "10px",
                    background: "rgba(0,0,0,0.5)",
                    borderRadius: "6px",
                    fontSize: "0.8rem",
                    color: "var(--fg)",
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {JSON.stringify(nodeDetail.genome, null, 2)}
                </pre>
              </div>
            )}

            {/* Tab 4: Ancestry & Children Lineage */}
            {detailTab === "lineage" && (
              <div className="scroll-y" style={{ flex: 1, overflowY: "auto", fontSize: "0.85rem" }}>
                <div style={{ marginBottom: 12 }}>
                  <div className="font-bold" style={{ marginBottom: 6 }}>Ancestry Chain:</div>
                  {nodeDetail.ancestry_chain?.map((anc) => (
                    <div
                      key={anc.id}
                      onClick={() => handleOpenDetail(anc.id)}
                      style={{
                        padding: "6px 10px",
                        background: anc.id === nodeDetail.id ? "rgba(59, 130, 246, 0.2)" : "rgba(255,255,255,0.03)",
                        border: "1px solid var(--border)",
                        borderRadius: "4px",
                        marginBottom: "4px",
                        cursor: "pointer",
                        display: "flex",
                        justifyContent: "space-between",
                      }}
                    >
                      <span>Node #{anc.id} (Gen {anc.generation}) · {anc.mutation_type || "seed"}</span>
                      <Pill status={anc.status} />
                    </div>
                  ))}
                </div>

                <div>
                  <div className="font-bold" style={{ marginBottom: 6 }}>Known Children ({nodeDetail.children?.length || 0}):</div>
                  {nodeDetail.children?.map((ch) => (
                    <div
                      key={ch.id}
                      onClick={() => handleOpenDetail(ch.id)}
                      style={{
                        padding: "6px 10px",
                        background: "rgba(255,255,255,0.03)",
                        border: "1px solid var(--border)",
                        borderRadius: "4px",
                        marginBottom: "4px",
                        cursor: "pointer",
                        display: "flex",
                        justifyContent: "space-between",
                      }}
                    >
                      <span>Child Node #{ch.id} · {ch.mutation_type}</span>
                      <Pill status={ch.status} />
                    </div>
                  ))}
                  {(!nodeDetail.children || nodeDetail.children.length === 0) && (
                    <div className="muted" style={{ padding: "8px" }}>No children spawned from this node yet.</div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 5. MULTI-NODE COMPARISON MODAL (User Spec §22) */}
      {showComparison && (
        <div
          className="modal-backdrop"
          onClick={() => setShowComparison(false)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.75)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
        >
          <div
            className="modal-content"
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: "8px",
              padding: "1.2rem",
              width: "900px",
              maxWidth: "96vw",
              maxHeight: "92vh",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <div className="flex justify-between items-center" style={{ borderBottom: "1px solid var(--border)", paddingBottom: "10px", marginBottom: "12px" }}>
              <h3 style={{ margin: 0 }}>
                ⚖ SIDE-BY-SIDE COMPARISON ({selectedNodesList.length} Nodes)
              </h3>
              <button className="btn btn-xs btn-subtle" onClick={() => setShowComparison(false)}>✕</button>
            </div>

            <div className="scroll-x" style={{ flex: 1, overflowY: "auto" }}>
              <table className="tbl" style={{ width: "100%", fontSize: "0.85rem" }}>
                <thead>
                  <tr>
                    <th>Metric</th>
                    {selectedNodesList.map((s) => (
                      <th key={s.id} className="mono">Node #{s.id}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td><b>Status</b></td>
                    {selectedNodesList.map((s) => (
                      <td key={s.id}><Pill status={s.status} /></td>
                    ))}
                  </tr>
                  <tr>
                    <td><b>Generation</b></td>
                    {selectedNodesList.map((s) => (
                      <td key={s.id} className="mono">Gen {s.generation}</td>
                    ))}
                  </tr>
                  <tr>
                    <td><b>Parent Node</b></td>
                    {selectedNodesList.map((s) => (
                      <td key={s.id} className="mono">{s.parent_id ? `#${s.parent_id}` : "None"}</td>
                    ))}
                  </tr>
                  <tr>
                    <td><b>Net Return %</b></td>
                    {selectedNodesList.map((s) => (
                      <td key={s.id}><SignedNum v={s.total_return_pct} pct /></td>
                    ))}
                  </tr>
                  <tr>
                    <td><b>Net Profit ($)</b></td>
                    {selectedNodesList.map((s) => (
                      <td key={s.id} className="mono font-bold">${s.net_profit?.toFixed(2) ?? "0.00"}</td>
                    ))}
                  </tr>
                  <tr>
                    <td><b>Max Drawdown %</b></td>
                    {selectedNodesList.map((s) => (
                      <td key={s.id} className="mono">{fmt.pct(s.max_drawdown_pct)}</td>
                    ))}
                  </tr>
                  <tr>
                    <td><b>Profit Factor</b></td>
                    {selectedNodesList.map((s) => (
                      <td key={s.id} className="mono font-bold">{fmt.num(s.profit_factor, 2)}</td>
                    ))}
                  </tr>
                  <tr>
                    <td><b>Sharpe Ratio</b></td>
                    {selectedNodesList.map((s) => (
                      <td key={s.id} className="mono">{s.sharpe?.toFixed(2) ?? "–"}</td>
                    ))}
                  </tr>
                  <tr>
                    <td><b>Win Rate %</b></td>
                    {selectedNodesList.map((s) => (
                      <td key={s.id} className="mono">{fmt.pct(s.win_rate)}</td>
                    ))}
                  </tr>
                  <tr>
                    <td><b>Total Trades</b></td>
                    {selectedNodesList.map((s) => (
                      <td key={s.id} className="mono font-bold">{s.trades}</td>
                    ))}
                  </tr>
                  <tr>
                    <td><b>Min Trade Duration</b></td>
                    {selectedNodesList.map((s) => (
                      <td key={s.id} className="mono font-bold">
                        {s.min_trade_duration_seconds > 0 ? `${(s.min_trade_duration_seconds / 60).toFixed(1)}m` : "–"}
                      </td>
                    ))}
                  </tr>
                  <tr>
                    <td><b>Avg Trade Duration</b></td>
                    {selectedNodesList.map((s) => (
                      <td key={s.id} className="mono">
                        {s.avg_trade_duration_seconds > 0 ? `${(s.avg_trade_duration_seconds / 60).toFixed(1)}m` : "–"}
                      </td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const inputStyle = {
  width: "100%",
  padding: "4px 8px",
  fontSize: "0.82rem",
  background: "var(--bg-box)",
  border: "1px solid var(--border)",
  borderRadius: "3px",
  color: "var(--fg)",
};

function MetricCard({ label, value, isPos = false, isNeg = false }) {
  let col = "var(--fg)";
  if (isPos) col = "var(--green)";
  if (isNeg) col = "var(--red)";
  return (
    <div style={{ background: "rgba(0,0,0,0.3)", padding: "8px 10px", borderRadius: "4px", border: "1px solid var(--border)" }}>
      <div className="muted" style={{ fontSize: "0.7rem", letterSpacing: "0.5px" }}>{label}</div>
      <div className="mono font-bold" style={{ fontSize: "1.05rem", color: col, marginTop: "2px" }}>{value}</div>
    </div>
  );
}
