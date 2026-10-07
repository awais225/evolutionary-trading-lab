import React, { useCallback, useEffect, useState } from "react";

/**
 * Build identity chip (V5.1a §5/§6/§7).
 *
 * The operator's recurring question has been "is the dashboard I am looking at
 * actually the current build?" — this answers it from the page itself:
 *
 *   * it fetches `/system/build`, which states the process identity, the code
 *     fingerprint and the bundle the server is serving *right now*;
 *   * it compares the entry script this page actually loaded against the entry
 *     assets the server advertises. If they differ, this tab is running a
 *     superseded bundle (a cached shell, an old tab, a second checkout) and the
 *     chip turns amber and offers a reload on a build-unique URL;
 *   * it shows the source fingerprint so it can be compared character for
 *     character with what `start.bat` printed and with the GitHub commit.
 *
 * Diagnostic only — it never changes trading, research or scheduling behaviour.
 */
function loadedEntryAssets() {
  const out = [];
  try {
    for (const el of document.querySelectorAll("script[src], link[rel=stylesheet][href]")) {
      const raw = el.getAttribute("src") || el.getAttribute("href") || "";
      if (/\/assets\//.test(raw)) out.push(raw.replace(/^\//, "").split("?")[0]);
    }
  } catch {
    /* a document without script tags is not an error worth reporting */
  }
  return out;
}

export default function BuildIdentityChip() {
  const [info, setInfo] = useState(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);

  const load = useCallback(() => {
    fetch("/system/build", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(setInfo)
      .catch(() => setFailed(true));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  if (failed || !info) return null;

  const served = info.served || {};
  const proc = info.process || {};
  const loaded = info.loaded || {};
  const serverAssets = new Set([...(served.entry_assets || []), ...(loaded.entry_assets || [])]);
  const mine = loadedEntryAssets();
  const staleShell = mine.length > 0 && serverAssets.size > 0 && mine.some((a) => !serverAssets.has(a));
  const distOk = info.dist_matches_src !== false;
  const problem = staleShell || !distOk;

  const commit = String(info.git_commit || "").slice(0, 7) || "—";
  const src = String(info.src_hash || "").slice(0, 8) || "—";
  const servedIndex = String(served.index_sha256 || "").slice(0, 8) || "—";
  const servedNow = served.index_sha256 || src;
  const tone = problem ? "var(--amber)" : "var(--green)";

  return (
    <span style={{ position: "relative", display: "inline-block" }}>
      <button
        className="status-row-item"
        onClick={() => setOpen((v) => !v)}
        title={
          problem
            ? "This browser tab is not running the bundle the server serves"
            : "Build identity of the served dashboard"
        }
        style={{
          display: "flex", alignItems: "center", gap: 5, cursor: "pointer",
          background: "transparent", border: "none", color: "inherit",
          font: "inherit", padding: "0 4px",
        }}
      >
        <span style={{ color: tone }}>{problem ? "▲" : "◆"}</span>
        <span style={{ fontWeight: 600 }}>BUILD:</span>
        <span style={{ color: tone }} className="mono">
          {info.release || info.version || "V5"} · {commit} · FE {src}
        </span>
      </button>

      {open && (
        <div
          className="panel"
          style={{
            position: "absolute", zIndex: 60, top: "130%", right: 0, width: 430,
            padding: "10px 12px", fontSize: 11.5, lineHeight: 1.55,
            boxShadow: "0 10px 30px rgba(0,0,0,.45)",
          }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>
            Dashboard build identity
          </div>
          {problem && (
            <div style={{ color: "var(--amber)", marginBottom: 6 }}>
              {staleShell
                ? `This tab loaded ${mine.join(", ")} but the server serves ${(served.entry_assets || []).join(", ") || "—"}. `
                : "The bundle on disk no longer matches frontend/src. "}
              Reload to run the build the server is serving.
            </div>
          )}
          <div className="mono" style={{ fontSize: 11 }}>
            <div>release    : {info.release || "—"}</div>
            <div>app        : {info.version_string || info.version}</div>
            <div>git commit : {info.git_commit || "—"}</div>
            <div>repo root  : {info.repo_root}</div>
            <div>src hash   : {info.src_hash}</div>
            <div>dist status: {info.dist_status} (matches src: {String(info.dist_matches_src)})</div>
            <div>served css/js: {(served.entry_assets || []).join(", ") || "—"}</div>
            <div>served index: {servedIndex}…</div>
            <div>this tab   : {mine.join(", ") || "—"}</div>
            <div>
              process    : pid {proc.pid ?? "—"} · started {proc.started_iso || "—"} ·{" "}
              up {proc.uptime_s ?? "—"}s
            </div>
            <div>launch token: {proc.start_token || "—"}</div>
            <div>code hash  : {String(proc.code_hash || "").slice(0, 16) || "—"}</div>
            <div>
              bundle at start: {String(loaded.index_sha256 || "").slice(0, 8) || "—"} ·{" "}
              changed since start: {String(served.changed_since_start)}
            </div>
          </div>
          <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center" }}>
            <button
              className="btn"
              onClick={() => window.location.replace(`/?v=${servedNow}`)}
            >
              Reload current build
            </button>
            <button className="btn" onClick={load}>Re-check</button>
            <span className="muted">always reloads the served build</span>
          </div>
        </div>
      )}
    </span>
  );
}
