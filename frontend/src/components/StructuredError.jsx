import React, { useState } from "react";

/**
 * StructuredError — Renders backend/validation errors with full diagnostic clarity.
 * Prevents stringified '[object Object]' artifacts and provides field-level insight.
 */
export default function StructuredError({ error, title = "Operation Failed", onDismiss = null }) {
  const [showDetails, setShowDetails] = useState(false);

  if (!error) return null;

  // Extract structured properties if present
  const status = error.status || (typeof error === "string" && error.includes("422") ? 422 : null);
  const endpoint = error.endpoint || "";
  const formattedMsg = error.formattedMessage || error.message || String(error);
  const rawJson = error.rawJson || error.detail;
  const timestamp = error.timestamp || new Date().toLocaleTimeString();
  // V4.8 — the node a failure belongs to belongs in the error block itself, so a
  // failing node never has to be guessed from a stack of console lines.
  const nodeRef = error.node_id ?? error.nodeId ?? error.node
    ?? (typeof rawJson === "object" && rawJson ? (rawJson.node_id ?? rawJson.node ?? rawJson.detail?.node_id) : null)
    ?? (/\bNode_(\d+)\b/.exec(String(formattedMsg))?.[1] ?? /\bnode[ _#]?(\d{3,})\b/i.exec(String(formattedMsg))?.[1] ?? null);

  // If rawJson contains FastAPI validation errors list
  const fieldErrors = Array.isArray(rawJson)
    ? rawJson
    : Array.isArray(rawJson?.detail)
    ? rawJson.detail
    : null;

  return (
    <div className="my-2 p-3.5 bg-rose-950/40 border border-rose-500/40 rounded-lg text-rose-200 text-xs shadow-lg animate-in fade-in duration-200">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-rose-500/20 text-rose-400 font-bold text-xs">
            ✕
          </span>
          <div>
            <div className="font-semibold text-rose-300 flex items-center gap-2">
              <span>{title}</span>
              {status && (
                <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-rose-900/60 border border-rose-700/50 text-rose-300">
                  HTTP {status}
                </span>
              )}
            </div>
            <div className="mt-0.5 text-rose-200 font-mono text-[11px] break-words">
              {formattedMsg}
            </div>
            {(endpoint || nodeRef) && (
              <div className="mt-1 text-[10px] text-rose-300/80 font-mono">
                {endpoint ? `endpoint: ${endpoint}` : ""}{endpoint && nodeRef ? " · " : ""}{nodeRef ? `node: ${nodeRef}` : ""}
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {rawJson && (
            <button
              type="button"
              onClick={() => setShowDetails(!showDetails)}
              className="px-2 py-0.5 rounded text-[10px] bg-rose-900/40 hover:bg-rose-800/60 text-rose-300 border border-rose-700/40 transition-colors"
            >
              {showDetails ? "Hide Details" : "View Raw"}
            </button>
          )}
          {onDismiss && (
            <button
              type="button"
              onClick={onDismiss}
              className="p-1 text-rose-400 hover:text-rose-200 transition-colors"
              title="Dismiss"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {fieldErrors && fieldErrors.length > 0 && (
        <div className="mt-2.5 pt-2 border-t border-rose-800/40">
          <div className="text-[10px] uppercase font-bold text-rose-400 tracking-wider mb-1">
            Validation Field Diagnostics:
          </div>
          <div className="space-y-1">
            {fieldErrors.map((f, i) => (
              <div
                key={i}
                className="bg-rose-900/30 px-2 py-1 rounded font-mono text-[10px] flex items-center gap-2"
              >
                <span className="text-amber-300 font-semibold">
                  {Array.isArray(f.loc) ? f.loc.filter((x) => x !== "body").join(".") : f.loc || "field"}:
                </span>
                <span className="text-rose-200">{f.msg}</span>
                {f.type && (
                  <span className="text-slate-400 text-[9px]">({f.type})</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {showDetails && rawJson && (
        <div className="mt-2 pt-2 border-t border-rose-800/40">
          <div className="flex flex-wrap justify-between items-center gap-2 text-[10px] text-slate-400 mb-1">
            <span>Status: {status || "n/a"} · Endpoint: {endpoint || "Local action"} · Node: {nodeRef ?? "—"}</span>
            <span>{timestamp}</span>
          </div>
          <pre className="p-2 bg-slate-950/80 rounded border border-rose-900/50 overflow-x-auto text-[10px] font-mono text-slate-300 max-h-36">
            {typeof rawJson === "object" ? JSON.stringify(rawJson, null, 2) : String(rawJson)}
          </pre>
        </div>
      )}
    </div>
  );
}
