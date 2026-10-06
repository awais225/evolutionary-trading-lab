import React from "react";
import { txt } from "../lib/safe.js";

/* V4.7 localised error boundary.
 *
 * The global boundary in main.jsx still catches anything that escapes, but a
 * single bad node/strategy must not take the whole dashboard down: each complex
 * page (Stats, Strategy Lab, Backtest Matrix, MT5 Historical Backtest, node
 * detail) is wrapped in its own boundary so the failure is contained to the
 * area that failed, with a useful state, the error text for diagnosis and a
 * retry that re-renders just that subtree.
 */
export class LocalErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null, info: null, showDetail: false };
    this.retry = this.retry.bind(this);
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    this.setState({ info });
    // keep the console record - the UI error state is never the only evidence
    console.error(`[${this.props.label || "panel"}] rendering error:`, error, info);
  }

  retry() {
    this.setState({ error: null, info: null, showDetail: false });
  }

  render() {
    const { error, info, showDetail } = this.state;
    if (!error) return this.props.children;
    const label = this.props.label || "this section";
    const where = info && info.componentStack ? String(info.componentStack).trim().split("\n")[0] : null;

    return (
      <div className="panel" style={{ border: "1px solid #7f1d1d", background: "#1b1113" }}>
        <h3 style={{ color: "#fca5a5", marginBottom: 4 }}>
          {label} could not be rendered
        </h3>
        <div className="muted" style={{ fontSize: 11.5, marginBottom: 8 }}>
          The rest of the dashboard is unaffected. This is a rendering failure in the
          {label} area only — the backend and the stored research data were not changed.
        </div>
        <div style={{
          fontFamily: "monospace", fontSize: 11.5, color: "#fca5a5", background: "#120a0b",
          border: "1px solid #7f1d1d", borderRadius: 6, padding: "8px 10px", wordBreak: "break-word",
        }}>
          {txt(error && (error.message || error.toString()), "unknown rendering error")}
        </div>
        <div className="row" style={{ gap: 8, marginTop: 10, display: "flex", alignItems: "center" }}>
          <button className="btn" onClick={this.retry}>Retry this section</button>
          <button className="btn" onClick={() => { window.location.reload(); }}>Reload dashboard</button>
          <button className="btn" onClick={() => this.setState({ showDetail: !showDetail })}>
            {showDetail ? "Hide diagnostics" : "Show diagnostics"}
          </button>
        </div>
        {showDetail && (
          <pre style={{
            marginTop: 10, maxHeight: 200, overflow: "auto", fontSize: 10.5, color: "#cbd5e1",
            background: "#090d16", border: "1px solid #1f2937", borderRadius: 6, padding: 10,
            whiteSpace: "pre-wrap",
          }}>
            {`label: ${label}\n${where ? `component: ${where}\n` : ""}${info && info.componentStack ? info.componentStack : "(no component stack)"}`}
          </pre>
        )}
      </div>
    );
  }
}

export default LocalErrorBoundary;
