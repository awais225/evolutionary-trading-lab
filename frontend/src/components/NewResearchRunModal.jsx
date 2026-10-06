/* V4.8 — START NEW RESEARCH RUN dialog (§1 Overview / Research Run).
 *
 * Exactly three choices over the existing V4.1 research-run machinery:
 *   1. Start New — Archive Old   (recommended: verified backup, then reset)
 *   2. Start New — Delete Old    (destructive: reset with no backup)
 *   3. Resume Existing           (no reset: raises the current target)
 *
 * Options 1 and 2 require the operator to type the exact current node count;
 * the confirm button stays disabled until it matches. Nothing here bypasses the
 * backend confirmation tokens, and LEGACY_TEST infrastructure rows are outside
 * this workflow (the backend re-checks the legacy count after every reset).
 */
import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { txt } from "../lib/safe.js";
import { Badge, Kpi, Progress, StateBlock } from "./ui.jsx";

const OPTIONS = [
  {
    key: "archive",
    title: "Start New — Archive Old",
    badge: { tone: "real", text: "Recommended" },
    tone: "archive",
    lines: [
      "Current research is backed up first (verified archive).",
      "Existing strategies, backtests and validation results are preserved in the backup.",
      "The working research population is then reset to zero.",
      "The new run starts from node #1.",
    ],
  },
  {
    key: "delete",
    title: "Start New — Delete Old",
    badge: { tone: "danger", text: "Destructive" },
    tone: "delete",
    lines: [
      "The research population is permanently deleted.",
      "No backup is created — there is no undo.",
      "The new run starts from node #1.",
      "LEGACY_TEST infrastructure rows are not touched by this workflow.",
    ],
  },
  {
    key: "resume",
    title: "Resume Existing",
    badge: { tone: "ok", text: "Green / No Reset" },
    tone: "resume",
    lines: [
      "Existing nodes remain completely untouched.",
      "Research resumes from the current state.",
      "This is the only option that increases the existing population.",
    ],
  },
];

/** Plain-English reason why research cannot start right now (or null). */
export function researchBlockReason(lab) {
  if (!lab || typeof lab !== "object") return null;
  const err = String(lab.last_error || "").trim();
  if (err) return `The lab reported an error: ${err}`;
  if (lab.safe_paused) return `Research is safe-paused: ${txt(lab.safe_paused_reason, "reason not stated")}.`;
  if (lab.datasets_ready === false) return "No usable market dataset is loaded, so the engine has nothing to research on.";
  if (lab.running === true) return "A research run is already executing — stop or pause it before starting another.";
  return null;
}

function fmtNum(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString() : txt(v, "N/A");
}

export default function NewResearchRunModal({ onClose, onDone }) {
  const [state, setState] = useState(null);
  const [lab, setLab] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);
  const [option, setOption] = useState(null);            // archive | delete | resume
  const [typed, setTyped] = useState("");
  const [additional, setAdditional] = useState("1000");
  const [startNow, setStartNow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [busyMsg, setBusyMsg] = useState("");
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState(null);
  const [opError, setOpError] = useState(null);
  const pollRef = useRef(null);

  const load = async () => {
    try {
      const [s, l] = await Promise.all([
        api.researchRunState().catch(() => null),
        api.labStatus().catch(() => null),
      ]);
      if (s) setState(s);
      if (l) setLab(l);
      if (!s && !l) setErr("Could not read the research-run state from the backend.");
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); return () => clearInterval(pollRef.current); }, []);

  const userCount = Number(state?.user_research_nodes ?? 0);
  const legacyCount = Number(state?.legacy_test_nodes ?? 0);
  const runId = state?.run_id;
  const target = state?.target;
  const alive = lab?.alive_nodes;
  const dead = lab?.dead_nodes;
  const qualified = lab?.qualified_nodes;
  const blocked = researchBlockReason(lab);
  const opBusy = state?.operation?.busy === true;

  const addNum = parseInt(additional, 10);
  const addValid = Number.isFinite(addNum) && addNum > 0;
  const confirmMatches = typed.trim() === String(userCount);
  /* V4.8 QA: "Resume & Add Nodes" only works on an existing study. With an empty
   * population the backend correctly refuses it ("no active USER_RESEARCH
   * population to resume"), so the option is offered as blocked-with-a-reason
   * instead of as a button that can only fail. */
  const noPopulation = !(Number(userCount) > 0);
  const optionBlocked = (key) => key === "resume" && noPopulation;
  const blockedReason = "there is no active USER_RESEARCH population to resume — archive or delete already emptied this study, so start a new run instead";

  const canRun = (opt) => {
    if (busy || opBusy) return false;
    if (opt === "resume") return addValid && !noPopulation;
    return confirmMatches;
  };

  const runOp = async (fn, label) => {
    setBusy(true); setErr(null); setResult(null); setOpError(null);
    setBusyMsg(label); setProgress(3);
    pollRef.current = setInterval(async () => {
      try {
        const s = await api.researchRunStatus();
        const stages = Array.isArray(s?.stages) ? s.stages : [];
        const done = stages.filter((x) => String(x?.status || "").toUpperCase() === "DONE").length;
        if (stages.length) setProgress(Math.max(5, Math.round((done / stages.length) * 95)));
        else setProgress((p) => Math.min(92, p + 3));
        if (s?.message) setBusyMsg(`${txt(s.stage, "running")}: ${s.message}`);
      } catch { /* progress polling is best-effort */ }
    }, 700);
    try {
      const res = await fn();
      setProgress(100);
      if (res && res.ok === false) setOpError(txt(res.error, "the backend refused the operation"));
      else setResult(res);
      await load();
      onDone?.();
    } catch (e) {
      setOpError(e.message || String(e));
      await load();
    } finally {
      clearInterval(pollRef.current);
      setBusy(false);
      setBusyMsg("");
    }
  };

  const doArchive = () => runOp(
    () => api.researchRunStartFresh({ mode: "backup_and_reset", confirm: "BACKUP_AND_RESET", start: startNow }),
    "Backing up the current study, verifying the archive, then resetting…",
  );
  const doDelete = () => runOp(
    () => api.researchRunStartFresh({ mode: "reset_only", confirm: "RESET_USER_RESEARCH", start: startNow }),
    "Deleting the research population and initialising a fresh run…",
  );
  const doResume = () => runOp(
    () => api.researchRunResumeAdd({ additional_nodes: addNum, start: startNow }),
    `Adding ${addNum} node(s) to the current study…`,
  );

  const run = { archive: doArchive, delete: doDelete, resume: doResume };
  const confirmLabel = { archive: "Archive & Start New", delete: "DELETE & Start New", resume: "Resume & Add Nodes" };

  return (
    <>
      <div className="drawer-backdrop" onClick={busy ? undefined : onClose} />
      <div className="modal" role="dialog" aria-label="Start new research run"
           style={{ position: "fixed", top: "5vh", left: "50%", transform: "translateX(-50%)",
                    maxHeight: "90vh", overflowY: "auto", zIndex: 61 }}>
        <div className="kit-head">
          <div>
            <div className="modal-title">🚀 New Research Run</div>
            <div className="muted" style={{ fontSize: 11.5, marginTop: 2 }}>
              Three explicit choices — the backend keeps its own confirmation tokens and re-checks the legacy population.
            </div>
          </div>
          <button className="btn" onClick={onClose} disabled={busy}>✕ close</button>
        </div>

        <StateBlock loading={loading} error={err} onRetry={load}>
          {/* ---------- population counters ---------- */}
          <div className="grid cols-4" style={{ gridTemplateColumns: "repeat(4, minmax(0,1fr))", marginBottom: 10 }}>
            <Kpi label="Total research nodes" value={fmtNum(userCount)} sub={`legacy excluded: ${fmtNum(legacyCount)}`} />
            <Kpi label="Alive" value={fmtNum(alive)} sub="currently researchable" tone="pos" />
            <Kpi label="Dead" value={fmtNum(dead)} sub="failed / killed history" tone="mute" />
            <Kpi label="Qualified" value={fmtNum(qualified)} sub="passed robustness" tone="pos" />
          </div>

          <div className="kit-cols" style={{ marginBottom: 12 }}>
            <div className="panel" style={{ flex: 1, minWidth: 220, padding: "8px 12px" }}>
              <div className="kit-kv"><span className="k">Current run id</span><span className="mono">{txt(runId, "none")}</span></div>
              <div className="kit-kv"><span className="k">Run target</span><span className="mono">{fmtNum(target)}</span></div>
              <div className="kit-kv"><span className="k">Next node number</span><span className="mono">{txt(state?.next_research_node_num, "—")}</span></div>
              <div className="kit-kv"><span className="k">Stage</span><span className="mono">{txt(lab?.current_stage, "—")}</span></div>
            </div>
            <div className="panel" style={{ flex: 1, minWidth: 220, padding: "8px 12px" }}>
              <div className="kit-kv"><span className="k">Target reached</span><span>{lab?.is_target_reached ? <Badge tone="real">yes</Badge> : <Badge tone="info">no</Badge>}</span></div>
              <div className="kit-kv"><span className="k">Dataset ready</span><span>{lab?.datasets_ready ? <Badge tone="real">yes</Badge> : <Badge tone="danger">no</Badge>}</span></div>
              <div className="kit-kv"><span className="k">Lab running</span><span>{lab?.running ? <Badge tone="ok">running</Badge> : <Badge tone="mute">idle</Badge>}</span></div>
              <div className="kit-kv"><span className="k">Last error</span><span className="muted">{txt(lab?.last_error, "none")}</span></div>
            </div>
          </div>

          {blocked && (
            <div className="warn-banner" role="status">
              <b>⚠ Research is currently blocked.</b>
              <div style={{ marginTop: 3 }}>{blocked}</div>
              <div className="muted" style={{ marginTop: 3 }}>
                You can still archive, delete or re-target the run — the engine simply will not generate nodes until this clears.
              </div>
            </div>
          )}

          {/* ---------- the three options ---------- */}
          <div className="kit-cols">
            {OPTIONS.map((o) => {
              const selected = option === o.key;
              const destructive = o.tone === "delete";
              return (
                <div key={o.key}
                     className="panel"
                     style={{ flex: "1 1 220px", cursor: busy ? "default" : "pointer",
                              borderColor: selected ? (destructive ? "#991b1b" : "var(--accent)") : undefined,
                              background: destructive ? "#1c1214" : undefined }}>
                  <div className="kit-head">
                    <div style={{ fontWeight: 650, fontSize: 13 }}>{o.title}</div>
                    <Badge tone={o.badge.tone}>{o.badge.text}</Badge>
                  </div>
                  <ul className="muted" style={{ margin: "0 0 8px 16px", padding: 0, fontSize: 11.5, lineHeight: 1.55 }}>
                    {o.lines.map((l) => <li key={l}>{l}</li>)}
                  </ul>
                  <button className={"btn " + (destructive ? "danger" : "primary")}
                          disabled={busy || optionBlocked(o.key)}
                          title={optionBlocked(o.key) ? blockedReason : ""}
                          style={{ width: "100%" }}
                          onClick={() => { setOption(o.key); setTyped(""); setResult(null); setOpError(null); }}>
                    {selected ? "Selected" : optionBlocked(o.key) ? "Not available" : "Choose"}
                  </button>
                  {optionBlocked(o.key) && (
                    <div className="muted" style={{ fontSize: 11.5, marginTop: 4 }}>{blockedReason}.</div>
                  )}
                </div>
              );
            })}
          </div>

          {/* ---------- per-option confirmation ---------- */}
          {option && (
            <div className="panel" style={{ marginTop: 12, borderColor: option === "delete" ? "#991b1b" : undefined }}>
              <div className="kit-head">
                <div style={{ fontWeight: 650 }}>{OPTIONS.find((o) => o.key === option).title}</div>
                <Badge tone={OPTIONS.find((o) => o.key === option).badge.tone}>{OPTIONS.find((o) => o.key === option).badge.text}</Badge>
              </div>

              {option === "resume" ? (
                <div>
                  <label className="fld" style={{ maxWidth: 260 }}>
                    Additional nodes to generate
                    <input value={additional} onChange={(e) => setAdditional(e.target.value)} disabled={busy} />
                  </label>
                  <div className="muted" style={{ fontSize: 12 }}>
                    Current: <b className="mono">{fmtNum(userCount)}</b> → new target{" "}
                    <b className="mono">{fmtNum(addValid ? userCount + addNum : userCount)}</b>{" "}
                    {runId ? <>for run <span className="mono">{runId}</span></> : null} — existing nodes are untouched.
                  </div>
                  {!addValid && <div className="kit-inline-err">Enter a positive whole number of nodes to add.</div>}
                </div>
              ) : (
                <div>
                  <div className={"warn-banner" + (option === "delete" ? "" : " muted")}
                       style={option === "delete" ? { borderColor: "#991b1b", background: "#2a1013", color: "#fecaca" } : undefined}>
                    {option === "delete"
                      ? "This permanently deletes the research population. There is no undo."
                      : "The current study is archived first; the archive is verified before anything is reset."}
                  </div>
                  <label className="fld" style={{ maxWidth: 320 }}>
                    Type the exact current node count to confirm: <b className="mono">{userCount}</b>
                    <input value={typed} onChange={(e) => setTyped(e.target.value)} disabled={busy}
                           placeholder={String(userCount)} className="mono" />
                    <span className="kit-hint">
                      {typed && !confirmMatches
                        ? `"${typed}" does not match ${userCount} — the button stays disabled.`
                        : "The button stays disabled until the number matches exactly."}
                    </span>
                  </label>
                </div>
              )}

              <label className="fld" style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input type="checkbox" style={{ width: "auto" }} checked={startNow}
                       onChange={(e) => setStartNow(e.target.checked)} disabled={busy} />
                Start generating immediately after the operation completes
              </label>

              <div className="btn-row" style={{ marginBottom: 0 }}>
                <button className={"btn " + (option === "delete" ? "danger" : "primary")}
                        disabled={!canRun(option)} onClick={run[option]}>
                  {busy ? "working…" : confirmLabel[option]}
                </button>
                <button className="btn" onClick={() => { setOption(null); setTyped(""); }} disabled={busy}>Back</button>
                {!startNow && option !== "resume" && (
                  <span className="muted" style={{ alignSelf: "center", fontSize: 12 }}>
                    The operation completes before any node is generated.
                  </span>
                )}
              </div>
            </div>
          )}

          {/* ---------- progress / result / error ---------- */}
          {(busy || progress > 0) && (
            <div style={{ marginTop: 12 }}>
              <Progress pct={progress} label={busyMsg || "working"} tone={option === "delete" ? "var(--red)" : "var(--accent)"} />
            </div>
          )}
          {opError && <div className="kit-inline-err"><b>The operation did not complete:</b><div>{opError}</div></div>}
          {result && (
            <div className="kit-ok" style={{ marginTop: 10 }}>
              <b>Completed.</b>
              <div className="mono" style={{ marginTop: 4, fontSize: 11.5, whiteSpace: "pre-wrap" }}>
                {JSON.stringify({
                  ok: result.ok, mode: result.mode, run_id: result.run_id,
                  backup: result.backup?.filename || result.backup_file || undefined,
                  reset: result.reset || result.reset_result || undefined,
                  new_target: result.target ?? result.new_target,
                  started: result.started,
                }, null, 1)}
              </div>
            </div>
          )}

          {state?.legacy_notice && (
            <div className="muted" style={{ fontSize: 11.5, marginTop: 10 }}>{state.legacy_notice}</div>
          )}
          {(state?.backups || []).length > 0 && (
            <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
              {state.backups.length} research backup(s) available — newest:{" "}
              <span className="mono">{txt(state.backups[0]?.filename)}</span>
            </div>
          )}
        </StateBlock>
      </div>
    </>
  );
}
