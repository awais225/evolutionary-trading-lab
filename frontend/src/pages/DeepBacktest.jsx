// V6.6 — DEEP TESTING tab: a persistent, MT5-terminal-style workspace.
//
// Redesigned under the V6.6 directive (Deep Testing only — every other tab,
// and LIVE TESTING in particular, stays frozen):
//   * persistent workspace state: global settings, period, filters, per-node
//     experimental settings, selected node, scroll-safe layout;
//   * the nine period buttons (1D/7D/14D/1M/3M/6M/1Y/2Y/3Y) select the HISTORY
//     RANGE only — the node's configured candle timeframe is never changed;
//   * authoritative inventory: the SAME nodes index + the SAME qualification
//     predicate as every other tab (counts come from the server's buckets —
//     never hardcoded); filters QUALIFIED (default) / ALIVE / DEAD / ALL;
//   * per node: GET MT5 DATA (incremental missing-range fetch) and DEEPTEST
//     (the node's ACTUAL strategy on validated history — historical simulation
//     only; no broker orders, no live-loop interference);
//   * the mandatory incomplete-history confirmation (YES-PROCEED / NO-CANCEL /
//     MANUALLY LOAD DATA-IMPORT CSV) with requested-vs-actual coverage;
//   * DEEPTESTED NODES: scenario count, preferred scenario, coverage (PARTIAL
//     COVERAGE always marked), statistics priority, inspect/compare/prefer;
//   * statistics priority (shared projection): preferred DeepTest → latest
//     valid DeepTest → generation → N/A.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, fmt } from "../api";
import { Badge, StateBlock } from "../components/ui";
import StrategyDrawer from "../components/StrategyDrawer";
import DeepCoveragePanel from "../components/DeepCoveragePanel";
import DeepCsvImportDialog from "../components/DeepCsvImportDialog";
import DeepScenarioPanel from "../components/DeepScenarioPanel";
import { ScheduleDialog } from "../components/LiveTestingPanels";
import { arr, txt } from "../lib/safe.js";

/* §5 — the node filters. ``qualified`` is the default: Deep Testing lists every
 * qualified node against the authoritative predicate; ALIVE / DEAD / ALL are one
 * click away. (The server's bucket names are the API contract: "failed" is the
 * judged-dead bucket.) */
export const NODE_FILTERS = [
  { key: "qualified", label: "Qualified", hint: "cleared the research gates against real data" },
  { key: "alive", label: "Alive", hint: "qualified + still in research (not a dead end)" },
  { key: "failed", label: "Dead", hint: "judged and did not make it" },
  { key: "all", label: "All", hint: "every node in the experiment" },
];

const PERIOD_KEYS = ["1D", "7D", "14D", "1M", "3M", "6M", "1Y", "2Y", "3Y"];

const fmtMoney = (v) => (v == null ? "N/A" : `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`);
const fmtPct = (v) => (v == null ? "N/A" : `${(Number(v) * 100).toFixed(3)}%`);
const fmtNum = (v, d = 3) => (v == null ? "N/A" : (typeof v === "number" ? Number(v).toFixed(d) : String(v)));
const isoOf = (ts) => (ts == null ? "—" : new Date(Number(ts) * 1000).toISOString().replace("T", " ").slice(0, 16) + "Z");

/* §4 — the default global settings (validated server-side, snapshotted per
 * scenario). Shown here exactly as stored — never rounded silently. */
const DEFAULT_SETTINGS = { total_capital: 200000, risk_pct: 1.0, max_active_trades_per_node: 0 };

export default function DeepBacktest() {
  // ---- persistent workspace state -----------------------------------------
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);
  const [settingsSaved, setSettingsSaved] = useState(null);
  const [settingsErr, setSettingsErr] = useState(null);
  const [periodKey, setPeriodKey] = useState("3M");
  const [filter, setFilter] = useState("qualified");
  const [search, setSearch] = useState("");
  const [nodes, setNodes] = useState([]);
  const [counts, setCounts] = useState(null);
  const [experiment, setExperiment] = useState(null);
  const [dtNodes, setDtNodes] = useState([]);       // DEEPTESTED NODES summaries
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState({});      // node_id -> true
  const [rowCfg, setRowCfg] = useState({});          // node_id -> per-node experimental settings
  const [fetchStates, setFetchStates] = useState({}); // "SYM|TF" -> job state
  const [runBusy, setRunBusy] = useState({});        // node_id -> "queued"|"running"|error text
  const [inspectNode, setInspectNode] = useState(null);
  const [drawerId, setDrawerId] = useState(null);
  const [csvFor, setCsvFor] = useState(null);        // {symbol, timeframe}
  const [confirmState, setConfirmState] = useState(null); // §9 popup
  const [sortKey, setSortKey] = useState("node_id");
  const [sortDir, setSortDir] = useState(1);
  // V6.7 §7.2 — the SCENARIO popup (scheduling/configuration; DeepTesting-only
  // storage through the shared presentation component — live settings frozen).
  const [scenarioFor, setScenarioFor] = useState(null);
  // V6.7 §8 — DEEPTESTED NODES table search + sort
  const [dtSearch, setDtSearch] = useState("");
  const [dtSortKey, setDtSortKey] = useState("node_id");
  const [dtSortDir, setDtSortDir] = useState(1);
  const pollRef = useRef(null);

  // ---- data loading --------------------------------------------------------
  const loadNodes = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.nodes({ filter, search, limit: 0, offset: 0, sort_by: "node_id" });
      setNodes(arr(res.nodes));
      setCounts(res.filter_totals || res.counts || null);
      setExperiment(res.experiment || null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [filter, search]);

  const loadDt = useCallback(async () => {
    try {
      const res = await api.deeptestNodes();
      setDtNodes(arr(res.nodes));
    } catch { /* the DEEPTESTED section degrades to empty, never breaks the page */ }
  }, []);

  const loadSettings = useCallback(async () => {
    try {
      const res = await api.deeptestSettings();
      if (res && res.settings) setSettings({ ...DEFAULT_SETTINGS, ...res.settings });
    } catch { /* defaults stay visible */ }
  }, []);

  useEffect(() => {
    loadNodes();
    loadDt();
    loadSettings();
  }, [loadNodes, loadDt, loadSettings]);

  // light polling while any job is active (fetch or run)
  useEffect(() => {
    const active = Object.keys(fetchStates).some((k) => fetchStates[k]?.status === "FETCHING")
      || Object.keys(runBusy).length > 0;
    if (!active) return undefined;
    const t = setInterval(async () => {
      try {
        const pairs = Object.keys(fetchStates).filter((k) => fetchStates[k]?.status === "FETCHING");
        for (const key of pairs) {
          const [sym, tf] = key.split("|");
          const st = await api.deeptestFetchStatus(sym, tf);
          if (st.job) setFetchStates((m) => ({ ...m, [key]: st.job }));
        }
        if (Object.keys(runBusy).length) {
          await loadDt();
          const res = await api.deeptestScenarios({ statuses: "QUEUED,FETCHING,VALIDATING,RUNNING" });
          const running = new Set(arr(res.scenarios).map((s) => String(s.node_id)));
          setRunBusy((m) => {
            const out = {};
            for (const [nid, v] of Object.entries(m)) if (running.has(String(nid)) || v === "queued") out[nid] = v;
            return out;
          });
          if (inspectNode != null) {
            // refresh the open scenario panel via key bump
            setInspectNode((x) => x);
          }
        }
      } catch { /* polling never crashes the page */ }
    }, 4000);
    return () => clearInterval(t);
  }, [fetchStates, runBusy, loadDt, inspectNode]);

  // ---- settings ------------------------------------------------------------
  const saveSettings = async () => {
    setSettingsErr(null);
    try {
      const res = await api.deeptestSaveSettings({
        total_capital: settings.total_capital,
        risk_pct: settings.risk_pct,
        max_active_trades_per_node: settings.max_active_trades_per_node,
      });
      if (!res.ok) {
        setSettingsErr((res.errors || []).map((e) => `${e.field}: ${e.error}`).join("; ") || "invalid settings");
        return;
      }
      setSettings({ ...DEFAULT_SETTINGS, ...res.settings });
      setSettingsSaved(new Date().toLocaleTimeString());
    } catch (e) {
      setSettingsErr(e.message || String(e));
    }
  };

  // ---- per-node experimental settings --------------------------------------
  const cfgOf = useCallback((nid) => ({
    risk_mode: "PERCENT", risk_pct: settings.risk_pct, risk_amount: 1000,
    max_trades: "", sl_offset_pips: 0, tp_offset_pips: 0,
    ...(rowCfg[nid] || {}),
  }), [rowCfg, settings.risk_pct]);

  const setCfgOf = (nid, patch) => setRowCfg((m) => ({ ...m, [nid]: { ...(m[nid] || {}), ...patch } }));

  // ---- actions -------------------------------------------------------------
  const onFetch = async (symbol, timeframe) => {
    const key = `${String(symbol).toUpperCase()}|${String(timeframe).toUpperCase()}`;
    try {
      const res = await api.deeptestFetch({ symbol, timeframe });
      if (res.job) setFetchStates((m) => ({ ...m, [key]: res.job }));
    } catch (e) {
      setFetchStates((m) => ({ ...m, [key]: { status: "FAILED", operation: e.message || String(e) } }));
    }
  };

  const nodeSettingsOf = (c) => ({
    risk_mode: c.risk_mode,
    risk_pct: c.risk_mode === "PERCENT" ? Number(c.risk_pct) : null,
    risk_amount: c.risk_mode === "AMOUNT" ? Number(c.risk_amount) : null,
    max_trades: c.max_trades === "" ? null : Number(c.max_trades),
    sl_offset_pips: Number(c.sl_offset_pips) || 0,
    tp_offset_pips: Number(c.tp_offset_pips) || 0,
  });

  const startDeeptest = async (node) => {
    const nid = node.node_id;
    const c = cfgOf(nid);
    setRunBusy((m) => ({ ...m, [nid]: "queued" }));
    try {
      // preflight must see EXACTLY the settings the run will use — the §9
      // confirmation is bound to them
      const pre = await api.deeptestPreflight({
        node_id: nid, duration_key: periodKey, settings,
        node_settings: nodeSettingsOf(c),
      });
      if (!pre.ok && !pre.needs_confirmation) {
        setRunBusy({ [nid]: pre.error || "preflight refused" });
        return;
      }
      if (pre.needs_confirmation) {
        setConfirmState({ pre, node, cfg: c });
        setRunBusy((m) => { const o = { ...m }; delete o[nid]; return o; });
        return;
      }
      await launchRun(node, c);
    } catch (e) {
      setRunBusy({ [nid]: e.message || String(e) });
    }
  };

  const launchRun = async (node, c) => {
    const nid = node.node_id;
    const res = await api.deeptestRun({
      node_id: nid,
      duration_key: periodKey,
      settings,
      node_settings: nodeSettingsOf(c),
      idempotency_key: `dt-${nid}-${periodKey}-${Date.now()}`,
    });
    if (res.ok) {
      setRunBusy((m) => ({ ...m, [nid]: "running" }));
      loadDt();
    } else if (res.needs_confirmation) {
      setConfirmState({ pre: res, node, cfg: c });
      setRunBusy((m) => { const o = { ...m }; delete o[nid]; return o; });
    } else {
      setRunBusy({ [nid]: res.error || "run refused" });
    }
  };

  const resolveConfirm = async (decision) => {
    const st = confirmState;
    if (!st) return;
    const fp = st.pre?.confirmation?.confirmation_fingerprint
      || st.pre?.coverage?.confirmation_fingerprint;
    try {
      if (decision === "YES_PROCEED") {
        await api.deeptestConfirm({ confirmation_fingerprint: fp, decision, node_id: st.node.node_id });
        setConfirmState(null);
        await launchRun(st.node, st.cfg);
        return;
      }
      if (decision === "NO_CANCEL") {
        await api.deeptestConfirm({ confirmation_fingerprint: fp, decision, node_id: st.node.node_id });
        setConfirmState(null);
        return;
      }
      if (decision === "LOAD_CSV") {
        await api.deeptestConfirm({ confirmation_fingerprint: fp, decision, node_id: st.node.node_id });
        setCsvFor({ symbol: st.node.symbol, timeframe: st.node.timeframe });
        setConfirmState(null);
      }
    } catch (e) {
      setConfirmState(null);
      setRunBusy({ [st.node.node_id]: e.message || String(e) });
    }
  };

  // ---- derived -------------------------------------------------------------
  const dtByNode = useMemo(() => {
    const m = {};
    for (const d of dtNodes || []) m[d.node_id] = d;
    return m;
  }, [dtNodes]);

  const sorted = useMemo(() => {
    // V6.7 §4.3 — sorting over the FULL dataset (limit:0 fetch), real numeric
    // comparisons, missing metrics never rank as best (always last), stable
    // tie-break on the canonical node id. Generation metrics and DeepTest
    // metrics are distinct sort keys — one can never read the other's values.
    const rows = [...nodes];
    const val = (n) => {
      switch (sortKey) {
        case "gen_return": return n.generation_metrics?.return_pct;
        case "gen_pf": return n.generation_metrics?.profit_factor;
        case "gen_dd": return n.generation_metrics?.max_drawdown_pct;
        case "gen_wr": return n.generation_metrics?.win_rate;
        case "gen_trades": return n.generation_metrics?.trades;
        case "gen_net": return n.generation_metrics?.net_profit;
        case "fitness": return n.fitness;
        case "dt": return dtByNode[n.node_id]?.scenario_count || 0;
        case "dt_return": return dtByNode[n.node_id]?.preferred?.analytics?.summary?.net_return_pct;
        case "coverage": return n.backtest_coverage?.bars;
        case "symbol": return n.symbol;
        case "timeframe": return n.timeframe;
        case "status": return n.bucket;
        case "generation": return n.generation;
        default: return n.node_id;
      }
    };
    rows.sort((a, b) => {
      const av = val(a); const bv = val(b);
      if (av == null && bv == null) return a.node_id - b.node_id;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string" || typeof bv === "string") {
        const cmp = String(av).localeCompare(String(bv));
        if (cmp !== 0) return sortDir > 0 ? cmp : -cmp;
      } else {
        if (av < bv) return -sortDir;
        if (av > bv) return sortDir;
      }
      return a.node_id - b.node_id;   // stable canonical-id tie-break
    });
    return rows;
  }, [nodes, sortKey, sortDir, dtByNode]);

  // V6.7 §8 — DEEPTESTED NODES rows: generation metrics joined from the same
  // canonical inventory, DeepTest metrics from the preferred scenario; sortable
  // (numeric, missing-last, stable id tie-break) and searchable.
  const genById = useMemo(() => {
    const m = {};
    for (const n of nodes) m[n.node_id] = { gen: n.generation_metrics, research_node_num: n.research_node_num };
    return m;
  }, [nodes]);
  const dtRows = useMemo(() => {
    const q = String(dtSearch || "").trim().toLowerCase();
    let rows = (dtNodes || []).map((d) => ({
      ...d,
      gen: genById[d.node_id]?.gen || d.generation_metrics || null,
      research_node_num: d.research_node_num ?? genById[d.node_id]?.research_node_num ?? null,
    }));
    if (q) {
      rows = rows.filter((d) => String(d.node_id).includes(q)
        || String(d.research_node_num ?? "").includes(q)
        || `node #${d.node_id}`.includes(q)
        || `research #${d.research_node_num}`.includes(q));
    }
    const val = (d) => {
      const sum = d.preferred?.analytics?.summary || {};
      switch (dtSortKey) {
        case "gen_return": return d.gen?.return_pct;
        case "gen_pf": return d.gen?.profit_factor;
        case "gen_dd": return d.gen?.max_drawdown_pct;
        case "gen_trades": return d.gen?.trades;
        case "dt_return": return sum.net_return_pct;
        case "dt_pf": return sum.profit_factor;
        case "dt_dd": return sum.max_drawdown_pct;
        case "dt_wr": return sum.win_rate;
        case "dt_trades": return sum.trades;
        case "dt_net": return sum.net_profit;
        case "dt": return d.scenario_count || 0;
        case "last_run": return d.last_run?.finished_at;
        default: return d.node_id;
      }
    };
    rows.sort((a, b) => {
      const av = val(a); const bv = val(b);
      if (av == null && bv == null) return a.node_id - b.node_id;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av < bv) return -dtSortDir;
      if (av > bv) return dtSortDir;
      return a.node_id - b.node_id;
    });
    return rows;
  }, [dtNodes, genById, dtSearch, dtSortKey, dtSortDir]);
  const setDtSort = (k) => {
    if (dtSortKey === k) setDtSortDir((v) => -v);
    else { setDtSortKey(k); setDtSortDir(1); }
  };
  const dtArrow = (k) => (dtSortKey === k ? (dtSortDir > 0 ? " ↑" : " ↓") : "");

  const selectedNodes = useMemo(() => nodes.filter((n) => selected[n.node_id]), [nodes, selected]);
  const pairs = useMemo(() => {
    const seen = new Set();
    const out = [];
    for (const n of (selectedNodes.length ? selectedNodes : nodes).slice(0, 12)) {
      const key = `${n.symbol}|${n.timeframe}`;
      if (!seen.has(key)) { seen.add(key); out.push({ symbol: n.symbol, timeframe: n.timeframe }); }
    }
    return out;
  }, [nodes, selectedNodes]);

  const anySelected = selectedNodes.length > 0;

  return (
    <div data-testid="deeptest-workspace" style={{ padding: 12, fontSize: 12 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>Deep Testing</h2>
        <span style={{ opacity: 0.8 }}>
          persistent workspace · historical simulation only — never places an order
        </span>
        {experiment && (
          <span className="mono" style={{ fontSize: 11, opacity: 0.85 }}>
            experiment {txt(experiment.run_id)} · population {experiment.population ?? "—"}
          </span>
        )}
      </div>

      {/* ---- §4 global settings ------------------------------------------ */}
      <div className="panel" style={{ marginTop: 8 }}>
        <b style={{ fontSize: 12.5 }}>Global settings</b>
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center", marginTop: 6 }}>
          <label style={{ fontSize: 11.5 }}>Total Capital&nbsp;
            <input style={{ width: 110 }} value={settings.total_capital}
                   onChange={(e) => setSettings({ ...settings, total_capital: e.target.value })} />
          </label>
          <label style={{ fontSize: 11.5 }}>SL Risk per Trade % (global)&nbsp;
            <input style={{ width: 70 }} value={settings.risk_pct}
                   onChange={(e) => setSettings({ ...settings, risk_pct: e.target.value })} />
          </label>
          <label style={{ fontSize: 11.5 }}>Max Active Trades/Node&nbsp;
            <input style={{ width: 60 }} value={settings.max_active_trades_per_node}
                   onChange={(e) => setSettings({ ...settings, max_active_trades_per_node: e.target.value })} />
          </label>
          <button onClick={saveSettings}>Save settings</button>
          {settingsSaved && <span style={{ fontSize: 11, opacity: 0.8 }}>saved {settingsSaved}</span>}
          {settingsErr && <span className="kit-inline-err">{settingsErr}</span>}
        </div>
        <div style={{ fontSize: 10.5, opacity: 0.75, marginTop: 4 }}>
          Total Capital is the simulated starting capital of every new scenario. SL risk per trade is
          taken as a % of the scenario's simulated equity at trade entry (equity starts at Total
          Capital). Max Active Trades/Node: 0 = unlimited; 1 = one open simulated trade at a time
          (a new entry only after a real strategy exit — never a premature close). These settings
          apply to Deep Testing only and never touch the LIVE TESTING configuration.
        </div>
      </div>

      {/* ---- §6 period selector ------------------------------------------ */}
      <div className="panel" style={{ marginTop: 8 }}>
        <b style={{ fontSize: 12.5 }}>Period (history range only — the node's candle timeframe is never changed)</b>
        <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
          {PERIOD_KEYS.map((k) => (
            <button key={k} onClick={() => setPeriodKey(k)}
                    style={{ fontSize: 13, fontWeight: periodKey === k ? 700 : 400,
                             padding: "6px 12px", minWidth: 46,
                             outline: periodKey === k ? "2px solid #4a9" : "none" }}>
              {k}
            </button>
          ))}
          <span style={{ marginLeft: 8, fontSize: 10.5, opacity: 0.75, alignSelf: "center" }}>
            calendar-based windows measured backwards from now (UTC): days = 24-hour periods;
            months/years = calendar months/years. Explicit start/end are persisted with each scenario.
          </span>
        </div>
      </div>

      {/* ---- inventory filters + actions --------------------------------- */}
      <div className="panel" style={{ marginTop: 8 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <b style={{ fontSize: 12.5 }}>Nodes</b>
          {NODE_FILTERS.map((f) => (
            <button key={f.key} title={f.hint} onClick={() => setFilter(f.key)}
                    style={{ fontWeight: filter === f.key ? 700 : 400,
                             outline: filter === f.key ? "2px solid #4a9" : "none" }}>
              {f.label}
            </button>
          ))}
          <input placeholder="search node id / symbol / status" value={search}
                 onChange={(e) => setSearch(e.target.value)} style={{ width: 220 }} />
          {counts && (
            <span style={{ fontSize: 10.5, opacity: 0.8 }}>
              authoritative counts:
              {Object.entries(counts).slice(0, 8).map(([k, v]) => ` ${k}=${v}`)}
              {" "}— same predicate as every tab (never hardcoded)
            </span>
          )}
          <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <button disabled={!anySelected} onClick={() => selectedNodes.forEach((n) => startDeeptest(n))}>
              DEEPTEST selected ({selectedNodes.length})
            </button>
          </span>
        </div>
        <StateBlock loading={loading} error={error} onRetry={loadNodes}
                    empty={!loading && !error && !nodes.length} emptyText="no nodes match this filter" />
        <div className="table-scroll" style={{ marginTop: 6 }}>
          <table style={{ width: "100%", fontSize: 10.8 }} className="tbl mono">
            <thead>
              <tr>
                <th></th>
                <th onClick={() => { setSortKey("node_id"); setSortDir(sortKey === "node_id" ? -sortDir : 1); }}>node id (canonical · research)</th>
                <th onClick={() => { setSortKey("status"); setSortDir(sortKey === "status" ? -sortDir : 1); }}>status</th>
                <th onClick={() => { setSortKey("generation"); setSortDir(sortKey === "generation" ? -sortDir : 1); }}>gen</th>
                <th>name / species</th>
                <th onClick={() => { setSortKey("symbol"); setSortDir(sortKey === "symbol" ? -sortDir : 1); }}>symbol</th>
                <th onClick={() => { setSortKey("timeframe"); setSortDir(sortKey === "timeframe" ? -sortDir : 1); }}>candle TF</th>
                <th onClick={() => { setSortKey("gen_return"); setSortDir(sortKey === "gen_return" ? -sortDir : 1); }} title="Generation Return — from the stored generation record only">Gen Return</th>
                <th onClick={() => { setSortKey("gen_pf"); setSortDir(sortKey === "gen_pf" ? -sortDir : 1); }} title="Generation profit factor (ratio)">Gen PF</th>
                <th onClick={() => { setSortKey("gen_dd"); setSortDir(sortKey === "gen_dd" ? -sortDir : 1); }} title="Generation max drawdown">Gen DD</th>
                <th onClick={() => { setSortKey("gen_wr"); setSortDir(sortKey === "gen_wr" ? -sortDir : 1); }} title="Generation win rate">Gen WR</th>
                <th onClick={() => { setSortKey("gen_trades"); setSortDir(sortKey === "gen_trades" ? -sortDir : 1); }} title="Generation closed trades">Gen Trades</th>
                <th onClick={() => { setSortKey("fitness"); setSortDir(sortKey === "fitness" ? -sortDir : 1); }}>fitness</th>
                <th onClick={() => { setSortKey("dt_return"); setSortDir(sortKey === "dt_return" ? -sortDir : 1); }} title="Preferred DeepTest metrics — never mixed into the generation columns">DeepTest (preferred)</th>
                <th>scenario + coverage</th>
                <th>risk mode / value</th>
                <th>max trades</th>
                <th>SL / TP offsets</th>
                <th>schedule</th>
                <th onClick={() => { setSortKey("coverage"); setSortDir(sortKey === "coverage" ? -sortDir : 1); }} title="recorded bars of the data backing this node">data coverage</th>
                <th>actions</th>
                <th onClick={() => { setSortKey("dt"); setSortDir(sortKey === "dt" ? -sortDir : 1); }}>scenarios</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((n) => {
                const d = dtByNode[n.node_id] || null;
                const pref = d?.preferred || null;
                const c = cfgOf(n.node_id);
                const busy = runBusy[n.node_id];
                return (
                  <tr key={n.node_id}>
                    <td><input type="checkbox" checked={!!selected[n.node_id]}
                               onChange={(e) => setSelected((m) => ({ ...m, [n.node_id]: e.target.checked }))} /></td>
                    <td>
                      <button className="btn" style={{ fontSize: 10.5 }} onClick={() => setDrawerId(n.node_id)}>
                        {n.node_label || `Node #${n.node_id}`}
                      </button>
                      {n.research_node_num != null && (
                        <div className="muted" style={{ fontSize: 9.5 }} title="study-local display number (explicit second namespace)">
                          · research #{n.research_node_num}
                        </div>
                      )}
                    </td>
                    <td title={txt(n.bucket_reason)}>{txt(n.bucket_label || n.bucket)}</td>
                    <td>{n.generation ?? "—"}</td>
                    <td title={txt(n.innovation ? `mutation: ${n.innovation}` : "")}>
                      {txt(n.family || (n.signal_logic?.entry_long ? "strategy" : "—"))}
                    </td>
                    <td>{txt(n.symbol)}</td>
                    <td>{txt(n.timeframe)}</td>
                    {/* V6.7 §4.2 — generation metrics come from the stored generation
                        record ONLY; real zeros show as 0, missing stays N/A. */}
                    <td className="mono">{fmtPct(n.generation_metrics?.return_pct)}</td>
                    <td className="mono">{fmtNum(n.generation_metrics?.profit_factor, 3)}</td>
                    <td className="mono">{fmtPct(n.generation_metrics?.max_drawdown_pct)}</td>
                    <td className="mono">{fmtPct(n.generation_metrics?.win_rate)}</td>
                    <td className="mono">{n.generation_metrics?.trades ?? "N/A"}</td>
                    <td className="mono">{fmtNum(n.fitness, 4)}</td>
                    <td>
                      {pref ? (
                        <span>
                          ret {fmtPct(pref.analytics?.summary?.net_return_pct)}
                          {" "}· PF {fmtNum(pref.analytics?.summary?.profit_factor)}
                          {" "}· WR {fmtPct(pref.analytics?.summary?.win_rate)}
                          {" "}· {pref.analytics?.summary?.trades ?? "N/A"} trades
                          {n.metrics_source && (
                            <div style={{ fontSize: 9.5, opacity: 0.8 }}>
                              effective stats from {txt(n.metrics_source_label || n.metrics_source)}
                            </div>
                          )}
                        </span>
                      ) : <span className="muted">NOT DEEPTESTED</span>}
                    </td>
                    <td>
                      {pref ? (
                        <span>
                          {txt(pref.duration_key || "—")} · {txt(pref.status)}
                          {pref.coverage_status === "PARTIAL" ? " · " : " · "}
                          {pref.coverage_status === "PARTIAL"
                            ? <Badge tone="warn">PARTIAL COVERAGE</Badge>
                            : <span>{txt(pref.coverage_status || "—")}</span>}
                        </span>
                      ) : "—"}
                    </td>
                    <td>
                      <select value={c.risk_mode} style={{ fontSize: 10 }}
                              onChange={(e) => setCfgOf(n.node_id, { risk_mode: e.target.value })}>
                        <option value="PERCENT">PERCENT</option>
                        <option value="AMOUNT">AMOUNT</option>
                      </select>
                      {c.risk_mode === "PERCENT" ? (
                        <input style={{ width: 46, fontSize: 10 }} value={c.risk_pct}
                               onChange={(e) => setCfgOf(n.node_id, { risk_pct: e.target.value })} />
                      ) : (
                        <input style={{ width: 60, fontSize: 10 }} value={c.risk_amount}
                               onChange={(e) => setCfgOf(n.node_id, { risk_amount: e.target.value })} />
                      )}
                    </td>
                    <td>
                      <input style={{ width: 46, fontSize: 10 }} placeholder="0" value={c.max_trades}
                             onChange={(e) => setCfgOf(n.node_id, { max_trades: e.target.value })} />
                    </td>
                    <td>
                      <input style={{ width: 40, fontSize: 10 }} value={c.sl_offset_pips}
                             onChange={(e) => setCfgOf(n.node_id, { sl_offset_pips: e.target.value })} />
                      {" / "}
                      <input style={{ width: 40, fontSize: 10 }} value={c.tp_offset_pips}
                             onChange={(e) => setCfgOf(n.node_id, { tp_offset_pips: e.target.value })} />
                    </td>
                    <td title={txt(n.schedule?.description || "")}>
                      {txt(n.schedule?.description || "—")}
                    </td>
                    <td>{txt(n.backtest_coverage?.bars ?? "—")} bars</td>
                    <td>
                      <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                        <button className="btn" style={{ fontSize: 10 }} onClick={() => onFetch(n.symbol, n.timeframe)}>
                          GET MT5 DATA
                        </button>
                        <button className="btn" style={{ fontSize: 10, fontWeight: 700 }} disabled={!!busy}
                                onClick={() => startDeeptest(n)}>
                          {busy ? (busy === "queued" || busy === "running" ? `${busy}…` : "failed") : "DEEPTEST"}
                        </button>
                        {/* §7.2 — SCENARIO opens the scheduling/configuration popup
                            (DeepTesting-only storage); INSPECT opens saved results. */}
                        <button className="btn" style={{ fontSize: 10 }}
                                title="Scenario / scheduling configuration for DeepTesting (saved separately from live settings)"
                                onClick={() => setScenarioFor(n)}>
                          SCENARIO
                        </button>
                        <button className="btn" style={{ fontSize: 10 }}
                                title="Inspect saved DeepTest scenarios and trades"
                                onClick={() => { setInspectNode(n.node_id); }}>
                          INSPECT
                        </button>
                      </div>
                      {busy && busy !== "queued" && busy !== "running" && (
                        <div className="kit-inline-err" style={{ fontSize: 9.5 }}>{String(busy)}</div>
                      )}
                    </td>
                    <td>{d ? d.scenario_count : 0}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div style={{ fontSize: 10.5, opacity: 0.75, marginTop: 6 }}>
          statistics priority: <b>preferred DeepTest → latest valid DeepTest → generation → N/A</b>.
          Generation metrics are preserved separately with provenance and never disappear. Live
          operational metrics on the LIVE TESTING tab (status, P&amp;L, positions, account, execution)
          are untouched by this precedence — only performance statistics display through it.
        </div>
      </div>

      {/* ---- §7 MT5 DATA coverage ---------------------------------------- */}
      <DeepCoveragePanel pairs={pairs} fetchStates={fetchStates} onFetch={onFetch} />

      {/* ---- §8 DEEPTESTED NODES ---------------------------------------- */}
      <div className="panel" style={{ marginTop: 8 }}>
        <b style={{ fontSize: 12.5 }}>DEEPTESTED NODES</b>
        <div style={{ fontSize: 10.5, opacity: 0.75, marginTop: 2 }}>
          one row per DeepTested node — generation metrics and DeepTest metrics side by side,
          preferred scenario, coverage (PARTIAL always marked), scenario count, last run.
          Sortable and searchable; Inspect opens the saved scenarios, trades and analysis.
        </div>
        {!!dtNodes.length && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6, flexWrap: "wrap" }}>
            <input placeholder="search node id / research #" value={dtSearch}
                   onChange={(e) => setDtSearch(e.target.value)} style={{ width: 200 }} />
            <span style={{ fontSize: 10.5, opacity: 0.75 }}>
              {dtRows.length} of {dtNodes.length} nodes shown
            </span>
          </div>
        )}
        {!dtNodes.length && <StateBlock empty emptyText="no DeepTest scenarios yet — run DEEPTEST on a node first" />}
        {!!dtNodes.length && (
          <div className="scroll-y table-scroll" style={{ marginTop: 6, maxHeight: 360 }}>
            <table style={{ width: "100%", fontSize: 11 }} className="tbl mono">
              <thead>
                <tr>
                  <th onClick={() => setDtSort("node_id")}>node (canonical · research){dtArrow("node_id")}</th>
                  <th onClick={() => setDtSort("gen_return")} title="from the stored generation record">Gen Return{dtArrow("gen_return")}</th>
                  <th onClick={() => setDtSort("gen_pf")}>Gen PF{dtArrow("gen_pf")}</th>
                  <th onClick={() => setDtSort("gen_dd")}>Gen DD{dtArrow("gen_dd")}</th>
                  <th onClick={() => setDtSort("gen_trades")}>Gen Trades{dtArrow("gen_trades")}</th>
                  <th onClick={() => setDtSort("dt_return")} title="preferred DeepTest scenario">DeepTest Ret{dtArrow("dt_return")}</th>
                  <th onClick={() => setDtSort("dt_pf")}>DeepTest PF{dtArrow("dt_pf")}</th>
                  <th onClick={() => setDtSort("dt_dd")}>DeepTest DD{dtArrow("dt_dd")}</th>
                  <th onClick={() => setDtSort("dt_wr")}>DeepTest WR{dtArrow("dt_wr")}</th>
                  <th onClick={() => setDtSort("dt_trades")}>DeepTest Trades{dtArrow("dt_trades")}</th>
                  <th onClick={() => setDtSort("dt_net")}>DeepTest Net{dtArrow("dt_net")}</th>
                  <th>period</th>
                  <th>coverage</th>
                  <th>status</th>
                  <th onClick={() => setDtSort("dt")}>scenarios{dtArrow("dt")}</th>
                  <th>preferred</th>
                  <th onClick={() => setDtSort("last_run")}>last run{dtArrow("last_run")}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {dtRows.map((d) => {
                  const sum = d.preferred?.analytics?.summary || {};
                  return (
                    <tr key={d.node_id}>
                      <td>
                        Node #{d.node_id}
                        {d.research_node_num != null && (
                          <span className="muted" style={{ fontSize: 9.5 }}> · research #{d.research_node_num}</span>
                        )}
                      </td>
                      <td className="mono">{fmtPct(d.gen?.return_pct)}</td>
                      <td className="mono">{fmtNum(d.gen?.profit_factor, 3)}</td>
                      <td className="mono">{fmtPct(d.gen?.max_drawdown_pct)}</td>
                      <td className="mono">{d.gen?.trades ?? "N/A"}</td>
                      <td className="mono">{fmtPct(sum.net_return_pct)}</td>
                      <td className="mono">{fmtNum(sum.profit_factor, 3)}</td>
                      <td className="mono">{fmtPct(sum.max_drawdown_pct)}</td>
                      <td className="mono">{fmtPct(sum.win_rate)}</td>
                      <td className="mono">{sum.trades ?? "N/A"}</td>
                      <td className="mono">{fmtMoney(sum.net_profit)}</td>
                      <td>{txt(d.preferred?.duration_key || "—")}</td>
                      <td>
                        {d.preferred?.coverage_status === "PARTIAL"
                          ? <Badge tone="warn">PARTIAL COVERAGE</Badge>
                          : txt(d.preferred?.coverage_status || "—")}
                      </td>
                      <td>{txt(d.preferred?.status || "—")}</td>
                      <td>{d.scenario_count ?? 0} ({d.valid_scenario_count ?? 0} valid)</td>
                      <td>{d.preferred ? txt(d.preferred.run_id) : "N/A"}</td>
                      <td>{isoOf(d.last_run?.finished_at)}</td>
                      <td>
                        <button className="btn" style={{ fontSize: 10 }} onClick={() => setInspectNode(d.node_id)}>
                          INSPECT
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {!dtRows.length && (
                  <tr><td colSpan={18} className="muted">no node matches this search</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
        {inspectNode != null && (
          <div>
            <button style={{ fontSize: 10, marginTop: 6 }} onClick={() => setInspectNode(null)}>close inspector</button>
            <DeepScenarioPanel key={inspectNode} nodeId={inspectNode}
                               node={nodes.find((n) => n.node_id === inspectNode) || null}
                               onChanged={() => { loadDt(); }} />
          </div>
        )}
      </div>

      {/* ---- §9 incomplete-history confirmation --------------------------- */}
      {confirmState && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 90,
                      display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div className="panel" style={{ width: 640, maxHeight: "84vh", overflow: "auto", padding: 14 }}
               data-testid="deeptest-coverage-confirm">
            <b>INCOMPLETE HISTORY — CONFIRM BEFORE RUNNING</b>
            <div style={{ fontSize: 11.5, marginTop: 8, lineHeight: 1.5 }}>
              <div><b>Node:</b> Node #{confirmState.node.node_id} · <b>Symbol:</b> {confirmState.node.symbol}
                {"  "}<b>Candle timeframe (never changed):</b> {confirmState.node.timeframe}</div>
              <div><b>Requested range:</b>{" "}
                {isoOf(confirmState.pre?.confirmation?.requested?.start_ts ?? confirmState.pre?.coverage?.requested?.start_ts)}
                {" → "}
                {isoOf(confirmState.pre?.confirmation?.requested?.end_ts ?? confirmState.pre?.coverage?.requested?.end_ts)}</div>
              <div><b>Actual available range:</b>{" "}
                {isoOf(confirmState.pre?.confirmation?.actual?.first_ts ?? confirmState.pre?.coverage?.actual?.first_ts)}
                {" → "}
                {isoOf(confirmState.pre?.confirmation?.actual?.last_ts ?? confirmState.pre?.coverage?.actual?.last_ts)}</div>
              <div><b>Bars available:</b> {confirmState.pre?.confirmation?.actual?.bars
                ?? confirmState.pre?.coverage?.actual?.bars ?? "—"}</div>
              <div><b>Coverage:</b> {txt(confirmState.pre?.confirmation?.coverage_status
                ?? confirmState.pre?.coverage?.coverage_status ?? "—")}
                {` (missing start: ${String(!!(confirmState.pre?.confirmation?.missing_start ?? confirmState.pre?.coverage?.missing_start))}`},
                {` missing end: ${String(!!(confirmState.pre?.confirmation?.missing_end ?? confirmState.pre?.coverage?.missing_end))})`}
              </div>
              <div><b>Gaps (listed, never filled):</b> {confirmState.pre?.confirmation?.internal_gaps
                ?? confirmState.pre?.coverage?.internal_gaps ?? "—"}</div>
              <div><b>Data source:</b> {txt(confirmState.pre?.confirmation?.data_source_label
                ?? confirmState.pre?.coverage?.data_source_label ?? "persisted store")}</div>
              {!!((confirmState.pre?.confirmation?.warnings || confirmState.pre?.coverage?.warnings || []).length) && (
                <div className="kit-inline-err" style={{ marginTop: 6 }}>
                  {(confirmState.pre?.confirmation?.warnings || confirmState.pre?.coverage?.warnings || []).join(" · ")}
                </div>
              )}
              <div style={{ marginTop: 6, opacity: 0.85 }}>
                If you proceed, the run is marked <b>PARTIAL COVERAGE</b> everywhere, only the actual
                tested range is persisted, and nothing is extrapolated or filled in.
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
              <button data-testid="confirm-yes" style={{ fontWeight: 700 }} onClick={() => resolveConfirm("YES_PROCEED")}>
                YES – PROCEED (mark PARTIAL COVERAGE)
              </button>
              <button data-testid="confirm-no" onClick={() => resolveConfirm("NO_CANCEL")}>
                NO – CANCEL (nothing is lost)
              </button>
              <button data-testid="confirm-csv" onClick={() => resolveConfirm("LOAD_CSV")}>
                MANUALLY LOAD DATA – IMPORT CSV
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ---- §10 CSV import ------------------------------------------------ */}
      <DeepCsvImportDialog open={!!csvFor}
                           symbol={csvFor?.symbol || ""} timeframe={csvFor?.timeframe || ""}
                           onClose={() => setCsvFor(null)}
                           onImported={() => { loadNodes(); loadDt(); }} />

      {drawerId != null && (
        <StrategyDrawer id={drawerId} onClose={() => setDrawerId(null)} />
      )}

      {/* V6.7 §7.2 — the SCENARIO popup: the shared scheduling presentation,
          DeepTesting-only persistence (never the live settings). */}
      {scenarioFor != null && (
        <ScheduleDialog
          nodeId={scenarioFor.node_id}
          subtitle={`DeepTesting scenario configuration for Node #${scenarioFor.node_id} — saved separately from live scheduling/risk settings`}
          loadFn={(id) => api.deeptestNodeConfig(id)}
          saveFn={(id, body) => api.deeptestNodeConfigSave(id, body)}
          onClose={() => setScenarioFor(null)}
          onSaved={() => { setScenarioFor(null); loadNodes(); }}
        />
      )}
    </div>
  );
}
