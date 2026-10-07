import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { arr, objOrNull, txt } from "../lib/safe.js";
import { Badge, ConfirmModal } from "./ui.jsx";

/**
 * V5 §10/§19-§21 — the dashboard Power button.
 *
 * The button asks the operator to confirm and then calls the backend's shutdown
 * endpoint, which stops ONLY the processes this dashboard started, in a defined
 * order, and verifies each one. The dialog says so explicitly: the browser stays
 * open, the operating system is untouched, an independently started MT5 terminal
 * keeps running and DATA is never modified.
 *
 * A "dry run" is offered as well: it performs every step's checks and stops
 * nothing, so the operator can see the plan without closing the dashboard.
 */
export default function PowerButton() {
  const [open, setOpen] = useState(false);
  const [session, setSession] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState("");
  const [report, setReport] = useState(null);
  const [lastReport, setLastReport] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await api.powerSession();
      setSession(res);
      setLastReport(objOrNull(res?.last_report));
    } catch (e) {
      setErr(e);
    }
  }, []);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const shutdown = async (dryRun) => {
    setBusy(dryRun ? "dry" : "real");
    setErr(null);
    try {
      const res = await api.powerShutdown(session?.shutdown_phrase || "SHUTDOWN DASHBOARD", dryRun);
      setReport(res);
      if (!dryRun && res?.ok) {
        // the backend is stopping: the page shows the report and stays open
        setErr(null);
      }
    } catch (e) {
      setErr(e);
    } finally {
      setBusy("");
    }
  };

  const owned = objOrNull(session?.owned) || {};
  const plan = arr(session?.plan);
  const neverTouched = arr(session?.never_touched);

  return (
    <>
      <button
        className="btn danger power-btn"
        title="Close the dashboard (stops only dashboard-owned processes)"
        onClick={() => { setOpen(true); setReport(null); }}
        style={{ display: "flex", alignItems: "center", gap: 6 }}
      >
        <span aria-hidden="true">⏻</span> Power
      </button>

      <ConfirmModal
        open={open}
        title="Are you sure you want to close the dashboard?"
        confirmLabel={busy === "real" ? "closing…" : "Yes, close the dashboard"}
        confirmTone="danger"
        busy={Boolean(busy)}
        onCancel={() => setOpen(false)}
        onConfirm={() => shutdown(false)}
        result={err ? { ok: false, error: err.message || String(err) } : null}
        dangerText="YES stops the dashboard backend and every process the dashboard started. NO leaves everything exactly as it is."
      >
        <div style={{ fontSize: 12.5 }}>
          <div className="warn-banner" style={{ marginBottom: 8 }}>
            <b>Only the dashboard is closed.</b> The browser window stays open, the operating system is not
            shut down/restarted/logged off/slept, processes the dashboard did not start keep running, an
            independently started MT5 terminal keeps running (the dashboard only disconnects from it) and
            DATA, node records, results, configuration and the git repository are never modified.
          </div>

          {session && (
            <div className="kit-strip" style={{ border: "none", padding: 0 }}>
              <div className="item"><span className="k">Session</span>
                <span className="v mono">{txt(session.session_id, "—")}</span></div>
              <div className="item"><span className="k">Owned processes</span>
                <span className="v mono">{txt(session.owned_process_count, "0")}</span></div>
              <div className="item"><span className="k">Accepting new tasks</span>
                <span className="v"><Badge tone={session.accepting_tasks ? "ok" : "warn"}>
                  {session.accepting_tasks ? "yes" : "no — shutting down"}
                </Badge></span></div>
            </div>
          )}

          {Object.keys(owned).length > 0 && (
            <table className="table compact" style={{ marginTop: 6 }}>
              <thead><tr><th>Role</th><th>PID</th><th>Started by</th><th>Alive</th><th>What</th></tr></thead>
              <tbody>
                {Object.entries(owned).flatMap(([role, recs]) =>
                  arr(recs).map((r) => (
                    <tr key={`${role}-${r.pid}`}>
                      <td className="mono">{role}</td>
                      <td className="mono">{txt(r.pid)}</td>
                      <td>{txt(r.launched_by)}</td>
                      <td>{r.alive ? <Badge tone="ok">yes</Badge> : <Badge tone="mute">gone</Badge>}</td>
                      <td className="muted" style={{ fontSize: 11 }}>{txt(r.label)}</td>
                    </tr>
                  )))}
              </tbody>
            </table>
          )}

          {plan.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div className="muted" style={{ fontSize: 11.5, marginBottom: 4 }}>
                The shutdown runs in this order, each step addressed to a recorded PID and verified:
              </div>
              <ol style={{ marginLeft: 16, fontSize: 11.5 }}>
                {plan.map((p) => <li key={p.step}><span className="mono">{p.step}</span> — {p.description}</li>)}
              </ol>
            </div>
          )}

          {neverTouched.length > 0 && (
            <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
              Never touched: {neverTouched.join("; ")}.
            </div>
          )}

          <div className="btn-row" style={{ marginTop: 8 }}>
            <button className="btn ghost" disabled={Boolean(busy)} onClick={() => shutdown(true)}>
              {busy === "dry" ? "checking…" : "Dry run (stops nothing)"}
            </button>
            <button className="btn ghost" disabled={Boolean(busy)} onClick={load}>Refresh ownership</button>
          </div>

          {report && (
            <div className={report.ok ? "kit-ok" : "kit-inline-err"} style={{ marginTop: 8 }}>
              <div className="kit-head">
                <b>{report.dry_run ? "Dry-run report (nothing was stopped)" : "Shutdown report"}</b>
                <Badge tone={report.ok ? "ok" : "warn"}>{report.ok ? "all steps completed" : "a step failed"}</Badge>
              </div>
              <table className="table compact">
                <thead><tr><th>#</th><th>Step</th><th>Status</th><th>Detail</th></tr></thead>
                <tbody>
                  {arr(report.steps).map((s) => (
                    <tr key={s.step}>
                      <td className="mono">{txt(s.order, "")}</td>
                      <td className="mono">{s.step}</td>
                      <td><Badge tone={s.status === "OK" ? "ok" : s.status === "SKIPPED" ? "mute" : "danger"}>{s.status}</Badge></td>
                      <td className="muted" style={{ fontSize: 11 }}>{txt(s.detail)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {report.backend_stopping && (
                <div className="muted" style={{ marginTop: 4, fontSize: 11.5 }}>
                  The backend has been asked to stop and will exit in a moment; this page stays open.
                  Start the dashboard again to continue.
                </div>
              )}
            </div>
          )}

          {lastReport && !report && (
            <div className="muted" style={{ marginTop: 8, fontSize: 11.5 }}>
              Last recorded shutdown: {new Date((lastReport.finished_at || 0) * 1000).toISOString().replace("T", " ").slice(0, 19)}{" "}
              by {txt(lastReport.actor, "unknown")}{lastReport.dry_run ? " (dry run)" : ""}.
            </div>
          )}
        </div>
      </ConfirmModal>
    </>
  );
}
