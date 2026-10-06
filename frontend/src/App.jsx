import React, { Suspense, createContext, lazy, useContext, useEffect, useState, useCallback, useRef } from "react";
import { api } from "./api.js";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import LocalErrorBoundary from "./components/LocalErrorBoundary.jsx";
import StartupBanner from "./components/StartupBanner.jsx";
// V4.7: pages are code-split (React.lazy). Each page (and the heavy libraries it
// pulls in - recharts, reactflow) is fetched only when that page is opened, so
// the initial dashboard bundle stays small. Page identity, routing, visuals and
// behaviour are unchanged.
const Overview = lazy(() => import("./pages/Overview.jsx"));
const Stats = lazy(() => import("./pages/Stats.jsx"));
const Activity = lazy(() => import("./pages/Activity.jsx"));
const EvolutionTree = lazy(() => import("./pages/EvolutionTree.jsx"));
const Population = lazy(() => import("./pages/Population.jsx"));
const StrategyLab = lazy(() => import("./pages/StrategyLab.jsx"));
const NodeEconomics = lazy(() => import("./pages/NodeEconomics.jsx"));
const BacktestMatrix = lazy(() => import("./pages/BacktestMatrix.jsx"));
const Mt5Backtest = lazy(() => import("./pages/Mt5Backtest.jsx"));
const LiveTesting = lazy(() => import("./pages/LiveTesting.jsx"));
const LiveTestResults = lazy(() => import("./pages/LiveTestResults.jsx"));
const Mt5DemoTrading = lazy(() => import("./pages/Mt5DemoTrading.jsx"));
const PaperTrading = lazy(() => import("./pages/PaperTrading.jsx"));
const MarketData = lazy(() => import("./pages/MarketData.jsx"));
const ResearchAI = lazy(() => import("./pages/ResearchAI.jsx"));
const Settings = lazy(() => import("./pages/Settings.jsx"));
const Logs = lazy(() => import("./pages/Logs.jsx"));
const FinalTesting = lazy(() => import("./pages/FinalTesting.jsx"));
import StrategyDrawer from "./components/StrategyDrawer.jsx";
import TopStatusRow from "./components/TopStatusRow.jsx";
import MilestoneProgressPath from "./components/MilestoneProgressPath.jsx";
import LiveActivitySidebar from "./components/LiveActivitySidebar.jsx";

// exported so tests can render individual pages inside the real context (V4.7)
export const LabContext = createContext(null);
export const useLab = () => useContext(LabContext);

const PAGES = [
  ["overview", "1", "Overview", Overview],
  ["stats", "2", "Stats", Stats],
  ["final_testing", "3", "Final Testing", FinalTesting],
  ["lab", "4", "Strategy Laboratory", StrategyLab],
  ["economics", "5", "Node Economics", NodeEconomics],
  ["matrix", "6", "Backtest Matrix", BacktestMatrix],
  ["mt5_backtest", "7", "MT5 Backtest", Mt5Backtest],
  ["live_test", "8", "Live Testing", LiveTesting],
  ["live_results", "9", "Live Test Results", LiveTestResults],
  ["mt5_demo", "10", "MT5 Demo Trading", Mt5DemoTrading],
  ["paper", "11", "Paper Trading", PaperTrading],
  ["activity", "12", "Activity Stream", Activity],
  ["tree", "13", "Evolution Tree", EvolutionTree],
  ["population", "14", "Population", Population],
  ["data", "15", "Market Data", MarketData],
  ["ai", "16", "Research AI", ResearchAI],
  ["settings", "17", "Settings", Settings],
  ["logs", "18", "Logs", Logs],
];

export default function App() {
  const [page, setPage] = useState("overview");
  const [status, setStatus] = useState(null);
  const [events, setEvents] = useState([]);
  const [connected, setConnected] = useState(false);
  const [selectedStrategy, setSelectedStrategy] = useState(null);
  const [selectedStrategyId, setSelectedStrategyId] = useState(240);
  const [shortlist, setShortlist] = useState([]);
  const [showMt5Modal, setShowMt5Modal] = useState(false);
  const [isActivityOpen, setIsActivityOpen] = useState(() => {
    try {
      const saved = localStorage.getItem("lab_live_activity_open");
      return saved !== null ? JSON.parse(saved) : false;
    } catch (e) {
      return false;
    }
  });

  // Load and synchronize shortlist across all tabs
  const refreshShortlist = useCallback(async () => {
    try {
      const res = await api.savedShortlist();
      setShortlist(res.shortlist || []);
    } catch (err) {
      console.warn("Failed to sync shortlist:", err);
    }
  }, []);

  const toggleShortlist = useCallback(async (sid, notes = "") => {
    try {
      const res = await api.toggleShortlist(sid, notes);
      setShortlist(res.shortlist || []);
      return res;
    } catch (err) {
      console.error("Toggle shortlist error:", err);
      throw err;
    }
  }, []);

  const navigateTab = useCallback((targetPage, strategyId = null) => {
    if (strategyId) setSelectedStrategyId(strategyId);
    setPage(targetPage);
  }, []);

  useEffect(() => {
    refreshShortlist();
  }, [refreshShortlist]);

  const handleToggleActivity = () => {
    setIsActivityOpen((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("lab_live_activity_open", JSON.stringify(next));
      } catch (e) {}
      return next;
    });
  };

  // MT5 flexible connection state
  const [mt5Terminals, setMt5Terminals] = useState([]);
  const [selectedCustomPath, setSelectedCustomPath] = useState("");
  const [mt5Connecting, setMt5Connecting] = useState(false);
  const [mt5Msg, setMt5Msg] = useState(null);

  const statusInFlightRef = useRef(false);
  const refreshStatus = useCallback(() => {
    if (statusInFlightRef.current) return;
    statusInFlightRef.current = true;
    api.status()
      .then((s) => setStatus(s))
      .catch(() => {})
      .finally(() => { statusInFlightRef.current = false; });
  }, []);

  const loadTerminals = useCallback(() => {
    api.mt5Terminals().then((t) => setMt5Terminals(t || [])).catch(() => {});
    api.mt5Config().then((cfg) => {
      if (cfg?.terminal_path) setSelectedCustomPath(cfg.terminal_path);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    refreshStatus();
    const interval = setInterval(refreshStatus, 3000);
    return () => clearInterval(interval);
  }, [refreshStatus]);

  useEffect(() => {
    if (showMt5Modal) loadTerminals();
  }, [showMt5Modal, loadTerminals]);

  useEffect(() => {
    let ws = null;
    let timer = null;
    function connect() {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${proto}//${window.location.host}/ws`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        timer = setTimeout(connect, 2000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data);
          if (data.type === "event") {
            setEvents((prev) => [data.data, ...prev].slice(0, 100));
          }
        } catch (e) {}
      };
    }
    connect();
    return () => {
      if (ws) ws.close();
      if (timer) clearTimeout(timer);
    };
  }, []);

  const handleMt5Connect = async (path = "") => {
    setMt5Connecting(true);
    setMt5Msg(null);
    try {
      const res = await api.mt5Connect({ terminal_path: path });
      setMt5Msg({
        ok: true,
        text: res.status?.connected ? "Successfully connected to MetaTrader 5!" : "MT5 initialization attempted. Check terminal status."
      });
      refreshStatus();
    } catch (err) {
      setMt5Msg({ ok: false, text: err.message || "Failed to connect to MT5." });
    } finally {
      setMt5Connecting(false);
    }
  };

  const handleMt5Disconnect = async () => {
    setMt5Connecting(true);
    setMt5Msg(null);
    try {
      await api.mt5Disconnect();
      setMt5Msg({ ok: true, text: "Disconnected MT5. Simulator mode active." });
      refreshStatus();
    } catch (err) {
      setMt5Msg({ ok: false, text: err.message });
    } finally {
      setMt5Connecting(false);
    }
  };

  const lab = status?.lab;
  const bridge = status?.bridge;
  const conn = status?.connection || {};

  function statusDotColor(val) {
    if (val === "CONNECTED" || val === "LIVE") return "var(--green)";
    if (val === "DISCONNECTED" || val === "STALE") return "var(--red)";
    if (val === "SIMULATED" || val === "PAUSED") return "var(--amber)";
    return "var(--muted)";
  }

  const activePageObj = PAGES.find(([k]) => k === page);
  const ActivePageComponent = activePageObj ? activePageObj[3] : Overview;
  const isMt5RealConnected = bridge?.connected && !bridge?.is_simulated;

  return (
    <LabContext.Provider
      value={{
        status,
        events,
        connected,
        refreshStatus,
        openStrategy: setSelectedStrategy,
        selectedStrategyId,
        setSelectedStrategyId,
        shortlist,
        toggleShortlist,
        navigateTab,
      }}
    >
      <div className="app">
        <div className="sidebar">
          <div className="logo">
            <h1>🧬 EVOLUTIONARY TRADING</h1>
            <div className="sub">RESEARCH LAB · V4</div>
          </div>
          <div className="nav">
            {PAGES.map(([key, num, label]) => (
              <button key={key} className={page === key ? "active" : ""}
                      onClick={() => setPage(key)}>
                <span className="num">{num}</span>{label}
              </button>
            ))}
          </div>
          <div className="side-status">
            <div className="row"><span>Research</span>
              <b style={{ color: lab?.running ? (lab.paused ? "var(--amber)" : "var(--green)") : "var(--muted)" }}>
                {lab?.running ? (lab.paused ? "PAUSED" : "RUNNING") : "IDLE"}
              </b></div>
            <div className="row"><span>Mode</span><b>{lab?.mode || "–"}</b></div>
            <div className="row"><span>Generation</span><b>{lab?.generation ?? "–"}</b></div>
            <div className="row"><span>Population</span><b>{lab?.population_active ?? "–"}</b></div>
            <div className="row"><span>Feed</span>
              <span className={"pill " + (isMt5RealConnected ? "real" : "sim")}>
                {isMt5RealConnected ? "MT5 LIVE" : "SIMULATOR"}
              </span></div>
            <div className="row"><span>WS</span>
              <b style={{ color: connected ? "var(--green)" : "var(--red)" }}>
                {connected ? "●" : "○"}</b></div>
          </div>
        </div>

        <div className="main">
          {/* Main Dashboard Top Status Row (User Spec V2.7) */}
          <TopStatusRow
            onOpenMt5Modal={() => setShowMt5Modal(true)}
          />

          {/* Main Milestone Progress Path: Metro / Bus Route (User Spec V2.7) */}
          <MilestoneProgressPath />

          {/* V4.7: honest backend startup/readiness state (hidden once ready) */}
          <StartupBanner />

          {!isMt5RealConnected && (
            <div className="warn-banner flex justify-between items-center" style={{ padding: "8px 14px", margin: "0 0 1rem 0" }}>
              <div>
                ⚠ Market feed is running in <b>SIMULATOR</b> mode (high-fidelity synthetic research data).
                Research engine, database, and evolution tree are fully functional without MT5.
              </div>
              <button className="btn btn-sm primary" onClick={() => setShowMt5Modal(true)}>
                Connect MT5
              </button>
            </div>
          )}

          <ErrorBoundary>
            <LocalErrorBoundary key={page} label={activePageObj ? activePageObj[2] : "page"}>
              <Suspense fallback={<div className="panel"><div className="muted" style={{ padding: 18 }}>loading {activePageObj ? activePageObj[2] : "page"}…</div></div>}>
                <ActivePageComponent />
              </Suspense>
            </LocalErrorBoundary>
          </ErrorBoundary>
        </div>

        {/* Live Activity Collapsible Right Sidebar (User Spec V2.7) */}
        <LiveActivitySidebar
          isOpen={isActivityOpen}
          onToggle={handleToggleActivity}
          onOpenMt5Modal={() => setShowMt5Modal(true)}
        />

        {/* MT5 Flexible Connection & Terminal Selection Modal */}
        {showMt5Modal && (
          <div className="modal-backdrop" onClick={() => setShowMt5Modal(false)}
               style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}
                 style={{ background: "var(--panel)", border: "1px solid var(--border)", borderRadius: "8px", padding: "1.5rem", width: "520px", maxWidth: "92vw" }}>
              <div className="flex justify-between items-center" style={{ marginBottom: "1rem", borderBottom: "1px solid var(--border)", paddingBottom: "8px" }}>
                <h3 style={{ margin: 0 }}>📊 MetaTrader 5 Flexible Connection</h3>
                <button className="btn btn-xs btn-subtle" onClick={() => setShowMt5Modal(false)}>✕</button>
              </div>

              {mt5Msg && (
                <div style={{ padding: "8px 12px", borderRadius: "4px", marginBottom: "12px", fontSize: "0.85rem", background: mt5Msg.ok ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)", border: `1px solid ${mt5Msg.ok ? "var(--green)" : "var(--red)"}` }}>
                  {mt5Msg.text}
                </div>
              )}

              {/* Current Connection Info */}
              <div style={{ display: "flex", flexDirection: "column", gap: "8px", fontSize: "0.88rem", marginBottom: "16px", background: "rgba(0,0,0,0.25)", padding: "12px", borderRadius: "6px" }}>
                <div className="flex justify-between">
                  <span className="muted">Connection Status:</span>
                  <b style={{ color: isMt5RealConnected ? "var(--green)" : "var(--amber)" }}>
                    {isMt5RealConnected ? "CONNECTED" : "NOT CONNECTED (SIMULATOR MODE)"}
                  </b>
                </div>
                {isMt5RealConnected && (
                  <>
                    <div className="flex justify-between">
                      <span className="muted">Terminal Name:</span>
                      <b>{bridge?.terminal_name || "MetaTrader 5"}</b>
                    </div>
                    <div className="flex justify-between">
                      <span className="muted">Terminal Build:</span>
                      <b>{bridge?.terminal_build ? `Build ${bridge.terminal_build}` : "–"}</b>
                    </div>
                    <div className="flex justify-between">
                      <span className="muted">Broker / Company:</span>
                      <b>{bridge?.terminal_company || bridge?.broker || "–"}</b>
                    </div>
                    <div className="flex justify-between">
                      <span className="muted">Account / Server:</span>
                      <b>{bridge?.account ? `${bridge.account} @ ${bridge.server || "–"}` : "–"}</b>
                    </div>
                    <div className="flex justify-between">
                      <span className="muted">Terminal Executable:</span>
                      <span className="mono" style={{ fontSize: "0.75rem", maxWidth: "260px", overflow: "hidden", textOverflow: "ellipsis" }} title={bridge?.terminal_path}>
                        {bridge?.terminal_path || "–"}
                      </span>
                    </div>
                  </>
                )}
                <div className="flex justify-between">
                  <span className="muted">Python MT5 Package:</span>
                  <b>{bridge?.mt5_package_installed ? `Installed (${bridge?.package_version || "ready"})` : "Not installed"}</b>
                </div>
              </div>

              {/* Terminal Discovery & Selection */}
              <div style={{ marginBottom: "16px" }}>
                <label style={{ fontSize: "0.82rem", fontWeight: 600, color: "var(--fg-dim)", display: "block", marginBottom: "4px" }}>
                  Discovered Installed MT5 Terminals:
                </label>
                {mt5Terminals.length > 0 ? (
                  <select
                    style={{ width: "100%", padding: "6px 8px", fontSize: "0.85rem", background: "var(--bg-box)", border: "1px solid var(--border)", borderRadius: "4px" }}
                    value={selectedCustomPath}
                    onChange={(e) => setSelectedCustomPath(e.target.value)}
                  >
                    <option value="">-- Select Discovered Terminal --</option>
                    {mt5Terminals.map((t, idx) => (
                      <option key={idx} value={t.path}>
                        {t.name} ({t.path})
                      </option>
                    ))}
                  </select>
                ) : (
                  <div className="muted" style={{ fontSize: "0.8rem", fontStyle: "italic" }}>
                    No standard terminals auto-detected. Enter path manually below.
                  </div>
                )}
              </div>

              {/* Manual Path Input */}
              <div style={{ marginBottom: "16px" }}>
                <label style={{ fontSize: "0.82rem", fontWeight: 600, color: "var(--fg-dim)", display: "block", marginBottom: "4px" }}>
                  Or Locate Terminal Executable (terminal64.exe):
                </label>
                <input
                  type="text"
                  placeholder="e.g. C:\Program Files\MetaTrader 5\terminal64.exe"
                  value={selectedCustomPath}
                  onChange={(e) => setSelectedCustomPath(e.target.value)}
                  style={{ width: "100%", padding: "6px 8px", fontSize: "0.85rem" }}
                />
              </div>

              {/* Action Buttons */}
              <div className="flex justify-between items-center" style={{ marginTop: "1rem" }}>
                <div>
                  {isMt5RealConnected && (
                    <button
                      className="btn btn-sm btn-subtle"
                      disabled={mt5Connecting}
                      onClick={handleMt5Disconnect}
                    >
                      Disconnect MT5
                    </button>
                  )}
                </div>
                <div className="flex gap-2">
                  <button
                    className="btn btn-sm btn-subtle"
                    disabled={mt5Connecting}
                    onClick={() => handleMt5Connect("")}
                  >
                    Auto-Discover MT5
                  </button>
                  <button
                    className="btn btn-sm primary"
                    disabled={mt5Connecting || !selectedCustomPath}
                    onClick={() => handleMt5Connect(selectedCustomPath)}
                  >
                    {mt5Connecting ? "Connecting..." : "Connect Selected"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {selectedStrategy != null && (
          <LocalErrorBoundary label="strategy detail drawer">
            <StrategyDrawer id={selectedStrategy} onClose={() => setSelectedStrategy(null)}
                            onOpen={(nid) => setSelectedStrategy(nid)} />
          </LocalErrorBoundary>
        )}
      </div>
    </LabContext.Provider>
  );
}
