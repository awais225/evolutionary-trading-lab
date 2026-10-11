// V6.6 §7 — MT5 DATA coverage panel: the real, persisted coverage of every
// (symbol, timeframe) history cache key the Deep Testing workspace touches.
// Read-only inspection + the explicit GET MT5 DATA trigger (never automatic).
import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Badge, StateBlock } from "./ui";

function pct(x) {
  return x == null ? "—" : `${(Number(x) * 100).toFixed(2)}%`;
}

export default function DeepCoveragePanel({ pairs = [], expanded = false, onFetch, fetchStates = {} }) {
  const [open, setOpen] = useState(!!expanded);
  const [cov, setCov] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    if (!pairs.length) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.deeptestCoveragePairs(pairs.map((p) => ({ symbol: p.symbol, timeframe: p.timeframe })));
      setCov(res.coverage || {});
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [pairs]);

  useEffect(() => { if (open) load(); }, [open, load]);

  return (
    <div className="panel" style={{ marginTop: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
           onClick={() => setOpen(!open)}>
        <b style={{ fontSize: 12.5 }}>MT5 DATA coverage</b>
        <Badge tone={open ? "sim" : "neutral"}>{open ? "hide" : "show"}</Badge>
        <span style={{ fontSize: 11, opacity: 0.75 }}>
          {pairs.length ? `${pairs.length} cache key(s) · real persisted bars only` : "no cache keys yet"}
        </span>
        <button style={{ marginLeft: "auto", fontSize: 11 }}
                onClick={(e) => { e.stopPropagation(); load(); }}>Refresh</button>
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          <StateBlock loading={loading} error={error} onRetry={load}
                      empty={!loading && !error && !pairs.length}
                      emptyText="select nodes — their (symbol, timeframe) cache keys appear here" />
          {pairs.map((p) => {
            const key = `${String(p.symbol).toUpperCase()}|${String(p.timeframe).toUpperCase()}`;
            const c = (cov || {})[key] || null;
            const fs = fetchStates[key] || null;
            return (
              <div key={key} style={{ borderTop: "1px solid var(--border, #333)", padding: "6px 0", fontSize: 11.5 }}>
                <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                  <b className="mono">{p.symbol} · {p.timeframe}</b>
                  {c && <span className="mono">bars {c.bars ?? "—"}</span>}
                  {c && <span className="mono">{c.first ?? "—"} → {c.last ?? "—"}</span>}
                  {c && c.coverage_label && <span>coverage ≈ {c.coverage_label}</span>}
                  {c && c.bars === 0 && <Badge tone="warn">EMPTY</Badge>}
                  {c && (c.quality?.gaps || 0) > 0 && (
                    <Badge tone="warn">{c.quality.gaps} gap(s)</Badge>
                  )}
                  {onFetch && (
                    <button style={{ marginLeft: "auto", fontSize: 11 }}
                            disabled={fs && fs.status === "FETCHING"}
                            onClick={() => onFetch(p.symbol, p.timeframe)}>
                      {fs && fs.status === "FETCHING" ? `Fetching… ${fs.progress_pct ?? 0}%` : "GET MT5 DATA"}
                    </button>
                  )}
                </div>
                {fs && fs.operation && (
                  <div className="mono" style={{ opacity: 0.8 }}>{fs.status}: {fs.operation}</div>
                )}
                {c && (
                  <div className="kit-strip" style={{ border: "none", padding: 0, flexWrap: "wrap" }}>
                    <div className="item"><span className="k">Source</span>
                      <span className="v mono">{c.source || "persisted store"}{c.server ? ` · ${c.server}` : ""}</span></div>
                    <div className="item"><span className="k">Fingerprint</span>
                      <span className="v mono">{c.fingerprint ? String(c.fingerprint).slice(0, 16) : "—"}</span></div>
                    <div className="item"><span className="k">Valid bars</span>
                      <span className="v mono">{c.bars ?? "—"}</span></div>
                    <div className="item"><span className="k">Duplicates</span>
                      <span className="v mono">{c.quality?.duplicate_timestamps ?? "—"}</span></div>
                    <div className="item"><span className="k">Missing bars (in gaps)</span>
                      <span className="v mono">{c.quality?.missing_bars_total ?? "—"}</span></div>
                  </div>
                )}
                {c && (c.missing_periods || []).length > 0 && (
                  <details style={{ fontSize: 11 }}>
                    <summary>missing periods ({c.missing_periods.length}{c.missing_periods_truncated ? "+ truncated" : ""})</summary>
                    <div className="mono" style={{ maxHeight: 120, overflow: "auto" }}>
                      {c.missing_periods.map((g, i) => (
                        <div key={i}>{g.after} → {g.before} ({g.missing_bars} bars)</div>
                      ))}
                    </div>
                  </details>
                )}
                {c && (c.warnings || []).length > 0 && (
                  <div className="kit-inline-err" style={{ fontSize: 11 }}>{c.warnings.join(" · ")}</div>
                )}
                {c && c.error && <div className="kit-inline-err">{c.error}</div>}
              </div>
            );
          })}
          <div style={{ fontSize: 10.5, opacity: 0.7, marginTop: 6 }}>
            gaps are listed for inspection and are never filled in; week-end/session closures are normal
            stored data. Coverage is read from the persistent candle store (source, bars, range, gaps,
            fingerprint) — never from a filename or a claimed period.
          </div>
        </div>
      )}
    </div>
  );
}
