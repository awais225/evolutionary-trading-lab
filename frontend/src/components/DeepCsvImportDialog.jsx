// V6.6 §10 — manual CSV import dialog: MT5-format support, explicit mapping,
// timezone confirmation when ambiguous, full validation report + preview
// BEFORE the transactional merge. Coverage is never inferred from the filename.
import React, { useState } from "react";
import { api } from "../api";
import { Badge } from "./ui";

export default function DeepCsvImportDialog({ open, symbol, timeframe, onClose, onImported }) {
  const [file, setFile] = useState(null);
  const [report, setReport] = useState(null);
  const [token, setToken] = useState(null);
  const [tz, setTz] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(null);

  if (!open) return null;

  const validate = async () => {
    if (!file) { setError("choose a CSV file first"); return; }
    setBusy(true); setError(null); setReport(null); setToken(null); setDone(null);
    try {
      const res = await api.deeptestCsvValidate(file, {
        symbol, timeframe,
        timezone_offset_hours: tz.trim() ? Number(tz.trim()) : null,
      });
      setReport(res.report || null);
      setToken(res.validation_token || null);
      if (res.needs_timezone_confirmation) {
        setError("the file's timestamps carry no timezone — confirm the timezone (e.g. 0 for UTC, " +
                 "or 5 for UTC+5 as seen on the chart) and Validate again before importing");
      }
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const commit = async () => {
    setBusy(true); setError(null);
    try {
      const res = await api.deeptestCsvCommit({
        validation_token: token,
        timezone_confirmed: tz.trim() ? `UTC${Number(tz.trim()) >= 0 ? "+" : ""}${Number(tz.trim())} (user confirmed)` : "UTC (user confirmed)",
      });
      if (!res.ok) {
        setError(res.error || "import refused");
        return;
      }
      setDone(res);
      if (onImported) onImported(res);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const r = report || {};
  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 80,
                  display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div className="panel" style={{ width: 680, maxHeight: "86vh", overflow: "auto", padding: 14 }}>
        <b>MANUALLY LOAD DATA — IMPORT CSV</b>
        <div style={{ fontSize: 11.5, opacity: 0.85, marginTop: 4 }}>
          MT5 format (<span className="mono">Date Time,Open,High,Low,Close,Tick_Vol,Vol,Spread</span>) and
          common generic candle CSVs. The target is <b className="mono">{symbol} / {timeframe}</b> —
          mismatched symbol or timeframe data is refused or must be mapped explicitly. Coverage comes
          from the file's ROWS, never its filename.
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
          <input type="file" accept=".csv,text/csv"
                 onChange={(e) => { setFile(e.target.files?.[0] || null); setReport(null); setToken(null); }} />
          <label style={{ fontSize: 11 }}>Timezone offset (hours):</label>
          <input style={{ width: 70 }} value={tz} placeholder="e.g. 0 or 5"
                 onChange={(e) => setTz(e.target.value)} />
          <button disabled={busy || !file} onClick={validate}>{busy ? "Working…" : "Validate file"}</button>
        </div>
        {error && <div className="kit-inline-err" style={{ marginTop: 8 }}>{error}</div>}
        {done && (
          <div style={{ marginTop: 8, fontSize: 12 }}>
            <b>Imported.</b> merge: accepted {done.merge?.accepted ?? 0}, identical duplicates skipped{" "}
            {done.merge?.duplicates_identical ?? 0}, conflicting rows kept stored value{" "}
            {done.merge?.conflicts ?? 0}.
            {" "}Re-run the coverage check — if history is still incomplete the confirmation will reappear.
          </div>
        )}
        {r.filename !== undefined && (
          <div style={{ marginTop: 10, fontSize: 11.5 }}>
            <div className="kit-strip" style={{ border: "none", padding: 0, flexWrap: "wrap" }}>
              <div className="item"><span className="k">File</span><span className="v mono">{r.filename}</span></div>
              <div className="item"><span className="k">SHA-256</span>
                <span className="v mono">{String(r.file_sha256 || "").slice(0, 16)}</span></div>
              <div className="item"><span className="k">Rows read / valid / rejected</span>
                <span className="v mono">{r.rows_read} / {r.rows_valid} / {r.rows_rejected}</span></div>
              <div className="item"><span className="k">Duplicate rows</span>
                <span className="v mono">{r.rows_duplicate ?? 0}</span></div>
              <div className="item"><span className="k">Actual coverage</span>
                <span className="v mono">{r.earliest} → {r.latest} ({r.actual_coverage_label})</span></div>
              <div className="item"><span className="k">Gaps in file</span>
                <span className="v mono">{r.gap_count} ({r.missing_bars_total} bars)</span></div>
              <div className="item"><span className="k">Timezone source</span>
                <span className="v mono">{r.timezone_source}</span></div>
              <div className="item"><span className="k">Field mapping</span>
                <span className="v mono">{Object.entries(r.mapping || {}).map(([k, v]) => `${k}←${v}`).join(", ")}</span></div>
            </div>
            {r.needs_timezone_confirmation && <Badge tone="warn">TIMEZONE CONFIRMATION REQUIRED</Badge>}
            {(r.rejects_sample || []).length > 0 && (
              <details style={{ marginTop: 6 }}>
                <summary>rejected rows ({r.rows_rejected}{r.rejects_truncated ? "+ truncated" : ""})</summary>
                <div className="mono" style={{ maxHeight: 110, overflow: "auto" }}>
                  {(r.rejects_sample || []).map((x, i) => (
                    <div key={i}>row {x.row}: {x.reason}</div>
                  ))}
                </div>
              </details>
            )}
            {(r.warnings || []).map((w, i) => (
              <div key={i} className="kit-inline-err" style={{ marginTop: 4 }}>{w}</div>
            ))}
          </div>
        )}
        <div style={{ display: "flex", gap: 8, marginTop: 12, justifyContent: "flex-end" }}>
          <button onClick={onClose}>Close</button>
          <button disabled={busy || !token || !r.filename
                           || (r.needs_timezone_confirmation && !tz.trim())
                           || r.rows_valid === 0}
                  onClick={commit}>
            Import into cache
          </button>
        </div>
      </div>
    </div>
  );
}
