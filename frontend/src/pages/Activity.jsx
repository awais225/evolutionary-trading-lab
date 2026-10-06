import React, { useEffect, useState, useMemo } from "react";
import { api, fmt } from "../api.js";
import { useLabEvents } from "../ws.js";
import { Card, Pill } from "../components/common.jsx";

const FILTERS = [
  "Newest", "Oldest", "Errors", "Warnings", "Active",
  "MT5", "Features", "Workers", "Dataset", "Research", "System"
];

export default function Activity() {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeFilter, setActiveFilter] = useState("Newest");
  const [filterText, setFilterText] = useState("");
  const [copyStatus, setCopyStatus] = useState("");
  const { events: wsEvents } = useLabEvents();

  const fetchEvents = () => {
    api.activity({ limit: 500, sort: activeFilter === "Oldest" ? "asc" : "desc" })
      .then((data) => {
        setEvents(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    fetchEvents();
  }, [activeFilter]);

  // Listen to live activity events
  useEffect(() => {
    if (wsEvents.length > 0 && wsEvents[0]?.type === "activity") {
      const newEv = wsEvents[0].payload;
      setEvents((prev) => [newEv, ...prev.slice(0, 499)]);
    }
  }, [wsEvents]);

  const handleCopyLog = async () => {
    try {
      const res = await api.diagnosticLog(300);
      const text = res.diagnostic_text || "No logs available";
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopyStatus("LOG COPIED");
      setTimeout(() => setCopyStatus(""), 3000);
    } catch (e) {
      setCopyStatus("FAILED");
      setTimeout(() => setCopyStatus(""), 3000);
    }
  };

  const filtered = useMemo(() => {
    let list = [...events];
    const f = activeFilter;

    if (f === "Oldest") {
      list.sort((a, b) => (a.ts || 0) - (b.ts || 0));
    } else {
      list.sort((a, b) => (b.ts || 0) - (a.ts || 0));
    }

    if (f === "Errors") {
      list = list.filter((e) => e.level === "ERROR" || e.status === "FAILED");
    } else if (f === "Warnings") {
      list = list.filter((e) => e.level === "WARNING" || e.status === "WARNING");
    } else if (f === "Active") {
      list = list.filter((e) => e.status === "RUNNING" || e.status === "STARTED" || e.status === "ACTIVE");
    } else if (f === "MT5") {
      list = list.filter((e) => e.category === "MT5");
    } else if (f === "Features") {
      list = list.filter((e) => e.category === "FEATURES" || e.category === "FEATURE");
    } else if (f === "Workers") {
      list = list.filter((e) => ["WORKERS", "WORKER", "RESOURCE"].includes(e.category));
    } else if (f === "Dataset") {
      list = list.filter((e) => ["DATA", "SYNC", "DATASET"].includes(e.category));
    } else if (f === "Research") {
      list = list.filter((e) => ["RESEARCH", "EVOLUTION", "BACKTEST", "VALIDATION"].includes(e.category));
    } else if (f === "System") {
      list = list.filter((e) => ["SYSTEM", "DATABASE", "GPU"].includes(e.category));
    }

    if (filterText) {
      list = list.filter((e) => e.message?.toLowerCase().includes(filterText.toLowerCase()));
    }
    return list;
  }, [events, activeFilter, filterText]);

  const levelColor = (lvl) => {
    switch (lvl) {
      case "SUCCESS": return "var(--green)";
      case "WARNING": return "var(--amber)";
      case "ERROR": return "var(--red)";
      default: return "var(--blue)";
    }
  };

  return (
    <div className="page activity-page">
      <div className="page-header flex justify-between items-center" style={{ marginBottom: "1.2rem" }}>
        <div>
          <h2>⚡ Operational Activity Stream</h2>
          <div className="sub muted">Real-time laboratory events & audit log (V3.2 structured logging)</div>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn btn-sm btn-primary"
            onClick={handleCopyLog}
            style={{
              fontWeight: 700,
              background: copyStatus === "LOG COPIED" ? "#059669" : undefined,
              borderColor: copyStatus === "LOG COPIED" ? "#10b981" : undefined,
            }}
          >
            {copyStatus === "LOG COPIED" ? "✓ LOG COPIED" : "📋 COPY LOG"}
          </button>
          <button className="btn btn-sm btn-subtle" onClick={fetchEvents}>
            ↻ Refresh
          </button>
        </div>
      </div>

      <Card style={{ marginBottom: "1rem" }}>
        <div className="flex gap-2 items-center flex-wrap" style={{ padding: "0.4rem 0" }}>
          <div className="flex gap-1 items-center flex-wrap">
            <span className="muted" style={{ fontSize: "0.85rem", marginRight: "4px" }}>Filter:</span>
            {FILTERS.map((cat) => (
              <button
                key={cat}
                className={`btn btn-xs ${activeFilter === cat ? "btn-primary" : "btn-subtle"}`}
                onClick={() => setActiveFilter(cat)}
              >
                {cat}
              </button>
            ))}
          </div>

          <div style={{ marginLeft: "auto" }}>
            <input
              type="text"
              placeholder="Search event message..."
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              style={{ width: "220px", padding: "4px 8px", fontSize: "0.85rem", borderRadius: "4px", background: "var(--bg-box)", color: "var(--fg)", border: "1px solid var(--border)" }}
            />
          </div>
        </div>
      </Card>

      <Card>
        <div className="table-wrap" style={{ maxHeight: "calc(100vh - 250px)", overflowY: "auto" }}>
          <table className="table" style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                <th style={{ width: "100px", padding: "8px" }}>Time</th>
                <th style={{ width: "90px", padding: "8px" }}>Level</th>
                <th style={{ width: "120px", padding: "8px" }}>Category</th>
                <th style={{ padding: "8px" }}>Message</th>
                <th style={{ width: "90px", padding: "8px", textAlign: "right" }}>Strategy</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e, idx) => (
                <tr key={idx} style={{ borderBottom: "1px solid var(--border-subtle, rgba(255,255,255,0.05))" }}>
                  <td className="muted mono" style={{ fontSize: "0.8rem", padding: "6px 8px" }}>
                    {fmt.ts(e.ts)}
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        fontWeight: 600,
                        padding: "2px 6px",
                        borderRadius: "3px",
                        color: levelColor(e.level),
                        border: `1px solid ${levelColor(e.level)}40`,
                        background: `${levelColor(e.level)}15`,
                      }}
                    >
                      {e.level}
                    </span>
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    <span className="mono" style={{ fontSize: "0.8rem", color: "var(--fg-dim)" }}>
                      {e.category}
                    </span>
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: "0.9rem", color: "var(--fg)" }}>
                    {e.message}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>
                    {e.strategy_id ? (
                      <span className="mono" style={{ color: "var(--blue)", fontWeight: 600 }}>
                        #{e.strategy_id}
                      </span>
                    ) : (
                      <span className="muted">–</span>
                    )}
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={5} style={{ textAlign: "center", padding: "2rem", color: "var(--muted)" }}>
                    {loading ? "Loading operational activity..." : "No events match current filter."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
