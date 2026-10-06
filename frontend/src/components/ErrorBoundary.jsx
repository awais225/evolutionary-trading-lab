import React from "react";

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      backendStatus: "CHECKING...",
      healthData: null,
      showLogs: false,
      recentLogs: "",
    };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo });
    this.checkBackendHealth();
    console.error("Dashboard caught rendering error:", error, errorInfo);
  }

  checkBackendHealth = async () => {
    try {
      const res = await fetch("/health", { signal: AbortSignal.timeout(3000) });
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        this.setState({ backendStatus: "CONNECTED", healthData: data });
      } else {
        this.setState({ backendStatus: "UNAVAILABLE", healthData: null });
      }
    } catch (e) {
      this.setState({ backendStatus: "UNAVAILABLE", healthData: null });
    }
  };

  fetchRecentLogs = async () => {
    try {
      const res = await fetch("/api/logs?limit=50", { signal: AbortSignal.timeout(4000) });
      if (res.ok) {
        const data = await res.json();
        const logsText = Array.isArray(data)
          ? data.map((l) => `[${l.level || "INFO"}] ${l.message || JSON.stringify(l)}`).join("\n")
          : JSON.stringify(data, null, 2);
        this.setState({ recentLogs: logsText, showLogs: true });
      } else {
        this.setState({ recentLogs: "Failed to fetch logs: HTTP " + res.status, showLogs: true });
      }
    } catch (e) {
      this.setState({ recentLogs: "Could not reach log server: " + e.message, showLogs: true });
    }
  };

  handleRetry = () => {
    window.location.reload();
  };

  handleOpenHealth = () => {
    window.open("/health", "_blank");
  };

  render() {
    if (this.state.hasError) {
      const { error, backendStatus, showLogs, recentLogs } = this.state;
      const isConnected = backendStatus === "CONNECTED";

      return (
        <div style={{
          minHeight: "100vh",
          backgroundColor: "#0b0f19",
          color: "#e2e8f0",
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "2rem 1rem"
        }}>
          <div style={{
            maxWidth: "760px",
            width: "100%",
            background: "#111827",
            border: "1px solid #374151",
            borderRadius: "12px",
            boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.5), 0 10px 10px -5px rgba(0, 0, 0, 0.04)",
            overflow: "hidden"
          }}>
            {/* Header */}
            <div style={{
              background: "#1f2937",
              borderBottom: "1px solid #374151",
              padding: "1.25rem 1.5rem",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between"
            }}>
              <div>
                <h1 style={{ margin: 0, fontSize: "1.25rem", fontWeight: 700, color: "#f8fafc", letterSpacing: "0.02em" }}>
                  🧬 EVOLUTIONARY TRADING RESEARCH LAB
                </h1>
                <div style={{ margin: "4px 0 0 0", fontSize: "0.88rem", color: "#94a3b8" }}>
                  The dashboard could not initialize.
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <span style={{ fontSize: "0.85rem", color: "#94a3b8" }}>Backend status:</span>
                <span style={{
                  padding: "3px 10px",
                  borderRadius: "9999px",
                  fontSize: "0.78rem",
                  fontWeight: 600,
                  letterSpacing: "0.05em",
                  backgroundColor: isConnected ? "rgba(34, 197, 94, 0.2)" : "rgba(239, 68, 68, 0.2)",
                  color: isConnected ? "#4ade80" : "#f87171",
                  border: `1px solid ${isConnected ? "rgba(34, 197, 94, 0.4)" : "rgba(239, 68, 68, 0.4)"}`
                }}>
                  {backendStatus}
                </span>
              </div>
            </div>

            {/* Body */}
            <div style={{ padding: "1.5rem" }}>
              <div style={{ marginBottom: "1rem" }}>
                <span style={{ fontSize: "0.85rem", fontWeight: 600, color: "#94a3b8", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                  Error
                </span>
                <div style={{
                  marginTop: "6px",
                  padding: "12px 14px",
                  background: "rgba(239, 68, 68, 0.08)",
                  border: "1px solid rgba(239, 68, 68, 0.3)",
                  borderRadius: "6px",
                  color: "#fca5a5",
                  fontSize: "0.95rem",
                  fontFamily: "monospace",
                  wordBreak: "break-all",
                  lineHeight: 1.4
                }}>
                  {error?.toString() || "Unknown initialization failure"}
                </div>
              </div>

              {/* Action Buttons */}
              <div style={{ display: "flex", flexWrap: "wrap", gap: "10px", marginTop: "1.5rem" }}>
                <button
                  onClick={this.handleRetry}
                  style={{
                    backgroundColor: "#2563eb",
                    color: "#ffffff",
                    border: "none",
                    borderRadius: "6px",
                    padding: "8px 18px",
                    fontWeight: 600,
                    fontSize: "0.88rem",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: "6px"
                  }}
                >
                  ↻ RETRY
                </button>
                <button
                  onClick={this.handleOpenHealth}
                  style={{
                    backgroundColor: "#374151",
                    color: "#f1f5f9",
                    border: "1px solid #4b5563",
                    borderRadius: "6px",
                    padding: "8px 16px",
                    fontWeight: 500,
                    fontSize: "0.88rem",
                    cursor: "pointer"
                  }}
                >
                  OPEN HEALTH STATUS
                </button>
                <button
                  onClick={this.fetchRecentLogs}
                  style={{
                    backgroundColor: "#374151",
                    color: "#f1f5f9",
                    border: "1px solid #4b5563",
                    borderRadius: "6px",
                    padding: "8px 16px",
                    fontWeight: 500,
                    fontSize: "0.88rem",
                    cursor: "pointer"
                  }}
                >
                  VIEW DIAGNOSTIC LOG
                </button>
              </div>

              {/* Diagnostic Log Display */}
              {showLogs && (
                <div style={{ marginTop: "1.5rem" }}>
                  <div style={{ fontSize: "0.82rem", fontWeight: 600, color: "#94a3b8", marginBottom: "6px" }}>
                    DIAGNOSTIC LOG (LAST ENTRIES):
                  </div>
                  <pre style={{
                    background: "#090d16",
                    border: "1px solid #1f2937",
                    borderRadius: "6px",
                    padding: "12px",
                    fontSize: "0.78rem",
                    color: "#cbd5e1",
                    maxHeight: "220px",
                    overflowY: "auto",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all"
                  }}>
                    {recentLogs || "No log entries found."}
                  </pre>
                </div>
              )}
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
