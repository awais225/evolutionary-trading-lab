# Phase Roadmap

Legend: ✅ done & verified · 🟡 partial/foundation only · ⛔ not started

## Phase 1 — Foundation ✅ (HERO-V1.0.0, 2026-11-06)
Repo structure, storage layer (registry + per-instrument SQLite + Alembic),
FastAPI surface (/health, instruments, settings, events, nodes), durable event
log + WS LIVE FEED (replay, no polling), web shell with the new visual
identity (8 tabs, instrument switcher, collapsible feed), `doctor`, CI
workflow, tests, docs. No trading capability.

## Phase 2 — Strategy specification, validation & deterministic compiler ✅ (HERO-V1.1.0, 2026-11-06; completed 2026-11-07)
Typed strategy spec DSL (never executable code): JSON-typed entry/exit/risk/
execution models, layered validator (structural → semantic → instrument →
timeframe → indicator → risk → execution → safety) with structured issues and
VALID/WARNING/NEEDS_METADATA/INVALID severities, deterministic compiler to
declarative IR with SHA-256 fingerprint, immutable node versioning with
parent-linked lineage, Manual Nodes spec workspace UI.
COMPLETION (2026-11-07): optional `management` section (break-even, stepped/fixed trailing, indicator trailing as definition), prioritized `exit_rules` (legacy `explicit_exit` = implicit priority 0), schema 1.1.0 with byte-identical fingerprints for 1.0.0 specs, compiler 1.2.0 / IR 1.1.0, and the pure shared stop-management reference evaluator in `hero1_exec_semantics` (activation, never-backwards, tick rounding, idempotence; indicator trailing explicitly refuses until Phase 3). Builder gained Trade Management + exit-rule editors.
NOT included (by design):
natural-language → spec generation (Phase 7/WWW), backtesting (Phase 4),
execution (Phase 5), indicator math (Phase 3).

## Phase 3 — Market data & indicator engine ✅ (indicators landed with DeepTesting, 2026-11-07; market data = ISSUE-051..060 line)
MT5 history sync to MARKET_DATA (Parquet), candle store, indicator library with
unit-verified formulas, timeframe handling, synthetic-data test fixtures.
Indicator mathematics landed 2026-11-07 in `hero1_indicators` (causal series,
warmups as None) together with the DeepTesting engine below.

## Phase 4 — DeepTesting & backtesting ✅ (implemented as "Phase 3: DeepTesting" per owner brief, 2026-11-07)
DELIVERED: one pure engine (`hero1_exec_semantics.engine`, v3.0.0) executes Phase 2 compiled IR over canonical candles under mandatory hard constraints (initial capital, %/fixed risk on an explicit basis, MAX ACTIVE TRADES default 1 per node/scenario), bid/ask + spread/slippage/commission/swap assumptions recorded per run, conservative same-candle SL/TP ambiguity flagging, gap-overrun fills, Phase 2 stop management plus indicator trailing (completed here), prioritized exit rules, causal indicators (SMA/EMA/RSI/ATR/MACD/BBANDS/ROC/STOCH/ADX/VWAP). ISSUE-060 readiness gates every run (BLOCKED persists as FAILED, never tested); runs/trades/scenarios persist immutably with config + data-quality snapshots (migration 0004); analytics (drawdown on equity, streaks, sessions/time buckets, Sharpe/Sortino on daily returns), scenario generation with explosion cap, TP/SL offsets by re-execution, ranking, comparison, fine-tune suggestions creating FINE_TUNED children. Verified: hand-computed acceptance fixtures + real XAUUSD H1 2024 run (5,938 candles, 175 trades, entries independently cross-verified, no overlapping positions, ledger reconciles).
Single execution engine behind every mode: initial backtest, OOS, walk-forward,
robustness, Monte-Carlo, comparison matrices, trade-level inspector, exports.
Runs execute via the API today (synchronous, candle-capped); a worker queue
remains a future scaling step.

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
