import React, { useEffect, useState } from "react";
import { api, fmt } from "../api.js";
import { ErrorNote, Spinner } from "../components/common.jsx";

const SECTIONS = [
  ["resources", "Computational Resource Limiting (spec §26-32)", [
    ["cpu_limit_enabled", "CPU limit enabled", "bool"],
    ["cpu_workers_max", "CPU worker limit (0 = auto cores)", "int"],
    ["memory_limit_enabled", "Memory limit enabled", "bool"],
    ["memory_limit_mode", "Memory mode: pct | gb", "text"],
    ["memory_limit_value", "Memory budget limit (pct or GB)", "float"],
    ["gpu_enabled", "GPU acceleration enabled", "bool"],
    ["gpu_workload_limit_pct", "GPU workload limit %", "int"],
  ]],
  ["mt5", "MetaTrader 5 Bridge & Connection (spec §13, §14)", [
    ["mode", "Bridge mode: auto | real | simulator", "text"],
    ["connection_mode", "Connection mode: auto | existing | path", "text"],
    ["login", "MT5 login (demo account #)", "int"],
    ["password", "MT5 password (never displayed/logged)", "text"],
    ["server", "MT5 server", "text"],
    ["path", "terminal64.exe path (optional)", "text"],
    ["feed_stale_s", "Feed stale threshold (seconds)", "int"],
    ["reconnect_min_s", "Min reconnect delay (seconds)", "int"],
  ]],
  ["research", "Research & Migration Policy (spec §40, §45)", [
    ["legacy_result_policy", "Legacy V1 result policy: reuse | revalidate", "text"],
    ["export_enabled", "Export complete Parquets to RESEARCH/", "bool"],
    ["activity_retention", "Activity log retention rows", "int"],
  ]],
  ["appearance", "Appearance & Tree Styling (spec §36, §39)", [
    ["theme", "Theme: dark | light | system", "text"],
    ["edge_color", "Tree edge line color", "text"],
    ["edge_width", "Tree edge line width", "float"],
    ["node_selected", "Selected node highlight color", "text"],
    ["node_hover", "Hover node highlight color", "text"],
  ]],
  ["evolution", "Evolution / Population", [
    ["population_size", "Population size", "int"],
    ["elite_pct", "Elite preservation %", "float01"],
    ["mutation_pct", "Mutation %", "float01"],
    ["crossover_pct", "Crossover %", "float01"],
    ["exploration_pct", "Random exploration %", "float01"],
    ["max_indicators", "Max indicators (early gens)", "int"],
    ["max_indicators_hard", "Max indicators (hard cap)", "int"],
    ["max_conditions", "Max conditions", "int"],
    ["simplify_probability", "Simplification probability", "float01"],
    ["mutation_rate", "Per-gene mutation rate", "float01"],
    ["species_cap_pct", "Species cap in elites %", "float01"],
    ["screen_batch_size", "Screening batch size", "int"],
    ["detail_batch_size", "Detail batch size", "int"],
    ["validation_batch_size", "Validation batch size", "int"],
    ["workers", "Configured CPU workers", "int"],
  ]],
  ["backtest", "Backtest / Execution model", [
    ["initial_balance", "Initial balance ($)", "float"],
    ["risk_per_trade", "Risk per trade (frac)", "float"],
    ["commission_per_lot", "Commission per lot ($ RT)", "float"],
    ["swap_per_lot_per_day", "Swap per lot/day ($)", "float"],
    ["contract_size", "Contract size", "float"],
    ["default_spread_points", "Default spread (points)", "float"],
    ["slippage_model", "Slippage model (normal|uniform|empirical)", "text"],
    ["slippage_mean_points", "Slippage mean (points)", "float"],
    ["slippage_std_points", "Slippage std (points)", "float"],
    ["slippage_max_points", "Slippage max (points)", "float"],
    ["execution_delay_ms", "Execution delay (ms)", "int"],
    ["train_fraction", "Train fraction (in-sample)", "float01"],
    ["min_trades", "Min trades (detail)", "int"],
    ["screen_min_trades", "Min trades (screening)", "int"],
    ["screen_max_hold_bars", "Screening max hold (bars)", "int"],
    ["max_hold_bars", "Global max hold (bars)", "int"],
  ]],
  ["fitness", "Fitness weights & death rules", [
    ["weights.profitability", "weight: profitability", "float01"],
    ["weights.risk_adjusted", "weight: risk adjusted", "float01"],
    ["weights.drawdown", "weight: drawdown", "float01"],
    ["weights.profit_factor", "weight: profit factor", "float01"],
    ["weights.consistency", "weight: consistency", "float01"],
    ["weights.oos", "weight: out-of-sample", "float01"],
    ["weights.robustness", "weight: robustness", "float01"],
    ["weights.complexity", "weight: complexity penalty", "float01"],
    ["complexity_penalty_per_indicator", "penalty / indicator", "float"],
    ["complexity_penalty_per_condition", "penalty / condition", "float"],
    ["max_drawdown_pct", "DEATH: max drawdown", "float01"],
    ["min_profit_factor", "DEATH: min profit factor", "float"],
    ["min_trades", "DEATH: min trades", "int"],
    ["min_sharpe", "DEATH: min sharpe (if unprofitable)", "float"],
    ["max_stress_degradation", "DEATH: max stress degradation", "float01"],
    ["max_perturbation_instability", "DEATH: max param instability", "float01"],
    ["oos_degradation_limit", "DEATH: OOS degradation limit", "float01"],
  ]],
  ["risk", "Global risk controls (independent of strategies & AI)", [
    ["max_daily_drawdown_pct", "Max daily drawdown", "float01"],
    ["max_strategy_drawdown_pct", "Max strategy drawdown", "float01"],
    ["max_spread_points", "Max spread (points)", "float"],
    ["max_position_size_lots", "Max position size (lots)", "float"],
    ["max_concurrent_positions", "Max concurrent positions", "int"],
    ["max_trades_per_minute", "Max trades / minute", "int"],
    ["min_holding_seconds", "Min holding (s)", "int"],
    ["max_holding_seconds", "Max holding (s)", "int"],
    ["max_allowed_slippage_points", "Max slippage (points)", "float"],
    ["allowed_sessions", "Allowed sessions (csv)", "csv"],
  ]],
  ["paper", "Paper trading & Hard Capital Risk Controls (V3.2)", [
    ["enabled_strategies_max", "Max strategies on paper", "int"],
    ["starting_capital", "Starting paper capital ($)", "float"],
    ["max_risk_per_trade_pct", "Max risk per trade (%)", "float01"],
    ["max_risk_per_trade_abs", "Max risk per trade abs ($)", "float"],
    ["max_daily_loss_pct", "Max daily loss (%)", "float01"],
    ["max_daily_loss_abs", "Max daily loss abs ($)", "float"],
    ["max_total_drawdown_pct", "Max total drawdown (%)", "float01"],
    ["max_concurrent_positions", "Max concurrent positions", "int"],
    ["max_exposure_pct", "Max portfolio exposure (%)", "float01"],
    ["tick_interval_ms", "Loop interval (ms)", "int"],
    ["latency_ms_mean", "Assumed latency mean (ms)", "int"],
    ["latency_ms_std", "Latency std (ms)", "int"],
    ["slippage_mean_points", "Paper slippage mean (pt)", "float"],
    ["slippage_std_points", "Paper slippage std (pt)", "float"],
    ["divergence_alert_threshold", "Divergence alert threshold", "float01"],
  ]],
  ["ai", "AI Researcher", [
    ["researcher_enabled", "Researcher enabled", "bool"],
    ["max_hypotheses_per_cycle", "Max hypotheses / cycle", "int"],
    ["llm_provider", "LLM provider (none = rule-based only)", "text"],
    ["llm_endpoint", "LLM endpoint (OpenAI-compatible /chat/completions)", "text"],
    ["llm_model", "LLM model", "text"],
    ["llm_api_key_env", "API key env var name", "text"],
  ]],
  ["data", "Data", [
    ["symbol", "Primary symbol", "text"],
    ["timeframes", "Timeframes (csv)", "csv"],
    ["enabled_symbols", "Enabled symbols (csv)", "csv"],
  ]],
];

function Field({ name, label, type, value, onChange }) {
  if (type === "bool") {
    return (
      <label className="fld">{label}
        <select value={value ? "true" : "false"}
                onChange={(e) => onChange(name, e.target.value === "true")}>
          <option value="true">true</option><option value="false">false</option>
        </select>
      </label>
    );
  }
  return (
    <label className="fld">{label}
      <input type={name.includes("password") ? "password" : "text"}
             value={value ?? ""}
             onChange={(e) => {
               let v = e.target.value;
               if (type === "int") v = v === "" ? "" : parseInt(v, 10);
               else if (type === "float" || type === "float01") v = v === "" ? "" : parseFloat(v);
               else if (type === "csv") v = v.split(",").map((s) => s.trim()).filter(Boolean);
               onChange(name, v);
             }} />
    </label>
  );
}

function getPath(obj, path) {
  return path.split(".").reduce((a, k) => (a == null ? a : a[k]), obj);
}

function setPath(obj, path, val) {
  const parts = path.split(".");
  const out = { ...obj };
  let cur = out;
  for (let i = 0; i < parts.length - 1; i++) {
    cur[parts[i]] = { ...(cur[parts[i]] || {}) };
    cur = cur[parts[i]];
  }
  cur[parts[parts.length - 1]] = val;
  return out;
}

export default function Settings() {
  const [cfg, setCfg] = useState(null);
  const [err, setErr] = useState(null);
  const [saved, setSaved] = useState("");
  const [realConfirm, setRealConfirm] = useState("");
  const [riskState, setRiskState] = useState(null);
  const [backupsList, setBackupsList] = useState([]);
  const [backupMsg, setBackupMsg] = useState("");
  const [resources, setResources] = useState(null);
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState(null);

  // V2.7 Computational Knobs
  const [cpuTarget, setCpuTarget] = useState(60);
  const [gpuToggle, setGpuToggle] = useState(false);

  // V2.8 Data Management state
  const [dataStatus, setDataStatus] = useState(null);
  const [dataStatusLoading, setDataStatusLoading] = useState(false);
  const [clearModal, setClearModal] = useState(null);
  const [allDataInput, setAllDataInput] = useState("");
  const [clearActionMsg, setClearActionMsg] = useState("");
  const [clearing, setClearing] = useState(false);

  const refreshDataStatus = () => {
    setDataStatusLoading(true);
    api.dataManagementStatus()
      .then(setDataStatus)
      .catch((e) => console.error("Failed to load data status", e))
      .finally(() => setDataStatusLoading(false));
  };

  useEffect(() => {
    api.settings().then((c) => {
      setCfg(c);
      if (c.cpu_target_pct != null) setCpuTarget(c.cpu_target_pct);
      else if (c.resources?.cpu_target_pct != null) setCpuTarget(c.resources.cpu_target_pct);
      if (c.gpu_enabled != null) setGpuToggle(c.gpu_enabled);
      else if (c.resources?.gpu_enabled != null) setGpuToggle(c.resources.gpu_enabled);
    }).catch((e) => setErr(e.message));
    api.resources().then(setResources).catch(() => {});
    api.risk().then(setRiskState).catch(() => {});
    api.backups().then(setBackupsList).catch(() => {});
    refreshDataStatus();
  }, []);

  const handleExecuteClear = async () => {
    if (!clearModal) return;
    setClearing(true);
    setClearActionMsg("");
    setErr(null);
    try {
      let res;
      if (clearModal.type === "xauusd") {
        res = await api.clearXauusdData();
      } else if (clearModal.type === "cache") {
        res = await api.clearCache();
      } else if (clearModal.type === "nodes") {
        res = await api.clearNodes();
      } else if (clearModal.type === "genomes") {
        res = await api.clearGenomes();
      } else if (clearModal.type === "all") {
        res = await api.clearAllData(allDataInput);
      }

      if (res?.status === "processing" && res?.job_id) {
        let attempts = 0;
        while (attempts < 15) {
          await new Promise((r) => setTimeout(r, 1000));
          attempts++;
          try {
            const j = await api.jobDetail(res.job_id);
            if (j?.status === "completed") {
              res = j.result || res;
              break;
            }
            if (j?.status === "failed") {
              throw new Error(j.error || "Clear operation failed in background");
            }
          } catch (pollErr) {
            if (pollErr.message && pollErr.message.includes("failed in background")) throw pollErr;
          }
        }
      }

      setClearActionMsg(res?.message || "Operation completed successfully");
      refreshDataStatus();
      setClearModal(null);
      setAllDataInput("");
      setTimeout(() => setClearActionMsg(""), 5000);
    } catch (e) {
      setErr("Clear operation failed: " + e.message);
    } finally {
      setClearing(false);
    }
  };

  const change = (section) => (name, val) => {
    setCfg((c) => ({ ...c, [section]: setPath(c[section], name, val) }));
    setSaved("");
  };

  const handleApplyAllSettings = async () => {
    setApplying(true);
    setErr(null);
    setApplyResult(null);
    try {
      const payload = {
        ...cfg,
        cpu_target_pct: cpuTarget,
        gpu_enabled: gpuToggle,
        resources: {
          ...(cfg?.resources || {}),
          cpu_target_pct: cpuTarget,
          gpu_enabled: gpuToggle,
        },
      };
      const res = await api.applySettings(payload);
      setCfg(res.settings);
      setResources(res.resources);
      setApplyResult(res);
      setSaved("All settings validated and persisted to CONFIG/settings.json ✓");
      setTimeout(() => setSaved(""), 4000);
    } catch (e) {
      setErr("Failed to apply settings: " + e.message);
    } finally {
      setApplying(false);
    }
  };

  const save = async (section) => {
    try {
      const body = {};
      const flat = SECTIONS.find((s) => s[0] === section)?.[2] || [];
      flat.forEach(([name]) => {
        const v = getPath(cfg[section], name);
        if (v !== undefined) {
          const top = name.split(".")[0];
          body[top] = name.includes(".") ? cfg[section][top] : v;
        }
      });
      await api.putSettings(section, body);
      setSaved(`${section} saved ✓`);
      setTimeout(() => setSaved(""), 2500);
    } catch (e) { setErr(e.message); }
  };

  const toggleReal = async (enabled) => {
    try {
      await api.realExecution(enabled);
      setSaved(enabled ? "REAL EXECUTION ENABLED — orders will still pass every risk gate"
                       : "real execution disabled");
      api.risk().then(setRiskState);
    } catch (e) { setErr(e.message); }
  };

  const triggerBackup = async () => {
    setBackupMsg("Creating backup...");
    try {
      const res = await api.backup();
      setBackupMsg(`Backup created: ${res.filename} (${res.size_mb} MB)`);
      api.backups().then(setBackupsList);
    } catch (e) {
      setBackupMsg(`Backup error: ${e.message}`);
    }
  };

  if (!cfg) return <Spinner />;

  const cores = resources?.cpu?.physical_cores || 2;
  const logicalCores = resources?.cpu?.logical_processors || 4;
  const estimatedWorkers = Math.max(1, Math.round(logicalCores * (cpuTarget / 100)));

  return (
    <div>
      <div className="flex justify-between items-center" style={{ marginBottom: "0.5rem" }}>
        <div>
          <h2 className="page-title">Settings</h2>
          <div className="page-sub">
            All values persist to CONFIG/settings.json and apply dynamically across running services.
            {saved && <b className="pos" style={{ marginLeft: 8 }}>{saved}</b>}
          </div>
        </div>
        <button
          className="btn btn-primary"
          style={{
            padding: "8px 24px",
            fontSize: "0.95rem",
            fontWeight: 800,
            background: "linear-gradient(90deg, #2563eb, #0891b2)",
            boxShadow: "0 4px 14px rgba(37,99,235,0.4)",
            cursor: "pointer",
          }}
          disabled={applying}
          onClick={handleApplyAllSettings}
        >
          {applying ? "⚙ APPLYING..." : "💾 APPLY SETTINGS"}
        </button>
      </div>
      <ErrorNote err={err} />

      {/* Applied Confirmation Box */}
      {applyResult && (
        <div style={{
          marginBottom: 14,
          padding: "10px 14px",
          borderRadius: "5px",
          background: "rgba(16, 185, 129, 0.15)",
          border: "1px solid rgba(16, 185, 129, 0.4)",
          color: "#a7f3d0",
          fontSize: "0.85rem",
        }}>
          <b>✓ SETTINGS APPLIED & PERSISTED (CONFIG/settings.json)</b>
          {applyResult.changes?.length > 0 && (
            <ul style={{ margin: "6px 0 0 0", paddingLeft: "18px" }}>
              {applyResult.changes.map((c, idx) => (
                <li key={idx}>{c}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* V3.5 Hardware Resource & Acceleration Control Panel */}
      <div className="panel" style={{
        marginBottom: 14,
        background: "linear-gradient(180deg, #162032 0%, #0d1522 100%)",
        border: "1px solid var(--border)",
        borderRadius: "6px",
        boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
      }}>
        <div className="flex justify-between items-center" style={{ marginBottom: 12, flexWrap: "wrap", gap: "8px" }}>
          <div>
            <h3 style={{ margin: 0, color: "var(--fg)", fontSize: "1rem" }}>
              ⚡ HARDWARE RESOURCE & ACCELERATION CONTROL (V3.5)
            </h3>
            <p className="muted" style={{ margin: "2px 0 0 0", fontSize: "0.78rem" }}>
              Dynamic CPU target scaling (25%, 50%, 75%, 100%), independent RAM telemetry, and worker pool governing.
            </p>
          </div>
          <button
            className="btn btn-sm btn-primary"
            disabled={applying}
            onClick={handleApplyAllSettings}
            style={{ fontWeight: 700 }}
          >
            APPLY SETTINGS
          </button>
        </div>

        <div className="grid cols-2" style={{ gap: "16px", marginBottom: "12px" }}>
          {/* CPU Target Control */}
          <div style={{ padding: "12px", background: "rgba(0,0,0,0.25)", borderRadius: "5px", border: "1px solid rgba(255,255,255,0.06)" }}>
            <div className="flex justify-between items-center" style={{ marginBottom: 6 }}>
              <label style={{ fontSize: "0.85rem", fontWeight: 700, color: "var(--fg)" }}>
                CPU UTILIZATION TARGET:
              </label>
              <span className="mono font-bold" style={{ fontSize: "1.1rem", color: "var(--blue)" }}>
                {cpuTarget}%
              </span>
            </div>
            <div style={{ fontSize: "0.75rem", color: "var(--muted)", marginBottom: 8 }}>
              Dynamic worker pool: <b style={{ color: "var(--green)" }}>{estimatedWorkers} workers</b> allocated on {logicalCores} logical threads ({cores} physical cores).
            </div>

            {/* Slider Control */}
            <input
              type="range"
              min="10"
              max="100"
              step="5"
              value={cpuTarget}
              onChange={(e) => setCpuTarget(Number(e.target.value))}
              style={{ width: "100%", marginBottom: "10px", cursor: "pointer", accentColor: "var(--blue)" }}
            />

            {/* Quick 25%, 50%, 75%, 100% and 10% step presets */}
            <div style={{ display: "flex", gap: "4px", flexWrap: "wrap", marginBottom: "8px" }}>
              {[25, 50, 75, 100].map((step) => (
                <button
                  key={step}
                  onClick={() => setCpuTarget(step)}
                  style={{
                    padding: "4px 10px",
                    fontSize: "0.76rem",
                    borderRadius: "3px",
                    border: "1px solid var(--border)",
                    background: cpuTarget === step ? "var(--blue)" : "rgba(255,255,255,0.07)",
                    color: cpuTarget === step ? "#fff" : "var(--fg)",
                    fontWeight: cpuTarget === step ? 800 : 700,
                    cursor: "pointer",
                  }}
                >
                  {step}%
                </button>
              ))}
              <span style={{ margin: "0 2px", color: "var(--border)", alignSelf: "center" }}>|</span>
              {[10, 20, 30, 40, 60, 70, 80, 90].map((step) => (
                <button
                  key={step}
                  onClick={() => setCpuTarget(step)}
                  style={{
                    padding: "3px 6px",
                    fontSize: "0.72rem",
                    borderRadius: "3px",
                    border: "1px solid var(--border)",
                    background: cpuTarget === step ? "var(--blue)" : "rgba(255,255,255,0.03)",
                    color: cpuTarget === step ? "#fff" : "var(--fg-dim)",
                    fontWeight: cpuTarget === step ? 800 : 500,
                    cursor: "pointer",
                  }}
                >
                  {step}%
                </button>
              ))}
            </div>

            <div style={{ fontSize: "0.72rem", color: "var(--muted)", paddingTop: "4px", borderTop: "1px dashed rgba(255,255,255,0.1)" }}>
              Live Telemetry: System CPU <b>{resources?.cpu?.percent ?? 0}%</b> · App CPU <b>{resources?.cpu?.app_percent ?? 0}%</b> · App RAM <b>{resources?.memory?.app_used_mb ?? 0} MB</b> (Peak: {resources?.memory?.app_peak_mb ?? 0} MB)
            </div>
          </div>

          {/* GPU Acceleration Control */}
          <div style={{ padding: "12px", background: "rgba(0,0,0,0.25)", borderRadius: "5px", border: "1px solid rgba(255,255,255,0.06)" }}>
            <div className="flex justify-between items-center" style={{ marginBottom: 6 }}>
              <label style={{ fontSize: "0.85rem", fontWeight: 700, color: "var(--fg)" }}>
                GPU ACCELERATION:
              </label>
              <span className={`pill ${gpuToggle ? "pos" : "muted"}`} style={{ fontSize: "0.8rem", fontWeight: 800 }}>
                {gpuToggle ? "ENABLED" : "DISABLED"}
              </span>
            </div>
            <div style={{ fontSize: "0.75rem", color: "var(--muted)", marginBottom: 8 }}>
              Hardware probe: <b>{resources?.gpu?.name || "CPU fallback"}</b> · VRAM: {resources?.gpu?.vram_used_mb ?? 0} / {resources?.gpu?.vram_total_mb ?? 0} MB
            </div>
            <div style={{ display: "flex", gap: "8px" }}>
              <button
                className={`btn btn-sm ${gpuToggle ? "btn-primary" : "btn-subtle"}`}
                style={{ flex: 1, fontWeight: 700 }}
                onClick={() => setGpuToggle(true)}
              >
                ON (Accelerated)
              </button>
              <button
                className={`btn btn-sm ${!gpuToggle ? "btn-primary" : "btn-subtle"}`}
                style={{ flex: 1, fontWeight: 700 }}
                onClick={() => setGpuToggle(false)}
              >
                OFF (CPU Only)
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Backup Section (spec §40) */}
      <div className="panel" style={{ marginBottom: 14, borderColor: "var(--blue)" }}>
        <div className="flex justify-between items-center">
          <div>
            <h3 style={{ margin: 0 }}>💾 DATABASE & RESEARCH BACKUP (spec §40)</h3>
            <p className="muted" style={{ fontSize: 12, margin: "4px 0 0 0" }}>
              Creates a consistent snapshot archive containing SQLite database, config, version manifest, and research summaries.
            </p>
          </div>
          <button className="btn btn-primary" onClick={triggerBackup}>
            📦 BACKUP NOW
          </button>
        </div>
        {backupMsg && (
          <div className="pos" style={{ marginTop: 8, fontSize: 13 }}>
            {backupMsg}
          </div>
        )}
        {backupsList.length > 0 && (
          <div style={{ marginTop: 10, fontSize: 12 }}>
            <span className="muted">Recent Backups in BACKUPS/:</span>
            <ul style={{ margin: "4px 0 0 0", paddingLeft: 20 }}>
              {backupsList.slice(0, 3).map((b) => (
                <li key={b.filename} className="mono">
                  {b.filename} ({b.size_mb} MB)
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* DATA MANAGEMENT Section (Spec V2.8 Step 4) */}
      <div className="panel" style={{
        marginBottom: 14,
        background: "linear-gradient(180deg, #181d28 0%, #0f141c 100%)",
        border: "1px solid #334155",
        borderRadius: "6px",
        boxShadow: "0 4px 12px rgba(0,0,0,0.35)",
      }}>
        <div className="flex justify-between items-center" style={{ marginBottom: 12, flexWrap: "wrap", gap: "8px" }}>
          <div>
            <h3 style={{ margin: 0, color: "#f8fafc", fontSize: "1.05rem", display: "flex", alignItems: "center", gap: "8px" }}>
              🗄️ DATA MANAGEMENT
              <span className="pill" style={{
                fontSize: "0.75rem",
                fontWeight: 800,
                background: dataStatus?.data_source === "MT5 REAL" ? "rgba(16, 185, 129, 0.2)" : (dataStatus?.data_source === "SIMULATOR" ? "rgba(245, 158, 11, 0.2)" : "rgba(148, 163, 184, 0.15)"),
                color: dataStatus?.data_source === "MT5 REAL" ? "#34d399" : (dataStatus?.data_source === "SIMULATOR" ? "#fbbf24" : "#94a3b8"),
                border: `1px solid ${dataStatus?.data_source === "MT5 REAL" ? "#10b981" : (dataStatus?.data_source === "SIMULATOR" ? "#f59e0b" : "#64748b")}`,
              }}>
                SOURCE: {dataStatus?.data_source || "CHECKING..."}
              </span>
            </h3>
            <p className="muted" style={{ margin: "3px 0 0 0", fontSize: "0.78rem" }}>
              Authoritative market data management, derived cache purging, research node cleanup, and system reset controls.
            </p>
          </div>
          <button
            className="btn btn-sm"
            onClick={refreshDataStatus}
            disabled={dataStatusLoading}
            style={{ fontSize: "0.78rem" }}
          >
            {dataStatusLoading ? "⟳ REFRESHING..." : "⟳ REFRESH STATUS"}
          </button>
        </div>

        {clearActionMsg && (
          <div style={{
            marginBottom: 12,
            padding: "8px 12px",
            borderRadius: "4px",
            background: "rgba(16, 185, 129, 0.15)",
            border: "1px solid rgba(16, 185, 129, 0.4)",
            color: "#6ee7b7",
            fontSize: "0.85rem",
          }}>
            ✓ {clearActionMsg}
          </div>
        )}

        {/* Real Raw Data Status Badges */}
        <div style={{
          padding: "12px",
          background: "rgba(0,0,0,0.3)",
          borderRadius: "5px",
          border: "1px solid rgba(255,255,255,0.06)",
          marginBottom: 14,
        }}>
          <div style={{ fontSize: "0.8rem", fontWeight: 700, color: "#cbd5e1", marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
            <span>REAL RAW DATA STATUS (DATA/MT5/XAUUSD):</span>
            <span className="muted" style={{ fontWeight: 400 }}>
              Validated Real Datasets: <b>{dataStatus?.valid_datasets_count ?? 0} / 5</b>
            </span>
          </div>
          <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", marginBottom: 12 }}>
            {["M1", "M5", "M15", "M30", "H1"].map((tf) => {
              const status = dataStatus?.timeframes?.[tf] || "CHECKING";
              const isFound = status === "FOUND";
              const isInvalid = status === "INVALID";
              const color = isFound ? "#34d399" : (isInvalid ? "#f87171" : "#94a3b8");
              const bg = isFound ? "rgba(16, 185, 129, 0.15)" : (isInvalid ? "rgba(239, 68, 68, 0.15)" : "rgba(100, 116, 139, 0.15)");
              const border = isFound ? "#059669" : (isInvalid ? "#dc2626" : "#475569");
              return (
                <div
                  key={tf}
                  style={{
                    padding: "6px 12px",
                    borderRadius: "4px",
                    background: bg,
                    border: `1px solid ${border}`,
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                  }}
                >
                  <span className="mono" style={{ fontWeight: 800, fontSize: "0.85rem", color: "#f8fafc" }}>
                    {tf}:
                  </span>
                  <span style={{ fontSize: "0.78rem", fontWeight: 800, color: color }}>
                    {status}
                  </span>
                </div>
              );
            })}
          </div>

          {/* Size Metrics */}
          <div className="grid cols-4" style={{ gap: "8px", fontSize: "0.75rem", borderTop: "1px solid rgba(255,255,255,0.06)", paddingTop: 8 }}>
            <div>
              <span className="muted">Raw XAUUSD Size:</span>{" "}
              <b className="mono" style={{ color: "#f1f5f9" }}>
                {dataStatus?.xauusd_size_bytes != null ? `${(dataStatus.xauusd_size_bytes / 1024 / 1024).toFixed(2)} MB` : "–"}
              </b>
            </div>
            <div>
              <span className="muted">Derived Cache Size:</span>{" "}
              <b className="mono" style={{ color: "#f1f5f9" }}>
                {dataStatus?.cache_size_bytes != null ? `${(dataStatus.cache_size_bytes / 1024 / 1024).toFixed(2)} MB` : "–"}
              </b>
            </div>
            <div>
              <span className="muted">Persisted Nodes:</span>{" "}
              <b className="mono" style={{ color: "#f1f5f9" }}>
                {dataStatus?.nodes_count ?? "–"}
              </b>
            </div>
            <div>
              <span className="muted">Persisted Genomes:</span>{" "}
              <b className="mono" style={{ color: "#f1f5f9" }}>
                {dataStatus?.genomes_count ?? "–"}
              </b>
            </div>
          </div>
        </div>

        {/* 5 Management Action Buttons */}
        <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
          <button
            className="btn btn-sm"
            style={{
              borderColor: "#d97706",
              color: "#fbbf24",
              background: "rgba(217, 119, 6, 0.1)",
              fontWeight: 700,
            }}
            onClick={() => setClearModal({
              type: "xauusd",
              title: "Clear Authoritative XAUUSD Data",
              target: "DATA/MT5/XAUUSD",
              message: "Deletes all authoritative real raw XAUUSD market data files (M1, M5, M15, M30, H1) and database registrations. The next run will require acquiring fresh data from MT5.",
            })}
          >
            🗑️ CLEAR XAUUSD DATA
          </button>

          <button
            className="btn btn-sm"
            style={{
              borderColor: "#38bdf8",
              color: "#7dd3fc",
              background: "rgba(56, 189, 248, 0.1)",
              fontWeight: 700,
            }}
            onClick={() => setClearModal({
              type: "cache",
              title: "Clear Derived Cache",
              target: "CACHE/ & DATA/cache/",
              message: "Deletes cached features, temp files, screening results cache, and manifests cache. Raw market data remains completely intact.",
            })}
          >
            🧹 CLEAR CACHE
          </button>

          <button
            className="btn btn-sm"
            style={{
              borderColor: "#818cf8",
              color: "#c7d2fe",
              background: "rgba(129, 140, 248, 0.1)",
              fontWeight: 700,
            }}
            onClick={() => setClearModal({
              type: "nodes",
              title: "Clear Persisted Nodes",
              target: "DATA/nodes/ & Database strategies",
              message: "Deletes all persisted research node records, backtests, validations, and strategy artifacts. Raw market data in DATA/MT5/XAUUSD remains intact.",
            })}
          >
            🧬 CLEAR NODES
          </button>

          <button
            className="btn btn-sm"
            style={{
              borderColor: "#c084fc",
              color: "#e9d5ff",
              background: "rgba(192, 132, 252, 0.1)",
              fontWeight: 700,
            }}
            onClick={() => setClearModal({
              type: "genomes",
              title: "Clear Persisted Genomes",
              target: "DATA/genomes/ & CACHE/GENOMES/",
              message: "Deletes persisted genome JSON files and genome manifests. Raw market data remains intact.",
            })}
          >
            📑 CLEAR GENOMES
          </button>

          <button
            className="btn btn-sm danger"
            style={{
              marginLeft: "auto",
              fontWeight: 800,
              boxShadow: "0 2px 8px rgba(220, 38, 38, 0.4)",
            }}
            onClick={() => {
              setAllDataInput("");
              setClearModal({
                type: "all",
                title: "⚠️ NUCLEAR RESET: Clear All Data",
                target: "ALL (Market Data, Cache, Research State)",
                message: "High-risk nuclear reset! This deletes all raw market data, feature caches, strategy nodes, and research history. To confirm, type 'CLEAR ALL DATA' or 'DELETE THE DATA'.",
                requireText: true,
              });
            }}
          >
            💥 CLEAR ALL DATA
          </button>
        </div>
      </div>

      {/* Confirmation Modal */}
      {clearModal && (
        <div style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: "rgba(0, 0, 0, 0.78)",
          backdropFilter: "blur(3px)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 9999,
          padding: "20px",
        }}>
          <div style={{
            background: "#151b26",
            border: `1px solid ${clearModal.requireText ? "#ef4444" : "#f59e0b"}`,
            borderRadius: "8px",
            maxWidth: "520px",
            width: "100%",
            padding: "20px",
            boxShadow: "0 10px 30px rgba(0,0,0,0.6)",
          }}>
            <h3 style={{ margin: "0 0 10px 0", color: clearModal.requireText ? "#fca5a5" : "#fcd34d", fontSize: "1.15rem" }}>
              {clearModal.title}
            </h3>
            <div style={{ fontSize: "0.82rem", color: "#94a3b8", marginBottom: "8px" }}>
              Target: <code style={{ color: "#f8fafc", background: "rgba(255,255,255,0.08)", padding: "2px 6px", borderRadius: "3px" }}>{clearModal.target}</code>
            </div>
            <p style={{ fontSize: "0.85rem", color: "#cbd5e1", lineHeight: 1.5, marginBottom: "16px" }}>
              {clearModal.message}
            </p>

            {clearModal.requireText && (
              <div style={{ marginBottom: "16px" }}>
                <label style={{ display: "block", fontSize: "0.8rem", color: "#fca5a5", fontWeight: 700, marginBottom: "6px" }}>
                  Type <span className="mono font-bold" style={{ color: "#fff", background: "#7f1d1d", padding: "1px 5px", borderRadius: "3px" }}>CLEAR ALL DATA</span> or <span className="mono font-bold" style={{ color: "#fff", background: "#7f1d1d", padding: "1px 5px", borderRadius: "3px" }}>DELETE THE DATA</span> to confirm:
                </label>
                <input
                  type="text"
                  value={allDataInput}
                  onChange={(e) => setAllDataInput(e.target.value)}
                  placeholder="CLEAR ALL DATA or DELETE THE DATA"
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    background: "#090d14",
                    border: "1px solid #dc2626",
                    borderRadius: "4px",
                    color: "#f8fafc",
                    fontFamily: "monospace",
                    fontSize: "0.95rem",
                  }}
                  autoFocus
                />
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <button
                className="btn btn-subtle"
                onClick={() => { setClearModal(null); setAllDataInput(""); }}
                disabled={clearing}
              >
                Cancel
              </button>
              <button
                className={`btn ${clearModal.requireText ? "danger" : "btn-primary"}`}
                onClick={handleExecuteClear}
                disabled={clearing || (clearModal.requireText && allDataInput !== "CLEAR ALL DATA" && allDataInput !== "DELETE THE DATA")}
                style={{ fontWeight: 700 }}
              >
                {clearing ? "Processing..." : (clearModal.requireText ? "CONFIRM NUCLEAR RESET" : "Confirm Clear")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Real-Money Execution Risk Panel */}
      <div className="panel" style={{ marginBottom: 14, borderColor: "#7f1d1d" }}>
        <h3 style={{ color: "#fca5a5" }}>Real-money execution (NEVER automatic)</h3>
        <div style={{ fontSize: 13, marginBottom: 8 }}>
          Current state: <b className={riskState?.real_execution_enabled ? "neg" : "pos"}>
            {riskState?.real_execution_enabled ? "ENABLED" : "DISABLED"}</b>
          {" "}· kill switch: <b>{riskState?.kill_switch ? "ENGAGED" : "released"}</b>
        </div>
        <p className="muted" style={{ fontSize: 12 }}>
          Requires a REAL MT5 bridge (Windows terminal connected) and typing the confirmation phrase.
          Even when enabled, every order passes the independent risk layer (spread, size, rate,
          session, drawdown, slippage limits, kill switch).
        </p>
        <div className="btn-row">
          <input placeholder='type: ENABLE REAL TRADING' value={realConfirm}
                 onChange={(e) => setRealConfirm(e.target.value)} style={{ width: 240 }} />
          <button className="btn danger" disabled={realConfirm !== "ENABLE REAL TRADING"}
                  onClick={() => toggleReal(true)}>Enable real execution</button>
          <button className="btn" onClick={() => toggleReal(false)}>Disable</button>
        </div>
      </div>

      {SECTIONS.map(([section, title, fields]) => (
        <div className="panel" key={section} style={{ marginBottom: 14 }}>
          <div className="flex justify-between items-center" style={{ marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>{title}</h3>
            <div className="flex gap-2">
              <button className="btn btn-sm" onClick={() => save(section)}>Save {section}</button>
              <button className="btn btn-sm btn-primary" onClick={handleApplyAllSettings}>Apply All</button>
            </div>
          </div>
          <div className="settings-grid">
            {fields.map(([name, label, type]) => {
              let v = getPath(cfg[section], name);
              if (type === "csv" && Array.isArray(v)) v = v.join(", ");
              if (type === "bool") v = !!v;
              return <Field key={name} name={name} label={label} type={type}
                            value={v} onChange={change(section)} />;
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
