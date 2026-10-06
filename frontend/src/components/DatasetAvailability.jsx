/* V4.8 — Market Data: physical availability (§15).
 *
 * A database row is NOT proof that real data exists on disk. This block asks the
 * backend for both facts and shows them side by side:
 *   /api/data/inventory          -> the registered datasets, their range/rows,
 *                                   the resolved file and its measured size
 *   /api/data/management/status  -> which timeframes physically exist on disk
 *                                   (FOUND / MISSING) and the active source
 *
 * Usability is then stated explicitly, with the reason when a dataset is not
 * usable — and the row is never presented as REAL when it is simulated.
 */
import React, { useCallback, useEffect, useState } from "react";
import { api, fmt } from "../api.js";
import { arr, NA_TEXT, numOrNull, objOrNull, txt } from "../lib/safe.js";
import { Badge, Card, SectionTitle, SourceChip, StateBlock } from "./ui.jsx";

function usability(d, tfStatus) {
  const reasons = [];
  const size = numOrNull(d.size_mb);
  const rows = numOrNull(d.rows);
  const onDisk = size !== null && size > 0;
  if (!onDisk) reasons.push("no physical file on disk (registered row only)");
  if (rows !== null && rows <= 0) reasons.push("no bars stored");
  if (tfStatus && String(tfStatus).toUpperCase() === "MISSING") reasons.push("timeframe not present in the DATA tree");
  const simulated = String(d.source || "").toUpperCase().includes("SIM");
  if (simulated) reasons.push("this is SIMULATED data, not broker data");
  return { onDisk, usable: onDisk && !(rows !== null && rows <= 0), reasons, simulated };
}

export default function DatasetAvailability() {
  const [inv, setInv] = useState(null);
  const [mgmt, setMgmt] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [onlyUsable, setOnlyUsable] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const [i, m] = await Promise.all([
        api.dataInventory().catch((e) => { throw e; }),
        api.dataManagementStatus().catch(() => null),
      ]);
      setInv(i); setMgmt(m);
    } catch (e) {
      setErr(e.message || String(e));
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  // a malformed inventory (null / non-object entries) must not crash the view
  const datasets = arr(inv?.datasets).filter((d) => d && typeof d === "object");
  const timeframes = objOrNull(mgmt?.timeframes) || {};
  const rows = datasets.map((d) => ({ d, u: usability(d, objOrNull(timeframes)[d.timeframe]) }));
  const shown = onlyUsable ? rows.filter((r) => r.u.usable) : rows;
  const realRows = rows.filter((r) => !r.u.simulated);
  const simRows = rows.filter((r) => r.u.simulated);
  const unusable = rows.filter((r) => !r.u.usable);

  return (
    <Card title="Physical availability (DATA tree vs database rows)"
          right={<span className="muted" style={{ fontSize: 11.5 }}>
            source: {txt(mgmt?.data_source, "unknown")} · {txt(mgmt?.raw_datasets_count, "—")} registered row(s)
          </span>}>
      <StateBlock loading={loading} error={err} onRetry={load}>
        <div className="kit-strip" style={{ border: "none", padding: 0, marginBottom: 8 }}>
          <div className="item"><span className="k">Registered datasets</span><span className="v mono">{datasets.length}</span></div>
          <div className="item"><span className="k">Real broker rows</span><span className="v">{realRows.length} <Badge tone="real">REAL</Badge></span></div>
          <div className="item"><span className="k">Simulated rows</span><span className="v">{simRows.length} <Badge tone="sim">SIMULATED</Badge></span></div>
          <div className="item"><span className="k">Not usable</span>
            <span className="v">{unusable.length} {unusable.length ? <Badge tone="warn">see reasons</Badge> : <Badge tone="ok">none</Badge>}</span></div>
          <div className="item"><span className="k">Timeframes on disk</span>
            <span className="v mono">
              {Object.entries(timeframes).map(([tf, st]) => (
                <span key={tf} style={{ marginRight: 8 }}>
                  {tf}: <span style={{ color: String(st).toUpperCase() === "FOUND" ? "var(--green)" : "var(--muted)" }}>{txt(st)}</span>
                </span>
              ))}
            </span></div>
        </div>

        <label className="fld" style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 6 }}>
          <input type="checkbox" style={{ width: "auto" }} checked={onlyUsable}
                 onChange={(e) => setOnlyUsable(e.target.checked)} />
          Show only usable datasets
        </label>

        <div className="kit-scroll">
          <table className="tbl">
            <thead>
              <tr><th>symbol</th><th>tf</th><th>source</th><th>broker</th><th>range</th><th>bars</th>
                <th>on disk</th><th>usable</th><th>reason</th></tr>
            </thead>
            <tbody>
              {shown.map(({ d, u }, i) => (
                <tr key={`${d.id}-${i}`}>
                  <td className="mono">{txt(d.symbol)}</td>
                  <td className="mono">{txt(d.timeframe)}</td>
                  <td><SourceChip source={d.source} size={9} /></td>
                  <td>{txt(d.broker, "—")}</td>
                  <td className="mono" style={{ fontSize: 11 }}>{txt(d.start_date, "—")} → {txt(d.end_date, "—")}</td>
                  <td className="mono">{fmt.num(d.rows, 0)}</td>
                  <td className="mono">{u.onDisk ? `${fmt.num(d.size_mb, 2)} MB` : <span className="muted">{NA_TEXT}</span>}</td>
                  <td>{u.usable ? <Badge tone="ok">YES</Badge> : <Badge tone="warn">NO</Badge>}</td>
                  <td className="muted" style={{ fontSize: 11.5 }}>{u.reasons.length ? u.reasons.join("; ") : "—"}</td>
                </tr>
              ))}
              {shown.length === 0 && (
                <tr><td colSpan={9} className="muted" style={{ textAlign: "center" }}>No dataset rows match this filter.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
          A registered row with no physical file is reported as <b>not usable</b> with the reason — the dashboard never
          treats a database row as proof that real broker data exists.
        </div>
      </StateBlock>
    </Card>
  );
}
