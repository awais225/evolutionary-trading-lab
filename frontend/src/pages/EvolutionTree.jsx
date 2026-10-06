import React, { useCallback, useEffect, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType,
  Handle,
  Position,
} from "reactflow";
import "reactflow/dist/style.css";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import { Pill, Spinner, ErrorNote } from "../components/common.jsx";

const STATUS_COLORS = {
  QUALIFIED: "#22c55e", // vibrant green
  PAPER: "#10b981",     // emerald green
  SURVIVED: "#3b82f6",  // blue
  VALIDATING: "#a78bfa",// purple
  BORN: "#38bdf8",      // sky blue
  BACKTESTING: "#0ea5e9",
  FAILED: "#ef4444",    // red / dead
  KILLED: "#dc2626",    // dark red / dead
  RETIRED: "#64748b",   // grey / dead
};

const DEAD_STATUSES = new Set(["FAILED", "KILLED", "RETIRED"]);

// Deterministic test data demonstrating tree connectors (Spec Part 44)
const DEMO_TREE_DATA = {
  nodes: [
    { id: 1, parent_id: null, generation: 0, status: "QUALIFIED", fitness: 2.105, symbol: "XAUUSD", timeframe: "M15", species: "M15|any|rsi", pf: 1.85, return_pct: 0.42, dd: 0.08, trades: 140, mutation_type: "root_seed", creation_reason: "Initial baseline seed" },
    { id: 2, parent_id: 1, generation: 1, status: "PAPER", fitness: 2.340, symbol: "XAUUSD", timeframe: "M15", species: "M15|any|rsi", pf: 2.10, return_pct: 0.55, dd: 0.06, trades: 125, mutation_type: "add_filter", creation_reason: "Added session filter (London/NY)" },
    { id: 3, parent_id: 1, generation: 1, status: "FAILED", fitness: 0.450, symbol: "XAUUSD", timeframe: "M15", species: "M15|any|rsi", pf: 0.82, return_pct: -0.15, dd: 0.22, trades: 190, mutation_type: "tighten_sl", creation_reason: "Tightened stop-loss to 1 ATR", failure_reason: "Over-trading triggered stop-loss cascades" },
    { id: 4, parent_id: 2, generation: 2, status: "QUALIFIED", fitness: 2.580, symbol: "XAUUSD", timeframe: "M15", species: "M15|any|rsi", pf: 2.35, return_pct: 0.68, dd: 0.05, trades: 110, mutation_type: "trend_filter", creation_reason: "EMA 200 trend filter applied", survival_reason: "Passed all 8 walk-forward robustness gates" },
    { id: 5, parent_id: 2, generation: 2, status: "FAILED", fitness: 0.610, symbol: "XAUUSD", timeframe: "M15", species: "M15|any|rsi", pf: 0.95, return_pct: -0.05, dd: 0.18, trades: 45, mutation_type: "adx_filter", creation_reason: "ADX threshold raised to 35", failure_reason: "Insufficient sample size (trades < 50)" },
    { id: 6, parent_id: 3, generation: 2, status: "KILLED", fitness: 0.210, symbol: "XAUUSD", timeframe: "M15", species: "M15|any|rsi", pf: 0.65, return_pct: -0.30, dd: 0.28, trades: 80, mutation_type: "martingale_probe", creation_reason: "Sizing multiplier experiment", failure_reason: "Drawdown kill limit exceeded" },
  ],
  edges: [
    { source: 1, target: 2, source_status: "QUALIFIED", target_status: "PAPER", branch_state: "active", is_alive: true, is_dead: false },
    { source: 1, target: 3, source_status: "QUALIFIED", target_status: "FAILED", branch_state: "dead", is_alive: false, is_dead: true },
    { source: 2, target: 4, source_status: "PAPER", target_status: "QUALIFIED", branch_state: "active", is_alive: true, is_dead: false },
    { source: 2, target: 5, source_status: "PAPER", target_status: "FAILED", branch_state: "dead", is_alive: false, is_dead: true },
    { source: 3, target: 6, source_status: "FAILED", target_status: "KILLED", branch_state: "dead", is_alive: false, is_dead: true },
  ],
  total_strategies: 6,
};

function EvoNode({ data, selected }) {
  const isDead = DEAD_STATUSES.has(data.status);
  const isGreen = ["QUALIFIED", "PAPER", "SURVIVED"].includes(data.status);

  return (
    <div
      className={"evo-node s-" + data.status + (selected ? " selected" : "")}
      onClick={() => data.onSelect(data.id)}
      onDoubleClick={() => data.onOpen(data.id)}
      title={`Strategy #${data.id} (Double-click to open full details)\nParent: #${data.parent_id ?? "Root"}\nGen: ${data.generation}\nStatus: ${data.status}\nFitness: ${data.fitness ?? "–"}\nCreation: ${data.creation_reason || data.mutation_type || "–"}`}
      style={{
        position: "relative",
        border: selected
          ? "2px solid #facc15"
          : isDead
          ? "1.5px dashed rgba(100, 116, 139, 0.45)"
          : isGreen
          ? "2px solid #22c55e"
          : `1.5px solid ${STATUS_COLORS[data.status] || "var(--border)"}`,
        boxShadow: selected
          ? "0 0 16px rgba(250, 204, 21, 0.7)"
          : isGreen
          ? "0 0 10px rgba(34, 197, 94, 0.25)"
          : "none",
        background: isDead
          ? "rgba(22, 14, 14, 0.88)"
          : isGreen
          ? "rgba(6, 26, 14, 0.92)"
          : "var(--panel)",
        opacity: isDead ? 0.68 : 1.0,
        borderRadius: 8,
        padding: "8px 10px",
        minWidth: 140,
        cursor: "pointer",
        transition: "all 0.15s ease",
      }}
    >
      {/* Anchor handle for incoming parent connector lines */}
      <Handle
        type="target"
        position={Position.Top}
        id="in"
        style={{
          background: isDead ? "#64748b" : isGreen ? "#22c55e" : "#38bdf8",
          width: 8,
          height: 8,
          borderRadius: "50%",
          border: "2px solid #0a0d13",
          top: -4,
        }}
      />

      <div className="hd flex justify-between items-center" style={{ borderBottom: "1px solid rgba(255,255,255,0.08)", paddingBottom: 3, marginBottom: 4 }}>
        <span style={{ fontWeight: 800, fontSize: "0.85rem", color: isDead ? "#94a3b8" : "#f8fafc" }}>
          #{data.id}
        </span>
        <span style={{ fontSize: "0.72rem", color: "var(--muted)" }}>
          Gen {data.generation}
        </span>
      </div>

      <div style={{ fontSize: "0.72rem", color: "#94a3b8", marginBottom: 3 }}>
        Parent: <b style={{ color: data.parent_id ? "#cbd5e1" : "#64748b" }}>{data.parent_id ? `#${data.parent_id}` : "Root"}</b>
      </div>

      <div className="mm" style={{ display: "flex", justifyContent: "space-between", fontSize: "0.74rem" }}>
        <span>Fit: <b style={{ color: isDead ? "#94a3b8" : "#38bdf8" }}>{data.fitness == null ? "–" : Number(data.fitness).toFixed(3)}</b></span>
        <span>PF: <b style={{ color: (data.pf || 0) >= 1.2 ? "var(--green)" : "inherit" }}>{data.pf == null ? "–" : fmt.num(data.pf, 2)}</b></span>
      </div>

      <div className="mm" style={{ display: "flex", justifyContent: "space-between", fontSize: "0.72rem", marginTop: 2 }}>
        <span>Ret: <span className={(data.return_pct || 0) >= 0 ? "pos" : "neg"}>{data.return_pct == null ? "–" : fmt.pct(data.return_pct, 0)}</span></span>
        <span>DD: {data.dd == null ? "–" : fmt.pct(data.dd, 0)}</span>
      </div>

      <div className="mm flex justify-between items-center" style={{ marginTop: 4, paddingTop: 3, borderTop: "1px solid rgba(255,255,255,0.06)" }}>
        <span style={{
          padding: "1px 5px",
          borderRadius: 3,
          fontSize: "0.68rem",
          fontWeight: 700,
          background: isDead ? "rgba(239, 68, 68, 0.15)" : isGreen ? "rgba(34, 197, 94, 0.2)" : "rgba(56, 189, 248, 0.15)",
          color: isDead ? "#ef4444" : isGreen ? "var(--green)" : "#38bdf8",
        }}>
          {data.status}
        </span>
        <span className="mono muted" style={{ fontSize: "0.7rem" }}>{data.timeframe}</span>
      </div>

      {/* Anchor handle for outgoing child connector lines */}
      <Handle
        type="source"
        position={Position.Bottom}
        id="out"
        style={{
          background: isDead ? "#64748b" : isGreen ? "#22c55e" : "#38bdf8",
          width: 8,
          height: 8,
          borderRadius: "50%",
          border: "2px solid #0a0d13",
          bottom: -4,
        }}
      />
    </div>
  );
}

const nodeTypes = { evo: EvoNode };

/* V4.7 audit remediation: the tree payload arrives from the API, so `nodes` and
   `edges` can be missing, of the wrong type, or contain non-object entries (a
   partial, degraded or error response). Both lists are normalised to arrays of
   objects here; a valid payload passes through untouched, so the layout and the
   rendering of valid data are exactly the same as before. */
function asNodeList(value) {
  if (!Array.isArray(value)) return [];
  // entries without a usable id/generation cannot be laid out (the layout groups
  // by generation and indexes by id) - they are dropped like a missing list
  return value.filter((n) => n && typeof n === "object"
    && Number.isFinite(Number(n.id)) && Number.isFinite(Number(n.generation)));
}

function asEdgeList(value) {
  if (!Array.isArray(value)) return [];
  return value.filter((e) => e && typeof e === "object"
    && Number.isFinite(Number(e.source)) && Number.isFinite(Number(e.target)));
}

// Hierarchical layout with generation rows & parent-anchored clustering
function layout(nodes, edges) {
  const nodeList = asNodeList(nodes);
  const edgeList = asEdgeList(edges);
  const byGen = {};
  nodeList.forEach((n) => { (byGen[n.generation] ||= []).push(n); });
  const gens = Object.keys(byGen).map(Number).sort((a, b) => a - b);
  const pos = {};
  const childOf = {};
  edgeList.forEach((e) => (childOf[e.target] = e.source));

  gens.forEach((g) => {
    const list = byGen[g];
    list.sort((a, b) => (childOf[a.id] ?? a.id) - (childOf[b.id] ?? b.id) || a.id - b.id);
    list.forEach((n, i) => { pos[n.id] = { x: i * 200, y: g * 165 }; });
  });

  gens.forEach((g) => {
    const list = byGen[g];
    const width = (list.length - 1) * 200;
    list.forEach((n) => { pos[n.id].x -= width / 2; });
  });
  return pos;
}

export default function EvolutionTree() {
  const { openStrategy } = useLab();
  const [data, setData] = useState(null);
  const [isDemoMode, setIsDemoMode] = useState(false);
  const [err, setErr] = useState(null);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [filters, setFilters] = useState({
    alive_only: false,
    dead_only: false,
    status: "",
    generation_min: "",
    generation_max: "",
    timeframe: "",
    search: "",
    focus_id: "",
    max_nodes: 350,
  });

  // Dedicated React Flow state hooks for both nodes AND edges
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState([]);

  const load = useCallback(() => {
    if (isDemoMode) {
      setData(DEMO_TREE_DATA);
      return;
    }
    const p = { max_nodes: filters.max_nodes };
    if (filters.alive_only) p.alive_only = true;
    if (filters.dead_only) p.dead_only = true;
    if (filters.status) p.status = filters.status;
    if (filters.timeframe) p.timeframe = filters.timeframe;
    if (filters.generation_min !== "") p.generation_min = filters.generation_min;
    if (filters.generation_max !== "") p.generation_max = filters.generation_max;
    if (filters.search) p.search = filters.search;
    if (filters.focus_id) p.focus_id = filters.focus_id;
    api.tree(p).then((res) => {
      setData(res);
      setErr(null);
    }).catch((e) => setErr(e.message));
  }, [filters, isDemoMode]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (isDemoMode) return;
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load, isDemoMode]);

  // Normalised API lists: a malformed payload yields empty lists instead of a
  // render crash, while valid payloads are passed through unchanged.
  const apiNodes = useMemo(() => asNodeList(data?.nodes), [data]);
  const apiEdges = useMemo(() => asEdgeList(data?.edges), [data]);

  // Selected node details object & immediate lineage
  const selectedNode = useMemo(() => {
    if (!selectedNodeId || !data) return null;
    return apiNodes.find((n) => n.id === selectedNodeId) || null;
  }, [selectedNodeId, data, apiNodes]);

  // Direct children of selected node
  const directChildren = useMemo(() => {
    if (!selectedNodeId || !data) return [];
    return apiNodes.filter((n) => n.parent_id === selectedNodeId);
  }, [selectedNodeId, data, apiNodes]);

  // Full lineage set (ancestors + descendants) for highlighting (spec §31)
  const highlightedIds = useMemo(() => {
    if (!selectedNodeId || !data) return new Set();
    const set = new Set([selectedNodeId]);

    // Add ancestors up to root
    let cur = selectedNodeId;
    while (cur) {
      const edge = apiEdges.find((e) => e.target === cur);
      if (edge && edge.source) {
        set.add(edge.source);
        cur = edge.source;
      } else {
        break;
      }
    }
    // Add all descendants down to leaves
    const frontier = [selectedNodeId];
    while (frontier.length > 0) {
      const top = frontier.pop();
      const kids = apiEdges.filter((e) => e.source === top).map((e) => e.target);
      kids.forEach((k) => {
        if (!set.has(k)) {
          set.add(k);
          frontier.push(k);
        }
      });
    }
    return set;
  }, [selectedNodeId, data, apiEdges]);

  // Build nodes & status-colored pathways (spec §20-§27)
  const { nodes, edges } = useMemo(() => {
    if (!data) return { nodes: [], edges: [] };
    const pos = layout(apiNodes, apiEdges);

    const nodes = apiNodes.map((n) => {
      const isSelected = selectedNodeId === n.id;
      const isLineage = highlightedIds.has(n.id);
      const isDead = DEAD_STATUSES.has(n.status);

      return {
        id: String(n.id),
        type: "evo",
        position: pos[n.id] || { x: 0, y: 0 },
        data: {
          ...n,
          onSelect: (id) => setSelectedNodeId(id),
          onOpen: (id) => openStrategy(id),
        },
        selected: isSelected,
        style: {
          opacity: selectedNodeId
            ? (isLineage ? 1.0 : 0.20)
            : (isDead ? 0.65 : 1.0),
          transition: "opacity 0.2s ease, transform 0.2s ease",
        },
      };
    });

    // Parent -> Child edges with status-based pathways (spec §22-§26)
    const edges = apiEdges.map((e) => {
      const targetNode = apiNodes.find((n) => n.id === e.target);
      const isDeadBranch = targetNode ? DEAD_STATUSES.has(targetNode.status) : false;
      const isQualifiedOrPaper = targetNode ? ["QUALIFIED", "PAPER"].includes(targetNode.status) : false;
      const isLineageEdge = highlightedIds.has(e.source) && highlightedIds.has(e.target);

      // Color rules:
      // 1. Lineage highlight: vivid gold #facc15
      // 2. Dead branch: grey #64748b (spec §24)
      // 3. Active / working branch: vibrant green #22c55e (spec §23)
      let strokeColor = isDeadBranch ? "#64748b" : "#22c55e";
      let arrowColor = isDeadBranch ? "#64748b" : "#22c55e";
      let strokeWidth = isDeadBranch ? 1.8 : 2.8;

      if (isLineageEdge) {
        strokeColor = "#facc15";
        arrowColor = "#facc15";
        strokeWidth = 3.5;
      }

      return {
        id: `e-${e.source}-${e.target}`,
        source: String(e.source),
        target: String(e.target),
        sourceHandle: "out",
        targetHandle: "in",
        type: "smoothstep",
        animated: isQualifiedOrPaper && !isDeadBranch,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: arrowColor,
          width: 14,
          height: 14,
        },
        style: {
          stroke: strokeColor,
          strokeWidth: strokeWidth,
          strokeDasharray: isDeadBranch ? "5 4" : undefined,
          opacity: selectedNodeId ? (isLineageEdge ? 1.0 : 0.12) : (isDeadBranch ? 0.60 : 0.95),
          transition: "stroke 0.2s, stroke-width 0.2s, opacity 0.2s",
        },
      };
    });

    return { nodes, edges };
  }, [data, openStrategy, selectedNodeId, highlightedIds, apiNodes, apiEdges]);

  // Synchronize both nodes and edges to React Flow internal state
  useEffect(() => {
    setRfNodes(nodes);
    setRfEdges(edges);
  }, [nodes, edges, setRfNodes, setRfEdges]);

  return (
    <div>
      <div className="flex justify-between items-center" style={{ marginBottom: "0.4rem" }}>
        <div>
          <h2 className="page-title">Evolution Tree & Genetic Lineage</h2>
          <div className="page-sub">
            Parent → Child ancestry graph ({data?.nodes?.length ?? 0} of {data?.total_strategies ?? 0} strategies).
            Connector lines visibly link parent and child nodes. Double-click any node to open full genome & backtest.
          </div>
        </div>
        <div className="btn-row">
          <button
            className={"btn btn-sm" + (isDemoMode ? " primary" : " btn-subtle")}
            onClick={() => {
              setIsDemoMode(!isDemoMode);
              setSelectedNodeId(null);
            }}
          >
            {isDemoMode ? "✓ Showing Demo Tree (6 Nodes)" : "Test 6-Node Demo Tree"}
          </button>
          {selectedNodeId && (
            <>
              <button className="btn btn-sm primary" onClick={() => openStrategy(selectedNodeId)}>
                Open Strategy #{selectedNodeId}
              </button>
              <button className="btn btn-sm btn-subtle" onClick={() => setSelectedNodeId(null)}>
                Clear Lineage Focus
              </button>
            </>
          )}
        </div>
      </div>

      <ErrorNote err={err} />

      {/* Visual Pipeline Legend (spec §23, §24) */}
      <div className="panel" style={{ padding: "8px 14px", marginBottom: "0.6rem", display: "flex", gap: "24px", alignItems: "center", fontSize: "0.82rem", background: "rgba(15, 23, 42, 0.6)" }}>
        <span style={{ fontWeight: 700, color: "var(--muted)", textTransform: "uppercase", fontSize: "0.72rem", letterSpacing: "0.05em" }}>Lineage Connectors:</span>
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 22, height: 3, background: "#22c55e", borderRadius: 2 }}></span>
          <b style={{ color: "var(--green)" }}>Green Line</b> = Active / Verified Pipeline
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 22, height: 2, borderTop: "2px dashed #64748b" }}></span>
          <span style={{ color: "#94a3b8" }}><b>Grey Dashed Line</b> = Dead / Abandoned Branch</span>
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 22, height: 3, background: "#facc15", borderRadius: 2 }}></span>
          <b style={{ color: "#facc15" }}>Gold Highlight</b> = Selected Lineage Pathway
        </span>
      </div>

      {/* Tree Controls Toolbar (spec §30) */}
      <div className="btn-row" style={{ alignItems: "center", flexWrap: "wrap", marginBottom: "0.6rem" }}>
        <label className="muted" style={{ fontSize: 12.5, cursor: "pointer", display: "flex", alignItems: "center" }}>
          <input
            type="checkbox"
            checked={filters.alive_only}
            disabled={filters.dead_only || isDemoMode}
            style={{ width: "auto", marginRight: 5 }}
            onChange={(e) => setFilters({ ...filters, alive_only: e.target.checked })}
          />
          Active Only (Green)
        </label>

        <label className="muted" style={{ fontSize: 12.5, cursor: "pointer", marginLeft: 8, display: "flex", alignItems: "center" }}>
          <input
            type="checkbox"
            checked={filters.dead_only}
            disabled={filters.alive_only || isDemoMode}
            style={{ width: "auto", marginRight: 5 }}
            onChange={(e) => setFilters({ ...filters, dead_only: e.target.checked })}
          />
          Dead Only (Grey)
        </label>

        <select disabled={isDemoMode} value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
          <option value="">Status: ALL</option>
          {["BORN", "BACKTESTING", "SURVIVED", "VALIDATING", "QUALIFIED", "PAPER", "FAILED", "KILLED", "RETIRED"]
            .map((s) => <option key={s}>{s}</option>)}
        </select>

        <select disabled={isDemoMode} value={filters.timeframe} onChange={(e) => setFilters({ ...filters, timeframe: e.target.value })}>
          <option value="">Timeframe: ALL</option>
          {["M1", "M5", "M15", "M30", "H1"].map((t) => <option key={t}>{t}</option>)}
        </select>

        <input disabled={isDemoMode} style={{ width: 75 }} placeholder="Gen ≥" value={filters.generation_min}
               onChange={(e) => setFilters({ ...filters, generation_min: e.target.value })} />
        <input disabled={isDemoMode} style={{ width: 75 }} placeholder="Gen ≤" value={filters.generation_max}
               onChange={(e) => setFilters({ ...filters, generation_max: e.target.value })} />
        <input disabled={isDemoMode} style={{ width: 160 }} placeholder="Search ID / mutation" value={filters.search}
               onChange={(e) => setFilters({ ...filters, search: e.target.value })} />
        <input disabled={isDemoMode} style={{ width: 120 }} placeholder="Focus Lineage #" value={filters.focus_id}
               onChange={(e) => setFilters({ ...filters, focus_id: e.target.value })} />

        <button className="btn btn-sm" onClick={load}>Refresh</button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: selectedNode ? "1fr 320px" : "1fr", gap: 12, height: "calc(100vh - 310px)", minHeight: 480 }}>
        {/* ReactFlow Interactive Graph with verified connector lines */}
        <div style={{ height: "100%", border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden" }}>
          {!data ? <Spinner /> : (
            <ReactFlow
              nodes={rfNodes}
              edges={rfEdges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              nodeTypes={nodeTypes}
              fitView
              minZoom={0.05}
              maxZoom={2.5}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="#1c2333" gap={26} />
              <Controls />
              <MiniMap
                pannable
                zoomable
                nodeColor={(n) => STATUS_COLORS[n.data?.status] || "#555"}
                style={{ background: "#0d1119" }}
              />
            </ReactFlow>
          )}
        </div>

        {/* Selected Node Lineage Inspector (spec §28, §29, §31) */}
        {selectedNode && (
          <div className="panel scroll-y" style={{ height: "100%", margin: 0, padding: 14 }}>
            <div className="flex justify-between items-center" style={{ borderBottom: "1px solid var(--border)", paddingBottom: 8, marginBottom: 10 }}>
              <h3 style={{ margin: 0 }}>Strategy #{selectedNode.id}</h3>
              <Pill status={selectedNode.status} />
            </div>

            <div style={{ fontSize: "0.82rem", marginBottom: 12 }}>
              <div style={{ color: "var(--muted)", marginBottom: 4, textTransform: "uppercase", fontSize: "0.7rem", letterSpacing: "0.05em" }}>Lineage Ancestry:</div>
              <div style={{ background: "rgba(0,0,0,0.3)", padding: "8px 10px", borderRadius: 6, border: "1px solid rgba(255,255,255,0.06)" }}>
                <div>Generation: <b>{selectedNode.generation}</b></div>
                <div style={{ marginTop: 4 }}>
                  Parent: {selectedNode.parent_id ? (
                    <a onClick={() => setSelectedNodeId(selectedNode.parent_id)} style={{ color: "var(--accent)", cursor: "pointer", fontWeight: 700 }}>
                      #{selectedNode.parent_id} (jump to parent)
                    </a>
                  ) : <span className="muted">None (Root Seed Strategy)</span>}
                </div>
                <div style={{ marginTop: 4 }}>
                  Children ({directChildren.length}):{" "}
                  {directChildren.length === 0 ? <span className="muted">No direct offspring</span> : (
                    <span>
                      {directChildren.map((c, i) => (
                        <a key={c.id} onClick={() => setSelectedNodeId(c.id)} style={{ color: "var(--green)", cursor: "pointer", fontWeight: 600, marginRight: 6 }}>
                          #{c.id}{i < directChildren.length - 1 ? "," : ""}
                        </a>
                      ))}
                    </span>
                  )}
                </div>
              </div>
            </div>

            <div style={{ fontSize: "0.82rem", marginBottom: 12 }}>
              <div style={{ color: "var(--muted)", marginBottom: 4, textTransform: "uppercase", fontSize: "0.7rem", letterSpacing: "0.05em" }}>Performance Metrics:</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                <div style={{ background: "rgba(0,0,0,0.25)", padding: 6, borderRadius: 4 }}>
                  <div className="muted" style={{ fontSize: "0.7rem" }}>Fitness</div>
                  <div style={{ fontWeight: 700, color: "#38bdf8" }}>{selectedNode.fitness != null ? Number(selectedNode.fitness).toFixed(4) : "–"}</div>
                </div>
                <div style={{ background: "rgba(0,0,0,0.25)", padding: 6, borderRadius: 4 }}>
                  <div className="muted" style={{ fontSize: "0.7rem" }}>Profit Factor</div>
                  <div style={{ fontWeight: 700 }}>{selectedNode.pf != null ? fmt.num(selectedNode.pf, 2) : "–"}</div>
                </div>
                <div style={{ background: "rgba(0,0,0,0.25)", padding: 6, borderRadius: 4 }}>
                  <div className="muted" style={{ fontSize: "0.7rem" }}>Total Return</div>
                  <div style={{ fontWeight: 700 }} className={(selectedNode.return_pct || 0) >= 0 ? "pos" : "neg"}>
                    {selectedNode.return_pct != null ? fmt.pct(selectedNode.return_pct, 1) : "–"}
                  </div>
                </div>
                <div style={{ background: "rgba(0,0,0,0.25)", padding: 6, borderRadius: 4 }}>
                  <div className="muted" style={{ fontSize: "0.7rem" }}>Max Drawdown</div>
                  <div style={{ fontWeight: 700 }}>{selectedNode.dd != null ? fmt.pct(selectedNode.dd, 1) : "–"}</div>
                </div>
              </div>
            </div>

            <div style={{ fontSize: "0.82rem", marginBottom: 12 }}>
              <div style={{ color: "var(--muted)", marginBottom: 4, textTransform: "uppercase", fontSize: "0.7rem", letterSpacing: "0.05em" }}>Genetic Attributes:</div>
              <div style={{ fontSize: "0.78rem", lineHeight: 1.6 }}>
                <div>Symbol: <b>{selectedNode.symbol}</b> · TF: <b>{selectedNode.timeframe}</b></div>
                <div>Species: <span className="mono" style={{ fontSize: "0.72rem" }}>{selectedNode.species || "–"}</span></div>
                <div>Mutation: <b>{selectedNode.mutation_type || "seed"}</b></div>
                {selectedNode.creation_reason && (
                  <div style={{ marginTop: 4, color: "var(--muted)" }}>
                    <i>{selectedNode.creation_reason}</i>
                  </div>
                )}
              </div>
            </div>

            {(selectedNode.failure_reason || selectedNode.survival_reason) && (
              <div style={{ fontSize: "0.82rem", marginBottom: 12, padding: "8px 10px", borderRadius: 6, background: selectedNode.failure_reason ? "rgba(239,68,68,0.12)" : "rgba(34,197,94,0.12)", border: `1px solid ${selectedNode.failure_reason ? "rgba(239,68,68,0.3)" : "rgba(34,197,94,0.3)"}` }}>
                <div style={{ fontWeight: 700, fontSize: "0.75rem", color: selectedNode.failure_reason ? "var(--red)" : "var(--green)", marginBottom: 2 }}>
                  {selectedNode.failure_reason ? "Failure Reason" : "Survival Reason"}:
                </div>
                <div style={{ fontSize: "0.76rem" }}>
                  {selectedNode.failure_reason || selectedNode.survival_reason}
                </div>
              </div>
            )}

            <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 8 }}>
              {!isDemoMode && (
                <button className="btn primary" onClick={() => openStrategy(selectedNode.id)}>
                  Inspect Full Strategy & Trades
                </button>
              )}
              <button
                className="btn btn-subtle"
                onClick={() => setFilters({ ...filters, focus_id: String(selectedNode.id) })}
              >
                Isolate Lineage in Graph
              </button>
              {filters.focus_id && (
                <button
                  className="btn btn-sm"
                  onClick={() => setFilters({ ...filters, focus_id: "" })}
                >
                  Clear Graph Isolation
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
