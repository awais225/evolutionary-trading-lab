import React, { useState, useEffect, useRef } from "react";
import { api } from "../api.js";

export default function MilestoneProgressPath() {
  const [routeState, setRouteState] = useState(null);
  const [selectedMilestone, setSelectedMilestone] = useState(null);
  const inFlightRef = useRef(false);

  const fetchRoute = () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    api.workflowMilestones()
      .then((r) => setRouteState(r))
      .catch(() => {})
      .finally(() => { inFlightRef.current = false; });
  };

  useEffect(() => {
    fetchRoute();
    const interval = setInterval(fetchRoute, 2000);
    return () => clearInterval(interval);
  }, []);

  if (!routeState || !routeState.milestones) return null;

  const milestones = routeState.milestones;
  const fillPct = Math.min(100, Math.max(0, routeState.fill_percentage || 0));

  return (
    <div
      className="milestone-route-container"
      style={{
        background: "var(--bg-panel)",
        border: "1px solid var(--border)",
        borderRadius: "8px",
        padding: "10px 14px",
        marginBottom: "1rem",
        position: "relative",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.5px", color: "var(--muted)" }}>
            WORKFLOW ROUTE:
          </span>
          <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--fg)" }}>
            {routeState.active_milestone?.label || "Ready"}
          </span>
          {routeState.active_milestone?.current_task && (
            <span className="mono muted" style={{ fontSize: "11px" }}>
              — {routeState.active_milestone.current_task}
            </span>
          )}
        </div>
        <div style={{ fontSize: "11.5px", fontWeight: 600, color: "var(--accent)" }}>
          {fillPct.toFixed(1)}% Complete ({routeState.current_nodes ?? 0} / {routeState.target_nodes ?? 1000})
        </div>
      </div>

      {/* Metro Route Track */}
      <div style={{ position: "relative", padding: "12px 10px 24px 10px" }}>
        {/* Background Track Line */}
        <div
          style={{
            position: "absolute",
            top: "22px",
            left: "24px",
            right: "24px",
            height: "4px",
            background: "rgba(255, 255, 255, 0.08)",
            borderRadius: "2px",
            zIndex: 1,
          }}
        />

        {/* Dynamic Filled Route Line */}
        <div
          style={{
            position: "absolute",
            top: "22px",
            left: "24px",
            width: `calc((100% - 48px) * ${fillPct / 100})`,
            height: "4px",
            background: "linear-gradient(90deg, #10b981 0%, #3b82f6 80%, #60a5fa 100%)",
            borderRadius: "2px",
            zIndex: 2,
            transition: "width 0.4s ease",
            boxShadow: "0 0 8px rgba(59, 130, 246, 0.5)",
          }}
        />

        {/* Milestone Stops */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            position: "relative",
            zIndex: 3,
          }}
        >
          {milestones.map((m, idx) => {
            const isComplete = m.status === "COMPLETE";
            const isRunning = m.status === "RUNNING";
            const isFailed = m.status === "FAILED";

            let nodeBg = "var(--bg-box)";
            let nodeBorder = "var(--border)";
            let nodeColor = "var(--muted)";

            if (isComplete) {
              nodeBg = "#10b981";
              nodeBorder = "#10b981";
              nodeColor = "#fff";
            } else if (isRunning) {
              nodeBg = "var(--bg-box)";
              nodeBorder = "#3b82f6";
              nodeColor = "#3b82f6";
            } else if (isFailed) {
              nodeBg = "#ef4444";
              nodeBorder = "#ef4444";
              nodeColor = "#fff";
            }

            return (
              <div
                key={m.id}
                onClick={() => setSelectedMilestone(m)}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  cursor: "pointer",
                  width: "70px",
                  textAlign: "center",
                }}
                title={`Click to view ${m.label} details`}
              >
                {/* Node Circle */}
                <div
                  style={{
                    width: "24px",
                    height: "24px",
                    borderRadius: "50%",
                    background: nodeBg,
                    border: `2px solid ${nodeBorder}`,
                    color: nodeColor,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: "11px",
                    fontWeight: 700,
                    marginBottom: "6px",
                    boxShadow: isRunning ? "0 0 10px rgba(59, 130, 246, 0.6)" : "none",
                    animation: isRunning ? "pulseRing 2s infinite" : "none",
                    transition: "all 0.2s ease",
                  }}
                >
                  {isComplete ? "✓" : isRunning ? "●" : isFailed ? "✕" : idx + 1}
                </div>

                {/* Node Label */}
                <span
                  style={{
                    fontSize: "10.5px",
                    fontWeight: isRunning ? 700 : 500,
                    color: isComplete ? "var(--fg)" : isRunning ? "#60a5fa" : "var(--muted)",
                    lineHeight: "1.2",
                  }}
                >
                  {m.label}
                </span>

                {/* Sub-status */}
                <span
                  style={{
                    fontSize: "9px",
                    color: isComplete ? "var(--green)" : isRunning ? "var(--accent)" : "var(--muted)",
                    marginTop: "2px",
                  }}
                >
                  {isComplete ? "Done" : isRunning ? `${m.progress.toFixed(0)}%` : "Wait"}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Interactive Milestone Detail Modal */}
      {selectedMilestone && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(0,0,0,0.65)",
            zIndex: 1200,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            backdropFilter: "blur(2px)",
          }}
          onClick={() => setSelectedMilestone(null)}
        >
          <div
            style={{
              background: "var(--bg-panel)",
              border: "1px solid var(--border)",
              borderRadius: "10px",
              padding: "20px 24px",
              width: "480px",
              maxWidth: "92vw",
              boxShadow: "0 12px 36px rgba(0,0,0,0.6)",
              fontSize: "12.5px",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", borderBottom: "1px solid var(--border)", paddingBottom: "8px" }}>
              <div>
                <h3 style={{ margin: 0, fontSize: "15px", color: "var(--fg)" }}>
                  {selectedMilestone.label}
                </h3>
                <span className="muted" style={{ fontSize: "11px" }}>
                  {selectedMilestone.description}
                </span>
              </div>
              <button
                onClick={() => setSelectedMilestone(null)}
                style={{ background: "transparent", border: "none", color: "var(--muted)", cursor: "pointer", fontSize: "16px" }}
              >
                ✕
              </button>
            </div>

            <div style={{ display: "flex", gap: "12px", marginBottom: "12px" }}>
              <div style={{ flex: 1, background: "var(--bg-box)", padding: "8px 10px", borderRadius: "6px" }}>
                <span className="muted" style={{ fontSize: "10px" }}>STATUS</span>
                <div style={{ fontWeight: 700, color: selectedMilestone.status === "COMPLETE" ? "var(--green)" : selectedMilestone.status === "RUNNING" ? "var(--accent)" : "var(--muted)" }}>
                  {selectedMilestone.status}
                </div>
              </div>
              <div style={{ flex: 1, background: "var(--bg-box)", padding: "8px 10px", borderRadius: "6px" }}>
                <span className="muted" style={{ fontSize: "10px" }}>PROGRESS</span>
                <div style={{ fontWeight: 700, color: "var(--fg)" }}>
                  {selectedMilestone.progress.toFixed(0)}%
                </div>
              </div>
              <div style={{ flex: 1, background: "var(--bg-box)", padding: "8px 10px", borderRadius: "6px" }}>
                <span className="muted" style={{ fontSize: "10px" }}>ELAPSED</span>
                <div style={{ fontWeight: 700, color: "var(--fg)" }}>
                  {selectedMilestone.elapsed_seconds ? `${selectedMilestone.elapsed_seconds}s` : "–"}
                </div>
              </div>
            </div>

            {selectedMilestone.current_task && (
              <div style={{ marginBottom: "12px", background: "var(--bg-box)", padding: "8px 12px", borderRadius: "6px" }}>
                <span className="muted" style={{ fontSize: "10px" }}>CURRENT TASK</span>
                <div className="mono" style={{ fontSize: "11.5px", color: "var(--fg)", wordBreak: "break-all" }}>
                  {selectedMilestone.current_task}
                </div>
              </div>
            )}

            {/* Subprocesses per dataset/timeframe */}
            {selectedMilestone.subprocesses && Object.keys(selectedMilestone.subprocesses).length > 0 && (
              <div style={{ marginBottom: "12px" }}>
                <div style={{ fontWeight: 600, fontSize: "11px", marginBottom: "6px", color: "var(--fg-dim)" }}>
                  SUBPROCESSES & TIMEFRAMES:
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "4px", maxHeight: "140px", overflowY: "auto" }}>
                  {Object.entries(selectedMilestone.subprocesses).map(([k, sp]) => (
                    <div
                      key={k}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        background: "var(--bg-box)",
                        padding: "4px 8px",
                        borderRadius: "4px",
                        fontSize: "11px",
                      }}
                    >
                      <span className="mono" style={{ fontWeight: 600 }}>{sp.dataset_id || k}</span>
                      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        <span className="muted">{sp.features || ""}</span>
                        <span style={{ color: sp.status === "COMPLETE" ? "var(--green)" : "var(--accent)", fontWeight: 600 }}>
                          {sp.status === "COMPLETE" ? "✓ READY" : `${sp.progress || 0}%`}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Completed Items */}
            {selectedMilestone.completed_items && selectedMilestone.completed_items.length > 0 && (
              <div style={{ marginBottom: "8px" }}>
                <div style={{ fontWeight: 600, fontSize: "11px", marginBottom: "4px", color: "var(--green)" }}>
                  COMPLETED WORK ITEMS:
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                  {selectedMilestone.completed_items.map((item, i) => (
                    <span
                      key={i}
                      style={{
                        background: "rgba(16, 185, 129, 0.12)",
                        border: "1px solid rgba(16, 185, 129, 0.3)",
                        color: "var(--green)",
                        borderRadius: "3px",
                        padding: "2px 6px",
                        fontSize: "10.5px",
                      }}
                    >
                      ✓ {item}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <button
              className="btn btn-sm w-full mt-3"
              onClick={() => setSelectedMilestone(null)}
              style={{ width: "100%", marginTop: "10px" }}
            >
              Close Details
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
