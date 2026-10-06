import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { txt } from "../lib/safe.js";

/* V4.7 startup / readiness banner.
 *
 * The backend now binds its socket before it finishes scanning DATA, so the
 * dashboard can render while the expensive startup steps are still running.
 * This banner makes that state visible instead of leaving the user guessing:
 *
 *   starting -> amber  "backend initialising (step X)"  (bounded steps, with the
 *                       steps that have already completed listed)
 *   failed   -> red    the failed step names and their error reasons
 *   degraded -> amber  non-critical startup problems, with reasons
 *   ready    -> nothing is rendered
 *
 * Polling stops entirely once the backend is ready or failed.
 */
export default function StartupBanner() {
  const [snap, setSnap] = useState(null);
  const [showDetail, setShowDetail] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const h = await api.health();
        if (cancelled) return;
        setSnap(h);
        if (h?.ready || h?.status === "error") return;   // stop polling
      } catch (e) {
        if (cancelled) return;
        setSnap({ status: "error", startup: { state: "unknown", failed_steps: [] }, error: txt(e?.message) });
        return;
      }
      if (!cancelled) timerRef.current = setTimeout(poll, 4000);
    };

    poll();
    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  if (!snap || snap.ready) return null;
  const st = snap.startup || {};
  const failed = Array.isArray(st.failed_steps) ? st.failed_steps : [];
  const isError = snap.status === "error" || st.state === "failed";
  const running = Array.isArray(st.running) ? st.running : [];
  const done = (Array.isArray(st.steps) ? st.steps : []).filter((s) => s.status === "DONE").map((s) => s.name);

  return (
    <div className="warn-banner" style={{
      background: isError ? "#2a0f12" : "#2a2110",
      borderColor: isError ? "#7f1d1d" : "#78350f",
      color: isError ? "#fca5a5" : "#fcd34d",
      padding: "8px 14px", margin: "0 0 1rem 0",
    }}>
      <div className="flex justify-between items-center">
        <div style={{ fontSize: 12.5 }}>
          {isError ? (
            <>
              <b>Backend startup failed.</b>{" "}
              {failed.length
                ? txt(failed.map((s) => `${s.name}: ${s.error}`).join(" — "))
                : txt(snap.error || "the backend reported a failed startup state")}
            </>
          ) : (
            <>
              <b>Backend is initialising…</b>{" "}
              {running.length
                ? <>running: <b>{txt(running.join(", "))}</b></>
                : "preparing data and database"}
              {done.length ? <> — completed: {txt(done.join(", "))}</> : null}
              {" "}(research, statistics and historical backtests become available as soon as it is ready)
            </>
          )}
        </div>
        <button className="btn btn-sm btn-subtle" onClick={() => setShowDetail((v) => !v)}>
          {showDetail ? "hide detail" : "detail"}
        </button>
      </div>
      {showDetail && (
        <pre style={{
          marginTop: 8, maxHeight: 220, overflow: "auto", fontSize: 10.5, color: "#e2e8f0",
          background: "#0b0e14", border: "1px solid #334155", borderRadius: 6, padding: 10,
        }}>
          {JSON.stringify(st, null, 2)}
        </pre>
      )}
    </div>
  );
}
