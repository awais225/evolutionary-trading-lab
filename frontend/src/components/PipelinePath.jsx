/* V4.8 — Research milestone path (§12).
 *
 * Research → Qualification → Backtest → Validation → MT5 → Live → Demo → Final.
 * Every stop is clickable and navigates to the page where that stage's records
 * live. Counts come from the authoritative statistics endpoints
 * (/api/stats/overview, /api/research/facets). Where the backend exposes no
 * aggregate figure for a stage the cell shows N/A — never a guessed number.
 */
import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { NA_TEXT, numOrNull, objOrNull, txt } from "../lib/safe.js";
import { useLab } from "../App.jsx";
import { Card } from "./ui.jsx";

const STOPS = [
  { key: "research", label: "Research", page: "lab", hint: "Strategy Laboratory — every USER_RESEARCH node" },
  { key: "qualification", label: "Qualification", page: "final_testing", hint: "Final Testing — qualified / survived / failed outcomes" },
  { key: "backtest", label: "Backtest", page: "matrix", hint: "Backtest Matrix — backtest layer columns" },
  { key: "validation", label: "Validation", page: "matrix", hint: "Backtest Matrix — validation layer columns" },
  { key: "mt5", label: "MT5", page: "mt5_backtest", hint: "MT5 Backtest — historical MT5 runs (simulator labelled)" },
  { key: "live", label: "Live", page: "live_test", hint: "Live Testing — forward test engine" },
  { key: "demo", label: "Demo", page: "mt5_demo", hint: "MT5 Demo Trading — demo account execution records" },
  { key: "final", label: "Final", page: "population", hint: "Population — the full node record" },
];

export default function PipelinePath() {
  const { navigateTab } = useLab() || {};
  const [stats, setStats] = useState(null);
  const [facets, setFacets] = useState(null);
  const [err, setErr] = useState(null);

  const load = useCallback(async () => {
    try {
      const [s, f] = await Promise.all([
        api.statsOverview().catch((e) => { throw e; }),
        api.researchFacets().catch(() => null),
      ]);
      setStats(s); setFacets(f); setErr(null);
    } catch (e) { setErr(e.message || String(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const pop = objOrNull(stats?.population) || objOrNull(facets?.population) || {};
  const bt = objOrNull(stats?.research_performance?.backtest) || {};
  const val = objOrNull(stats?.research_performance?.validation) || {};
  const exec = objOrNull(stats?.execution_records) || {};

  const counts = {
    research: numOrNull(pop.total),
    qualification: numOrNull(pop.qualified),
    backtest: numOrNull(bt.total_backtests),
    validation: numOrNull(val.records),
    mt5: null,                                  // no aggregate MT5-backtest counter is exposed
    live: numOrNull(objOrNull(exec.live_test)?.trades),
    demo: numOrNull(objOrNull(exec.mt5_demo)?.trades),
    final: null,                                // FINAL_CANDIDATE has no aggregate counter
  };

  return (
    <Card title="Research milestone path"
          right={<span className="muted" style={{ fontSize: 11.5 }}>click any stop to open its page</span>}>
      {err && <div className="kit-inline-err" style={{ marginBottom: 8 }}>{err}</div>}
      <div className="kit-track" style={{ flexWrap: "wrap", gap: 6 }}>
        {STOPS.map((s, i) => (
          <React.Fragment key={s.key}>
            <button className="kit-star" style={{ padding: "6px 10px", cursor: "pointer" }}
                    title={`${s.hint}${counts[s.key] === null ? " — no aggregate counter exposed by the backend" : ""}`}
                    onClick={() => navigateTab && navigateTab(s.page)}>
              <span style={{ fontWeight: 700 }}>{s.label}</span>
              <span className="mono" style={{ marginLeft: 6, color: counts[s.key] === null ? "var(--muted)" : "var(--accent)" }}>
                {counts[s.key] === null ? NA_TEXT : counts[s.key].toLocaleString()}
              </span>
            </button>
            {i < STOPS.length - 1 && <span className="muted" aria-hidden="true">→</span>}
          </React.Fragment>
        ))}
      </div>
      <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
        Research {txt(counts.research, NA_TEXT)} · qualified {txt(counts.qualification, NA_TEXT)} ·
        backtest rows {txt(counts.backtest, NA_TEXT)} · validation records {txt(counts.validation, NA_TEXT)} ·
        live-test trades {txt(counts.live, NA_TEXT)} · demo trades {txt(counts.demo, NA_TEXT)}.
        Stages without a backend aggregate (MT5 stage, Final candidate) are shown as {NA_TEXT} and still navigate.
      </div>
    </Card>
  );
}
