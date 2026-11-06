# Architecture — implementation truth

**Status legend** (applies to every section):
- **Implemented** — code exists in this repo and is verified (tests/smoke).
- **Partially implemented** — usable but incomplete; gaps listed explicitly.
- **Planned** — designed/documented; no code or placeholder only.
- **Unverified** — code exists but has never run on the required environment.
- **Not supported** — intentionally absent or refused (by design).

Last synchronized: 2026-11-06 (HERO-V1.1.0). Keep in sync with source on every change.

## Layers

```
┌──────────────────────────────────────────────────────────────┐
│ apps/web  React 18 · TypeScript · Vite · react-router-dom     │
│  IMPLEMENTED: shell, sidebar, topbar, instrument context,      │
│  LIVE FEED (WS), Manual Nodes spec workspace, Settings page     │
└───────────────┬──────────────────────────────────────────────┘
                │  HTTP /api/v1/*, /health, WS /ws/v1/events
┌───────────────▼──────────────────────────────────────────────┐
│ services/api  FastAPI · Pydantic v2 · structured JSON logs     │
│  IMPLEMENTED: routers health/instruments/settings/events/nodes │
│  nodes: spec validate/compile/version endpoints (Phase 2)      │
│  CENTRAL: 500{error:{code,message}} · KeyError→404 · DI        │
└───────────────┬──────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────┐
│ packages/storage  SQLAlchemy 2.x · Alembic · SQLite            │
│  IMPLEMENTED: registry + per-instrument DBs, FK, indexes,      │
│  RLock engine, inside-repo DATA refusal, migration parity      │
├───────────────────────────────────────────────────────────────┤
│ packages/eventbus  durable events · in-memory fanout            │
│  IMPLEMENTED: publish→persist→call_soon_threadsafe fanout;     │
│  since_id replay over WS; drop-oldest on full consumer queue    │
├───────────────────────────────────────────────────────────────┤
│ packages/strategy_spec  IMPLEMENTED (Phase 2): typed DSL,      │
│  layered validator, deterministic compiler → IR + SHA-256       │
│ packages/exec_semantics · indicators · research                  │
│  Planned skeletons (Phase 3+) — no math, no execution          │
│ packages/ai_orchestrator  AIProvider interface                 │
│  PARTIALLY IMPLEMENTED: interface + OllamaProvider + JSON probe │
│ packages/mt5_adapter      availability probe                    │
│  PARTIALLY IMPLEMENTED / UNVERIFIED: import-level probe only    │
│  NOT SUPPORTED: order placement of any kind (until Phase 5)     │
└───────────────────────────────────────────────────────────────┘
```

## Instrument isolation model — IMPLEMENTED & VERIFIED
- Global **registry** DB (`DATABASE/hero1_registry.db`): instruments,
  application_settings, global_events (+alembic_version). No instrument tables.
- Per-instrument DB (`<INSTRUMENT>/DATABASE/hero1.db`): nodes, node_lineage,
  backtest_runs, deeptest_scenarios, deeptest_trades, research_tasks/findings/
  sources, live_sessions, live_configs, live_trades, approvals, activity_events
  (+alembic_version).
- Every instrument-scoped repository call and route takes `instrument=`
  explicitly; unknown symbol → 404/422 before any file opened; no defaults.
- Cross-test proof: `tests/test_nodes.py::test_cross_instrument_isolation`.
  IDs repeat across DBs BY DESIGN — never assert isolation on bare ids
  (ISSUE-004).

## Events — IMPLEMENTED & VERIFIED
- Design: durable first, live second. `publish_event()` inserts into
  `global_events` (mirrors to `activity_events` for instrument-scoped events),
  then fans out via `loop.call_soon_threadsafe` to WS subscribers — safe from
  sync endpoints in the threadpool (ISSUE-003 fix).
- Replay: `GET /ws/v1/events?since_id=N` → `{type:'replay', events:[…≤200]}`
  then `{type:'event'}` frames. Filters: `instrument`, `category`, `severity`.
  Client reconnects with backoff and resends last since_id — nothing polls.

## API surface (through Phase 2) — IMPLEMENTED & VERIFIED (pytest + live smoke)
| Method | Path | Status |
|---|---|---|
| GET | /health | IMPLEMENTED (5s registry cache; 30s Ollama/MT5/GPU caches) |
| GET | /api/v1/instruments · /instruments/{symbol} | IMPLEMENTED |
| GET | /api/v1/settings | IMPLEMENTED (defaults merge) |
| PUT | /api/v1/settings | IMPLEMENTED (known-key whitelist + range validators) |
| GET/POST | /api/v1/events · /events/categories | IMPLEMENTED |
| GET/POST | /api/v1/instruments/{s}/nodes · /nodes/{id} (GET/PATCH) | IMPLEMENTED (Phase 2: PATCH = metadata only; spec_json rejected 422) |
| POST | /api/v1/instruments/{s}/nodes/validate | IMPLEMENTED — validate WITHOUT persisting (server authority) |
| POST | /api/v1/instruments/{s}/nodes/compile | IMPLEMENTED — validate + compile → IR; 422 SPEC_INVALID/SPEC_NEEDS_METADATA |
| POST | /api/v1/instruments/{s}/nodes/{id}/versions | IMPLEMENTED — immutable new version (parent untouched) |
| GET | /api/v1/instruments/{s}/nodes/{id}/lineage | IMPLEMENTED — versioned lineage component (oldest→newest) |
| WS | /ws/v1/events | IMPLEMENTED (replay+live, 25s heartbeat) |

## Error model — IMPLEMENTED
Domain errors → `{"detail": "…"}`; unhandled → 500 `{"error":{"code":"internal_error","message":…}}`; stack traces never leak to clients.

## Spec pipeline (Phase 2) — IMPLEMENTED & VERIFIED (pytest + live uvicorn smoke)
- **DSL as data, never code.** `packages/strategy_spec` (Pydantic v2 strict,
  SCHEMA_VERSION 1.0.0): typed expression trees (AND/OR/NOT over typed
  comparisons on candle/indicator/constant operands; discriminated unions —
  validation is linear, deep trees cannot backtrack-explode), 10 indicators
  (metadata + param constraints only — NO math), explicit exits
  (SL/TP/explicit/max-holding/time-exit; **never silently invented**), risk with
  explicit distance units (absolute/points/ticks/pips/ATR-multiples — pip/tick
  sizes come ONLY from external broker metadata; absent ⇒ NEEDS_METADATA,
  never guessed), execution config as declarative intent
  (entry timing, completed-vs-forming candle) with lookahead-safe candle
  indexing (`offset=0` = latest completed candle; cross ops compare current
  vs previous; forming candles forbidden unless explicitly permitted).
- **Validator** (`validate_spec`): layered — structural → identity/timeframe →
  expression tree → indicators → risk → execution → safety (size/depth caps,
  no strings-eval, no code). Severities **VALID / WARNING / NEEDS_METADATA /
  INVALID**; structured issues `{code, path, message, severity, remediation}`.
- **Compiler** (`compile_spec`): canonical JSON (sorted keys, no whitespace,
  NaN refused) → deterministic IR (IR_VERSION 1.0.0); SHA-256 fingerprint of the
  canonical bytes; insertion-order independent; records schema+compiler
  versions; refuses INVALID/NEEDS_METADATA (WARNING compiles). NO eval/exec/
  dynamic imports anywhere; IR is declarative input for Phase 3/4 — NOT an
  execution engine.
- **Server is the sole fingerprint authority.** `spec_json` is stored canonical;
  client-supplied hashes are format-checked but ignored (compat shim only).
- **Node versioning (immutability):** semantic spec change ⇒ validate → compile
  → NEW node version linked to its parent (`parent_node_id`, `node_lineage`
  row); parent row never modified; `next_version = max(component)+1` within the
  lineage component (instrument-scoped; cross-instrument parents rejected 422).
  PATCH rejects `spec_json` (extra=forbid). `NodeOut` now exposes `spec_json`.
- **Duplicate identical (spec_hash, version)** ⇒ 409 SPEC_DUPLICATE (DB unique
  constraint mapped honestly, not a 500).
- Events: validate/compile/create/version/validation-failure publish NODE
  events (server-side; no DB polling; consumers keep WS replay semantics).

## Frontend shell — IMPLEMENTED (verified via vitest + build; browser E2E NOT DONE)
- `InstrumentProvider` (context + localStorage) drives top-bar switcher + all
  instrument-scoped API calls; `FeedProvider` controls collapsible LIVE FEED.
- **Manual Nodes → spec workspace (Phase 2):** timeframe select, load bullish
  engulfing example, JSON editor, per-instrument broker metadata inputs (never
  assumed; blank ⇒ server NEEDS_METADATA), Validate/Compile/Save/Save-as-new-
  version, structured issue panel (severity+code+path+remediation), IR +
  fingerprint display, registry rows with version/hash, lineage viewer.
- Build: `tsc --noEmit && vite build` (ISSUE-007/008). Design tokens in
  `src/styles/theme.css` (mission-control slate + signal amber).
- Tabs: Manual Nodes + SETTINGS implemented; WWW Research / Interactive WWW /
  DeepTesting / Fine Tuning / Live Testing / LOG = explicit Placeholder
  components (PLANNED features, NOT AVAILABLE) — no fake data by design.

## AI — PARTIALLY IMPLEMENTED / UNVERIFIED at production scale
- AIProvider ABC + OllamaProvider (health check, structured JSON probe,
  schema validation helpers, timeout/budget config). IMPLEMENTED & smoke-tested
  with qwen2.5:0.5b on the LMS host (PASS).
- NOT VERIFIED: configured model qwen2.5-coder:14b-instruct-q5_K_M on RTX 4090
  (not pulled, not benchmarked). No performance claims may be made.
- NOT SUPPORTED: executing AI output as code; AI granting approvals.

## MT5 — PARTIALLY IMPLEMENTED / UNVERIFIED (Windows)
- IMPLEMENTED: read-only availability probe (package import, platform check);
  `doctor --mt5` deep probe code path (import + terminal init + account READ).
- UNVERIFIED: everything on Windows; MetaTrader5 not installable on Linux.
- NOT SUPPORTED (by design): any order placement, modification, cancellation;
  live execution; approval issuance. Phase 5 only, after Phase 4.

## Testing infrastructure — IMPLEMENTED
- Backend pytest (**86 passed, 1 skipped** at HERO-V1.1.0) incl. synthetic-data
  spec/validation/compiler/API suites + alembic parity + repo hygiene.
- Frontend vitest (**24 passed**) with mocked fetch + stub WS; jsdom.
- CI (.github/workflows/ci.yml): pytest; npm build+vitest on ubuntu-latest.
- NOT DONE: browser E2E (Playwright), load testing, Windows CI.

## DATA layout — IMPLEMENTED (see docs/DATA_LAYOUT.md)
HERO1_DATA_ROOT (canonical default <repo>/DATA, git-ignored; any other
in-checkout path refused); the required subdirectories are created by
scripts/init_db.py --create (idempotent) and PREREQUISITES.bat.

## Deviations from the original brief (documented per spec)
1. Backend port default is **8788** (not 8787) to avoid legacy-service clash.
2. Alembic lives **inside** packages/storage (single installable unit); env
   files are imported programmatically (no alembic.ini).
3. Event categories include **WARNING** as first-class (matches severity axis).
4. Tags are local-only until GitHub publishing is unblocked (push requires
   secure auth — see docs/ISSUES_AND_FIXES.md ISSUE-010).

## Permanent commit/push protocol (condensed — full rule in CHATGPT.txt §17–19)
Before every commit: update CHATGPT.txt + CHANGELOG.md (+ ISSUES_AND_FIXES.md
if relevant) + archive prompt in PROMPTS/; run tests; inspect staged diff;
verify no DATA/secrets staged. Docs ship in the SAME commit as the change.
After every push: record SHA/branch/version in handoff docs; verify remote with
git ls-remote/GitHub; if SHA-recording needs another commit, push that too.

## Repository identity (2026-11-06, HERO V1)
- Active project: `https://github.com/awais225/evolutionary-trading-lab`
  (PUBLIC — explicit owner decision 2026-11-06). `main` = HERO V1, tag HERO-V1.0.0.
- Legacy Evolutionary Trading Lab preserved on branch `legacy-archive` @
  d96ec4baae56054729b3dd961aea9b7f0b067114 (verified remotely 2026-11-06);
  legacy branches v10.2-dashboard-manual-testing / v10.3-speed-diagnostics /
  v10.4-throughput and legacy tags (archive-v5.4-line-d9ea8e7,
  V5.3.1-V6-BASELINE) untouched. No legacy DATA or LFS objects deleted.
- Runtime architecture is unchanged: local-first FastAPI + Vite + SQLite.
