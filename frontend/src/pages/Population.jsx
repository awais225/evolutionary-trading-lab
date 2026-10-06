import React, { useEffect, useState } from "react";
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend,
} from "recharts";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import { Pill, SignedNum, ErrorNote, Spinner } from "../components/common.jsx";
import PopulationSummary from "../components/PopulationSummary.jsx";

const AXIS_OPTIONS = [
  ["total_return_pct", "Return %"], ["max_drawdown_pct", "Max DD %"],
  ["profit_factor", "Profit Factor"], ["sharpe", "Sharpe"], ["sortino", "Sortino"],
  ["trades", "Trades"], ["win_rate", "Win rate"], ["fitness", "Fitness"],
  ["complexity", "Complexity"], ["generation", "Generation"], ["consistency", "Consistency"],
];

const COLOR_KEYS = ["status", "generation", "species", "timeframe"];
const PALETTE = ["#4f8ef7", "#34d399", "#f87171", "#fbbf24", "#a78bfa", "#22d3ee",
                 "#f472b6", "#94a3b8", "#fb923c", "#4ade80", "#818cf8", "#e879f9"];

export default function Population() {
  const { openStrategy } = useLab();
  const [x, setX] = useState("total_return_pct");
  const [y, setY] = useState("max_drawdown_pct");
  const [color, setColor] = useState("status");
  const [scatter, setScatter] = useState(null);
  const [rows, setRows] = useState([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.scatter({ x, y, color, limit: 900 }).then(setScatter).catch((e) => setErr(e.message));
  }, [x, y, color]);

  useEffect(() => {
    const load = () => api.population({ limit: 250, sort: "fitness", status: statusFilter || undefined })
      .then((r) => setRows(r.strategies || [])).catch((e) => setErr(e.message));
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [statusFilter]);

  const groups = {};
  (scatter?.points || []).forEach((p) => {
    const k = String(p.color ?? "?");
    (groups[k] ||= []).push({ x: p.x, y: p.y, id: p.id });
  });
  const gkeys = Object.keys(groups).sort();

  return (
    <div>
      {/* V4.8 — population counters, distribution and search (dead nodes stay visible) */}
      <PopulationSummary onPick={(id) => openStrategy(id)} statusFilter={statusFilter}
                         setStatusFilter={setStatusFilter} search={search} setSearch={setSearch} />

      <h2 className="page-title">Population</h2>
      <div className="page-sub">
        Live population visualization — diversity is preserved via species (timeframe + direction +
        indicator families); a lower-profit but structurally different strategy can survive.
      </div>
      <ErrorNote err={err} />

      <div className="panel" style={{ marginBottom: 14 }}>
        <div className="btn-row" style={{ marginBottom: 8 }}>
          <select value={x} onChange={(e) => setX(e.target.value)}>
            {AXIS_OPTIONS.map(([v, l]) => <option key={v} value={v}>X: {l}</option>)}
          </select>
          <select value={y} onChange={(e) => setY(e.target.value)}>
            {AXIS_OPTIONS.map(([v, l]) => <option key={v} value={v}>Y: {l}</option>)}
          </select>
          <select value={color} onChange={(e) => setColor(e.target.value)}>
            {COLOR_KEYS.map((v) => <option key={v} value={v}>color: {v}</option>)}
          </select>
          <span className="muted" style={{fontSize:12}}>{scatter?.points?.length ?? 0} points</span>
        </div>
        <ResponsiveContainer width="100%" height={380}>
          <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
            <CartesianGrid stroke="#1c2333" />
            <XAxis type="number" dataKey="x" name={x} tick={{ fill: "#8b93a7", fontSize: 11 }}
                   stroke="#2a3348" />
            <YAxis type="number" dataKey="y" name={y} tick={{ fill: "#8b93a7", fontSize: 11 }}
                   stroke="#2a3348" width={70} />
            <Tooltip cursor={{ strokeDasharray: "3 3", stroke: "#3a4460" }}
                     contentStyle={{ background: "#151a26", border: "1px solid #232b3d", fontSize: 12 }}
                     formatter={(v) => fmt.num(v, 3)}
                     labelFormatter={() => ""} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {gkeys.map((k, i) => (
              <Scatter key={k} name={k} data={groups[k]}
                       fill={PALETTE[i % PALETTE.length]}
                       onClick={(pt) => { const id = pt?.id ?? pt?.payload?.id; if (id) openStrategy(id); }} />
            ))}
          </ScatterChart>
        </ResponsiveContainer>
        <div className="muted" style={{ fontSize: 11.5 }}>click a point to open the strategy</div>
      </div>

      <div className="panel">
        <h3>Population table
          <select style={{ marginLeft: 12 }} value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">all statuses</option>
            {["BORN","BACKTESTING","SURVIVED","VALIDATING","QUALIFIED","PAPER","FAILED","KILLED","RETIRED"]
              .map((s) => <option key={s}>{s}</option>)}
          </select>
        </h3>
        <div className="scroll-y" style={{ maxHeight: 420 }}>
          <table className="tbl">
            <thead><tr>
              <th>Absolute ID</th><th>Research Node</th><th>status</th><th>gen</th><th>parent</th><th>tf</th><th>dir</th>
              <th>fitness</th><th>PF</th><th>return</th><th>DD</th><th>trades</th>
              <th>cx</th><th>species</th><th>mutation</th>
            </tr></thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} onClick={() => openStrategy(s.id)} style={{ cursor: "pointer" }}>
                  <td className="mono" style={{ fontWeight: 600 }}>Node_{s.id}</td>
                  <td className="mono" style={{ color: "#4f8ef7", fontWeight: 500 }}>
                    {s.research_node_num ? `Research Node #${s.research_node_num.toLocaleString()}` : (s.data_source === "LEGACY_TEST" ? "Legacy" : `#${s.id}`)}
                  </td>
                  <td><Pill status={s.status} /></td>
                  <td>{s.generation}</td>
                  <td>{s.parent_id ? `Node_${s.parent_id}` : "–"}</td>
                  <td>{s.timeframe}</td>
                  <td>{s.direction}</td>
                  <td>{fmt.num(s.fitness, 3)}</td>
                  <td>{fmt.num(s.pf, 2)}</td>
                  <td><SignedNum v={s.return_pct} pct /></td>
                  <td>{fmt.pct(s.dd)}</td>
                  <td>{s.trades ?? "–"}</td>
                  <td>{s.complexity}</td>
                  <td className="mono" style={{ fontSize: 10 }}>{s.species_key}</td>
                  <td className="muted" style={{ fontSize: 11 }}>{s.mutation_type || s.origin}</td>
                </tr>
              ))}
              {!rows.length && <tr><td colSpan={14}><Spinner /></td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
