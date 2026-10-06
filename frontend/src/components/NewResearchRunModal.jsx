import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { ErrorNote, Spinner } from "./common.jsx";

/**
 * V4.1 START NEW RESEARCH RUN dialog.
 *
 * Three explicit options over the existing machinery:
 *   1. BACKUP CURRENT NODES + START FROM ZERO   (backup -> verify -> reset -> new run)
 *   2. RESUME CURRENT STUDY + ADD NODES         (target += N, nothing regenerated)
 *   3. START NEW WITHOUT BACKUP                 (destructive reset, typed confirmation)
 *
 * LEGACY_TEST infrastructure nodes are outside this workflow and are never
 * touched by any option (the backend scopes every destructive statement to
 * USER_RESEARCH and re-checks the legacy count afterwards).
 */
export default function NewResearchRunModal({ onClose, onDone }) {
  const [state, setState] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [option, setOption] = useState(null);      // "backup" | "resume" | "fresh"
  const [additional, setAdditional] = useState("1000");
  const [confirmText, setConfirmText] = useState("");
  const [busyMsg, setBusyMsg] = useState("");
  const [result, setResult] = useState(null);
  // "start immediately" is opt-in for all three options: the operation itself is
  // always completed first (backup -> verify -> reset -> init), then the run starts.
  const [startNow, setStartNow] = useState(false);
  // option 1 always resets the study after the verified backup: require an
  // explicit acknowledgement so it can never happen by one accidental click.
  const [ackBackup, setAckBackup] = useState(false);

  const load = () => {
    api.researchRunState()
      .then(setState)
      .catch((e) => setErr(e.message));
  };
  useEffect(() => { load(); }, []);

  const userCount = state?.user_research_nodes ?? 0;
  const legacyCount = state?.legacy_test_nodes ?? 0;
  const addNum = parseInt(additional, 10);
  const addValid = Number.isFinite(addNum) && addNum > 0;
  const newTotal = addValid ? userCount + addNum : userCount;

  const runOp = async (fn, label) => {
    setBusy(true);
    setErr(null);
    setResult(null);
    setBusyMsg(label);
    // poll the backend operation status so progress is visible while it works
    const poll = setInterval(() => {
      api.researchRunStatus()
        .then((s) => { if (s?.message) setBusyMsg(`${s.stage}: ${s.message}`); })
        .catch(() => {});
    }, 700);
    try {
      const res = await fn();
      setResult(res);
      load();
      onDone?.();
    } catch (e) {
      // FastAPI puts the structured detail into the message via formatErrorDetail
      setErr(e.message);
      load();
    } finally {
      clearInterval(poll);
      setBusy(false);
      setBusyMsg("");
    }
  };

  const doBackupReset = () => runOp(
    () => api.researchRunStartFresh({
      mode: "backup_and_reset",
      confirm: "BACKUP_AND_RESET",
      start: startNow,
    }),
    "Creating backup, verifying, resetting and initialising the new run…",
  );

  const doResumeAdd = () => runOp(
    () => api.researchRunResumeAdd({ additional_nodes: addNum, start: startNow }),
    `Adding ${addNum} nodes to the current study…`,
  );

  const doFreshNoBackup = () => runOp(
    () => api.researchRunStartFresh({
      mode: "reset_only",
      confirm: "RESET_USER_RESEARCH",
      start: startNow,
    }),
    "Resetting the USER_RESEARCH study without backup…",
  );

  return (
    <>
      <div className="drawer-backdrop" onClick={busy ? undefined : onClose} />
      <div
        role="dialog"
        aria-label="Start new research run"
        style={{
          position: "fixed", top: "6vh", left: "50%", transform: "translateX(-50%)",
          width: "min(760px, 94vw)", maxHeight: "88vh", overflowY: "auto",
          background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 10,
          padding: 18, zIndex: 50,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
          <h2 style={{ margin: 0, fontSize: "1.05rem" }}>🚀 START NEW RESEARCH RUN</h2>
          <button className="btn" onClick={onClose} disabled={busy}>✕ close</button>
        </div>

        {!state && !err && <Spinner />}
        {err && <div style={{ marginTop: 10 }}><ErrorNote err={err} /></div>}

        {state && (
          <>
            <div className="panel" style={{ marginTop: 12, background: "var(--bg-panel)" }}>
              <div style={{ display: "flex", gap: 22, flexWrap: "wrap", fontSize: "0.85rem" }}>
                <span>USER_RESEARCH nodes: <b className="mono" style={{ color: "var(--green)" }}>{userCount.toLocaleString()}</b></span>
                <span>LEGACY_TEST nodes: <b className="mono" style={{ color: "var(--muted)" }}>{legacyCount.toLocaleString()}</b></span>
                <span>Active run: <b className="mono">{state.run_id || "—"}</b></span>
                <span>Target: <b className="mono">{state.target?.toLocaleString()}</b></span>
              </div>
              <div className="muted" style={{ fontSize: "0.75rem", marginTop: 6 }}>{state.legacy_notice}</div>
            </div>

            {busy && (
              <div className="warn-banner" style={{ marginTop: 6 }}>
                ⏳ {busyMsg || "working…"} <span className="muted">(do not close this window)</span>
              </div>
            )}

            <label
              className="muted"
              style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.8rem", marginTop: 10 }}
            >
              <input
                type="checkbox"
                checked={startNow}
                disabled={busy}
                onChange={(e) => setStartNow(e.target.checked)}
              />
              START IMMEDIATELY — begin the research loop as soon as the operation completes
            </label>

            {/* -------- option 1: backup + start from zero -------- */}
            <OptionCard
              title="1 · BACKUP CURRENT NODES + START FROM ZERO"
              tone="var(--blue)"
              disabled={busy}
              selected={option === "backup"}
              onSelect={() => setOption("backup")}
              summary="Safe reset with recoverable backup."
            >
              Copies the current USER_RESEARCH study into a new backup archive,
              verifies it is readable and complete, and only then resets the
              research population and initialises a fresh run (target{" "}
              <b>{state.configured_fresh_target?.toLocaleString()}</b>).
              LEGACY_TEST nodes are not included in the backup and are not reset.
              <label
                className="muted"
                style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.78rem", marginTop: 8 }}
              >
                <input
                  type="checkbox"
                  checked={ackBackup}
                  disabled={busy}
                  onChange={(e) => setAckBackup(e.target.checked)}
                />
                I understand the current {userCount.toLocaleString()}-node study is reset after the backup is verified.
              </label>
              <div style={{ marginTop: 8 }}>
                <button className="btn primary" disabled={busy || !ackBackup} onClick={doBackupReset}>
                  BACKUP + START FROM ZERO
                </button>
              </div>
            </OptionCard>

            {/* -------- option 2: resume + add nodes -------- */}
            <OptionCard
              title="2 · RESUME CURRENT STUDY + ADD NODES"
              tone="var(--green)"
              disabled={busy}
              selected={option === "resume"}
              onSelect={() => setOption("resume")}
              summary="Preserve current research and increase the target."
            >
              Existing nodes, statuses, metrics and ancestry are preserved.
              Only the additional nodes are generated; nothing is regenerated.
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, flexWrap: "wrap" }}>
                <label>ADDITIONAL NODES:</label>
                <input
                  className="mono"
                  style={{ width: 120, padding: "4px 8px", background: "var(--bg)", color: "var(--fg)", border: "1px solid var(--border)", borderRadius: 4 }}
                  value={additional}
                  disabled={busy}
                  onChange={(e) => setAdditional(e.target.value.replace(/[^0-9]/g, ""))}
                />
                <span className="muted">
                  {addValid
                    ? <>target {userCount.toLocaleString()} → <b className="mono" style={{ color: "var(--green)" }}>{newTotal.toLocaleString()}</b></>
                    : "enter a positive number"}
                </span>
                <button className="btn primary" disabled={busy || !addValid} onClick={doResumeAdd}>
                  ADD NODES &amp; CONTINUE
                </button>
              </div>
            </OptionCard>

            {/* -------- option 3: start new without backup -------- */}
            <OptionCard
              title="3 · START NEW WITHOUT BACKUP"
              tone="var(--red)"
              disabled={busy}
              selected={option === "fresh"}
              onSelect={() => setOption("fresh")}
              summary="Destructive reset with no backup."
            >
              <b style={{ color: "var(--red)" }}>
                This will permanently reset the current USER_RESEARCH study
                ({userCount.toLocaleString()} nodes) without creating a backup.
                LEGACY_TEST infrastructure data ({legacyCount.toLocaleString()} nodes) will not be affected.
              </b>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, flexWrap: "wrap" }}>
                <label>Type <b className="mono">RESET</b> to confirm:</label>
                <input
                  className="mono"
                  style={{ width: 140, padding: "4px 8px", background: "var(--bg)", color: "var(--fg)", border: "1px solid var(--border)", borderRadius: 4 }}
                  value={confirmText}
                  disabled={busy}
                  onChange={(e) => setConfirmText(e.target.value)}
                />
                <button
                  className="btn danger"
                  disabled={busy || confirmText.trim().toUpperCase() !== "RESET"}
                  onClick={doFreshNoBackup}
                >
                  RESET WITHOUT BACKUP
                </button>
              </div>
            </OptionCard>

            {result && (
              <div className="panel" style={{ marginTop: 10, background: "var(--bg-panel)", border: "1px solid var(--border)" }}>
                <h3 style={{ margin: "0 0 6px", fontSize: "0.8rem", color: "var(--green)" }}>✔ OPERATION COMPLETE</h3>
                <div className="mono" style={{ fontSize: "0.75rem", whiteSpace: "pre-wrap" }}>
                  {JSON.stringify({
                    mode: result.mode,
                    run_id: result.run_id,
                    target: result.new_target ?? result.target,
                    existing_nodes: result.existing_nodes,
                    nodes_to_generate: result.nodes_to_generate,
                    user_research_nodes: result.after?.user_research_nodes ?? result.existing_nodes,
                    legacy_test_nodes: result.legacy_test_nodes ?? result.after?.legacy_test_nodes,
                    legacy_untouched: result.legacy_untouched,
                    backup: result.backup?.filename,
                  }, null, 2)}
                </div>
                <div className="muted" style={{ fontSize: "0.75rem", marginTop: 6 }}>
                  Dashboard statistics will refresh automatically.
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}

function OptionCard({ title, tone, children, summary, disabled, selected, onSelect }) {
  return (
    <div
      className="panel"
      style={{
        marginTop: 10, background: "var(--bg-panel)",
        border: `1px solid ${selected ? tone : "var(--border)"}`,
        opacity: disabled ? 0.6 : 1,
      }}
      onClick={() => !disabled && onSelect?.()}
    >
      <h3 style={{ margin: "0 0 4px", fontSize: "0.82rem", color: tone }}>{title}</h3>
      <div className="muted" style={{ fontSize: "0.75rem", marginBottom: 6 }}>{summary}</div>
      <div style={{ fontSize: "0.82rem" }}>{children}</div>
    </div>
  );
}
