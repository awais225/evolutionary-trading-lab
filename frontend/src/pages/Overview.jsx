import React, { useEffect, useState, useMemo } from "react";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import { Metric, Pill, EventFeed, SignedNum, ErrorNote } from "../components/common.jsx";
import NewResearchRunModal from "../components/NewResearchRunModal.jsx";
import PowerButton from "../components/PowerButton.jsx";

export default function Overview() {
  const { status, events, connected, refreshStatus, openStrategy } = useLab();
  const [top, setTop] = useState([]);
  const [resources, setResources] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState("");
  const [newRunOpen, setNewRunOpen] = useState(false);   // V4.1 START NEW RESEARCH RUN dialog
  const [sortCol, setSortCol] = useState("fitness");
  const [sortDir, setSortDir] = useState("desc");

  // V2.1 Total Node Target & Recheck State
  const [targetInput, setTargetInput] = useState("");
  const [recheckData, setRecheckData] = useState(null);
  const [rechecking, setRechecking] = useState(false);

  // V2.7 Data Inventory & Jobs State
  const [inventory, setInventory] = useState(null);
  const [jobsData, setJobsData] = useState(null);
  const [hwBusy, setHwBusy] = useState(false);

  // V3.2 Research Run Management & Paper Candidate Selection (spec §6, §8, §9)
  const [runsData, setRunsData] = useState([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [paperCandidates, setPaperCandidates] = useState([]);
  const [paperLoading, setPaperLoading] = useState(false);
  const [promotingId, setPromotingId] = useState(null);

  // Minimum Trade Time & Post-Evaluation Filter State
  const [minTradeTimeInput, setMinTradeTimeInput] = useState("2");
  const [shortlistData, setShortlistData] = useState(null);
  const [shortlistLoading, setShortlistLoading] = useState(false);
  const [filterParams, setFilterParams] = useState({
    min_trades: 5,
    min_trade_duration_minutes: 2,
    min_return_pct: 0.0,
    max_drawdown_pct: 35.0,
    min_win_rate: 0.35,
    min_profit_factor: 1.0,
    stage: "",
  });

  const lab = status?.lab;
  const counts = status?.status_counts || {};

  // Sync targetInput with lab state initially or on update
  useEffect(() => {
    if (lab?.target && targetInput === "") {
      setTargetInput(String(lab.target));
    }
  }, [lab?.target]);

  const fetchRuns = async () => {
    try {
      const res = await api.labRuns();
      if (res?.runs) {
        setRunsData(res.runs);
        if (!selectedRunId && res.active_run_id) {
          setSelectedRunId(res.active_run_id);
        }
      }
    } catch (e) {
      console.warn("Failed fetching runs", e);
    }
  };

  const fetchPaperCandidates = async () => {
    setPaperLoading(true);
    try {
      const res = await api.paperCandidates(100);
      if (res?.candidates) {
        setPaperCandidates(res.candidates);
      }
    } catch (e) {
      console.warn("Failed fetching paper candidates", e);
    } finally {
      setPaperLoading(false);
    }
  };

  const handleSwitchRun = async (newRunId) => {
    setSelectedRunId(newRunId);
    if (newRunId && newRunId !== "ALL") {
      try {
        await api.labSetActiveRun(newRunId);
        refreshStatus();
        fetchRuns();
      } catch (e) {
        setErr("Failed switching run: " + e.message);
      }
    }
  };

  const handlePromoteCandidate = async (cid) => {
    setPromotingId(cid);
    try {
      await api.paperPromoteCandidate(cid);
      fetchPaperCandidates();
      refreshStatus();
    } catch (e) {
      setErr("Failed promoting candidate: " + e.message);
    } finally {
      setPromotingId(null);
    }
  };

  const handleDemoteCandidate = async (cid) => {
    setPromotingId(cid);
    try {
      await api.paperDemoteCandidate(cid);
      fetchPaperCandidates();
      refreshStatus();
    } catch (e) {
      setErr("Failed demoting candidate: " + e.message);
    } finally {
      setPromotingId(null);
    }
  };

  const handleBatchPromote = async () => {
    setPaperLoading(true);
    try {
      const eligible = paperCandidates.filter((c) => !c.promoted_to_paper).slice(0, 10).map((c) => c.id);
      if (eligible.length === 0) return;
      await api.paperBatchPromote(eligible);
      fetchPaperCandidates();
      refreshStatus();
    } catch (e) {
      setErr("Failed batch promoting: " + e.message);
    } finally {
      setPaperLoading(false);
    }
  };

  const fetchShortlist = async (customParams) => {
    setShortlistLoading(true);
    try {
      const p = customParams || filterParams;
      const res = await api.researchFilter({
        ...p,
        min_trade_duration_seconds: Math.round((parseFloat(p.min_trade_duration_minutes) || 0) * 60),
      });
      setShortlistData(res);
    } catch (e) {
      console.error("Shortlist fetch failed", e);
    } finally {
      setShortlistLoading(false);
    }
  };

  const handleUpdateMinTradeTime = async (val) => {
    const mins = parseFloat(val);
    if (isNaN(mins) || mins < 0) return;
    try {
      const secs = Math.round(mins * 60);
      await api.applySettings({ min_trade_duration_seconds: secs });
      setMinTradeTimeInput(String(mins));
      setFilterParams((prev) => ({ ...prev, min_trade_duration_minutes: mins }));
      fetchShortlist({ ...filterParams, min_trade_duration_minutes: mins });
    } catch (e) {
      setErr("Failed to update min trade time: " + e.message);
    }
  };

  useEffect(() => {
    const fetchTop = () => {
      api.population({ limit: 40, sort: "fitness" })
        .then((r) => setTop(r.strategies || []))
        .catch((e) => setErr(e.message));
    };
    const fetchRes = () => {
      api.resources().then(setResources).catch(() => {});
    };
    const fetchInventory = () => {
      api.dataInventory().then(setInventory).catch(() => {});
    };
    const fetchJobs = () => {
      api.jobs().then(setJobsData).catch(() => {});
    };
    const fetchInitialSettings = () => {
      api.settings().then((s) => {
        if (s?.backtest?.min_trade_duration_seconds) {
          const m = s.backtest.min_trade_duration_seconds / 60.0;
          setMinTradeTimeInput(String(m));
          setFilterParams((prev) => ({ ...prev, min_trade_duration_minutes: m }));
        }
      }).catch(() => {});
    };

    fetchTop();
    fetchRes();
    fetchInventory();
    fetchJobs();
    fetchInitialSettings();
    fetchShortlist();
    fetchRuns();
    fetchPaperCandidates();

    let inFlightOverview = false;
    const t = setInterval(async () => {
      if (inFlightOverview) return;
      inFlightOverview = true;
      try {
        await Promise.all([
          api.population({ limit: 40, sort: "fitness" }).then((r) => setTop(r.strategies || [])).catch(() => {}),
          api.resources().then(setResources).catch(() => {}),
          api.dataInventory().then(setInventory).catch(() => {}),
          api.jobs().then(setJobsData).catch(() => {}),
          fetchRuns(),
        ]);
      } finally {
        inFlightOverview = false;
      }
    }, 4000);
    return () => clearInterval(t);
  }, [events.length]);

  const handleToggleGpu = async () => {
    setHwBusy(true);
    try {
      const nextGpu = !resources?.gpu?.enabled;
      await api.applySettings({ gpu_enabled: nextGpu });
      const res = await api.resources();
      setResources(res);
      refreshStatus();
    } catch (e) {
      setErr("Failed to toggle GPU: " + e.message);
    } finally {
      setHwBusy(false);
    }
  };

  const handleSetCpuTarget = async (val) => {
    setHwBusy(true);
    try {
      await api.applySettings({ cpu_target_pct: val });
      const res = await api.resources();
      setResources(res);
      refreshStatus();
    } catch (e) {
      setErr("Failed to set CPU target: " + e.message);
    } finally {
      setHwBusy(false);
    }
  };

  const act = async (name, fn) => {
    setBusy(name);
    try { await fn(); } catch (e) { setErr(e.message); }
    setBusy("");
    setTimeout(refreshStatus, 400);
  };

  // V2.1 RECHECK: fast reconstruction from SQLite without rerun (spec §V2.1 G)
  const handleRecheck = async () => {
    setRechecking(true);
    setErr(null);
    try {
      const res = await api.recheck();
      setRecheckData(res);
      if (res.target) setTargetInput(String(res.target));
      refreshStatus();
    } catch (e) {
      setErr("Recheck failed: " + e.message);
    } finally {
      setRechecking(false);
    }
  };

  // V2.1 Set Total Node Target
  const handleUpdateTarget = async (newVal) => {
    const num = parseInt(newVal, 10);
    if (!num || num <= 0) return;
    try {
      const res = await api.setTarget(num);
      setRecheckData(res);
      setTargetInput(String(res.target));
      refreshStatus();
    } catch (e) {
      setErr("Failed to update target: " + e.message);
    }
  };

  // V2.1 START / CONTINUE EVOLUTION
  const handleStartContinue = async () => {
    const num = parseInt(targetInput, 10);
    const effTotal = recheckData?.total_nodes ?? lab?.total_nodes ?? 0;
    const effTarget = recheckData?.target ?? lab?.target ?? 500;
    if (effTotal >= effTarget && (!num || num <= effTotal)) {
      setErr(`Target of ${effTarget} is already reached (${effTotal}/${effTarget}). Enter a higher target (e.g. ${effTotal + 50}) to continue evolution.`);
      return;
    }
    setBusy("start");
    setErr(null);
    try {
      await api.labStart(lab?.mode || "continuous", num > effTarget ? num : undefined, "resume");
      setTimeout(refreshStatus, 400);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  };

  const pState = status?.pipeline_state || {};
  const currentRun = useMemo(() => {
    if (selectedRunId && selectedRunId !== "ALL") {
      const found = runsData.find((r) => r.run_id === selectedRunId);
      if (found) return found;
    }
    return pState.current_run || (runsData.length > 0 ? runsData[0] : null);
  }, [selectedRunId, runsData, pState]);

  const cumulativeStats = useMemo(() => {
    return pState.cumulative || {
      total_nodes: recheckData?.total_nodes ?? lab?.total_nodes ?? 0,
      completed_nodes: counts.QUALIFIED || 0,
      qualified_nodes: counts.QUALIFIED || 0,
      total_runs: runsData.length || 1,
    };
  }, [pState, recheckData, lab, counts, runsData]);

  const runGenNodes = currentRun?.generated_nodes ?? (recheckData?.total_nodes ?? lab?.total_nodes ?? 0);
  const runCeiling = currentRun?.node_ceiling ?? (recheckData?.target ?? lab?.target ?? 500);
  const runCompleted = currentRun?.completed_nodes ?? 0;
  const runQualified = currentRun?.qualified_nodes ?? ((counts.QUALIFIED || 0) + (counts.PAPER || 0));
  const runPendingBt = currentRun?.pending_backtesting ?? (counts.BACKTESTING || 0);
  const runPendingVal = currentRun?.pending_validation ?? (counts.VALIDATING || 0);
  const runGen = currentRun?.current_generation ?? (recheckData?.current_generation ?? lab?.generation ?? 0);
  const runGenPct = runCeiling > 0 ? Math.min(100, Math.round((runGenNodes / runCeiling) * 1000) / 10) : 100;
  const runCompPct = runCeiling > 0 ? Math.min(100, Math.round((runCompleted / runCeiling) * 1000) / 10) : 100;
  const runTargetReached = runGenNodes >= runCeiling;

  // V3 RESUME EXISTING RESEARCH FROM PERSISTED FRONTIER (spec §8)
  const handleResumeResearch = async () => {
    const num = parseInt(targetInput, 10);
    const effTotal = runGenNodes;
    const effTarget = runCeiling;
    const targetVal = num && num > effTotal ? num : (effTarget > effTotal ? effTarget : effTotal + 50);
    setBusy("resume");
    setErr(null);
    try {
      await api.labStart(lab?.mode || "continuous", targetVal, "resume");
      setTimeout(() => { refreshStatus(); fetchRuns(); }, 400);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  };

  // V4.1 START NEW RESEARCH RUN (spec §7): the button now opens the three-option
  // NewResearchRunModal (backup+reset / resume+add / reset without backup) instead
  // of silently starting a bare new run id.

  // Raw numeric sorting (spec §38)
  const handleSort = (col) => {
    if (sortCol === col) {
      setSortDir((prev) => (prev === "desc" ? "asc" : "desc"));
    } else {
      setSortCol(col);
      setSortDir("desc");
    }
  };

  const sortedTop = useMemo(() => {
    const list = [...top];
    list.sort((a, b) => {
      let va = a[sortCol];
      let vb = b[sortCol];
      if (sortCol === "dd") { va = a.dd; vb = b.dd; }
      if (sortCol === "return_pct") { va = a.return_pct; vb = b.return_pct; }
      if (va == null) va = -Infinity;
      if (vb == null) vb = -Infinity;
      return sortDir === "desc" ? (vb > va ? 1 : vb < va ? -1 : 0) : (va > vb ? 1 : va < vb ? -1 : 0);
    });
    return list.slice(0, 15);
  }, [top, sortCol, sortDir]);

  const sortArrow = (col) => {
    if (sortCol !== col) return "";
    return sortDir === "desc" ? " ↓" : " ↑";
  };

  // Derived V2.1 metrics
  const totalNodes = recheckData?.total_nodes ?? lab?.total_nodes ?? 0;
  const target = recheckData?.target ?? lab?.target ?? 500;
  const remaining = recheckData?.remaining ?? lab?.remaining ?? Math.max(0, target - totalNodes);
  const alive = recheckData?.alive ?? lab?.alive ?? 0;
  const dead = recheckData?.dead ?? lab?.dead ?? 0;
  const backtesting = recheckData?.backtesting ?? lab?.backtesting ?? (counts.BACKTESTING || 0);
  const validating = recheckData?.validating ?? lab?.validating ?? (counts.VALIDATING || 0);
  const qualified = recheckData?.qualified ?? lab?.qualified ?? ((counts.QUALIFIED || 0) + (counts.PAPER || 0));
  const currentGen = recheckData?.current_generation ?? lab?.generation ?? 0;
  const targetReached = totalNodes >= target;
  const progressPct = target > 0 ? Math.min(100, Math.round((totalNodes / target) * 1000) / 10) : 100;

  const cpu = resources?.cpu;
  const mem = resources?.memory;
  const gpu = resources?.gpu;
  const jobs = resources?.jobs;
  const tasks = resources?.tasks;

  return (
    <div>
      {newRunOpen && (
        <NewResearchRunModal
          onClose={() => setNewRunOpen(false)}
          onDone={() => { refreshStatus(); fetchRuns?.(); }}
        />
      )}
      <div className="flex justify-between items-center" style={{ marginBottom: "0.5rem" }}>
        {/* V5 §10 — Power sits top-left: closing the DASHBOARD only, after a Yes/No confirmation */}
        <PowerButton />
        <div style={{ flex: "1 1 auto", textAlign: "center" }}>
          <h2 className="page-title">Overview</h2>
          <div className="page-sub">
            Autonomous evolutionary research laboratory · objective: robust positive expectancy.
          </div>
        </div>
        <div style={{ width: 90 }} aria-hidden="true" />
      </div>

      <ErrorNote err={err} />

      {/* V2.1 TOTAL NODE TARGET & EVOLUTION CONTROL PANEL (spec §V2.1 D, G, H, I) */}
      <div className="panel" style={{
        marginBottom: 14,
        background: "linear-gradient(180deg, rgba(30, 41, 59, 0.7) 0%, rgba(15, 23, 42, 0.9) 100%)",
        border: "1px solid var(--border)",
        boxShadow: "0 4px 12px rgba(0,0,0,0.25)"
      }}>
        <div className="flex justify-between items-center" style={{ flexWrap: "wrap", gap: "10px", marginBottom: 12 }}>
          <div className="flex items-center" style={{ gap: "12px", flexWrap: "wrap" }}>
            <label style={{ fontSize: "0.85rem", fontWeight: 700, letterSpacing: "0.05em", color: "var(--fg)" }}>
              TOTAL NODE TARGET:
            </label>
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <input
                type="number"
                min="1"
                step="10"
                value={targetInput}
                onChange={(e) => setTargetInput(e.target.value)}
                onBlur={() => handleUpdateTarget(targetInput)}
                onKeyDown={(e) => { if (e.key === "Enter") handleUpdateTarget(targetInput); }}
                placeholder="e.g. 200"
                style={{
                  width: "100px",
                  padding: "6px 10px",
                  background: "var(--bg-box)",
                  border: "1px solid var(--border)",
                  borderRadius: "4px",
                  color: "var(--fg)",
                  fontWeight: "bold",
                  fontSize: "0.95rem",
                  fontFamily: "monospace",
                }}
              />
              <button
                className="btn"
                style={{ padding: "6px 10px", fontSize: "0.8rem" }}
                onClick={() => handleUpdateTarget(targetInput)}
              >
                SET TARGET
              </button>
            </div>

            <label style={{ fontSize: "0.85rem", fontWeight: 700, letterSpacing: "0.05em", color: "var(--fg)", marginLeft: "8px" }}>
              Minimum trade time (minutes):
            </label>
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <input
                type="number"
                min="0"
                step="0.5"
                value={minTradeTimeInput}
                onChange={(e) => setMinTradeTimeInput(e.target.value)}
                onBlur={() => handleUpdateMinTradeTime(minTradeTimeInput)}
                onKeyDown={(e) => { if (e.key === "Enter") handleUpdateMinTradeTime(minTradeTimeInput); }}
                placeholder="2"
                style={{
                  width: "70px",
                  padding: "6px 8px",
                  background: "var(--bg-box)",
                  border: "1px solid var(--border)",
                  borderRadius: "4px",
                  color: "var(--fg)",
                  fontWeight: "bold",
                  fontSize: "0.95rem",
                  fontFamily: "monospace",
                }}
              />
              <button
                className="btn"
                style={{ padding: "6px 10px", fontSize: "0.8rem" }}
                onClick={() => handleUpdateMinTradeTime(minTradeTimeInput)}
              >
                APPLY
              </button>
            </div>

            <button
              className="btn"
              style={{
                padding: "6px 14px",
                fontSize: "0.85rem",
                fontWeight: 700,
                background: "#334155",
                borderColor: "#475569",
                color: "#f8fafc"
              }}
              disabled={rechecking}
              onClick={handleRecheck}
            >
              {rechecking ? "🔄 RECHECKING..." : "🔄 RECHECK"}
            </button>

            <button
              className="btn success"
              style={{
                padding: "6px 16px",
                fontSize: "0.85rem",
                fontWeight: 700,
              }}
              disabled={busy === "resume" || (lab?.running && !lab?.paused)}
              onClick={handleResumeResearch}
            >
              ▶ RESUME EXISTING RESEARCH
            </button>

            <button
              className="btn primary"
              style={{
                padding: "6px 16px",
                fontSize: "0.85rem",
                fontWeight: 700,
                background: "var(--blue)",
              }}
              disabled={busy === "new_run"}
              onClick={() => setNewRunOpen(true)}
            >
              🚀 START NEW RESEARCH RUN
            </button>

            {lab?.running && !lab?.paused && (
              <button className="btn" style={{ padding: "6px 12px" }} onClick={() => act("pause", api.labPause)}>
                ⏸ PAUSE
              </button>
            )}
            {lab?.running && lab?.paused && (
              <button className="btn" style={{ padding: "6px 12px" }} onClick={() => act("resume", api.labResume)}>
                ⏵ RESUME
              </button>
            )}
            {lab?.running && (
              <button className="btn danger" style={{ padding: "6px 12px" }} onClick={() => act("stop", api.labStop)}>
                ⏹ STOP
              </button>
            )}
          </div>

          <div style={{ fontSize: "0.8rem", color: "var(--muted)", display: "flex", gap: "14px", alignItems: "center" }}>
            <span>Run: <b className="mono" style={{ color: "var(--fg)" }}>{status?.pipeline_state?.run_id || "RUN-MT5-ACTIVE"}</b></span>
            <span>Frontier: <b className="mono" style={{ color: "var(--green)" }}>{totalNodes} / {target}</b></span>
            <span>Mode: <b style={{ color: "var(--blue)" }}>{lab?.mode || "continuous"}</b></span>
          </div>
        </div>

        {/* Target Reached Banner */}
        {targetReached && (
          <div style={{
            marginBottom: 12,
            padding: "10px 16px",
            borderRadius: "4px",
            background: "rgba(16, 185, 129, 0.15)",
            border: "1px solid rgba(16, 185, 129, 0.4)",
            color: "#a7f3d0",
            fontSize: "0.85rem",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: "8px"
          }}>
            <span>
              🎯 <b>RESEARCH CEILING REACHED ({totalNodes} / {target})</b> — Lifecycle status: <code style={{ color: "#34d399", fontWeight: 700 }}>COMPLETED_AT_CEILING</code>. Node generation intentionally halted. All historical research, qualified strategies, and matrices are preserved.
            </span>
          </div>
        )}

        {/* Progress Bar (spec §V2.1 I) */}
        <div style={{ marginBottom: 12 }}>
          <div className="flex justify-between" style={{ fontSize: "0.75rem", marginBottom: 4 }}>
            <span className="muted">PROGRESS</span>
            <span className="mono font-bold" style={{ color: targetReached ? "var(--green)" : "var(--fg)" }}>
              {totalNodes} / {target} ({progressPct}%)
            </span>
          </div>
          <div style={{ height: "6px", background: "rgba(255,255,255,0.08)", borderRadius: "3px", overflow: "hidden" }}>
            <div style={{
              height: "100%",
              width: `${Math.min(100, progressPct)}%`,
              background: targetReached ? "linear-gradient(90deg, #10b981, #059669)" : "linear-gradient(90deg, #3b82f6, #06b6d4)",
              transition: "width 0.4s ease"
            }} />
          </div>
        </div>

        {/* 9 Display Metrics (spec §V2.1 I) */}
        <div className="grid cols-5" style={{ gap: "8px", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))" }}>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem", letterSpacing: "0.05em" }}>TOTAL NODES</div>
            <div style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--fg)", fontFamily: "monospace" }}>{totalNodes}</div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Historical count</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem", letterSpacing: "0.05em" }}>TARGET</div>
            <div style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--blue)", fontFamily: "monospace" }}>{target}</div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Max node ceiling</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem", letterSpacing: "0.05em" }}>REMAINING</div>
            <div style={{ fontSize: "1.25rem", fontWeight: 700, color: remaining > 0 ? "var(--green)" : "var(--muted)", fontFamily: "monospace" }}>{remaining}</div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Nodes allowed</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem", letterSpacing: "0.05em" }}>ALIVE</div>
            <div style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--green)", fontFamily: "monospace" }}>{alive}</div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Active / Survived</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem", letterSpacing: "0.05em" }}>DEAD</div>
            <div style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--red)", fontFamily: "monospace" }}>{dead}</div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Failed / Retired</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem", letterSpacing: "0.05em" }}>BACKTESTING</div>
            <div style={{ fontSize: "1.25rem", fontWeight: 700, color: backtesting > 0 ? "var(--blue)" : "var(--fg)", fontFamily: "monospace" }}>{backtesting}</div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>In screen/detail</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem", letterSpacing: "0.05em" }}>VALIDATING</div>
            <div style={{ fontSize: "1.25rem", fontWeight: 700, color: validating > 0 ? "#a78bfa" : "var(--fg)", fontFamily: "monospace" }}>{validating}</div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>In validation</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem", letterSpacing: "0.05em" }}>QUALIFIED</div>
            <div style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--green)", fontFamily: "monospace" }}>{qualified}</div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Robust survivors</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="muted" style={{ fontSize: "0.7rem", letterSpacing: "0.05em" }}>CURRENT GENERATION</div>
            <div style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--fg)", fontFamily: "monospace" }}>Gen {currentGen}</div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Highest gen</div>
          </div>
        </div>
      </div>

      {/* V3.2 RESEARCH RUN MANAGEMENT & HISTORICAL SELECTOR (spec §6, §8) */}
      <div className="panel" style={{
        marginBottom: 14,
        background: "var(--bg-panel)",
        border: "1px solid var(--border)",
        borderRadius: "6px",
        boxShadow: "0 2px 8px rgba(0,0,0,0.2)"
      }}>
        <div className="flex justify-between items-center" style={{ marginBottom: 12, flexWrap: "wrap", gap: "10px" }}>
          <div className="flex items-center gap-3">
            <h3 style={{ margin: 0, fontSize: "0.95rem", letterSpacing: "0.03em" }}>
              🔬 RESEARCH RUN MANAGEMENT (V3.6)
            </h3>
            <span className="pill" style={{
              background: currentRun?.run_status === "RUNNING" ? "rgba(16, 185, 129, 0.2)" : "rgba(148, 163, 184, 0.15)",
              color: currentRun?.run_status === "RUNNING" ? "var(--green)" : "var(--muted)",
              fontWeight: 700,
              fontSize: "0.75rem",
              border: "1px solid currentColor"
            }}>
              ● {currentRun?.run_status || "IDLE"}
            </span>
          </div>

          {/* Historical Run Selector (Dropdown) */}
          <div className="flex items-center gap-2">
            <label style={{ fontSize: "0.75rem", color: "var(--muted)", fontWeight: 700 }}>
              INSPECT RUN:
            </label>
            <select
              value={selectedRunId || currentRun?.run_id || "ALL"}
              onChange={(e) => handleSwitchRun(e.target.value)}
              style={{
                padding: "4px 10px",
                fontSize: "0.8rem",
                background: "var(--bg-box)",
                color: "var(--fg)",
                border: "1px solid var(--border)",
                borderRadius: "4px",
                fontFamily: "monospace"
              }}
            >
              {runsData.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.is_active ? "★ ACTIVE: " : ""}{r.run_id} ({r.experiment_id} · {r.generated_nodes} nodes · {r.run_status})
                </option>
              ))}
              <option value="ALL">🌐 ALL RUNS (CUMULATIVE DATABASE VIEW)</option>
            </select>
          </div>
        </div>

        {/* Dual Progress Bars: Generation vs Lifecycle Evaluation */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "14px", marginBottom: 14 }}>
          <div>
            <div className="flex justify-between" style={{ fontSize: "0.75rem", marginBottom: 4 }}>
              <span className="muted font-bold">1. CANDIDATE GENERATION PROGRESS</span>
              <span className="mono font-bold" style={{ color: runTargetReached ? "var(--green)" : "var(--blue)" }}>
                {runGenNodes} / {runCeiling} ({runGenPct}%)
              </span>
            </div>
            <div style={{ height: "6px", background: "rgba(255,255,255,0.08)", borderRadius: "3px", overflow: "hidden" }}>
              <div style={{
                height: "100%",
                width: `${runGenPct}%`,
                background: runTargetReached ? "linear-gradient(90deg, #10b981, #059669)" : "linear-gradient(90deg, #3b82f6, #06b6d4)",
                transition: "width 0.3s ease"
              }} />
            </div>
          </div>

          <div>
            <div className="flex justify-between" style={{ fontSize: "0.75rem", marginBottom: 4 }}>
              <span className="muted font-bold">2. LIFECYCLE EVALUATION PROGRESS</span>
              <span className="mono font-bold" style={{ color: runCompleted >= runCeiling ? "var(--green)" : "#eab308" }}>
                {runCompleted} / {runCeiling} ({runCompPct}%)
              </span>
            </div>
            <div style={{ height: "6px", background: "rgba(255,255,255,0.08)", borderRadius: "3px", overflow: "hidden" }}>
              <div style={{
                height: "100%",
                width: `${runCompPct}%`,
                background: runCompleted >= runCeiling ? "linear-gradient(90deg, #10b981, #059669)" : "linear-gradient(90deg, #eab308, #f59e0b)",
                transition: "width 0.3s ease"
              }} />
            </div>
          </div>
        </div>

        {/* 13 Explicit Metadata Metrics from Spec §6 */}
        <div className="grid" style={{
          gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
          gap: "8px",
          marginBottom: 10
        }}>
          <div style={{ padding: "6px 8px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <div className="muted" style={{ fontSize: "0.68rem" }}>EXPERIMENT ID</div>
            <div className="mono font-bold" style={{ fontSize: "0.85rem", color: "var(--blue)" }}>{currentRun?.experiment_id || "EXP-XAUUSD-M15"}</div>
          </div>
          <div style={{ padding: "6px 8px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <div className="muted" style={{ fontSize: "0.68rem" }}>RUN ID</div>
            <div className="mono font-bold" style={{ fontSize: "0.85rem", color: "var(--fg)" }}>{currentRun?.run_id || "RUN-MT5-ACTIVE"}</div>
          </div>
          <div style={{ padding: "6px 8px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <div className="muted" style={{ fontSize: "0.68rem" }}>CREATED</div>
            <div className="mono" style={{ fontSize: "0.75rem", color: "var(--fg)" }}>
              {currentRun?.created_at ? new Date(currentRun.created_at * 1000).toISOString().replace("T", " ").substring(0, 19) : "—"}
            </div>
          </div>
          <div style={{ padding: "6px 8px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <div className="muted" style={{ fontSize: "0.68rem" }}>CURRENT GENERATION</div>
            <div className="mono font-bold" style={{ fontSize: "0.95rem", color: "var(--fg)" }}>Gen {runGen}</div>
          </div>
          <div style={{ padding: "6px 8px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <div className="muted" style={{ fontSize: "0.68rem" }}>NODE CEILING</div>
            <div className="mono font-bold" style={{ fontSize: "0.95rem", color: "var(--blue)" }}>{runCeiling}</div>
          </div>
          <div style={{ padding: "6px 8px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <div className="muted" style={{ fontSize: "0.68rem" }}>GENERATED NODES</div>
            <div className="mono font-bold" style={{ fontSize: "0.95rem", color: "var(--fg)" }}>{runGenNodes}</div>
          </div>
          <div style={{ padding: "6px 8px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <div className="muted" style={{ fontSize: "0.68rem" }}>COMPLETED NODES</div>
            <div className="mono font-bold" style={{ fontSize: "0.95rem", color: "var(--green)" }}>{runCompleted}</div>
          </div>
          <div style={{ padding: "6px 8px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <div className="muted" style={{ fontSize: "0.68rem" }}>QUALIFIED NODES</div>
            <div className="mono font-bold" style={{ fontSize: "0.95rem", color: "var(--green)" }}>{runQualified}</div>
          </div>
          <div style={{ padding: "6px 8px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <div className="muted" style={{ fontSize: "0.68rem" }}>PENDING VALIDATION</div>
            <div className="mono font-bold" style={{ fontSize: "0.95rem", color: runPendingVal > 0 ? "#a78bfa" : "var(--muted)" }}>{runPendingVal}</div>
          </div>
          <div style={{ padding: "6px 8px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <div className="muted" style={{ fontSize: "0.68rem" }}>PENDING BACKTESTING</div>
            <div className="mono font-bold" style={{ fontSize: "0.95rem", color: runPendingBt > 0 ? "var(--blue)" : "var(--muted)" }}>{runPendingBt}</div>
          </div>
          <div style={{ padding: "6px 8px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <div className="muted" style={{ fontSize: "0.68rem" }}>RUN STATUS</div>
            <div className="mono font-bold" style={{ fontSize: "0.85rem", color: "var(--fg)" }}>{currentRun?.run_status || "IDLE"}</div>
          </div>
          <div style={{ padding: "6px 8px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <div className="muted" style={{ fontSize: "0.68rem" }}>PERSISTENCE STATUS</div>
            <div className="mono font-bold" style={{ fontSize: "0.85rem", color: "var(--green)" }}>{currentRun?.persistence_status || "PERSISTED"}</div>
          </div>
          <div style={{ padding: "6px 8px", background: "var(--bg-box)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <div className="muted" style={{ fontSize: "0.68rem" }}>LAST CHECKPOINT</div>
            <div className="mono" style={{ fontSize: "0.72rem", color: "var(--muted)" }}>
              {currentRun?.last_checkpoint ? new Date(currentRun.last_checkpoint * 1000).toISOString().replace("T", " ").substring(11, 19) + " UTC" : "VERIFIED"}
            </div>
          </div>
        </div>

        {/* Clear Run vs Cumulative Distinction Callout */}
        <div style={{
          padding: "8px 12px",
          background: "rgba(15, 23, 42, 0.6)",
          borderRadius: "4px",
          border: "1px solid rgba(255, 255, 255, 0.05)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "8px",
          fontSize: "0.8rem"
        }}>
          <div>
            <span style={{ color: "var(--muted)" }}>Current Research Run:</span>{" "}
            <b className="mono" style={{ color: "var(--fg)" }}>{runGenNodes} / {runCeiling} nodes ({runGenPct}%)</b>
            {runPendingBt + runPendingVal > 0 && (
              <span style={{ color: "#eab308", marginLeft: "6px" }}>
                · {runPendingBt + runPendingVal} in-flight evaluations remaining
              </span>
            )}
          </div>
          <div>
            <span style={{ color: "var(--muted)" }}>Cumulative Database:</span>{" "}
            <b className="mono" style={{ color: "var(--green)" }}>{cumulativeStats.total_nodes} nodes</b>{" "}
            <span className="muted">across {cumulativeStats.total_runs} historical runs ({cumulativeStats.qualified_nodes} qualified)</span>
          </div>
        </div>
      </div>

      {/* Mode Control Buttons */}
      <div className="btn-row" style={{ marginBottom: 14 }}>
        {["exploration", "evolution", "validation", "paper", "continuous"].map((m) => (
          <button key={m} className={"btn" + (lab?.mode === m && lab?.running ? " primary" : "")}
                  disabled={busy === "start"}
                  onClick={() => act("start", () => api.labStart(m))}>
            ▶ {m.toUpperCase()}
          </button>
        ))}
        <button className="btn" onClick={() => act("reset", api.labReset)}>↺ RESET GENERATION</button>
        <button className="btn" onClick={() => act("clear", api.labClearFailed)}>🧹 CLEAR FAILED</button>
      </div>

      {/* Resource Monitor Panel (spec §32, V2.7, V3.5 Phase 2) */}
      <div className="panel" style={{ marginBottom: 14, background: "var(--bg-panel)", border: "1px solid var(--border)" }}>
        <div className="flex justify-between items-center" style={{ marginBottom: 10, flexWrap: "wrap", gap: "8px" }}>
          <div>
            <h3 style={{ margin: 0, fontSize: "0.95rem" }}>⚙ COMPUTATIONAL RESOURCE & ACCELERATION CONTROL (V3.5)</h3>
            <span className="muted" style={{ fontSize: "0.75rem" }}>
              Target CPU: <b>{cpu?.target_pct ?? 60}%</b> · Dynamic Worker Pool: <b style={{ color: "var(--green)" }}>{cpu?.effective_workers ?? 2} workers</b> · GPU: <b style={{ color: gpu?.enabled ? "var(--green)" : "var(--muted)" }}>{gpu?.enabled ? "ON" : "OFF"}</b>
            </span>
          </div>

          <div className="flex items-center gap-2">
            <button
              className={`btn btn-xs ${gpu?.enabled ? "btn-primary" : "btn-subtle"}`}
              style={{ padding: "4px 10px", fontSize: "0.75rem", fontWeight: 700 }}
              disabled={hwBusy}
              onClick={handleToggleGpu}
            >
              GPU: {gpu?.enabled ? "ON (Accelerated)" : "OFF (CPU Only)"}
            </button>
          </div>
        </div>

        {/* Dynamic CPU Target Slider & Step Selector */}
        <div style={{
          marginBottom: 10,
          padding: "8px 12px",
          background: "rgba(0,0,0,0.25)",
          borderRadius: "4px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: "10px"
        }}>
          <div className="flex items-center gap-2">
            <span style={{ fontSize: "0.78rem", fontWeight: 700, color: "var(--fg-dim)" }}>
              CPU ALLOCATION TARGET:
            </span>
            <input
              type="range"
              min="10"
              max="100"
              step="5"
              disabled={hwBusy}
              value={cpu?.target_pct ?? 60}
              onChange={(e) => handleSetCpuTarget(Number(e.target.value))}
              style={{ width: "120px", cursor: "pointer", accentColor: "var(--blue)" }}
            />
            <span className="mono font-bold" style={{ fontSize: "0.88rem", color: "var(--blue)", minWidth: "36px" }}>
              {cpu?.target_pct ?? 60}%
            </span>
          </div>

          {/* Quick Allocation Presets */}
          <div className="flex items-center gap-1" style={{ flexWrap: "wrap" }}>
            <span className="muted" style={{ fontSize: "0.72rem", marginRight: "4px" }}>PRESETS:</span>
            {[25, 50, 75, 100].map((pct) => (
              <button
                key={pct}
                disabled={hwBusy}
                onClick={() => handleSetCpuTarget(pct)}
                style={{
                  padding: "3px 8px",
                  fontSize: "0.74rem",
                  borderRadius: "3px",
                  border: "1px solid var(--border)",
                  background: (cpu?.target_pct === pct) ? "var(--blue)" : "rgba(255,255,255,0.06)",
                  color: (cpu?.target_pct === pct) ? "#fff" : "var(--fg)",
                  fontWeight: (cpu?.target_pct === pct) ? 800 : 600,
                  cursor: "pointer",
                }}
              >
                {pct}%
              </button>
            ))}
            <span style={{ margin: "0 4px", color: "var(--border)" }}>|</span>
            {[10, 20, 30, 40, 60, 70, 80, 90].map((pct) => (
              <button
                key={pct}
                disabled={hwBusy}
                onClick={() => handleSetCpuTarget(pct)}
                style={{
                  padding: "2px 5px",
                  fontSize: "0.70rem",
                  borderRadius: "3px",
                  border: "1px solid var(--border)",
                  background: (cpu?.target_pct === pct) ? "var(--blue)" : "rgba(255,255,255,0.03)",
                  color: (cpu?.target_pct === pct) ? "#fff" : "var(--fg-dim)",
                  fontWeight: (cpu?.target_pct === pct) ? 800 : 500,
                  cursor: "pointer",
                }}
              >
                {pct}%
              </button>
            ))}
          </div>
        </div>

        <div className="grid cols-4" style={{ gap: "10px" }}>
          {/* CPU Card */}
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px" }}>
            <div className="muted" style={{ fontSize: "0.75rem" }}>CPU UTILIZATION</div>
            <div style={{ fontSize: "1.2rem", fontWeight: 700, color: "var(--fg)" }}>
              {cpu?.percent ?? 0}% <span style={{ fontSize: "0.75rem", fontWeight: 500, color: "var(--accent)" }}>({cpu?.app_percent ?? 0}% app)</span>
            </div>
            <div className="muted" style={{ fontSize: "0.72rem" }}>
              Target: {cpu?.target_pct ?? 60}% · {cpu?.logical_processors ?? cpu?.physical_cores} logical cores
            </div>
          </div>

          {/* RAM Card */}
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px" }}>
            <div className="muted" style={{ fontSize: "0.75rem" }}>RAM USAGE & RSS</div>
            <div style={{ fontSize: "1.2rem", fontWeight: 700, color: (mem?.percent > 85 ? "var(--red)" : "var(--fg)") }}>
              {mem?.used_mb ? (mem.used_mb / 1024).toFixed(1) : "–"} GB <span style={{ fontSize: "0.75rem", fontWeight: 500, color: "var(--green)" }}>({mem?.app_used_mb ?? 0} MB app)</span>
            </div>
            <div className="muted" style={{ fontSize: "0.72rem" }}>
              Avail: {mem?.available_mb ? (mem.available_mb / 1024).toFixed(1) : "–"} GB · Peak: {mem?.app_peak_mb ?? 0} MB
            </div>
          </div>

          {/* GPU Card */}
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px" }}>
            <div className="muted" style={{ fontSize: "0.75rem" }}>GPU ACCELERATION</div>
            <div style={{ fontSize: "1.2rem", fontWeight: 700, color: (gpu?.enabled && gpu?.available ? "var(--green)" : "var(--muted)") }}>
              {gpu?.enabled && gpu?.available ? `${gpu?.utilization_pct}%` : (gpu?.enabled ? "PROBING / CPU" : "OFF")}
            </div>
            <div className="muted" style={{ fontSize: "0.72rem" }}>
              {gpu?.name ? (gpu.name.length > 20 ? gpu.name.slice(0, 18) + "..." : gpu.name) : "CPU Fallback"}
            </div>
          </div>

          {/* Worker Pool & Throughput Card */}
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px" }}>
            <div className="muted" style={{ fontSize: "0.75rem" }}>DYNAMIC WORKERS & TASKS</div>
            <div style={{ fontSize: "1.2rem", fontWeight: 700, color: "var(--blue)" }}>
              {cpu?.active_workers ?? 0} active / {cpu?.effective_workers ?? 2} max
            </div>
            <div className="muted" style={{ fontSize: "0.72rem" }}>
              Idle: {cpu?.idle_workers ?? Math.max(0, (cpu?.effective_workers || 0) - (cpu?.active_workers || 0))} · {tasks?.tasks_per_minute ?? 0}/min · {tasks?.avg_duration_ms ?? 0}ms avg
            </div>
          </div>
        </div>
      </div>

      {/* Real Multi-Level Progress & Stall Detection Widget (V2.7) */}
      {jobsData?.active_job && (
        <div className="panel" style={{
          marginBottom: 14,
          background: "linear-gradient(180deg, #182335 0%, #101826 100%)",
          border: `1px solid ${jobsData.active_job.is_stalled ? "#f59e0b" : "var(--border)"}`,
          boxShadow: "0 4px 12px rgba(0,0,0,0.3)"
        }}>
          <div className="flex justify-between items-center" style={{ marginBottom: 6 }}>
            <div className="flex items-center gap-2">
              <span style={{ fontWeight: 800, fontSize: "0.85rem", color: "var(--fg)" }}>
                ⚡ ACTIVE JOB: {jobsData.active_job.name}
              </span>
              <span className={`pill ${jobsData.active_job.is_stalled ? "neg" : (jobsData.active_job.status === "COMPLETED" ? "pos" : "sim")}`}
                    style={{ fontSize: "0.7rem", fontWeight: 800 }}>
                {jobsData.active_job.status}
              </span>
              {jobsData.active_job.is_stalled && (
                <span className="mono neg font-bold" style={{ fontSize: "0.75rem" }}>
                  ⚠ STALLED ({jobsData.active_job.stalled_duration_s}s inactive)
                </span>
              )}
            </div>
            <div className="mono" style={{ fontSize: "0.8rem" }}>
              <span className="muted">Elapsed: </span><b>{jobsData.active_job.elapsed_seconds}s</b>
              {jobsData.active_job.eta_seconds != null && (
                <> · <span className="muted">ETA: </span><b style={{ color: "var(--blue)" }}>{jobsData.active_job.eta_seconds}s</b></>
              )}
            </div>
          </div>

          <div style={{ height: "6px", background: "rgba(255,255,255,0.08)", borderRadius: "3px", overflow: "hidden", marginBottom: 8 }}>
            <div style={{
              height: "100%",
              width: `${jobsData.active_job.overall_progress_pct}%`,
              background: jobsData.active_job.is_stalled ? "#f59e0b" : "linear-gradient(90deg, #3b82f6, #10b981)",
              transition: "width 0.4s ease"
            }} />
          </div>

          {jobsData.active_job.stages?.length > 0 && (
            <div className="flex gap-2" style={{ flexWrap: "wrap", fontSize: "0.75rem" }}>
              {jobsData.active_job.stages.map((st) => (
                <div key={st.stage_id} style={{
                  padding: "3px 8px",
                  borderRadius: "3px",
                  background: "rgba(0,0,0,0.25)",
                  border: "1px solid rgba(255,255,255,0.06)"
                }}>
                  <span className="muted">{st.name}: </span>
                  <b style={{ color: st.status === "COMPLETED" ? "var(--green)" : "var(--fg)" }}>{st.progress_pct}%</b>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Top High-Level Metrics */}
      <div className="grid cols-4" style={{ marginBottom: 14 }}>
        <Metric label="Generation" value={currentGen} sub={`${lab?.cycle ?? 0} orchestrator cycles`} />
        <Metric label="Active population" value={lab?.population_active ?? "–"}
                sub={`target ${status?.config_summary?.population_size ?? "?"} · duplicates blocked ${lab?.duplicates_blocked ?? 0}`} />
        <Metric label="Qualified / Paper" value={`${counts.QUALIFIED || 0} / ${counts.PAPER || 0}`}
                sub={`validated ${lab?.counters?.validated ?? 0} · killed ${lab?.counters?.killed ?? 0}`} color="var(--green)" />
        <Metric label="Screened / Detailed" value={`${lab?.counters?.screened ?? 0} / ${lab?.counters?.detailed ?? 0}`}
                sub={`born ${lab?.counters?.born ?? 0} · hypotheses ${lab?.counters?.hypotheses ?? 0}`} />
      </div>

      {/* Top Strategies Table with Raw Numeric Sorting (spec §38) */}
      <div className="grid cols-2" style={{ gridTemplateColumns: "2fr 1.2fr", marginBottom: 14 }}>
        <div className="panel">
          <div className="flex justify-between items-center">
            <h3>Top strategies (sortable, click column header)</h3>
            <span className="muted" style={{ fontSize: "0.8rem" }}>Sorted by: <b>{sortCol}</b> ({sortDir})</span>
          </div>
          <div className="scroll-y" style={{ maxHeight: 330 }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th onClick={() => handleSort("id")} style={{ cursor: "pointer" }}>id{sortArrow("id")}</th>
                  <th>status</th>
                  <th onClick={() => handleSort("generation")} style={{ cursor: "pointer" }}>gen{sortArrow("generation")}</th>
                  <th>tf</th>
                  <th onClick={() => handleSort("fitness")} style={{ cursor: "pointer" }}>fitness{sortArrow("fitness")}</th>
                  <th onClick={() => handleSort("pf")} style={{ cursor: "pointer" }}>PF{sortArrow("pf")}</th>
                  <th onClick={() => handleSort("return_pct")} style={{ cursor: "pointer" }}>return{sortArrow("return_pct")}</th>
                  <th onClick={() => handleSort("dd")} style={{ cursor: "pointer" }}>DD{sortArrow("dd")}</th>
                  <th onClick={() => handleSort("trades")} style={{ cursor: "pointer" }}>trades{sortArrow("trades")}</th>
                  <th>qualification / survival evidence</th>
                </tr>
              </thead>
              <tbody>
                {sortedTop.map((s) => (
                  <tr key={s.id} onClick={() => openStrategy(s.id)}>
                    <td className="mono">#{s.id}</td>
                    <td><Pill status={s.status} /></td>
                    <td>{s.generation}</td>
                    <td>{s.timeframe}</td>
                    <td className="mono font-bold">{fmt.num(s.fitness, 3)}</td>
                    <td className="mono">{fmt.num(s.pf, 2)}</td>
                    <td><SignedNum v={s.return_pct} pct /></td>
                    <td className="mono">{fmt.pct(s.dd)}</td>
                    <td>{s.trades ?? "–"}</td>
                    <td className="muted" style={{ fontSize: 10.5, maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                        title={s.survival_reason || s.creation_reason || ""}>
                      {s.survival_reason || s.creation_reason || "–"}
                    </td>
                  </tr>
                ))}
                {!sortedTop.length && <tr><td colSpan={10} className="muted">no tested strategies yet — press START</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
        <div className="panel">
          <h3>Live event stream {connected ? <span className="pos">●</span> : <span className="neg">○</span>}</h3>
          <EventFeed events={events} limit={80} />
        </div>
      </div>

      <div className="grid cols-2" style={{ marginBottom: 14 }}>
        <div className="panel">
          <h3>Pipeline status counts</h3>
          <table className="tbl">
            <tbody>
              {Object.entries(counts).map(([k, v]) => (
                <tr key={k} style={{ cursor: "default" }}><td><Pill status={k} /></td><td>{v}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="panel">
          <h3>Component readiness (operational / simulated / pending)</h3>
          <div className="scroll-y" style={{ maxHeight: 260 }}>
            <table className="tbl">
              <tbody>
                {Object.entries(status?.components || {}).map(([k, v]) => (
                  <tr key={k} style={{ cursor: "default" }}>
                    <td className="mono">{k}</td>
                    <td className={v === "operational" ? "pos" : v.startsWith("PENDING") ? "neg" : "muted"}
                        style={{ fontSize: 11.5 }}>{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* V3.6 PAPER TRADING CANDIDATE SELECTION */}
      <div className="panel" style={{ marginBottom: 14, background: "var(--bg-panel)", border: "1px solid var(--border)" }}>
        <div className="flex justify-between items-center" style={{ marginBottom: 10, flexWrap: "wrap", gap: "8px" }}>
          <div>
            <h3 style={{ margin: 0, fontSize: "0.95rem" }}>
              📈 QUALIFIED CANDIDATES FOR PAPER TRADING (V3.6)
            </h3>
            <span className="muted" style={{ fontSize: "0.75rem" }}>
              Promote validated historical strategies into live market forward test with risk controls.
            </span>
          </div>

          <div className="flex items-center gap-2">
            <button
              className="btn btn-sm success"
              disabled={paperLoading}
              onClick={handleBatchPromote}
              style={{ padding: "4px 12px", fontSize: "0.8rem", fontWeight: 700 }}
            >
              🚀 PROMOTE TOP 10 QUALIFIED CANDIDATES
            </button>
            <button
              className="btn btn-sm"
              disabled={paperLoading}
              onClick={fetchPaperCandidates}
              style={{ padding: "4px 8px", fontSize: "0.8rem" }}
            >
              🔄 REFRESH
            </button>
          </div>
        </div>

        <div className="scroll-y" style={{ maxHeight: 280 }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Strategy ID</th>
                <th>Run ID</th>
                <th>Symbol / TF</th>
                <th>Fitness</th>
                <th>Robustness</th>
                <th>Profit Factor</th>
                <th>Return %</th>
                <th>Win Rate</th>
                <th>Trades</th>
                <th>Paper Status</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {paperCandidates.slice(0, 25).map((c) => (
                <tr key={c.id}>
                  <td className="mono font-bold">
                    <a onClick={() => openStrategy(c.id)} style={{ cursor: "pointer", color: "var(--blue)" }}>
                      Node_{c.id} {c.research_node_num ? <span style={{ color: "#4f8ef7", fontSize: "0.75rem", fontWeight: 500 }}>(#{c.research_node_num})</span> : ""}
                    </a>
                  </td>
                  <td className="mono muted" style={{ fontSize: "0.75rem" }}>{c.run_id}</td>
                  <td className="mono">{c.symbol} <span className="pill" style={{ fontSize: "0.7rem", padding: "1px 4px" }}>{c.timeframe}</span></td>
                  <td className="mono font-bold" style={{ color: "var(--green)" }}>{fmt.num(c.fitness, 3)}</td>
                  <td className="mono">{fmt.num(c.robustness_score, 2)}</td>
                  <td className="mono">{fmt.num(c.profit_factor, 2)}</td>
                  <td className="mono"><SignedNum v={c.total_return_pct * 100} pct /></td>
                  <td className="mono">{fmt.pct(c.win_rate)}</td>
                  <td className="mono">{c.trades}</td>
                  <td>
                    <span className={`pill ${c.promoted_to_paper ? "pos" : "subtle"}`} style={{ fontSize: "0.7rem" }}>
                      {c.promoted_to_paper ? "PAPER ACTIVE" : "QUALIFIED"}
                    </span>
                  </td>
                  <td>
                    {c.promoted_to_paper ? (
                      <button
                        className="btn btn-xs"
                        style={{ padding: "2px 6px", fontSize: "0.7rem" }}
                        disabled={promotingId === c.id}
                        onClick={() => handleDemoteCandidate(c.id)}
                      >
                        DEMOTE
                      </button>
                    ) : (
                      <button
                        className="btn btn-xs success"
                        style={{ padding: "2px 6px", fontSize: "0.7rem" }}
                        disabled={promotingId === c.id}
                        onClick={() => handlePromoteCandidate(c.id)}
                      >
                        PROMOTE
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {paperCandidates.length === 0 && (
                <tr>
                  <td colSpan={11} className="muted" style={{ textAlign: "center", padding: "16px" }}>
                    No qualified paper trading candidates currently available. Run research to qualify strategies.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* V2.7 Persistent Data Architecture Inventory Panel */}
      <div className="panel" style={{
        marginBottom: 14,
        background: "linear-gradient(180deg, #131d2e 0%, #0e1624 100%)",
        border: "1px solid var(--border)",
        borderRadius: "6px",
      }}>
        <div className="flex justify-between items-center" style={{ marginBottom: 10, flexWrap: "wrap", gap: "8px" }}>
          <div>
            <h3 style={{ margin: 0, fontSize: "0.95rem" }}>🗄️ PERSISTENT DATA ARCHITECTURE INVENTORY (DATA_ROOT)</h3>
            <span className="muted" style={{ fontSize: "0.78rem" }}>
              Permanent Parquet storage · Incremental range sync · Stable schema versioning (<code className="mono">core_v1</code>) · Reusable cache
            </span>
          </div>

          <div className="flex items-center gap-2">
            <span className="pill pos" style={{ fontSize: "0.72rem", fontWeight: 700 }}>
              ✓ REUSE ENGINE ACTIVE
            </span>
          </div>
        </div>

        {/* Data Architecture Summary Metrics */}
        <div className="grid cols-4" style={{ gap: "8px", marginBottom: 12 }}>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>DATASETS DISCOVERED</div>
            <div style={{ fontSize: "1.2rem", fontWeight: 700, color: "var(--fg)" }}>
              {inventory?.summary?.total_datasets ?? 0}
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Permanent in DATA/MT5/raw/</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>TOTAL RAW BARS</div>
            <div style={{ fontSize: "1.2rem", fontWeight: 700, color: "var(--blue)" }}>
              {fmt.num(inventory?.summary?.total_raw_bars ?? 0, 0)}
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Monotonic & integrity validated</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>CACHED FEATURE SETS</div>
            <div style={{ fontSize: "1.2rem", fontWeight: 700, color: "var(--green)" }}>
              {inventory?.summary?.total_features_cached ?? 0}
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Persistent in DATA/features/</div>
          </div>
          <div style={{ padding: "8px 10px", background: "var(--bg-box)", borderRadius: "4px" }}>
            <div className="muted" style={{ fontSize: "0.7rem" }}>PERSISTED STORAGE</div>
            <div style={{ fontSize: "1.2rem", fontWeight: 700, color: "var(--fg)" }}>
              {inventory?.summary?.total_storage_mb ?? 0} MB
            </div>
            <div className="muted" style={{ fontSize: "0.68rem" }}>Relative portable paths</div>
          </div>
        </div>

        {/* Persisted Datasets Table */}
        <div className="scroll-y" style={{ maxHeight: 280 }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Symbol / TF</th>
                <th>Rows</th>
                <th>Stored Range (UTC)</th>
                <th>Source / Broker</th>
                <th>Feature Status</th>
                <th>Size</th>
                <th>Data Policy</th>
              </tr>
            </thead>
            <tbody>
              {(inventory?.datasets || []).map((d) => (
                <tr key={d.id} style={{ cursor: "default" }}>
                  <td className="mono font-bold" style={{ color: "var(--fg)" }}>
                    {d.symbol} <span className="pill" style={{ fontSize: "0.7rem", padding: "1px 4px" }}>{d.timeframe}</span>
                  </td>
                  <td className="mono">{fmt.num(d.rows, 0)}</td>
                  <td className="mono" style={{ fontSize: "0.75rem" }}>
                    {d.start_date} → {d.end_date}
                  </td>
                  <td>
                    <span className={`pill ${d.source === "SIMULATOR" ? "sim" : "real"}`} style={{ fontSize: "0.7rem" }}>
                      {d.source}
                    </span>{" "}
                    <span className="muted" style={{ fontSize: "0.75rem" }}>({d.broker})</span>
                  </td>
                  <td>
                    <span className="pill pos" style={{ fontSize: "0.7rem" }}>
                      {d.feature_status}
                    </span>
                  </td>
                  <td className="mono" style={{ fontSize: "0.75rem" }}>{d.size_mb} MB</td>
                  <td>
                    <span style={{
                      fontSize: "0.72rem",
                      fontWeight: 700,
                      color: "var(--green)",
                      background: "rgba(16, 185, 129, 0.12)",
                      padding: "2px 6px",
                      borderRadius: "3px",
                      border: "1px solid rgba(16, 185, 129, 0.25)"
                    }}>
                      REUSABLE · NO RE-FETCH
                    </span>
                  </td>
                </tr>
              ))}
              {(!inventory?.datasets || inventory.datasets.length === 0) && (
                <tr>
                  <td colSpan={7} className="muted" style={{ textAlign: "center", padding: "12px" }}>
                    No persisted datasets discovered in DATA/.
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
