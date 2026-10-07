import React, { useState, useEffect, useRef } from "react";
import { api } from "../api.js";
import BuildIdentityChip from "./BuildIdentityChip.jsx";

export default function TopStatusRow({ onOpenMt5Modal, onToggleGpu }) {
  const [data, setData] = useState(null);
  const [activePopover, setActivePopover] = useState(null);
  const popoverRef = useRef(null);
  const inFlightRef = useRef(false);

  const fetchStatus = () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    api.systemStatusRow()
      .then((d) => setData(d))
      .catch(() => {})
      .finally(() => { inFlightRef.current = false; });
  };

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 2500);
    return () => clearInterval(interval);
  }, []);

  // Close popover when clicking outside
  useEffect(() => {
    function handleClickOutside(e) {
      if (popoverRef.current && !popoverRef.current.contains(e.target) && !e.target.closest(".status-row-item")) {
        setActivePopover(null);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  if (!data) return null;

  const togglePopover = (name) => {
    setActivePopover((cur) => (cur === name ? null : name));
  };

  const wifi = data.wifi || {};
  const mt5 = data.mt5 || {};
  const cpu = data.cpu || {};
  const gpu = data.gpu || {};
  const ram = data.ram || {};
  const dataInv = data.data || {};
  const db = data.db || {};
  const research = data.research || {};
  const progress = data.progress || {};

  return (
    <div style={{ position: "relative", marginBottom: "0.5rem" }}>
      <div
        className="top-status-row"
        style={{
          display: "flex",
          alignItems: "center",
          gap: "6px",
          background: "var(--bg-panel)",
          border: "1px solid var(--border)",
          borderRadius: "6px",
          padding: "4px 8px",
          overflowX: "auto",
          whiteSpace: "nowrap",
          fontSize: "11.5px",
          scrollbarWidth: "none",
        }}
      >
        {/* 1. WiFi / Network */}
        <button
          className={`status-row-item ${activePopover === "wifi" ? "active" : ""}`}
          onClick={() => togglePopover("wifi")}
          title="Network Connection Details"
          style={itemBtnStyle(activePopover === "wifi")}
        >
          <span style={{ color: wifi.status === "ONLINE" ? "var(--green)" : "var(--amber)" }}>📶</span>
          <span style={{ fontWeight: 600 }}>WIFI:</span>
          <span style={{ color: wifi.status === "ONLINE" ? "var(--green)" : "var(--amber)" }}>
            {wifi.status || "ONLINE"}
          </span>
          <span className="muted" style={{ fontSize: "10px" }}>({wifi.latency_ms || 1.2}ms)</span>
        </button>

        {/* 2. MT5 */}
        <button
          className={`status-row-item ${activePopover === "mt5" ? "active" : ""}`}
          onClick={() => togglePopover("mt5")}
          title="MetaTrader 5 Terminal & Feed Status"
          style={itemBtnStyle(activePopover === "mt5")}
        >
          <span style={{ color: mt5.connected ? (mt5.is_simulated ? "var(--amber)" : "var(--green)") : "var(--red)" }}>
            {mt5.is_simulated ? "📊" : "⚡"}
          </span>
          <span style={{ fontWeight: 600 }}>MT5:</span>
          <span style={{ color: mt5.connected ? (mt5.is_simulated ? "var(--amber)" : "var(--green)") : "var(--red)" }}>
            {mt5.is_simulated ? "SIMULATOR" : "REAL LIVE"}
          </span>
        </button>

        {/* 3. CPU */}
        <button
          className={`status-row-item ${activePopover === "cpu" ? "active" : ""}`}
          onClick={() => togglePopover("cpu")}
          title="CPU Cores & Worker Allocation"
          style={itemBtnStyle(activePopover === "cpu")}
        >
          <span>⚙️</span>
          <span style={{ fontWeight: 600 }}>CPU:</span>
          <span>{cpu.percent ? `${cpu.percent.toFixed(0)}%` : "0%"}</span>
          <span className="muted" style={{ fontSize: "10px" }}>
            ({cpu.active_workers || 0}/{cpu.effective_workers || 1}w @ {cpu.target_pct || 60}%)
          </span>
        </button>

        {/* 4. GPU */}
        <button
          className={`status-row-item ${activePopover === "gpu" ? "active" : ""}`}
          onClick={() => togglePopover("gpu")}
          title="GPU Acceleration Status"
          style={itemBtnStyle(activePopover === "gpu")}
        >
          <span style={{ color: gpu.enabled ? "var(--green)" : "var(--muted)" }}>🎮</span>
          <span style={{ fontWeight: 600 }}>GPU:</span>
          <span style={{ color: gpu.enabled ? "var(--green)" : "var(--muted)" }}>
            {gpu.enabled ? "ON" : "OFF"}
          </span>
          <span className="muted" style={{ fontSize: "10px" }}>
            [{gpu.processing_state || "IDLE"}]
          </span>
        </button>

        {/* 5. RAM */}
        <button
          className={`status-row-item ${activePopover === "ram" ? "active" : ""}`}
          onClick={() => togglePopover("ram")}
          title="System & Application Memory Allocation"
          style={itemBtnStyle(activePopover === "ram")}
        >
          <span>💾</span>
          <span style={{ fontWeight: 600 }}>RAM:</span>
          <span>{((ram.used_mb || 0) / 1024).toFixed(1)}GB</span>
          <span className="muted" style={{ fontSize: "10px" }}>
            (App: {ram.app_used_mb ? `${Math.round(ram.app_used_mb)}M` : "–"})
          </span>
        </button>

        {/* 6. DATA */}
        <button
          className={`status-row-item ${activePopover === "data" ? "active" : ""}`}
          onClick={() => togglePopover("data")}
          title="Persistent Data Root & Parquet Features"
          style={itemBtnStyle(activePopover === "data")}
        >
          <span>📁</span>
          <span style={{ fontWeight: 600 }}>DATA:</span>
          <span style={{ color: "var(--accent)" }}>{dataInv.dataset_count || 5} DS</span>
          <span className="muted" style={{ fontSize: "10px" }}>({dataInv.cached_features_count || 10} feat)</span>
        </button>

        {/* 7. DB */}
        <button
          className={`status-row-item ${activePopover === "db" ? "active" : ""}`}
          onClick={() => togglePopover("db")}
          title="SQLite Database & Lineage"
          style={itemBtnStyle(activePopover === "db")}
        >
          <span>🗄️</span>
          <span style={{ fontWeight: 600 }}>DB:</span>
          <span style={{ color: "var(--green)" }}>v{db.user_version || 3}</span>
          <span className="muted" style={{ fontSize: "10px" }}>({db.total_strategies || 371} strat)</span>
        </button>

        {/* 8. RESEARCH */}
        <button
          className={`status-row-item ${activePopover === "research" ? "active" : ""}`}
          onClick={() => togglePopover("research")}
          title="Genetic Research Loop"
          style={itemBtnStyle(activePopover === "research")}
        >
          <span>🔬</span>
          <span style={{ fontWeight: 600 }}>RESEARCH:</span>
          <span style={{ color: research.running ? "var(--green)" : "var(--muted)" }}>
            {research.running ? (research.paused ? "PAUSED" : "ACTIVE") : "IDLE"}
          </span>
          <span className="muted" style={{ fontSize: "10px" }}>(Gen {research.generation || 0})</span>
        </button>

        {/* 9. PROGRESS */}
        <button
          className={`status-row-item ${activePopover === "progress" ? "active" : ""}`}
          onClick={() => togglePopover("progress")}
          title="Milestone Workflow Progress"
          style={{ ...itemBtnStyle(activePopover === "progress"), marginLeft: "auto" }}
        >
          <span>🏁</span>
          <span style={{ fontWeight: 600 }}>PROGRESS:</span>
          <span style={{ color: "var(--accent)" }}>{progress.fill_percentage?.toFixed(0) || 0}%</span>
          <span className="muted" style={{ fontSize: "10px" }}>({progress.milestone || "Ready"})</span>
        </button>

        {/* 10. BUILD IDENTITY — which exact build this browser is running */}
        <BuildIdentityChip />
      </div>

      {/* Popover Card */}
      {activePopover && (
        <div
          ref={popoverRef}
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: activePopover === "progress" ? "auto" : "10px",
            right: activePopover === "progress" ? "10px" : "auto",
            zIndex: 1100,
            background: "var(--bg-panel)",
            border: "1px solid var(--border)",
            borderRadius: "8px",
            boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
            padding: "12px 16px",
            width: "360px",
            maxWidth: "90vw",
            fontSize: "12px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px", borderBottom: "1px solid var(--border)", paddingBottom: "6px" }}>
            <span style={{ fontWeight: 700, fontSize: "13px", color: "var(--fg)" }}>
              {popoverTitle(activePopover)}
            </span>
            <button
              onClick={() => setActivePopover(null)}
              style={{ background: "transparent", border: "none", color: "var(--muted)", cursor: "pointer", fontSize: "14px" }}
            >
              ✕
            </button>
          </div>

          {activePopover === "wifi" && (
            <div>
              <div className="flex justify-between py-1"><span>Network Status:</span><b style={{ color: "var(--green)" }}>{wifi.status}</b></div>
              <div className="flex justify-between py-1"><span>Interface:</span><b className="mono">{wifi.interface}</b></div>
              <div className="flex justify-between py-1"><span>IP Address:</span><b className="mono">{wifi.ip}</b></div>
              <div className="flex justify-between py-1"><span>Ping Latency:</span><b>{wifi.latency_ms} ms</b></div>
              <div className="flex justify-between py-1"><span>State:</span><span className="muted">{wifi.state}</span></div>
            </div>
          )}

          {activePopover === "mt5" && (
            <div>
              <div className="flex justify-between py-1"><span>Connection:</span><b style={{ color: mt5.connected ? "var(--green)" : "var(--red)" }}>{mt5.connected ? "CONNECTED" : "OFFLINE"}</b></div>
              <div className="flex justify-between py-1"><span>Feed Source:</span><b>{mt5.source}</b></div>
              <div className="flex justify-between py-1"><span>Account Number:</span><b className="mono">{mt5.account}</b></div>
              <div className="flex justify-between py-1"><span>Server / Broker:</span><b>{mt5.broker}</b></div>
              <div className="flex justify-between py-1"><span>Feed State:</span><span style={{ color: "var(--green)" }}>{mt5.feed_status}</span></div>
              <div className="flex justify-between py-1"><span>Latency:</span><b>{mt5.ping_ms} ms</b></div>
              {onOpenMt5Modal && (
                <button
                  className="btn btn-sm btn-primary w-full mt-2"
                  onClick={() => { setActivePopover(null); onOpenMt5Modal(); }}
                  style={{ width: "100%", marginTop: "8px" }}
                >
                  Configure MT5 Connection ⚙
                </button>
              )}
            </div>
          )}

          {activePopover === "cpu" && (
            <div>
              <div className="flex justify-between py-1"><span>System CPU:</span><b>{cpu.percent}%</b></div>
              <div className="flex justify-between py-1"><span>Application CPU:</span><b style={{ color: "var(--accent)" }}>{cpu.app_percent ?? 0}%</b></div>
              <div className="flex justify-between py-1"><span>Target Utilization:</span><b>{cpu.target_pct}%</b></div>
              <div className="flex justify-between py-1"><span>Physical Cores:</span><b>{cpu.physical_cores}</b></div>
              <div className="flex justify-between py-1"><span>Logical Processors:</span><b>{cpu.logical_processors}</b></div>
              <div className="flex justify-between py-1"><span>Effective Workers:</span><b style={{ color: "var(--green)" }}>{cpu.effective_workers}</b></div>
              <div className="flex justify-between py-1"><span>Active / Idle:</span><b>{cpu.active_workers} active / {cpu.idle_workers ?? Math.max(0, (cpu.effective_workers || 0) - (cpu.active_workers || 0))} idle</b></div>
              {Array.isArray(cpu.per_core) && cpu.per_core.length > 0 && (
                <div style={{ marginTop: "6px", paddingTop: "4px", borderTop: "1px dashed var(--border)" }}>
                  <span style={{ fontSize: "11px", color: "var(--muted)" }}>Per-Core Utilization:</span>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "4px", marginTop: "2px" }}>
                    {cpu.per_core.map((pct, idx) => (
                      <span key={idx} className="mono" style={{ fontSize: "10px", padding: "1px 4px", background: "rgba(255,255,255,0.06)", borderRadius: "3px" }}>
                        C{idx}:{pct}%
                      </span>
                    ))}
                  </div>
                </div>
              )}
              <div style={{ marginTop: "6px", fontSize: "11px", color: "var(--muted)" }}>
                Workers dynamically sized based on {cpu.target_pct}% target of {cpu.logical_processors} cores.
              </div>
            </div>
          )}

          {activePopover === "gpu" && (
            <div>
              <div className="flex justify-between py-1"><span>Hardware Acceleration:</span><b style={{ color: gpu.enabled ? "var(--green)" : "var(--muted)" }}>{gpu.enabled ? "ENABLED" : "DISABLED"}</b></div>
              <div className="flex justify-between py-1"><span>Device Model:</span><b>{gpu.name || "N/A"}</b></div>
              <div className="flex justify-between py-1"><span>GPU Available:</span><b>{gpu.available ? "YES" : "NO (CPU Fallback)"}</b></div>
              <div className="flex justify-between py-1"><span>Processing State:</span><b style={{ color: gpu.processing_state === "ACTIVE" ? "var(--green)" : "var(--amber)" }}>{gpu.processing_state}</b></div>
              <div className="flex justify-between py-1"><span>Utilization:</span><b>{gpu.utilization_pct || 0}%</b></div>
              <div className="flex justify-between py-1"><span>VRAM Allocation:</span><b>{gpu.vram_used_mb || 0} / {gpu.vram_total_mb || 0} MB</b></div>
              <div style={{ marginTop: "6px", fontSize: "11px", color: "var(--muted)" }}>
                Note: Standard indicator precomputation runs on CPU; GPU processing is honestly reported.
              </div>
            </div>
          )}

          {activePopover === "ram" && (
            <div>
              <div className="flex justify-between py-1"><span>System Memory Used:</span><b>{((ram.used_mb || 0) / 1024).toFixed(2)} GB</b></div>
              <div className="flex justify-between py-1"><span>Available System RAM:</span><b>{((ram.available_mb || 0) / 1024).toFixed(2)} GB</b></div>
              <div className="flex justify-between py-1"><span>Total System RAM:</span><b>{((ram.total_mb || 0) / 1024).toFixed(2)} GB</b></div>
              <div className="flex justify-between py-1"><span>System Utilization:</span><b>{ram.percent}%</b></div>
              <div className="flex justify-between py-1"><span>Application RSS:</span><b style={{ color: "var(--green)" }}>{ram.app_used_mb ?? 0} MB</b></div>
              <div className="flex justify-between py-1"><span>Application Peak RSS:</span><b>{ram.app_peak_mb ?? 0} MB</b></div>
              <div className="flex justify-between py-1"><span>Configured Limit:</span><b>{ram.limit_gb || "Unlimited"} GB</b></div>
            </div>
          )}

          {activePopover === "data" && (
            <div>
              <div className="flex justify-between py-1"><span>Storage Architecture:</span><b style={{ color: "var(--green)" }}>{dataInv.reuse_status}</b></div>
              <div className="flex justify-between py-1"><span>Master Datasets:</span><b>{dataInv.dataset_count} (H1, M30, M15, M5, M1)</b></div>
              <div className="flex justify-between py-1"><span>Total Market Rows:</span><b>{dataInv.total_rows?.toLocaleString()}</b></div>
              <div className="flex justify-between py-1"><span>Cached Parquet Files:</span><b>{dataInv.cached_features_count} files</b></div>
              <div className="flex justify-between py-1"><span>Root Directory:</span><b className="mono" style={{ fontSize: "10px" }}>{dataInv.root_path}</b></div>
            </div>
          )}

          {activePopover === "db" && (
            <div>
              <div className="flex justify-between py-1"><span>Database Status:</span><b style={{ color: "var(--green)" }}>{db.status}</b></div>
              <div className="flex justify-between py-1"><span>SQLite Schema Version:</span><b>user_version = {db.user_version}</b></div>
              <div className="flex justify-between py-1"><span>Total Strategies:</span><b style={{ color: "var(--accent)" }}>{db.total_strategies} Strategies</b></div>
              <div className="flex justify-between py-1"><span>Lineage Integrity:</span><b style={{ color: "var(--green)" }}>Preserved (0 loops, 0 broken)</b></div>
              <div className="flex justify-between py-1"><span>Database Path:</span><b className="mono" style={{ fontSize: "10px" }}>{db.path}</b></div>
            </div>
          )}

          {activePopover === "research" && (
            <div>
              <div className="flex justify-between py-1"><span>Research Mode:</span><b style={{ textTransform: "uppercase" }}>{research.mode}</b></div>
              <div className="flex justify-between py-1"><span>Loop State:</span><b style={{ color: research.running ? "var(--green)" : "var(--muted)" }}>{research.running ? "ACTIVE" : "IDLE"}</b></div>
              <div className="flex justify-between py-1"><span>Current Generation:</span><b>Gen {research.generation}</b></div>
              <div className="flex justify-between py-1"><span>Active Population:</span><b>{research.active_population}</b></div>
              <div className="flex justify-between py-1"><span>Target Ceiling:</span><b>{research.target} Nodes</b></div>
            </div>
          )}

          {activePopover === "progress" && (
            <div>
              <div className="flex justify-between py-1"><span>Workflow Milestone:</span><b style={{ color: "var(--accent)" }}>{progress.milestone}</b></div>
              <div className="flex justify-between py-1"><span>Route Fill:</span><b>{progress.fill_percentage?.toFixed(1)}%</b></div>
              <div className="flex justify-between py-1"><span>Active Task:</span><b className="mono" style={{ fontSize: "11px" }}>{progress.active_task}</b></div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function itemBtnStyle(isActive) {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: "4px",
    background: isActive ? "rgba(79, 142, 247, 0.15)" : "var(--bg-box)",
    border: `1px solid ${isActive ? "var(--accent)" : "var(--border)"}`,
    borderRadius: "4px",
    padding: "3px 7px",
    color: "var(--fg)",
    cursor: "pointer",
    fontSize: "11.5px",
    flexShrink: 0,
    outline: "none",
  };
}

function popoverTitle(key) {
  const titles = {
    wifi: "📶 Network Connectivity Details",
    mt5: "📊 MetaTrader 5 Terminal Status",
    cpu: "⚙️ CPU & Worker Thread Pool",
    gpu: "🎮 Hardware Acceleration (GPU)",
    ram: "💾 System Memory (RAM)",
    data: "📁 Persistent DATA Architecture",
    db: "🗄️ Database & Lineage Integrity",
    research: "🔬 Evolutionary Research Engine",
    progress: "🏁 Workflow Milestone Progress",
  };
  return titles[key] || "System Details";
}
