import React, { useEffect, useState, useMemo, useRef } from "react";
import { api, fmt } from "../api.js";
import { useLabEvents } from "../ws.js";

export default function LiveActivitySidebar({ isOpen, onToggle, onOpenMt5Modal }) {
  const [events, setEvents] = useState([]);
  const [currentTask, setCurrentTask] = useState(null);
  const [stageState, setStageState] = useState(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [filterCat, setFilterCat] = useState("Newest");
  const [copyStatus, setCopyStatus] = useState("");
  const [unreadCount, setUnreadCount] = useState(0);
  const [expandedEventId, setExpandedEventId] = useState(null);
  const [lastActivityAge, setLastActivityAge] = useState(0);
  const lastEventTsRef = useRef(Date.now() / 1000);
  const inFlightTaskRef = useRef(false);
  const { events: wsEvents } = useLabEvents(300);

  // 1. Initial fetch & periodic poll for authoritative backend stage state
  const fetchTaskState = () => {
    if (inFlightTaskRef.current) return;
    inFlightTaskRef.current = true;
    api.workflowTaskState()
      .then((st) => {
        if (st) {
          setStageState(st);
          if (st.active_task) {
            setCurrentTask(st.active_task);
          }
        }
      })
      .catch(() => {})
      .finally(() => { inFlightTaskRef.current = false; });
  };

  useEffect(() => {
    api.activity({ limit: 150 })
      .then((data) => {
        if (Array.isArray(data)) setEvents(data);
      })
      .catch(() => {});

    fetchTaskState();
    const interval = setInterval(fetchTaskState, 2000);
    return () => clearInterval(interval);
  }, []);

  // 2. Real-time WebSocket event ingestion
  useEffect(() => {
    if (!wsEvents || wsEvents.length === 0) return;
    const latest = wsEvents[0];
    lastEventTsRef.current = Date.now() / 1000;

    if (latest?.type === "activity" && latest.payload) {
      setEvents((prev) => {
        const item = latest.payload;
        if (prev.length > 0 && prev[0].ts === item.ts && prev[0].message === item.message) {
          return prev;
        }
        if (!isOpen) {
          setUnreadCount((c) => Math.min(99, c + 1));
        }
        return [item, ...prev].slice(0, 400);
      });

      if (latest.payload.status === "RUNNING" || latest.payload.status === "STARTED") {
        setCurrentTask((prev) => ({
          ...(prev || {}),
          name: latest.payload.message,
          message: latest.payload.message,
          operation_id: latest.payload.operation_id,
          started_at: prev?.started_at || latest.payload.ts,
          updated_at: latest.payload.ts,
          status: "RUNNING",
          category: latest.payload.category,
          stage: latest.payload.details?.stage || latest.payload.category,
          progress: latest.payload.progress,
          percentage: latest.payload.progress || 0,
          current: latest.payload.details?.current || 0,
          total: latest.payload.details?.total || 0,
          current_item: latest.payload.details?.item || "",
          throughput: latest.payload.details?.throughput || (latest.payload.details?.bars_per_sec ? `${latest.payload.details.bars_per_sec.toLocaleString()} bars/s` : null),
        }));
      } else if (["SUCCESS", "COMPLETE", "REUSED"].includes(latest.payload.status)) {
        if (latest.payload.operation_id === currentTask?.operation_id || !latest.payload.operation_id) {
          setCurrentTask((prev) => (prev ? { ...prev, status: "COMPLETE", percentage: 100 } : null));
        }
      }
    } else if (latest?.type === "stage_transition" && latest.payload) {
      fetchTaskState();
    }
  }, [wsEvents, isOpen]);

  // Reset unread count when opening sidebar
  useEffect(() => {
    if (isOpen) setUnreadCount(0);
  }, [isOpen]);

  // Elapsed time & last activity age clock
  useEffect(() => {
    const timer = setInterval(() => {
      const now = Date.now() / 1000;
      setLastActivityAge(Math.max(0, now - lastEventTsRef.current));
      if (currentTask && (currentTask.status === "RUNNING" || currentTask.status === "ACTIVE") && currentTask.started_at) {
        setElapsedSeconds(Math.max(0, Math.floor(now - currentTask.started_at)));
      }
    }, 500);
    return () => clearInterval(timer);
  }, [currentTask]);

  const handleCopyLog = async () => {
    let text = "";
    try {
      const res = await api.diagnosticLog(300);
      text = res?.diagnostic_text || "";
    } catch (e) {
      // Fallback: format locally buffered operational events so copy log never stalls
      text = events.map((e) => `[${e.time_str || fmt.ts(e.ts)}] [${e.level || "INFO"}] [${e.category || "SYSTEM"}] ${e.message}`).join("\n");
    }

    if (!text && events.length > 0) {
      text = events.map((e) => `[${e.time_str || fmt.ts(e.ts)}] [${e.level || "INFO"}] [${e.category || "SYSTEM"}] ${e.message}`).join("\n");
    }
    if (!text) text = "No logs available";

    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopyStatus("✓ COPIED");
      setTimeout(() => setCopyStatus(""), 2500);
    } catch (e) {
      setCopyStatus("FAILED");
      setTimeout(() => setCopyStatus(""), 2500);
    }
  };

  const filteredEvents = useMemo(() => {
    let list = [...events];
    const cat = filterCat;

    if (cat === "Oldest") {
      list.sort((a, b) => (a.ts || 0) - (b.ts || 0));
      return list;
    }

    list.sort((a, b) => (b.ts || 0) - (a.ts || 0));

    if (cat === "Newest" || cat === "ALL") return list;
    if (cat === "Active") return list.filter((e) => ["RUNNING", "STARTED", "ACTIVE"].includes(e.status));
    if (cat === "Warnings") return list.filter((e) => e.level === "WARNING" || e.status === "WARNING");
    if (cat === "Errors") return list.filter((e) => e.level === "ERROR" || e.status === "FAILED");
    if (cat === "Completed") return list.filter((e) => ["SUCCESS", "COMPLETE", "REUSED"].includes(e.status) || e.level === "SUCCESS");
    return list;
  }, [events, filterCat]);

  function formatDuration(sec) {
    if (!sec || isNaN(sec)) return "0.0s";
    if (sec < 60) return `${sec.toFixed(1)}s`;
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}m ${s}s`;
  }

  const activeTaskObj = currentTask?.status === "RUNNING" || currentTask?.status === "ACTIVE" ? currentTask : stageState?.active_task;
  const activeCount = stageState?.active_tasks_count || (activeTaskObj ? 1 : 0);
  const currentPct = activeTaskObj?.percentage != null ? activeTaskObj.percentage : (activeTaskObj?.progress != null ? activeTaskObj.progress : 100);

  // 3. COLLAPSED VIEW: Slim strip with compact indicator (User Spec §9)
  if (!isOpen) {
    return (
      <div
        className="live-activity-collapsed-strip"
        onClick={onToggle}
        title="Click to expand Live Activity feed"
        style={{
          width: "38px",
          background: "var(--bg-panel)",
          borderLeft: "1px solid var(--border)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          padding: "12px 0",
          cursor: "pointer",
          userSelect: "none",
          transition: "all 0.2s ease",
          zIndex: 90,
          flexShrink: 0,
        }}
      >
        <button
          onClick={(e) => { e.stopPropagation(); onToggle(); }}
          style={{
            background: "var(--bg-box)",
            border: "1px solid var(--border)",
            borderRadius: "4px",
            color: "var(--fg)",
            cursor: "pointer",
            width: "24px",
            height: "24px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "12px",
            marginBottom: "16px",
          }}
          title="Expand Activity Panel"
        >
          ◀
        </button>

        {unreadCount > 0 ? (
          <div
            style={{
              background: "var(--accent)",
              color: "#fff",
              borderRadius: "10px",
              padding: "2px 5px",
              fontSize: "10px",
              fontWeight: 700,
              marginBottom: "12px",
              boxShadow: "0 0 8px rgba(79, 142, 247, 0.6)",
            }}
          >
            {unreadCount}
          </div>
        ) : activeCount > 0 ? (
          <div
            style={{
              background: "rgba(16, 185, 129, 0.2)",
              border: "1px solid var(--green)",
              color: "var(--green)",
              borderRadius: "10px",
              padding: "2px 5px",
              fontSize: "9px",
              fontWeight: 700,
              marginBottom: "12px",
            }}
          >
            {currentPct.toFixed(0)}%
          </div>
        ) : null}

        <div
          style={{
            writingMode: "vertical-rl",
            transform: "rotate(180deg)",
            fontSize: "11px",
            fontWeight: 700,
            letterSpacing: "1.5px",
            color: activeCount > 0 ? "var(--green)" : "var(--fg-dim)",
            display: "flex",
            alignItems: "center",
            gap: "8px",
          }}
        >
          <span>
            {activeCount > 0 ? `● LIVE ACTIVITY • ${activeCount} ACTIVE` : `LIVE ACTIVITY • ${currentPct.toFixed(0)}%`}
          </span>
        </div>
      </div>
    );
  }

  // 4. EXPANDED VIEW: Rich operational diagnostics, progress bars, subtask ticks (User Spec §6-§10)
  return (
    <div
      className="live-activity-sidebar"
      style={{
        width: "360px",
        minWidth: "320px",
        maxWidth: "420px",
        background: "linear-gradient(180deg, #131b2a 0%, #0c121e 100%)",
        borderLeft: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        zIndex: 90,
        boxShadow: "-4px 0 16px rgba(0,0,0,0.3)",
        flexShrink: 0,
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "10px 14px",
          borderBottom: "1px solid var(--border)",
          background: "rgba(0,0,0,0.25)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontSize: "14px", color: activeCount > 0 ? "var(--green)" : "var(--accent)" }}>
            {activeCount > 0 ? "●" : "⚡"}
          </span>
          <span style={{ fontSize: "12px", fontWeight: 700, letterSpacing: "0.5px" }}>
            LIVE ACTIVITY
          </span>
          <span className="muted" style={{ fontSize: "11px" }}>
            ({filteredEvents.length} events)
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <button
            onClick={handleCopyLog}
            className="btn btn-sm"
            style={{
              fontSize: "10.5px",
              padding: "2px 8px",
              background: copyStatus ? "rgba(40,167,69,0.2)" : "var(--bg-box)",
              borderColor: copyStatus ? "var(--green)" : "var(--border)",
              color: copyStatus ? "var(--green)" : "var(--fg)",
              fontWeight: 600,
            }}
            title="Copy recent diagnostic log to clipboard"
          >
            {copyStatus || "📋 COPY LOG"}
          </button>
          <button
            onClick={onToggle}
            style={{
              background: "var(--bg-box)",
              border: "1px solid var(--border)",
              borderRadius: "4px",
              color: "var(--fg)",
              cursor: "pointer",
              width: "24px",
              height: "24px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: "12px",
            }}
            title="Collapse Activity Panel"
          >
            ▶
          </button>
        </div>
      </div>

      {/* Active Task Operational Diagnostic (User Spec §6, §25) */}
      {activeTaskObj && (
        <div
          style={{
            margin: "8px 10px",
            background: "rgba(79, 142, 247, 0.08)",
            border: "1px solid rgba(79, 142, 247, 0.3)",
            borderRadius: "6px",
            padding: "8px 10px",
            fontSize: "11.5px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <span style={{ color: "#38bdf8", fontSize: "12px", fontWeight: 700 }}>▶</span>
              <span style={{ fontSize: "10.5px", fontWeight: 700, color: "var(--accent)", letterSpacing: "0.5px" }}>
                ACTIVE TASK
              </span>
            </div>
            <span className="mono" style={{ fontSize: "11px", fontWeight: 700, color: "var(--accent)" }}>
              {formatDuration(activeTaskObj.elapsed_seconds || elapsedSeconds)}
            </span>
          </div>

          <div style={{ fontWeight: 600, color: "var(--fg)", marginBottom: "4px", wordBreak: "break-all" }}>
            {activeTaskObj.message || activeTaskObj.name || "Processing..."}
          </div>

          {activeTaskObj.current_item && (
            <div className="mono muted" style={{ fontSize: "10.5px", marginBottom: "4px" }}>
              Operation: {activeTaskObj.current_item}
            </div>
          )}

          {/* Subtask count & character progress bar (spec §25) */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "10.5px", fontWeight: 700, margin: "2px 0" }}>
            <span className="mono" style={{ color: "var(--fg)" }}>
              {activeTaskObj.total > 0 ? `${activeTaskObj.current || 0} / ${activeTaskObj.total}` : `${currentPct.toFixed(0)}%`}
            </span>
            <span className="mono" style={{ color: "#38bdf8" }}>{currentPct.toFixed(1)}%</span>
          </div>

          <div style={{ fontFamily: "monospace", fontSize: "11px", letterSpacing: "1px", color: "#38bdf8", margin: "1px 0 4px 0" }}>
            {(() => {
              const filled = Math.min(16, Math.max(0, Math.round((currentPct / 100) * 16)));
              const empty = Math.max(0, 16 - filled);
              return "█".repeat(filled) + "░".repeat(empty);
            })()}
          </div>

          {/* Graphical Progress Bar */}
          <div style={{ display: "flex", alignItems: "center", gap: "8px", margin: "4px 0" }}>
            <div style={{ flex: 1, background: "rgba(255,255,255,0.08)", height: "5px", borderRadius: "3px", overflow: "hidden" }}>
              <div
                style={{
                  width: `${Math.min(100, Math.max(0, currentPct))}%`,
                  height: "100%",
                  background: "linear-gradient(90deg, #10b981 0%, #3b82f6 100%)",
                  transition: "width 0.3s ease",
                }}
              />
            </div>
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "10px", color: "var(--muted)", marginTop: "4px" }}>
            <span>Throughput: {activeTaskObj.throughput || "Active"}</span>
            <span>Last activity: {lastActivityAge < 1 ? "just now" : `${lastActivityAge.toFixed(1)}s ago`}</span>
          </div>
        </div>
      )}

      {/* Node Progress Banner (User Spec §6) */}
      {(() => {
        const curNodes = stageState?.current_nodes ?? 0;
        const targetNodes = stageState?.node_target ?? 1000;
        const pct = targetNodes > 0 ? (curNodes / targetNodes) * 100 : 0;
        return (
          <div
            style={{
              margin: "8px 10px 4px 10px",
              background: "rgba(16, 185, 129, 0.08)",
              border: "1px solid rgba(16, 185, 129, 0.3)",
              borderRadius: "6px",
              padding: "7px 10px",
              fontSize: "11px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
              <span style={{ fontWeight: 700, color: "var(--green)", letterSpacing: "0.5px" }}>
                NODE GENERATION {curNodes} / {targetNodes}
              </span>
              <span className="mono font-bold" style={{ color: "var(--fg)", fontSize: "11px" }}>
                {pct.toFixed(1)}%
              </span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", color: "var(--muted)", marginBottom: "4px" }}>
              <span>GEN {stageState?.generation ?? 0}</span>
              <span>{stageState?.remaining_nodes ?? Math.max(0, targetNodes - curNodes)} REMAINING</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "2px", fontSize: "9.5px", color: "var(--muted)", marginBottom: "6px" }}>
              <span>Alive: <b style={{ color: "var(--green)" }}>{stageState?.alive_nodes ?? 0}</b></span>
              <span>Dead: <b style={{ color: "var(--red)" }}>{stageState?.dead_nodes ?? 0}</b></span>
              <span>Backtest: <b style={{ color: "var(--accent)" }}>{stageState?.backtesting_nodes ?? 0}</b></span>
              <span>Validating: <b style={{ color: "var(--amber)" }}>{stageState?.validating_nodes ?? 0}</b></span>
              <span>Qualified: <b style={{ color: "var(--green)" }}>{stageState?.qualified_nodes ?? 0}</b></span>
              <span>Stage: <b style={{ color: "var(--fg)" }}>{stageState?.current_stage ?? "–"}</b></span>
            </div>
            <div style={{ background: "rgba(255,255,255,0.08)", height: "6px", borderRadius: "3px", overflow: "hidden" }}>
              <div
                style={{
                  width: `${Math.min(100, Math.max(0, pct))}%`,
                  height: "100%",
                  background: "linear-gradient(90deg, #10b981 0%, #3b82f6 100%)",
                  transition: "width 0.4s ease",
                }}
              />
            </div>
          </div>
        );
      })()}

      {/* Major Pipeline Progress Overview (User Spec §25) */}
      <div
        style={{
          margin: "4px 10px 8px 10px",
          background: "rgba(0,0,0,0.2)",
          border: "1px solid var(--border)",
          borderRadius: "6px",
          padding: "6px 10px",
          fontSize: "11px",
        }}
      >
        <div style={{ fontSize: "10px", fontWeight: 700, color: "var(--muted)", marginBottom: "4px", letterSpacing: "0.5px" }}>
          STAGE PROGRESS:
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
          <PipelineStageRow
            label="DATA DISCOVERY"
            status="COMPLETE"
            pct={100}
          />
          <PipelineStageRow
            label="DATA SYNC"
            status="COMPLETE"
            pct={100}
          />
          <PipelineStageRow
            label="FEATURE DISCOVERY"
            status="COMPLETE"
            pct={100}
          />
          <PipelineStageRow
            label="FEATURE COMPUTATION"
            status={stageState?.current_stage === "FEATURE_PRECOMPUTATION" ? "RUNNING" : "COMPLETE"}
            pct={stageState?.current_stage === "FEATURE_PRECOMPUTATION" ? (currentPct || 67) : 100}
          />
          <PipelineStageRow
            label="NODE GENERATION"
            status={["RESEARCH", "EVOLUTION"].includes(stageState?.current_stage) ? "RUNNING" : ((stageState?.current_nodes ?? 0) >= (stageState?.node_target ?? 1000) ? "COMPLETE" : "WAITING")}
            pct={stageState?.node_target > 0 ? Math.min(100, ((stageState?.current_nodes ?? 0) / stageState.node_target) * 100) : 0}
          />
          <PipelineStageRow
            label="EVALUATION"
            status={["BACKTESTING", "VALIDATION", "QUALIFICATION"].includes(stageState?.current_stage) ? "RUNNING" : ((stageState?.current_nodes ?? 0) >= (stageState?.node_target ?? 1000) ? "COMPLETE" : "WAITING")}
            pct={["BACKTESTING", "VALIDATION", "QUALIFICATION"].includes(stageState?.current_stage) ? (currentPct || 50) : ((stageState?.current_nodes ?? 0) >= (stageState?.node_target ?? 1000) ? 100 : 0)}
          />
        </div>
      </div>

      {/* Filter Tabs (User Spec §10) */}
      <div
        style={{
          display: "flex",
          gap: "4px",
          padding: "6px 10px",
          borderBottom: "1px solid var(--border)",
          borderTop: "1px solid var(--border)",
          overflowX: "auto",
          scrollbarWidth: "none",
        }}
      >
        {["Newest", "Active", "Completed", "Warnings", "Errors", "Oldest"].map((cat) => (
          <button
            key={cat}
            onClick={() => setFilterCat(cat)}
            style={{
              padding: "2px 7px",
              fontSize: "10.5px",
              fontWeight: filterCat === cat ? 700 : 500,
              background: filterCat === cat ? "rgba(79, 142, 247, 0.2)" : "transparent",
              border: filterCat === cat ? "1px solid var(--accent)" : "1px solid transparent",
              borderRadius: "4px",
              color: filterCat === cat ? "var(--fg)" : "var(--muted)",
              cursor: "pointer",
              whiteSpace: "nowrap",
            }}
          >
            {cat}
          </button>
        ))}
      </div>

      {/* Event Stream (Newest First + Subtask Ticks) */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "8px 10px",
          display: "flex",
          flexDirection: "column",
          gap: "6px",
        }}
      >
        {filteredEvents.length === 0 ? (
          <div className="muted" style={{ textAlign: "center", padding: "24px", fontSize: "11px" }}>
            No activity logged yet.
          </div>
        ) : (
          filteredEvents.map((ev, idx) => {
            const isExpanded = expandedEventId === (ev.ts || idx);
            const isError = ev.level === "ERROR" || ev.status === "FAILED";
            const isWarning = ev.level === "WARNING" || ev.status === "WARNING";
            const isSuccess = ev.level === "SUCCESS" || ev.status === "SUCCESS" || ev.status === "COMPLETE";
            const isRunning = ev.status === "RUNNING" || ev.status === "STARTED";

            let icon = "✓";
            let iconColor = "var(--green)";
            if (isError) { icon = "✕"; iconColor = "var(--red)"; }
            else if (isWarning) { icon = "⚠"; iconColor = "var(--amber)"; }
            else if (isRunning) { icon = "●"; iconColor = "var(--accent)"; }

            return (
              <div
                key={ev.ts || idx}
                onClick={() => setExpandedEventId(isExpanded ? null : (ev.ts || idx))}
                style={{
                  background: isExpanded ? "var(--bg-box)" : "rgba(255,255,255,0.02)",
                  border: `1px solid ${isExpanded ? "var(--accent)" : "rgba(255,255,255,0.05)"}`,
                  borderRadius: "5px",
                  padding: "6px 8px",
                  fontSize: "11px",
                  cursor: "pointer",
                  transition: "background 0.15s ease",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "3px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                    <span style={{ color: iconColor, fontSize: "11px", fontWeight: 700 }}>
                      {icon}
                    </span>
                    <span
                      style={{
                        fontSize: "9.5px",
                        fontWeight: 700,
                        color: "var(--fg-dim)",
                        background: "rgba(255,255,255,0.05)",
                        padding: "1px 4px",
                        borderRadius: "3px",
                      }}
                    >
                      {ev.category || "SYSTEM"}
                    </span>
                  </div>
                  <span className="mono muted" style={{ fontSize: "10px" }}>
                    {ev.time_str || fmt.ts(ev.ts)}
                  </span>
                </div>

                <div
                  style={{
                    color: isError ? "var(--red)" : isWarning ? "var(--amber)" : "var(--fg)",
                    lineHeight: "1.3",
                    wordBreak: "break-word",
                  }}
                >
                  {ev.message}
                </div>

                {/* Inline Progress Bar for operations with percentages */}
                {ev.progress != null && ev.progress > 0 && ev.progress <= 100 && (
                  <div style={{ marginTop: "4px", display: "flex", alignItems: "center", gap: "6px" }}>
                    <div style={{ flex: 1, background: "rgba(255,255,255,0.06)", height: "3px", borderRadius: "2px" }}>
                      <div
                        style={{
                          width: `${ev.progress}%`,
                          height: "100%",
                          background: isSuccess ? "var(--green)" : "var(--accent)",
                        }}
                      />
                    </div>
                    <span className="mono muted" style={{ fontSize: "9.5px" }}>
                      {ev.progress.toFixed(0)}%
                    </span>
                  </div>
                )}

                {isExpanded && ev.details && Object.keys(ev.details).length > 0 && (
                  <pre
                    className="mono"
                    style={{
                      marginTop: "6px",
                      padding: "6px",
                      background: "rgba(0,0,0,0.4)",
                      borderRadius: "4px",
                      fontSize: "10px",
                      color: "var(--muted)",
                      whiteSpace: "pre-wrap",
                      maxHeight: "120px",
                      overflowY: "auto",
                    }}
                  >
                    {JSON.stringify(ev.details, null, 2)}
                  </pre>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function PipelineStageRow({ label, status, pct }) {
  const isComplete = status === "COMPLETE" || status === "COMPLETED";
  const isRunning = status === "RUNNING";
  const isFailed = status === "FAILED";
  const symbol = isComplete ? "✓" : (isRunning ? "▶" : (isFailed ? "✕" : "○"));
  const color = isComplete ? "var(--green)" : (isRunning ? "#38bdf8" : (isFailed ? "var(--red)" : "var(--muted)"));

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "10px", margin: "1px 0" }}>
      <span style={{ color: color, width: "14px", fontWeight: 700, fontSize: "11px" }}>
        {symbol}
      </span>
      <span style={{ width: "135px", color: isComplete ? "var(--fg)" : (isRunning ? "#38bdf8" : "var(--muted)"), fontWeight: isRunning ? 700 : 500, fontSize: "9.5px", letterSpacing: "0.2px" }}>
        {label}
      </span>
      <div style={{ flex: 1, background: "rgba(255,255,255,0.06)", height: "4px", borderRadius: "2px", overflow: "hidden" }}>
        <div
          style={{
            width: `${Math.min(100, Math.max(0, pct))}%`,
            height: "100%",
            background: isComplete ? "var(--green)" : (isRunning ? "linear-gradient(90deg, #10b981 0%, #38bdf8 100%)" : "transparent"),
            transition: "width 0.3s ease",
          }}
        />
      </div>
      <span style={{ width: "36px", textAlign: "right", color: color, fontWeight: 700, fontSize: "9.5px", fontFamily: "monospace" }}>
        {Math.round(pct)}%
      </span>
    </div>
  );
}
