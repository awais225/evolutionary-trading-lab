import React from "react";
import { fmt } from "../api.js";
import { Card, Spinner } from "./common.jsx";
import { NA, money, pct, ratio } from "./NodeResearchDetail.jsx";

/* V4.5 Backtest Matrix — comparison table over stored research results.
 *
 * Rendered entirely from the API payload: the columns, their layer and the
 * "derived" flags come from GET /api/research/matrix, so the table can never
 * disagree with the server about what a column is. Research columns
 * (BACKTEST / VALIDATION) are visually separated from the ENGINE identity
 * columns and from the EXECUTION RECORDS column, which is informational only
 * and never feeds a research number. A metric the stored data does not support
 * renders as N/A, never as 0.
 */

const LAYER_TONE = {
  IDENTITY: "#7ea6ff",
  BACKTEST: "#59d8ff",
  VALIDATION: "#52ffa8",
  ENGINE: "#b49aff",
  EXECUTION: "#ffcf6b",
};

function cell(value, column) {
  const key = column.key;
  if (key === "execution_records") {
    const ex = value || {};
    return (
      <span className="mono" style={{ fontSize: 11 }}>
        {fmt.num(ex.records_total || 0, 0)}
        <span className="muted" title={`paper ${ex.paper_trades || 0} · live test ${ex.live_test_trades || 0} · mt5 demo ${ex.mt5_demo_trades || 0}`}>
          {" "}(exec)
        </span>
      </span>
    );
  }
  if (value === null || value === undefined) {
    return (
      <NA reason={`not stored for this node — ${column.label} (${column.layer})`} />
    );
  }
  if (key === "net_profit") return money(value);
  if (key === "win_rate" || key === "return_pct" || key === "max_drawdown_pct") return pct(value, 2);
  if (key === "profit_factor" || key === "expectancy" || key === "reward_risk_ratio" || key === "robustness_score") {
    return ratio(value, key === "expectancy" ? 4 : 3);
  }
  if (key === "validation_passed" || key === "qualified") return value ? "YES" : "NO";
  if (key === "id") return <span className="mono">Node_{value}</span>;
  if (key === "generation") return <span className="mono">G{value}</span>;
  if (typeof value === "number") return <span className="mono">{fmt.num(value, 0)}</span>;
  return <span className="mono">{String(value)}</span>;
}

function LayerHeader({ columns }) {
  const groups = [];
  columns.forEach((c) => {
    const last = groups[groups.length - 1];
    if (last && last.layer === c.layer) last.span += 1;
    else groups.push({ layer: c.layer, span: 1 });
  });
  return (
    <tr>
      {groups.map((g) => (
        <th key={g.layer} colSpan={g.span}
            style={{ color: LAYER_TONE[g.layer] || undefined, textAlign: "center", fontSize: 10 }}>
          {g.layer}
        </th>
      ))}
    </tr>
  );
}

export default function BacktestMatrixTable({ data, loading, error, emptyHint, onSelectNode, maxHeight = 420 }) {
  if (error) {
    return (
      <Card>
        <div className="neg" style={{ fontSize: 12 }}>{String(error.message || error)}</div>
      </Card>
    );
  }
  if (loading) return <Card><Spinner /> loading matrix…</Card>;

  const rows = data?.rows || [];
  if (!rows.length) {
    return (
      <Card>
        <div className="muted" style={{ fontSize: 12 }}>
          {emptyHint || "No strategies in the matrix yet — select strategies in the table above (or adjust the filters)."}
        </div>
      </Card>
    );
  }

  const columns = data.columns || [];
  const notes = data.notes || [];

  return (
    <Card>
      <div className="scroll-y" style={{ maxHeight }}>
        <table className="tbl" style={{ fontSize: 11.5 }}>
          <thead>
            <LayerHeader columns={columns} />
            <tr>
              {columns.map((c) => (
                <th key={c.key} title={c.derived ? `derived${c.derived_from ? `: ${c.derived_from}` : ""}` : c.layer}
                    style={{ whiteSpace: "nowrap" }}>
                  {c.label}{c.derived ? " *" : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const node = r.node || {};
              const legacy = node.research_eligible === false;
              return (
                <tr key={node.id}
                    style={legacy ? { opacity: 0.65, background: "#2a1f10" } : undefined}
                    onClick={onSelectNode ? () => onSelectNode(node.id) : undefined}>
                  {columns.map((c) => (
                    <td key={c.key} style={{ textAlign: ["id", "generation", "symbol", "timeframe", "status", "stage"].includes(c.key) ? "left" : "right" }}>
                      {c.key === "status" ? (
                        <span className="pill" style={{ fontSize: 9 }}>{r.row?.status || "—"}</span>
                      ) : cell(c.key === "id" ? node.id : r.row?.[c.key], c)}
                      {c.key === "id" && legacy ? (
                        <span className="pill" style={{ fontSize: 9, marginLeft: 6 }}
                              title="LEGACY_TEST infrastructure record — diagnostic scope only">LEGACY</span>
                      ) : null}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
        {data.mode === "ids"
          ? `Selection mode: ${data.count} node(s) compared (requested ${(data.requested_ids || []).length}).`
          : `Filter mode: ${data.count} of ${fmt.num(data.total_matching, 0)} matching nodes (research scope ${data.scope}).`}
        {(data.missing_ids || []).length > 0 && (
          <span className="warn-banner" style={{ display: "inline-block", marginLeft: 8 }}>
            not found: {(data.missing_ids || []).join(", ")}
          </span>
        )}
      </div>
      <div className="muted" style={{ fontSize: 10.5, marginTop: 4 }}>
        * = derived from stored values; columns grouped by layer, EXECUTION RECORDS are informational
        only and never mixed into a research number.
      </div>
      {notes.length > 0 && (
        <details style={{ marginTop: 4 }}>
          <summary className="muted" style={{ fontSize: 11, cursor: "pointer" }}>matrix notes</summary>
          <ul className="muted" style={{ fontSize: 11, paddingLeft: 18 }}>
            {notes.map((n) => <li key={n}>{n}</li>)}
          </ul>
        </details>
      )}
    </Card>
  );
}
