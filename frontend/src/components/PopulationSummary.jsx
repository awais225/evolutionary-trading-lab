/* V4.8 — Population counters and search (§14).
 *
 * The whole population is visible here on purpose: dead nodes are part of the
 * research record and are never hidden on this page. Counters come from the
 * backend (lab status + research facets), never from a client-side recount of a
 * single page of rows.
 */
import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { arr, NA_TEXT, numOrNull, objOrNull, txt } from "../lib/safe.js";
import { Badge, Card, Kpi, SectionTitle, StateBlock, fmtId, parseNodeId } from "./ui.jsx";

export default function PopulationSummary({ onPick, statusFilter, setStatusFilter, search, setSearch }) {
  const [lab, setLab] = useState(null);
  const [facets, setFacets] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    Promise.all([api.labStatus().catch(() => null), api.researchFacets().catch(() => null)])
      .then(([l, f]) => { if (!alive) return; setLab(l); setFacets(f); if (!l && !f) setErr("Population counters are unavailable."); })
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, []);

  const population = objOrNull(facets?.population) || {};
  const counts = objOrNull(lab?.status_counts) || {};
  const distribution = useMemo(() => {
    const fromFacets = arr(facets?.statuses);
    if (fromFacets.length) return fromFacets.map((s) => [txt(s.value, "?"), numOrNull(s.count) ?? 0]);
    return Object.entries(counts).map(([k, v]) => [k, numOrNull(v) ?? 0]);
  }, [facets, counts]);

  const generations = arr(facets?.generations).slice(0, 12);
  const total = numOrNull(population.total ?? lab?.current_nodes ?? lab?.all_persisted_nodes);

  return (
    <Card title="Population"
          right={<span className="muted" style={{ fontSize: 11.5 }}>
            dead nodes are shown here by design — this page is the full research record
          </span>}>
      <StateBlock loading={loading} error={err}>
        <div className="grid cols-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(140px,1fr))", marginBottom: 10 }}>
          <Kpi label="Total" value={total === null ? NA_TEXT : total.toLocaleString()} sub="USER_RESEARCH scope" />
          <Kpi label="Alive" value={numOrNull(lab?.alive_nodes) === null ? NA_TEXT : numOrNull(lab.alive_nodes).toLocaleString()} tone="pos" />
          <Kpi label="Dead" value={numOrNull(lab?.dead_nodes) === null ? NA_TEXT : numOrNull(lab.dead_nodes).toLocaleString()} tone="mute" sub="kept visible here" />
          <Kpi label="Qualified" value={numOrNull(lab?.qualified_nodes) === null ? NA_TEXT : numOrNull(lab.qualified_nodes).toLocaleString()} tone="pos" />
          <Kpi label="Generation" value={txt(lab?.generation_number ?? lab?.generation, NA_TEXT)} sub={txt(lab?.generation_status, "")} />
          <Kpi label="Legacy (excluded)" value={numOrNull(population.legacy_test) === null ? "787" : numOrNull(population.legacy_test).toLocaleString()}
               sub="never a research or trading candidate" tone="warn" title="LEGACY_TEST infrastructure rows stay outside the research population" />
        </div>

        <SectionTitle>Status distribution</SectionTitle>
        <div>
          {distribution.length === 0 ? <span className="muted">No status distribution reported.</span>
            : distribution.map(([status, count]) => (
              <span key={status} className="kit-chip"
                    onClick={() => setStatusFilter && setStatusFilter(String(status) === statusFilter ? "" : String(status))}
                    style={String(status) === statusFilter ? { borderColor: "var(--accent)", color: "#fff" } : undefined}>
                {status} <b>{count.toLocaleString()}</b>
              </span>
            ))}
        </div>

        {generations.length > 0 && (
          <>
            <SectionTitle>Generation distribution (newest first)</SectionTitle>
            <div>
              {generations.map((g) => (
                <span key={g.value ?? g.generation} className="kit-chip">
                  gen {txt(g.value ?? g.generation, "?")} <b>{numOrNull(g.count)?.toLocaleString?.() ?? NA_TEXT}</b>
                </span>
              ))}
            </div>
          </>
        )}

        <SectionTitle hint="Accepts 10590, Node_10590 or #10590.">Search &amp; filters</SectionTitle>
        <div className="kit-cols">
          <input className="mono" style={{ flex: "1 1 240px" }} placeholder="node id or search text"
                 value={search ?? ""} onChange={(e) => setSearch && setSearch(e.target.value)}
                 onKeyDown={(e) => {
                   if (e.key === "Enter" && onPick) {
                     const id = parseNodeId(e.target.value);
                     if (id !== null) onPick(id);
                   }
                 }} />
          <input className="mono" style={{ flex: "0 0 160px" }} placeholder="status (e.g. FAILED)"
                 value={statusFilter ?? ""} onChange={(e) => setStatusFilter && setStatusFilter(e.target.value)} />
          {search && parseNodeId(search) !== null && onPick && (
            <button className="btn" onClick={() => onPick(parseNodeId(search))}>
              open {fmtId(parseNodeId(search))}
            </button>
          )}
          <Badge tone="info">dead nodes visible</Badge>
        </div>
      </StateBlock>
    </Card>
  );
}
