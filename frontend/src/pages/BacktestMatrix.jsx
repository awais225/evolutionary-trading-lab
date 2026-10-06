import React, { useEffect, useState } from "react";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import { MatrixTable, ErrorNote, Spinner, SignedNum, Pill } from "../components/common.jsx";
import StructuredError from "../components/StructuredError.jsx";

export default function BacktestMatrix() {
  const { selectedStrategyId, setSelectedStrategyId } = useLab() || {};
  const [candidates, setCandidates] = useState([]);
  const [sid, setSid] = useState(selectedStrategyId ? String(selectedStrategyId) : "");
  const [detail, setDetail] = useState(null);
  const [runFilter, setRunFilter] = useState("USER_RESEARCH");
  const [generating, setGenerating] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (selectedStrategyId) {
      setSid(String(selectedStrategyId));
    }
  }, [selectedStrategyId]);

  const loadCandidates = () => {
    const p = {
      limit: 100,
      sort: "fitness",
      data_source: runFilter === "ALL" ? undefined : runFilter
    };
    api.population(p)
      .then((r) => {
        let list = r.strategies || [];
        if (list.length === 0 && runFilter === "USER_RESEARCH") {
          api.population({ limit: 100, sort: "fitness" }).then((allR) => {
            setCandidates(allR.strategies || []);
            if (!sid && allR.strategies?.length) {
              const q = allR.strategies.find((s) => ["QUALIFIED", "PAPER", "SURVIVED"].includes(s.status)) || allR.strategies[0];
              setSid(String(q.id));
            }
          }).catch((e) => setErr(e.message));
        } else {
          setCandidates(list);
          if (!sid && list.length) {
            const q = list.find((s) => ["QUALIFIED", "PAPER", "SURVIVED"].includes(s.status)) || list[0];
            setSid(String(q.id));
          }
        }
      })
      .catch((e) => setErr(e.message));
  };

  useEffect(() => {
    loadCandidates();
  }, [runFilter]);

  useEffect(() => {
    if (!sid) return;
    setDetail(null);
    api.strategy(sid)
      .then((d) => setDetail(d))
      .catch((e) => setErr(e.message));
  }, [sid]);

  const handleGenerateMatrix = () => {
    if (!sid) return;
    setGenerating(true);
    api.generateMatrix(sid)
      .then((res) => {
        setGenerating(false);
        if (res.matrices) {
          setDetail((prev) => prev ? { ...prev, matrices: res.matrices } : prev);
        }
      })
      .catch((e) => {
        setGenerating(false);
        setErr(e.message);
      });
  };

  const mats = detail?.matrices;
  const detailBt = detail?.backtests?.find((b) => b.stage === "detail") || detail?.backtests?.[0];
  const m = detailBt?.metrics || {};
  const s = detail?.strategy;

  return (
    <div>
      <h2 className="page-title">Backtest Matrix (V3.6)</h2>
      <div className="page-sub">
        Authoritative multi-dimensional diagnostic matrix per strategy: TIMEFRAME, SESSION, DAY,
        REGIME, and DIRECTION. Verified against real persisted trade and backtest evaluations.
      </div>
      {err && <StructuredError error={err} onDismiss={() => setErr(null)} />}

      <div className="btn-row" style={{ flexWrap: "wrap", gap: 10 }}>
        <select
          value={runFilter}
          onChange={(e) => setRunFilter(e.target.value)}
          style={{ fontWeight: 600, borderColor: "#4f8ef7" }}
          title="Filter candidates by data source"
        >
          <option value="USER_RESEARCH">Current Research Run (default)</option>
          <option value="ALL">All Runs (including Historical)</option>
          <option value="LEGACY_TEST">Legacy / Test Data</option>
        </select>

        <select value={sid} onChange={(e) => setSid(e.target.value)} style={{ minWidth: 360 }}>
          {candidates.map((c) => (
            <option key={c.id} value={c.id}>
              Node_{c.id} {c.research_node_num ? `· Research Node #${c.research_node_num.toLocaleString()}` : ""} · {c.status} · {c.timeframe} · fit {fmt.num(c.fitness, 3)}
            </option>
          ))}
        </select>

        {s && (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="mono" style={{ fontWeight: 600, color: "#4f8ef7" }}>
              {s.research_node_num ? `Research Node #${s.research_node_num.toLocaleString()}` : `Node_${s.id}`}
            </span>
            <Pill status={s.status} />
            <span className="muted" style={{ fontSize: 12 }}>{s.symbol} {s.timeframe}</span>
          </div>
        )}
      </div>

      {!detail && <Spinner />}
      {detail && (
        <>
          <div className="grid cols-4" style={{ marginBottom: 14 }}>
            {[["Trades", m.trades], ["Net PnL", <SignedNum v={m.net_profit} />],
              ["PF", fmt.num(m.profit_factor, 2)], ["Max DD", fmt.pct(m.max_drawdown_pct)],
              ["Return", <SignedNum v={m.total_return_pct} pct />],
              ["Sharpe", fmt.num(m.sharpe, 2)], ["Win rate", fmt.pct(m.win_rate)],
              ["Spread cost", fmt.num(m.total_spread_cost)]].map(([l, v]) => (
              <div className="panel metric-card" key={l}>
                <div className="label">{l}</div><div className="value" style={{ fontSize: 16 }}>{v ?? "–"}</div>
              </div>
            ))}
          </div>

          {!mats && (
            <div className="panel" style={{ textAlign: "center", padding: "24px 16px" }}>
              <p className="muted" style={{ marginBottom: 12 }}>
                Diagnostic matrices for <strong>Node_{sid}</strong> have not been generated yet.
                Screening and detail metrics above are persisted from real backtest evaluations.
              </p>
              <button
                className="btn btn-primary"
                onClick={handleGenerateMatrix}
                disabled={generating}
              >
                {generating ? "Generating Diagnostic Matrix..." : `⚡ Generate & Persist Matrix for Node_${sid}`}
              </button>
            </div>
          )}

          {mats && (
            <div className="grid cols-2">
              <div>
                <MatrixTable title="TIMEFRAME" data={mats.timeframe}
                             valueKeys={["trades", "profit", "pf", "dd", "return_pct", "sharpe", "fitness", "error"]} />
                <MatrixTable title="SESSION" data={mats.session} />
                <MatrixTable title="DAY" data={mats.day} />
              </div>
              <div>
                <MatrixTable title="REGIME" data={mats.regime} />
                <MatrixTable title="DIRECTION" data={mats.direction} />
                {detail.validation && (
                  <div className="panel">
                    <h3>Validation summary</h3>
                    <div className="mono" style={{ fontSize: 12 }}>
                      robustness {fmt.num(detail.validation.robustness_score, 3)} ·
                      passed: {detail.validation.passed ? "YES" : "NO"} ·
                      OOS degradation {fmt.pct(detail.validation.oos?.degradation)} ·
                      WF positive folds {detail.validation.walkforward?.positive_folds}/{detail.validation.walkforward?.n_folds} ·
                      MC positive {fmt.pct(detail.validation.montecarlo?.return_positive_frac, 0)} ·
                      worst stress degradation {fmt.pct(detail.validation.spread_stress?.worst_degradation)}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
