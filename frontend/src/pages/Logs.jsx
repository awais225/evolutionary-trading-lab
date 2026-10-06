import React, { useEffect, useState, useMemo, useRef } from "react";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import { EventFeed } from "../components/common.jsx";

const LEVEL_COLORS = {
  ERROR: "#ef4444",
  CRITICAL: "#ef4444",
  WARNING: "#f59e0b",
  SUCCESS: "#10b981",
  INFO: "#3b82f6",
  DEBUG: "#94a3b8",
};

const FILTER_CATEGORIES = [
  "ALL",
  "INFO",
  "SUCCESS",
  "WARNING",
  "ERROR",
  "DEBUG",
  "DATA",
  "FEATURES",
  "NODE",
  "GPU",
  "MT5",
  "DATABASE",
  "SYSTEM",
];

export default function Logs() {
  const { events } = useLab();
  const [logs, setLogs] = useState([]);
  const [activeFilter, setActiveFilter] = useState("ALL");
  const [searchQuery, setSearchQuery] = useState("");
  const [tab, setTab] = useState("terminal");
  const [pipelineState, setPipelineState] = useState(null);
  const [copied, setCopied] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const scrollRef = useRef(null);
  const inFlightLoadRef = useRef(false);

  // Poll logs and pipeline state
  useEffect(() => {
    const load = () => {
      if (inFlightLoadRef.current) return;
      inFlightLoadRef.current = true;
      Promise.all([
        api.logs(600).then((r) => {
          if (r && Array.isArray(r.logs)) setLogs(r.logs);
        }).catch(() => {}),
        api.pipelineState().then((ps) => {
          if (ps) setPipelineState(ps);
        }).catch(() => {})
      ]).finally(() => {
        inFlightLoadRef.current = false;
      });
    };

    load();
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, []);

  // Auto scroll to bottom when new logs arrive
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  // V4.7: the export is built by a background job so the browser never waits on
  // a large log; the synchronous endpoint stays as the fallback path.
  const fetchExportText = async () => {
    if (api.exportLogsAsync) {
      try {
        const started = await api.exportLogsAsync();
        const jobId = started?.job_id;
        if (jobId) {
          for (let i = 0; i < 20; i += 1) {
            await new Promise((r) => setTimeout(r, 400));
            try {
              const done = await api.exportLogsResult(jobId);
              if (done?.log_text) return done.log_text;
            } catch (e) {
              const msg = String(e?.message || "");
              if (msg.includes("404") || msg.includes("failed")) break;   // fall through to sync
            }
          }
        }
      } catch (e) {
        // fall through to the synchronous export
      }
    }
    const res = await api.exportLogs();
    return res?.log_text;
  };

  const handleCopyLogs = async () => {
    try {
      const res = { log_text: await fetchExportText() };
      const text = res?.log_text || logs.map((l) => `${l.ts} [${l.level}] [${l.module || l.logger}] ${l.message}`).join("\n");
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
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch (e) {
      // Fallback to local logs
      const text = logs.map((l) => `${l.ts} [${l.level}] [${l.module || l.logger}] ${l.message}`).join("\n");
      navigator.clipboard.writeText(text).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2500);
      }).catch(() => {});
    }
  };

  const filteredLogs = useMemo(() => {
    let list = [...logs];

    if (activeFilter !== "ALL") {
      const fl = activeFilter.toUpperCase();
      if (["INFO", "SUCCESS", "WARNING", "ERROR", "DEBUG"].includes(fl)) {
        list = list.filter((l) => (l.level || "INFO").toUpperCase() === fl);
      } else {
        list = list.filter((l) => {
          const cat = (l.category || "").toUpperCase();
          const mod = (l.module || "").toUpperCase();
          const msg = (l.message || "").toUpperCase();
          return cat === fl || mod.includes(fl) || msg.includes(fl);
        });
      }
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter((l) => {
        return (
          (l.message && l.message.toLowerCase().includes(q)) ||
          (l.module && l.module.toLowerCase().includes(q)) ||
          (l.dataset && l.dataset.toLowerCase().includes(q)) ||
          (l.action && l.action.toLowerCase().includes(q)) ||
          (l.stage && l.stage.toLowerCase().includes(q))
        );
      });
    }

    return list;
  }, [logs, activeFilter, searchQuery]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 120px)" }}>
      {/* Top Title & Pipeline Status Banner */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10, flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h2 className="page-title" style={{ margin: 0 }}>Diagnostic Execution Console</h2>
            <span className="pill" style={{ background: "rgba(59, 130, 246, 0.15)", color: "var(--accent)", border: "1px solid rgba(59, 130, 246, 0.3)", fontSize: "0.75rem", fontWeight: 700 }}>
              TECHNICAL LOGS
            </span>
          </div>
          <div className="page-sub" style={{ margin: "3px 0 0 0" }}>
            Real-time pipeline execution, subtask ticks, dataset reuse decisions, and full error tracebacks.
          </div>
        </div>

        {/* Global Pipeline Stage Badge */}
        {pipelineState && (
          <div style={{
            background: "rgba(19, 27, 42, 0.9)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "6px 12px",
            display: "flex",
            alignItems: "center",
            gap: 12,
            fontSize: "0.8rem",
          }}>
            <div>
              <div className="muted" style={{ fontSize: "0.68rem" }}>PIPELINE STAGE</div>
              <div style={{ fontWeight: 700, color: "var(--accent)", fontFamily: "monospace" }}>
                [{pipelineState.stage_index}/8] {pipelineState.stage_name}
              </div>
            </div>
            <div style={{ borderLeft: "1px solid var(--border)", paddingLeft: 10 }}>
              <div className="muted" style={{ fontSize: "0.68rem" }}>NODE PROGRESS</div>
              <div style={{ fontWeight: 700, color: "var(--green)", fontFamily: "monospace" }}>
                {pipelineState.node_completed} / {pipelineState.node_total} ({pipelineState.overall_percent}%)
              </div>
            </div>
            <div style={{ borderLeft: "1px solid var(--border)", paddingLeft: 10 }}>
              <div className="muted" style={{ fontSize: "0.68rem" }}>RESOURCES</div>
              <div style={{ fontWeight: 600, color: "var(--fg)", fontFamily: "monospace", fontSize: "0.75rem" }}>
                CPU: {pipelineState.cpu_usage}% · GPU: {pipelineState.gpu_status}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* 8-Stage Milestone Ribbon */}
      {pipelineState?.stages && (
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(8, 1fr)",
          gap: 4,
          marginBottom: 10,
          background: "rgba(0,0,0,0.3)",
          padding: 6,
          borderRadius: 6,
          border: "1px solid var(--border)",
          overflowX: "auto",
        }}>
          {pipelineState.stages.map((st) => {
            const isRunning = st.status === "RUNNING";
            const isDone = st.status === "COMPLETED";
            return (
              <div
                key={st.index}
                style={{
                  background: isRunning ? "rgba(59, 130, 246, 0.2)" : (isDone ? "rgba(16, 185, 129, 0.12)" : "rgba(255,255,255,0.03)"),
                  border: isRunning ? "1px solid var(--accent)" : (isDone ? "1px solid rgba(16, 185, 129, 0.3)" : "1px solid transparent"),
                  borderRadius: 4,
                  padding: "4px 6px",
                  textAlign: "center",
                  fontSize: "0.7rem",
                }}
              >
                <div style={{
                  color: isRunning ? "var(--accent)" : (isDone ? "var(--green)" : "var(--muted)"),
                  fontWeight: 700,
                  fontSize: "0.75rem",
                  marginBottom: 1,
                }}>
                  {st.symbol} [{st.index}/8]
                </div>
                <div style={{
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  color: isRunning ? "var(--fg)" : "var(--muted)",
                  fontWeight: isRunning ? 700 : 500,
                }}>
                  {st.label}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Tab controls */}
      <div className="tabs" style={{ marginBottom: 8 }}>
        <button className={tab === "terminal" ? "active" : ""} onClick={() => setTab("terminal")}>
          Execution Console ({filteredLogs.length})
        </button>
        <button className={tab === "events" ? "active" : ""} onClick={() => setTab("events")}>
          Raw Events Feed ({events.length})
        </button>
      </div>

      {tab === "terminal" && (
        <div className="panel" style={{ flex: 1, display: "flex", flexDirection: "column", padding: 10, minHeight: 0 }}>
          {/* Action Row: Copy Button, Search, Filters */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
            {/* Filter buttons */}
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap", alignItems: "center" }}>
              {FILTER_CATEGORIES.map((cat) => {
                const isSel = activeFilter === cat;
                return (
                  <button
                    key={cat}
                    onClick={() => setActiveFilter(cat)}
                    style={{
                      padding: "3px 8px",
                      fontSize: "0.75rem",
                      fontWeight: isSel ? 700 : 500,
                      background: isSel ? "var(--accent)" : "rgba(255,255,255,0.05)",
                      border: isSel ? "1px solid var(--accent)" : "1px solid var(--border)",
                      borderRadius: 4,
                      color: isSel ? "#fff" : "var(--fg)",
                      cursor: "pointer",
                      transition: "all 0.15s ease",
                    }}
                  >
                    {cat}
                  </button>
                );
              })}
            </div>

            {/* Right Controls: Search, Auto-scroll, Copy Button */}
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input
                type="text"
                placeholder="Search console logs..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{
                  padding: "4px 8px",
                  fontSize: "0.8rem",
                  background: "var(--bg-box)",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  color: "var(--fg)",
                  width: 170,
                }}
              />

              <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "0.75rem", color: "var(--muted)", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={autoScroll}
                  onChange={(e) => setAutoScroll(e.target.checked)}
                />
                Auto-scroll
              </label>

              {/* COPY LOG Button (spec §29) */}
              <button
                className="btn primary"
                onClick={handleCopyLogs}
                style={{
                  padding: "5px 12px",
                  fontSize: "0.8rem",
                  fontWeight: 700,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  background: copied ? "var(--green)" : undefined,
                  borderColor: copied ? "var(--green)" : undefined,
                }}
                title="Copy the entire available technical log to clipboard"
              >
                {copied ? "✓ LOG COPIED" : "📋 COPY LOG"}
              </button>
            </div>
          </div>

          {/* Technical Execution Console Log Stream */}
          <div
            ref={scrollRef}
            className="scroll-y mono"
            style={{
              flex: 1,
              background: "#080c14",
              border: "1px solid var(--border)",
              borderRadius: 4,
              padding: "8px 10px",
              fontSize: "11.5px",
              lineHeight: 1.5,
              overflowY: "auto",
            }}
          >
            {filteredLogs.map((l, i) => {
              const color = LEVEL_COLORS[l.level] || "var(--fg)";
              const isError = l.level === "ERROR" || l.level === "CRITICAL";
              return (
                <div
                  key={l.id || i}
                  style={{
                    borderBottom: "1px solid rgba(255,255,255,0.04)",
                    padding: "4px 0",
                    background: isError ? "rgba(239, 68, 68, 0.08)" : "transparent",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 2 }}>
                    <span style={{ color: "#64748b", fontSize: "0.7rem" }}>{l.ts || l.time_str}</span>
                    <span
                      style={{
                        color: "#fff",
                        background: color,
                        fontSize: "0.65rem",
                        fontWeight: 700,
                        padding: "1px 5px",
                        borderRadius: 3,
                      }}
                    >
                      {l.level || "INFO"}
                    </span>
                    <span style={{ color: "var(--accent)", fontWeight: 600, fontSize: "0.72rem" }}>
                      [{l.module || l.logger || "SYSTEM"}]
                    </span>
                    {l.stage && l.stage !== "PIPELINE" && (
                      <span className="pill" style={{ fontSize: "0.65rem", padding: "1px 5px" }}>
                        {l.stage}
                      </span>
                    )}
                    {l.dataset && (
                      <span style={{ color: "var(--amber)", fontSize: "0.7rem", fontWeight: 600 }}>
                        {l.dataset}
                      </span>
                    )}
                    {l.action && (
                      <span style={{ color: "var(--green)", fontSize: "0.7rem", fontWeight: 700 }}>
                        ACTION: {l.action}
                      </span>
                    )}
                    {l.progress && (
                      <span style={{ color: "#38bdf8", fontSize: "0.7rem", fontWeight: 600 }}>
                        {l.progress}
                      </span>
                    )}
                    {l.worker && (
                      <span style={{ color: "#94a3b8", fontSize: "0.68rem" }}>
                        {l.worker}
                      </span>
                    )}
                  </div>

                  <div style={{ whiteSpace: "pre-wrap", color: "var(--fg)", wordBreak: "break-word" }}>
                    {l.message}
                  </div>

                  {l.exc_text && (
                    <pre
                      style={{
                        margin: "4px 0 2px 0",
                        padding: 8,
                        background: "rgba(0,0,0,0.4)",
                        border: "1px solid #ef4444",
                        borderRadius: 3,
                        color: "#fca5a5",
                        fontSize: "10.5px",
                        overflowX: "auto",
                      }}
                    >
                      {l.exc_text}
                    </pre>
                  )}
                </div>
              );
            })}

            {filteredLogs.length === 0 && (
              <div style={{ textAlign: "center", color: "var(--muted)", padding: 30 }}>
                No technical log entries match the current filter or search query.
              </div>
            )}
          </div>
        </div>
      )}

      {tab === "events" && (
        <div className="panel" style={{ flex: 1, overflowY: "auto" }}>
          <EventFeed events={events} limit={300} />
        </div>
      )}
    </div>
  );
}
