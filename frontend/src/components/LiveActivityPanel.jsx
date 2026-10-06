import React, { useEffect, useState, useMemo, useRef } from "react";
import { api, fmt } from "../api.js";
import { useLabEvents } from "../ws.js";

export default function LiveActivityPanel({ status, onOpenMt5Modal }) {
  const [events, setEvents] = useState([]);
  const [currentTask, setCurrentTask] = useState(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [filterCat, setFilterCat] = useState("Newest");
  const [copyStatus, setCopyStatus] = useState("");
  const [isExpanded, setIsExpanded] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState(null);
  const { events: wsEvents, connected: wsConnected } = useLabEvents(250);

  // 1. Initial history fetch
  useEffect(() => {
    api.activity({ limit: 120 })
      .then((data) => {
        if (Array.isArray(data)) setEvents(data);
      })
      .catch(() => {});

    api.currentTask()
      .then((task) => {
        if (task) setCurrentTask(task);
      })
      .catch(() => {});
  }, []);

  // 2. Real-time WebSocket event ingestion
  useEffect(() => {
    if (!wsEvents || wsEvents.length === 0) return;
    const latest = wsEvents[0];
    if (latest?.type === "activity" && latest.payload) {
      setEvents((prev) => {
        const item = latest.payload;
        // Avoid duplicate by timestamp & message
        if (prev.length > 0 && prev[0].ts === item.ts && prev[0].message === item.message) {
          return prev;
        }
        return [item, ...prev].slice(0, 300);
      });
      // Update task if event has status
      if (latest.payload.status === "RUNNING" || latest.payload.status === "STARTED") {
        setCurrentTask({
          name: latest.payload.message,
          operation_id: latest.payload.operation_id,
          started_at: latest.payload.ts,
          started_time: latest.payload.time_str || fmt.ts(latest.payload.ts),
          status: "RUNNING",
          category: latest.payload.category,
          progress: latest.payload.progress,
        });
      } else if (latest.payload.status === "SUCCESS") {
        setCurrentTask((prev) => {
          if (!prev || prev.operation_id === latest.payload.operation_id || prev.status === "RUNNING") {
            return { name: "Idle", status: "IDLE", operation_id: null, started_at: null };
          }
          return prev;
        });
      } else if (latest.payload.status === "FAILED") {
        setCurrentTask({
          name: `${latest.payload.message} failed`,
          operation_id: latest.payload.operation_id,
          started_at: latest.payload.ts,
          started_time: latest.payload.time_str || fmt.ts(latest.payload.ts),
          status: "FAILED",
          category: latest.payload.category,
          reason: latest.payload.details?.reason || latest.payload.message,
        });
      }
    } else if (latest?.type === "current_task" && latest.payload) {
      setCurrentTask(latest.payload);
    }
  }, [wsEvents]);

  // 3. Keep elapsed timer running when task is active
  useEffect(() => {
    if (!currentTask || currentTask.status !== "RUNNING" || !currentTask.started_at) {
      setElapsedSeconds(0);
      return;
    }
    const updateElapsed = () => {
      const now = Date.now() / 1000;
      setElapsedSeconds(Math.max(0, Math.floor(now - currentTask.started_at)));
    };
    updateElapsed();
    const timer = setInterval(updateElapsed, 1000);
    return () => clearInterval(timer);
  }, [currentTask]);

  // Trigger on-demand asynchronous data synchronization
  const handleTriggerSync = async () => {
    setSyncing(true);
    setSyncMsg("Requesting data synchronization...");
    try {
      await api.dataSync({ async: true });
      setSyncMsg("Sync started in background");
      setTimeout(() => setSyncMsg(null), 3000);
    } catch (e) {
      setSyncMsg(`Sync error: ${e.message}`);
    } finally {
      setSyncing(false);
    }
  };

  const conn = status?.connection || {};
  const bridge = status?.bridge || {};
  const lab = status?.lab || {};
  const isMt5Connected = bridge.connected && !bridge.is_simulated;
  const isAccountConnected = isMt5Connected && Boolean(conn.details?.account_masked || bridge.account);

  function formatDuration(sec) {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    const h = Math.floor(m / 60);
    return `${String(h).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  const handleCopyLog = async () => {
    let text = "";
    try {
      const res = await api.diagnosticLog(300);
      text = res?.diagnostic_text || "";
    } catch (e) {
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
      setCopyStatus("LOG COPIED");
      setTimeout(() => setCopyStatus(""), 3000);
    } catch (e) {
      setCopyStatus("FAILED");
      setTimeout(() => setCopyStatus(""), 3000);
    }
  };

  const filteredEvents = useMemo(() => {
    let list = [...events];
    const cat = filterCat;

    if (cat === "Oldest") {
      list.sort((a, b) => (a.ts || 0) - (b.ts || 0));
      return list;
    }

    // Default newest at top
    list.sort((a, b) => (b.ts || 0) - (a.ts || 0));

    if (cat === "Newest" || cat === "ALL") return list;
    if (cat === "Errors") return list.filter((e) => e.level === "ERROR" || e.status === "FAILED");
    if (cat === "Warnings") return list.filter((e) => e.level === "WARNING" || e.status === "WARNING");
    if (cat === "Active") return list.filter((e) => e.status === "RUNNING" || e.status === "STARTED" || e.status === "ACTIVE");
    if (cat === "MT5") return list.filter((e) => e.category === "MT5");
    if (cat === "Features") return list.filter((e) => e.category === "FEATURES" || e.category === "FEATURE");
    if (cat === "Workers") return list.filter((e) => ["WORKERS", "WORKER", "RESOURCE"].includes(e.category));
    if (cat === "Dataset") return list.filter((e) => ["DATA", "SYNC", "DATASET"].includes(e.category));
    if (cat === "Research") return list.filter((e) => ["RESEARCH", "EVOLUTION", "BACKTEST", "VALIDATION"].includes(e.category));
    if (cat === "System") return list.filter((e) => ["SYSTEM", "DATABASE", "GPU"].includes(e.category));

    return list.filter((e) => e.category === cat);
  }, [events, filterCat]);

  return (
    <div className="panel live-activity-panel" style={{
      marginBottom: "1rem",
      background: "linear-gradient(180deg, #131b2a 0%, #0c121e 100%)",
      border: "1px solid var(--border)",
      borderRadius: "6px",
      boxShadow: "0 4px 16px rgba(0,0,0,0.35)",
      padding: "12px 14px",
    }}>
      {/* 1. SYSTEM STATUS BAR (User Spec V2.6+ §12) */}
      <div className="flex justify-between items-center" style={{
        flexWrap: "wrap",
        gap: "10px",
        paddingBottom: "10px",
        borderBottom: "1px solid rgba(255,255,255,0.08)",
        marginBottom: "10px"
      }}>
        <div className="flex items-center gap-3 flex-wrap">
          <span style={{ fontSize: "0.78rem", fontWeight: 800, letterSpacing: "0.06em", color: "var(--fg-dim)" }}>
            SYSTEM STATUS:
          </span>

          {/* MT5 Status */}
          <div className="status-pill flex items-center gap-1"
               onClick={onOpenMt5Modal}
               title="Click to configure MetaTrader 5 connection"
               style={{ cursor: "pointer", fontSize: "0.78rem", padding: "2px 8px", background: "rgba(0,0,0,0.3)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <span style={{ color: isMt5Connected ? "var(--green)" : "var(--amber)" }}>●</span>
            <span style={{ fontWeight: 600 }}>MT5 {isMt5Connected ? "CONNECTED" : "DISCONNECTED"}</span>
          </div>

          {/* Account Status */}
          <div className="status-pill flex items-center gap-1"
               style={{ fontSize: "0.78rem", padding: "2px 8px", background: "rgba(0,0,0,0.3)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <span style={{ color: isAccountConnected ? "var(--green)" : "var(--muted)" }}>●</span>
            <span style={{ fontWeight: 600 }}>
              ACCOUNT {isAccountConnected ? (conn.details?.account_masked || bridge.account || "CONNECTED") : "NONE"}
            </span>
          </div>

          {/* Market Data Status */}
          <div className="status-pill flex items-center gap-1"
               style={{ fontSize: "0.78rem", padding: "2px 8px", background: "rgba(0,0,0,0.3)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <span style={{ color: conn.data_feed === "LIVE" ? "var(--green)" : (conn.data_feed === "STALE" ? "var(--amber)" : "var(--red)") }}>●</span>
            <span style={{ fontWeight: 600 }}>MARKET DATA {conn.data_feed || "LIVE"}</span>
          </div>

          {/* Database Status */}
          <div className="status-pill flex items-center gap-1"
               style={{ fontSize: "0.78rem", padding: "2px 8px", background: "rgba(0,0,0,0.3)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <span style={{ color: "var(--green)" }}>●</span>
            <span style={{ fontWeight: 600 }}>DATABASE READY</span>
          </div>

          {/* Research Status */}
          <div className="status-pill flex items-center gap-1"
               style={{ fontSize: "0.78rem", padding: "2px 8px", background: "rgba(0,0,0,0.3)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <span style={{ color: lab?.running ? (lab?.paused ? "var(--amber)" : "var(--green)") : "var(--muted)" }}>●</span>
            <span style={{ fontWeight: 600 }}>RESEARCH {lab?.running ? (lab?.paused ? "PAUSED" : "RUNNING") : "IDLE"}</span>
          </div>

          {/* Market Bridge Mode */}
          <div className="status-pill flex items-center gap-1"
               style={{ fontSize: "0.78rem", padding: "2px 8px", background: "rgba(0,0,0,0.3)", borderRadius: "4px", border: "1px solid var(--border)" }}>
            <span style={{ color: isMt5Connected ? "var(--green)" : "var(--amber)" }}>●</span>
            <span style={{ fontWeight: 600 }}>
              BRIDGE: {isMt5Connected ? "MT5 REAL" : "SIMULATOR"}
            </span>
          </div>
        </div>

        {/* Sync Trigger Action */}
        <div className="flex items-center gap-2">
          {syncMsg && <span className="mono muted" style={{ fontSize: "0.75rem" }}>{syncMsg}</span>}
          <button
            className="btn btn-xs"
            disabled={syncing}
            onClick={handleTriggerSync}
            title="Trigger full market data synchronization across all timeframes"
            style={{ padding: "3px 10px", fontSize: "0.75rem", background: "#1e293b", borderColor: "#334155" }}
          >
            {syncing ? "↻ SYNCING..." : "↻ SYNC MARKET DATA"}
          </button>
        </div>
      </div>

      {/* 2. CURRENT TASK INDICATOR (User Spec V2.6+ §12) */}
      <div className="current-task-box flex justify-between items-center" style={{
        padding: "6px 10px",
        background: "rgba(0, 0, 0, 0.35)",
        borderRadius: "4px",
        border: "1px solid rgba(255, 255, 255, 0.05)",
        marginBottom: "10px",
        fontSize: "0.82rem"
      }}>
        <div className="flex items-center gap-2" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          <span className="muted" style={{ fontWeight: 700, fontSize: "0.75rem" }}>CURRENT TASK:</span>
          {currentTask?.status === "RUNNING" && (
            <span style={{ color: "var(--blue)", animation: "pulse 1.5s infinite" }}>⚙</span>
          )}
          {currentTask?.status === "FAILED" && (
            <span style={{ color: "var(--red)" }}>✗</span>
          )}
          <span style={{
            fontWeight: 600,
            color: currentTask?.status === "FAILED" ? "var(--red)" : (currentTask?.status === "RUNNING" ? "var(--fg)" : "var(--muted)")
          }}>
            {currentTask?.name || "Idle"}
          </span>
          {currentTask?.category && currentTask?.status === "RUNNING" && (
            <span className="pill" style={{ fontSize: "0.68rem", padding: "1px 5px", background: "#1e3a5f" }}>
              {currentTask.category}
            </span>
          )}
        </div>

        <div className="flex items-center gap-3 mono" style={{ fontSize: "0.78rem" }}>
          {currentTask?.started_time && (
            <div>
              <span className="muted">Started: </span>
              <b>{currentTask.started_time}</b>
            </div>
          )}
          {currentTask?.status === "RUNNING" && (
            <div>
              <span className="muted">Elapsed: </span>
              <b style={{ color: "var(--blue)" }}>{formatDuration(elapsedSeconds)}</b>
            </div>
          )}
        </div>
      </div>

      {/* 3. LIVE ACTIVITY STREAM HEADER & CONTROLS */}
      <div className="flex justify-between items-center" style={{ marginBottom: "8px" }}>
        <div className="flex items-center gap-2">
          <span style={{ fontWeight: 700, fontSize: "0.84rem", letterSpacing: "0.04em", color: "var(--fg)" }}>
            ⚡ LIVE ACTIVITY
          </span>
          <span className="mono muted" style={{ fontSize: "0.75rem" }}>
            ({filteredEvents.length} events {wsConnected ? "· live" : "· reconnecting"})
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* V2.7 Filter controls: Newest, Oldest, Errors, Warnings, Active, MT5, Features, Workers, Dataset, Research, System */}
          <div className="flex gap-1" style={{ flexWrap: "wrap" }}>
            {[
              "Newest", "Oldest", "Errors", "Warnings", "Active",
              "MT5", "Features", "Workers", "Dataset", "Research", "System"
            ].map((cat) => (
              <button
                key={cat}
                onClick={() => setFilterCat(cat)}
                style={{
                  padding: "2px 7px",
                  fontSize: "0.7rem",
                  borderRadius: "3px",
                  border: "1px solid var(--border)",
                  background: filterCat === cat ? "var(--blue)" : "rgba(255,255,255,0.04)",
                  color: filterCat === cat ? "#fff" : "var(--fg-dim)",
                  fontWeight: filterCat === cat ? 800 : 500,
                  cursor: "pointer",
                }}
              >
                {cat}
              </button>
            ))}
          </div>

          {/* COPY LOG Button (spec §V2.7) */}
          <button
            onClick={handleCopyLog}
            style={{
              padding: "2px 10px",
              fontSize: "0.72rem",
              fontWeight: 700,
              borderRadius: "3px",
              border: "1px solid var(--border)",
              background: copyStatus === "LOG COPIED" ? "#059669" : "#1e293b",
              color: copyStatus === "LOG COPIED" ? "#fff" : "var(--fg)",
              cursor: "pointer",
              transition: "all 0.2s ease",
            }}
            title="Copy formatted diagnostic log to clipboard"
          >
            {copyStatus === "LOG COPIED" ? "✓ LOG COPIED" : "📋 COPY LOG"}
          </button>

          <button
            onClick={() => setIsExpanded(!isExpanded)}
            style={{
              padding: "2px 8px",
              fontSize: "0.72rem",
              background: "transparent",
              border: "1px solid var(--border)",
              color: "var(--muted)",
              cursor: "pointer",
              borderRadius: "3px",
            }}
          >
            {isExpanded ? "▲ Collapse" : "▼ Expand"}
          </button>
        </div>
      </div>

      {/* 4. ACTIVITY FEED LIST (User Spec V2.6+ §5, §8, §9, §11) */}
      {isExpanded && (
        <div className="activity-stream-scroll" style={{
          maxHeight: "220px",
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: "6px",
          paddingRight: "4px",
        }}>
          {filteredEvents.length === 0 ? (
            <div className="muted" style={{ fontSize: "0.8rem", padding: "12px", textAlign: "center", fontStyle: "italic" }}>
              No operational events recorded yet.
            </div>
          ) : (
            filteredEvents.slice(0, 80).map((ev, idx) => {
              const isError = ev.level === "ERROR" || ev.status === "FAILED";
              const isSuccess = ev.level === "SUCCESS" || ev.status === "SUCCESS";
              const isRunning = ev.status === "RUNNING" || ev.status === "STARTED";
              const isWarning = ev.level === "WARNING" || ev.status === "WARNING";

              let icon = "✓";
              let iconColor = "var(--green)";
              if (isError) {
                icon = "✗";
                iconColor = "var(--red)";
              } else if (isRunning) {
                icon = ev.category === "FEATURES" ? "⚙" : "↓";
                iconColor = "var(--blue)";
              } else if (isWarning) {
                icon = "⚠";
                iconColor = "var(--amber)";
              }

              const timeStr = ev.time_str || fmt.ts(ev.ts);

              return (
                <div key={idx} style={{
                  padding: "6px 8px",
                  borderRadius: "4px",
                  background: isError ? "rgba(239, 68, 68, 0.12)" : "rgba(255, 255, 255, 0.025)",
                  border: `1px solid ${isError ? "rgba(239, 68, 68, 0.35)" : "rgba(255, 255, 255, 0.05)"}`,
                  fontSize: "0.82rem",
                }}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="mono muted" style={{ fontSize: "0.75rem", minWidth: "55px" }}>
                        {timeStr}
                      </span>
                      <span style={{ color: iconColor, fontWeight: 800, fontSize: "0.9rem" }}>
                        {icon}
                      </span>
                      <span className="pill" style={{
                        fontSize: "0.68rem",
                        padding: "1px 5px",
                        background: isError ? "rgba(239, 68, 68, 0.3)" : "rgba(255,255,255,0.08)",
                        color: isError ? "var(--red)" : "var(--fg-dim)",
                      }}>
                        {ev.category}
                      </span>
                      <span style={{
                        fontWeight: 600,
                        color: isError ? "#fca5a5" : "var(--fg)",
                      }}>
                        {ev.message}
                      </span>
                    </div>

                    {/* Progress percentage if available */}
                    {ev.progress != null && (
                      <div className="flex items-center gap-2 mono" style={{ fontSize: "0.75rem" }}>
                        <div style={{
                          width: "70px",
                          height: "6px",
                          background: "rgba(255,255,255,0.1)",
                          borderRadius: "3px",
                          overflow: "hidden"
                        }}>
                          <div style={{
                            width: `${Math.min(100, Math.max(0, ev.progress))}%`,
                            height: "100%",
                            background: isSuccess ? "var(--green)" : "var(--blue)",
                          }} />
                        </div>
                        <span className="muted">{Math.round(ev.progress)}%</span>
                      </div>
                    )}
                  </div>

                  {/* Rich error breakdown (User Spec V2.6+ §11) */}
                  {isError && ev.details && (
                    <div style={{
                      marginTop: "5px",
                      marginLeft: "65px",
                      padding: "6px 8px",
                      borderRadius: "3px",
                      background: "rgba(0, 0, 0, 0.4)",
                      border: "1px solid rgba(239, 68, 68, 0.25)",
                      fontSize: "0.76rem",
                      display: "flex",
                      flexDirection: "column",
                      gap: "3px",
                    }}>
                      {ev.details.reason && (
                        <div>
                          <span className="muted">Reason: </span>
                          <span className="mono" style={{ color: "var(--red)" }}>{ev.details.reason}</span>
                        </div>
                      )}
                      {ev.operation_id && (
                        <div>
                          <span className="muted">Operation: </span>
                          <span className="mono">{ev.operation_id}</span>
                        </div>
                      )}
                      {ev.details.platform && (
                        <div>
                          <span className="muted">Platform: </span>
                          <span className="mono">{ev.details.platform}</span>
                        </div>
                      )}
                      {ev.details.next_action && (
                        <div>
                          <span className="muted">Next action: </span>
                          <span>{ev.details.next_action}</span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
