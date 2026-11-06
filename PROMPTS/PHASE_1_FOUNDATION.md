# PROMPT — HERO1 TASK 2: PROJECT INITIALIZATION AND PHASE 1 FOUNDATION

> ⚠ ARCHIVAL NOTE: The verbatim original prompt for Task 2 was not preserved
> character-for-character. The text below is a faithful reconstruction from the
> condensed session memory, retaining the original constraints and acceptance
> criteria. It is marked as reconstruction — do NOT treat wording as verbatim.
> Outcome section at the end records what was actually done.

Authorized scope (reconstructed):

- Initialize Hero1 as a NEW private GitHub repo `hero1-trading-platform` ONLY IF
  secure auth available; initial project structure + foundational backend/frontend;
  install only needed deps; safe tests on synthetic temp data; commit; push to
  new repo's main ONLY IF secure auth available.
- NOT authorized: modify/rename/archive/delete/push old repo; modify/delete old
  DATA; import old prod DBs/datasets; MT5 order execution or live trading;
  claim GPU functionality without hardware verification; full AI research or
  fine-tuning; unrelated changes. Old repo stays untouched as legacy reference.

Stack (mandated): Python+FastAPI backend; React+TypeScript+Vite frontend;
SQLAlchemy 2.x; Alembic from day 0; SQLite (initial); Pydantic settings/schemas;
pytest backend; Vitest frontend. Modular; avoid giant files. Structure:
hero1/{apps/web, services/api, services/worker, packages/{strategy_spec,
exec_semantics, indicators, storage, eventbus, mt5_adapter, ai_orchestrator,
research}, tests/, scripts/, docs/, .github/, README.md, .gitignore, pyproject.toml}.

DATA protection: prod root D:\Hero1\DATA (Windows); env var HERO1_DATA_ROOT;
DATA never in repo; temp dirs in tests; safe dev default OUTSIDE source tree
(~/.hero1/data); validate configured path at startup; never silently fall back
inside repo; do not create/modify real Windows DATA dir. .env.example dummy
only; never commit .env. Instruments: XAUUSD, EURUSD, BTCUSD — distinct
data/storage namespaces.

Database foundation: global registry DB (Instrument, ApplicationSetting,
GlobalEvent) + independent SQLite per instrument (Node, NodeLineage,
BacktestRun, DeepTestScenario, DeepTestTrade, ResearchTask, ResearchFinding,
ResearchSource, LiveSession, LiveConfig, LiveTrade, Approval, ActivityEvent).
Origins: CUSTOM, WWW, INTERACTIVE, FINE_TUNED. Lifecycle + approval states;
live approval separate from testing qualification. SQLAlchemy models+
repositories; Alembic migrations; explicit instrument scoping on every
instrument query; FK enforcement; composite indexes; transaction-safe writes.
Do NOT build complete testing engine.

Backend: FastAPI with /health, /api/v1/instruments, /api/v1/settings,
/api/v1/events, /api/v1/nodes basic CRUD; instrument-aware validation;
Pydantic schemas; structured logging; central exception handling; DI;
OpenAPI. Instrument EXPLICIT for instrument-specific endpoints; invalid/missing
identifier rejected. `doctor` command reports: Python version, config, DATA
root validity, registry DB connectivity, per-instrument DB connectivity, Ollama
endpoint availability, MT5 availability status WITHOUT orders, GPU only if
genuinely detectable. Must distinguish unavailable vs unconfigured vs healthy;
never report simulated MT5 as real.

Events: durable records; WebSocket live delivery; replay on reconnect;
instrument/subsystem/severity/timestamp fields; filtering; no continuous
expensive DB polling. Categories: SYSTEM, RESEARCH, NODE, BACKTEST, DEEPTEST,
FINE_TUNING, MT5, LIVE_TRADING, ERROR, WARNING.

AI: provider-independent AIProvider interface; OllamaProvider; model config;
connection health check; structured JSON handling; JSON Schema validation;
timeout/error handling; token/context budget config. Use existing local Ollama
for small integration tests; do NOT download larger models; do NOT claim LMS
model represents RTX 4090 laptop performance; AI text never executes as Python.

Frontend: NEW visual identity (do NOT copy old dashboard CSS/layout); 8 tabs:
Manual Nodes, WWW Research, Interactive WWW, DeepTesting, Fine Tuning, Live
Testing, LOG, SETTINGS. Build: shell, sidebar, instrument selector, status
indicators, collapsible global LIVE FEED, basic event display, design tokens,
loading/empty/error states. Unimplemented tabs = clearly marked placeholders,
NO fake functionality/results. Modern, professional, workstation-grade design.

Testing (safe/synthetic only): backend startup; /health; instrument registry;
per-instrument DB creation; cross-instrument isolation (XAUUSD query must NOT
return EURUSD/BTCUSD rows); invalid instrument rejection; basic node
create/retrieve; node origin validation; event create/retrieve; WebSocket
delivery + replay; settings validation; Ollama success/failure where possible;
frontend production build; frontend navigation; no DATA paths in repo; no
committed credentials.

Docs: README.md, docs/ARCHITECTURE.md, docs/DATA_LAYOUT.md,
docs/DEVELOPMENT.md, docs/WINDOWS_SETUP.md, docs/PHASE_ROADMAP.md,
docs/SECURITY.md.

Completion protocol: verify new repo separate from old; strict .gitignore;
run tests; frontend build; review diff; check secrets/DATA; commit; push to
main IF secure auth available; report exact commit SHA, repo URL + branch,
tests + results, exact Windows run commands. If auth unavailable: preserve
local work, explain blocker, NEVER claim push succeeded. Then STOP; wait for
approval before Phase 2.

## OUTCOME (2026-11-06 · v0.1.0 · commit 840bf71d9d3adbb556be1bf19771b63d98ae3e57)
Completed as authorized, except push. Details: CHATGPT.txt §7–§14,
docs/ISSUES_AND_FIXES.md (ISSUE-001..008), CHANGELOG.md [v0.1.0].
Key results: 28 pytest passed · 6 vitest passed · vite build OK · uvicorn
smoke OK (HTTP + WS replay/live) · 28 files docs · repo initialized locally
(main, no remotes, tags v0.1.0 local). GitHub push BLOCKED — no secure auth
available (see PROMPTS/PHASE_1_1_GITHUB_HANDOFF.md and ISSUE-010).
