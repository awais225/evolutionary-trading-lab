import React, { useEffect, useState } from "react";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import { Pill, ErrorNote, Spinner } from "../components/common.jsx";

export default function ResearchAI() {
  const { openStrategy, events } = useLab();
  const [hyps, setHyps] = useState(null);
  const [memory, setMemory] = useState([]);
  const [activeTab, setActiveTab] = useState("hypotheses"); // "hypotheses" | "memory"
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = () => {
    api.hypotheses().then((r) => setHyps(r.hypotheses)).catch((e) => setErr(e.message));
    api.researchMemory().then((r) => setMemory(r.memory || [])).catch(() => {});
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [events.length]);

  const apply = async (id) => {
    setBusy(id);
    try { await api.applyHypothesis(id); load(); } catch (e) { setErr(e.message); }
    setBusy(null);
  };

  return (
    <div>
      <div className="flex justify-between items-center" style={{ marginBottom: "0.5rem" }}>
        <div>
          <h2 className="page-title">Research AI & Scientific Memory</h2>
          <div className="page-sub">
            The intelligent research scientist: analyzes experiment metrics, observes patterns, forms structured hypotheses,
            and maintains accumulated experimental knowledge to prevent redundant dead-ends.
          </div>
        </div>
        <div className="btn-row">
          <button
            className={"btn" + (activeTab === "hypotheses" ? " primary" : "")}
            onClick={() => setActiveTab("hypotheses")}
          >
            💡 Active Hypotheses ({hyps?.length ?? 0})
          </button>
          <button
            className={"btn" + (activeTab === "memory" ? " primary" : "")}
            onClick={() => setActiveTab("memory")}
          >
            🧠 Research Knowledge Memory ({memory.length})
          </button>
        </div>
      </div>

      <ErrorNote err={err} />

      {activeTab === "hypotheses" && (
        <>
          <div className="panel" style={{ marginBottom: 14 }}>
            <h3>Deterministic Security & Scientific Exploration Model (spec §20-28)</h3>
            <ul className="muted" style={{ fontSize: 12.5, margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
              <li><b>Structured Hypotheses:</b> proposals use a strict genome directive whitelist (add_adx_filter, restrict_sessions, exclude_day, change_timeframe, add_regime_filter).</li>
              <li><b>No Arbitrary Code:</b> AI layer cannot emit executable Python code, place orders, or bypass validation floor gates.</li>
              <li><b>Controlled Experiments:</b> children generated from hypotheses are tested against parents, siblings, and historical regimes.</li>
            </ul>
          </div>

          {!hyps && <Spinner />}
          {hyps && !hyps.length && (
            <div className="panel muted">
              No hypotheses yet. They are generated automatically in CONTINUOUS research mode once strategies have matrices/validation results.
            </div>
          )}
          {hyps?.map((h) => (
            <div className="panel" key={h.id} style={{ marginBottom: 10 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <h3>#{h.id} · source: {h.source} · {fmt.dt(h.created_at)}</h3>
                <span>
                  <Pill status={h.status === "APPLIED" ? "QUALIFIED" : h.status === "REJECTED" ? "KILLED" : "BORN"} />
                </span>
              </div>
              <div style={{ fontSize: 13 }}><b>Observation:</b> {h.observation}</div>
              <div style={{ fontSize: 13, marginTop: 4 }}><b>Hypothesis:</b> {h.hypothesis}</div>
              <div className="mono muted" style={{ fontSize: 11.5, marginTop: 6 }}>
                proposal → {h.proposal?.action} {JSON.stringify(h.proposal?.params)}
                {h.child_strategy_id ? <> · child <a onClick={() => openStrategy(h.child_strategy_id)}>#{h.child_strategy_id}</a></> : ""}
              </div>
              <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
                {h.strategy_id && (
                  <button className="btn" onClick={() => openStrategy(h.strategy_id)}>
                    open subject #{h.strategy_id}
                  </button>
                )}
                {h.status === "PROPOSED" && (
                  <button className="btn primary" disabled={busy === h.id} onClick={() => apply(h.id)}>
                    ⚗ apply → create child strategy
                  </button>
                )}
              </div>
            </div>
          ))}
        </>
      )}

      {activeTab === "memory" && (
        <div className="panel">
          <div className="flex justify-between items-center" style={{ marginBottom: 12 }}>
            <h3>Accumulated Experimental Knowledge Base (spec §23, §24, §26)</h3>
            <span className="muted" style={{ fontSize: "0.8rem" }}>
              Total Experiments Logged: <b>{memory.length}</b>
            </span>
          </div>
          {!memory.length && (
            <div className="muted" style={{ padding: "20px 0" }}>
              No experiment outcomes recorded in memory yet. As strategies complete screening and validation, comparative knowledge is accumulated here.
            </div>
          )}
          {memory.length > 0 && (
            <div className="scroll-y" style={{ maxHeight: 500 }}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Parent → Child</th>
                    <th>Action</th>
                    <th>Outcome</th>
                    <th>Fitness Δ</th>
                    <th>Changed Variables</th>
                    <th>Learned Rule / Failure Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {memory.map((m) => (
                    <tr key={m.id}>
                      <td className="mono" style={{ fontSize: "0.75rem" }}>{fmt.dt(m.created_at)}</td>
                      <td className="mono">
                        <a onClick={() => openStrategy(m.parent_id)}>#{m.parent_id}</a> → <a onClick={() => openStrategy(m.child_id)}>#{m.child_id}</a>
                      </td>
                      <td className="mono">{m.action}</td>
                      <td>
                        <span style={{
                          padding: "2px 6px",
                          borderRadius: "3px",
                          fontSize: "0.75rem",
                          fontWeight: 700,
                          background: m.outcome === "IMPROVED" ? "rgba(34, 197, 94, 0.2)" : m.outcome === "FAILED" ? "rgba(239, 68, 68, 0.2)" : "rgba(234, 179, 8, 0.2)",
                          color: m.outcome === "IMPROVED" ? "var(--green)" : m.outcome === "FAILED" ? "var(--red)" : "#facc15"
                        }}>
                          {m.outcome}
                        </span>
                      </td>
                      <td className="mono" style={{ fontWeight: 700 }}>
                        {m.fitness_delta > 0 ? `+${m.fitness_delta}` : m.fitness_delta}
                      </td>
                      <td style={{ fontSize: "0.8rem" }}>{m.changed_variables || "–"}</td>
                      <td style={{ fontSize: "0.8rem" }}>
                        {m.learned_rule || m.failure_reason || m.survival_reason || "–"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
