/* V5.2 §10 — ONE authoritative node-population status model, shown everywhere.
 *
 * Every page that talks about "how many nodes are there / how many can be deep
 * tested / live tested" renders this strip. It reads the single backend
 * authority (`GET /api/nodes/populations` → ``state``), which is derived by
 * ``backend/app/research/populations.py`` from the same classifier the engine
 * uses. Pages no longer recount rows locally, and they no longer disagree:
 * Dashboard, MT5 Demo Trading, Live Testing, Final Testing and Deep Testing all
 * show these numbers, each name carrying its own definition.
 *
 * The strip never invents a number: when the endpoint cannot answer it says so
 * and shows nothing for the values (fail-visible, never zero-filled).
 */
import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { Badge } from "./ui.jsx";

const ORDER = ["TOTAL", "ALIVE", "QUALIFIED", "FINAL_TESTING_ELIGIBLE",
               "DEEP_TESTING_ELIGIBLE", "LIVE_TESTING_ELIGIBLE"];
const SHORT = {
  TOTAL: "TOTAL",
  ALIVE: "ALIVE",
  QUALIFIED: "QUALIFIED",
  FINAL_TESTING_ELIGIBLE: "FINAL ELIGIBLE",
  DEEP_TESTING_ELIGIBLE: "DEEP ELIGIBLE",
  LIVE_TESTING_ELIGIBLE: "LIVE ELIGIBLE",
};
const TONE = {
  TOTAL: "mute", ALIVE: "ok", QUALIFIED: "good",
  FINAL_TESTING_ELIGIBLE: "violet", DEEP_TESTING_ELIGIBLE: "accent",
  LIVE_TESTING_ELIGIBLE: "real",
};

export default function NodePopulationStrip({ compact = false, refreshMs = 30000 }) {
  const [state, setState] = useState(null);
  const [defs, setDefs] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let live = true;
    const load = () => api.nodePopulations()
      .then((d) => {
        if (!live) return;
        if (d && d.ok === false) { setErr(d.error || "population endpoint refused"); return; }
        setState((d && d.state) || null);
        setDefs((d && d.definitions) || null);
        setErr(null);
      })
      .catch((e) => live && setErr(String((e && e.message) || e)));
    load();
    if (!refreshMs) return () => { live = false; };
    const t = setInterval(load, refreshMs);
    return () => { live = false; clearInterval(t); };
  }, [refreshMs]);

  if (err) {
    return (
      <div className="kit-chip" style={{ borderColor: "#7f1d1d", color: "#ff8f8f" }}
           title={`GET /api/nodes/populations — ${err}`}>
        node populations unavailable — {err}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}
         data-testid="node-population-strip"
         title="One authority: GET /api/nodes/populations (backend/app/research/populations.py)">
      <span className="muted" style={{ fontSize: 11.5, marginRight: 2 }}>NODES</span>
      {ORDER.map((k) => {
        const v = state ? state[k] : null;
        const d = (defs && defs[k]) || (typeof defs?.[k] === "string" ? defs[k] : null);
        return (
          <Badge key={k} tone={TONE[k] || "mute"}
                 title={typeof d === "string" ? d : undefined}>
            {SHORT[k]} {v === null || v === undefined ? "–" : v.toLocaleString()}
          </Badge>
        );
      })}
      {!compact && (
        <span className="muted" style={{ fontSize: 11 }}>
          identical on every page — the count a page shows is this authority, never a local recount
        </span>
      )}
    </div>
  );
}
