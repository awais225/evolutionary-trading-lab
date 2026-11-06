# Phase Roadmap

Legend: ✅ done & verified · 🟡 partial/foundation only · ⛔ not started

## Phase 1 — Foundation ✅ (this milestone)
Repo structure, storage layer (registry + per-instrument SQLite + Alembic),
FastAPI surface (/health, instruments, settings, events, nodes), durable event
log + WS LIVE FEED (replay, no polling), web shell with the new visual
identity (8 tabs, instrument switcher, collapsible feed), `doctor`, CI
workflow, 34 automated tests, docs. No trading capability.

## Phase 2 — Strategy compiler & node editing ⛔
Typed strategy spec (entry/exit/filters/session/risk), JSON-Schema validation,
natural-language → spec draft generation (AI-assisted, never executed), spec
hashing + lineage, manual editor UI. Deliverable: editable, validatable node specs.

## Phase 3 — Market data & indicator engine ⛔
MT5 history sync to MARKET_DATA (Parquet), candle store, indicator library with
unit-verified formulas, timeframe handling, synthetic-data test fixtures.

## Phase 4 — DeepTesting & backtesting ⛔
Single execution engine behind every mode: initial backtest, OOS, walk-forward,
robustness, Monte-Carlo, comparison matrices, trade-level inspector, exports.
`services/worker` hosts long runs; FE tabs for DEEPTEST/COMPARISON.

## Phase 5 — MT5 live testing (Windows) ⛔
Demo-first ordered flow: approved version + kill switch + account guard.
Approval ledger separate from testing qualification. Auto-INACTIVE on restart.
Redacted journals under LIVE_TESTING/. **Requires Windows + MT5 terminal.**

## Phase 6 — LOG & observability ⛔
Unified searchable log UI, server-side secret redaction, retention policy,
log export (.txt), correlation ids across API/worker/WS.

## Phase 7 — WWW Research & Interactive WWW ⛔
Budget-ledgered autonomous research (start/pause/resume/stop, checkpoints,
evidence quotes + source URLs), cross-instrument evidence store, bulk node
creation, conversational research agent with spec output.

## Phase 8 — Fine Tuning ⛔
Trade-history RAG over DeepTest results: losing-bucket analysis, session and
volatility breakdowns, child-node proposals with baseline+OOS gates, overfit
heuristics and warnings. Originals never overwritten.

## Phase 9 — Learning loop & hardening ⛔
Scheduled re-validation, drift detection on live vs expected PnL curves,
approval expiry/re-qualification, full audit exports, final security review.

## Sequencing rules (unchanged)
- MT5 **orders** only in Phase 5 and only after Phase 4 proves the engine.
- No phase ships fake UI: placeholder tabs stay explicit until implemented.
- Live approval is always a separate record from test qualification.
