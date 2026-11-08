# Architecture — implementation truth

**Status legend** (applies to every section):
- **Implemented** — code exists in this repo and is verified (tests/smoke).
- **Partially implemented** — usable but incomplete; gaps listed explicitly.
- **Planned** — designed/documented; no code or placeholder only.
- **Unverified** — code exists but has never run on the required environment.
- **Not supported** — intentionally absent or refused (by design).

Last synchronized: 2026-11-07 (HERO-V1.4.1 · ISSUE-037 launcher persistence). Keep in sync with source on every change.

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
│ packages/exec_semantics  PARTIAL (Phase 2 completion): pure stop-
│   management reference evaluator (shared DeepTesting/Live semantics);
│   indicators · research  (Phase 3+)                  │
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
  `src/styles/theme.css` (mission-control slate + signal amber) — the
  binding graphical-theme standard lives in **`docs/DESIGN_SYSTEM.md`**
  (tokens, tier rules, state matrix, shared components incl. the custom
  `InstrumentSelect` listbox). All 8 tabs follow it.
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
- IMPLEMENTED (HERO V1.4.2, TASK 12): `packages/mt5_adapter` with
  `connection.py` (terminal discovery, single session lock, account snapshot —
  missing fields stay `None`, never fabricated), `metadata.py` (normalized
  broker metadata snapshots), `sizing.py` (pip = 10·point on 3/5-digit feeds
  else point — from digits, never from the symbol name; tick rounding; volume
  normalize; stops-distance check; non-finite/zero-step inputs REFUSED).
  Snapshots are stored per broker/server+symbol at
  `DATA/MARKET_METADATA/<server>/<SYMBOL>.json` with a manifest + SHA-256;
  two servers' snapshots for one symbol never overwrite each other.
- Validation consumes snapshots: `validate`/`compile` return `NEEDS_METADATA`
  until a snapshot exists for the exact broker/server/symbol, and are never
  forced to VALID. Startup auto-connection (ISSUE-056): a non-blocking
  worker connects only when the MT5 API can verify exactly one unambiguous
  running terminal/account (a saved user-chosen terminal wins; multiple
  running terminals stay a manual dropdown choice; nothing is launched
  automatically); a deduplicated background refresh then retrieves broker
  metadata for the actually-connected account (snapshots stale-labelled
  across account changes). The TopBar MT5 terminal/account selection is recorded for
  **Live Testing only** and never affects instruments, nodes, fingerprints
  or DATA. The spec's `identity.direction` participates in the strategy
  fingerprint; inherently directional examples carry `LONG`/`SHORT` (never
  stamped `BOTH`); `BOTH` is the default only for new blank strategies.
- Fetch conflict audit (ISSUE-054): conflicting overlaps are persisted
  in the additive `fetch_conflicts` table (stored + incoming values,
  provenances, field diffs, resolution state) and resolved only via
  explicit per-record decisions; stored candles are never overwritten
  automatically. Node deletion is dependency-guarded in the storage
  repository (descendants opt-in; dependent testing/live records
  block), and Duplicate & Edit creates an independent node with
  parent lineage via `POST /nodes/{id}/duplicate`.
- Data-quality semantics (ISSUE-055): availability is computed per
  timeframe with DB-derived gap classification (weekend closure /
  session break / unverified) and completed-bar freshness
  (ISSUE-056: expected-latest-candle under a measured session model -
  weekend closure, Friday-evening close, Sunday 23:00 UTC reopen,
  Mon-Thu nightly break - never a raw clock comparison);
  post-commit read-back verification of every fetch append
  (ISSUE-056 POST_COMMIT_VERIFICATION stage);
  `data_quality()` exposes a READY / READY_WITH_WARNINGS / BLOCKED (range-scoped, ISSUE-060) report for
  backtesting readiness. Table sorting is standardized on
  `apps/web/src/lib/tableSort.ts` with per-table persisted state.
- Fetch write path (ISSUE-053): stored timestamps are read once per
  timeframe before the transaction and new candles are inserted with a
  single `executemany` batch, keeping write cost linear in the incoming
  batch; store connections use WAL + `busy_timeout=30000` +
  `synchronous=NORMAL`. Per-stage DB elapsed timings go to the operation log.
- Market data: `scripts/import_market_data.py` imports MT History-Center CSV
  exports into `DATA/MARKET_DATA/market_data.db` + `import_manifest.json`
  (all-or-nothing, duplicates collapsed-or-fatal, gaps reported never filled,
  naive broker-server timestamps). 419,013 XAUUSD candles imported and
  verified on Linux.
- IMPLEMENTED (HERO V1.5.0, TASK 13; awaiting Windows acceptance): the
  adapter now declares the Windows-only MetaTrader5 dependency; connection is
  a lifecycle (DISCOVERED/RUNNING/INITIALIZING/CONNECTED/ACCOUNT_LOADED/
  FAILED/UNAVAILABLE) with terminal launch and bounded initialize wait. A
  shared `packages/market_data` store backs both CSV import and incremental
  MT5 FETCH DATA (`copy_rates_range` with timezone-aware UTC datetimes;
  completed candles only; conflicts never overwritten). Stored timestamps
  remain naive broker-server epochs; `server_time.py` bridges to UTC using
  the legacy-verified IC Markets GMT+2/GMT+3 US-DST rule. Manual Nodes shows
  historical coverage separately from broker metadata.
- UNVERIFIED: everything against a real terminal on Windows; MetaTrader5 is
  not installable on Linux, where every MT5 path verifiably answers
  `MT5_UNAVAILABLE` instead of simulating data. Mocked MT5 tests exercise the
  lifecycle/fetch logic but are not real-terminal evidence.
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

## Launcher architecture — IMPLEMENTED & LINUX-VERIFIED (Windows UNPROVEN)

2026-11-07 (ISSUE-037): the monolithic launcher was split by design:
- `START.bat` is a thin PERSISTENT OUTER shell; it prints its own absolute
  path, size, modified time, certutil SHA-256 and git HEAD (all guarded), then
  executes `scripts\START_INTERNAL.bat` inside `cmd /k call`. The cmd /k
  session outlives anything the inner procedure does — a missing label, syntax
  error, Python exception, native crash, database error or unexpected
  termination can at worst leave cmd /k at a prompt; the window cannot close
  itself. After the inner procedure returns (however), the outer prints a
  final status block and pauses. No relaunch loops.
- `scripts\START_INTERNAL.bat` is the startup procedure (10 uniform stages;
  banner + command + cwd + resolved paths + captured rc (string compare,
  signed-native-code safe) + PASS/FAIL; console and LOGS\startup.log carry the
  same lines via the :LOG subroutine; every failure routes to ONE :STAGE_FAILED
  handler which prints stage/reason/rc/reproduce-command and the last 30 lines
  of startup.log, then pauses; the monitor loop routes to the same handler if
  the backend drops).
- `START_DIAGNOSTIC.bat` is a standalone read-only diagnostic (7 sections;
  PRAGMA integrity_check via `file:...?mode=ro`; writes LOGS\diagnostic.log;
  always pauses; never writes to any database).
scripts/audit_launcher.py + tests/test_launcher_bat.py enforce ASCII/CRLF/
label-resolution/no-plain-exit/pause-on-failure/no-powershell-pipes plus the
three-file architecture contract (outer cmd /k call + certutil + pause;
internal 10 stage markers + :STAGE_FAILED(pause); diagnostic pause + PRAGMA +
exit /b 0) in CI.

Exit-code contract (2026-11-06, ISSUE-036): every external call captures rc into
RC and is compared as a STRING against "0" (native-crash negative codes can no
longer read as success); rc is echoed per call; on any failure the launcher
prints command + exit code + last 30 lines of LOGS\startup.log and pauses.
The database check prints its full read-only report to the console (tee).

Adapted from the legacy v10.4-throughput launcher (read-only reference; see
docs/PIPELINE.md §1 for inspected files/commits):
- Stages: root detect → venv → import check (`sys.path.insert services/api`) →
  DB check (`init_db.py --check`) → frontend deps (node_modules; no auto-install)
  → port ownership (`scripts/check_port.py`: 0 free / 10 verified-Hero1 /
  20 foreign — foreign is NEVER reused or killed) → persistent windows
  (`cmd /k` + visible CMD) → real HTTP readiness (`scripts/wait_ready.py`)
  → instance identity (`scripts/verify_instance.py`: `/health` schema +
  `project_root` must match this repo; fresh launches additionally require the
  per-run `HERO1_START_TOKEN`) → frontend proxy verification (page 200 AND
  `/health` via vite proxy = real backend link) → browser → foreground monitor
  loop. Every failure path prints diagnostics and pauses — nothing exits
  silently.
- Backend serves ONLY the API (:8788). The dashboard is the vite dev server
  (:5173, `strictPort`) — there is no production-bundle serving path in the
  normal launcher, so a stale dist can never be served silently; a stale dist
  is reported as advisory only.
- `/health` additionally echoes `start_token`/`project_root` when the launcher
  sets them (localhost-only, no secrets).
- The legacy single-port model (backend serving dist on 8787) was NOT copied:
  Hero1's architecture is two services by design; everything else transfers.
- Windows: files written for cmd.exe with CRLF + `enabledelayedexpansion`;
  label/goto/paren/quote integrity verified statically; real Windows run
  PENDING (see docs/ISSUES_AND_FIXES.md ISSUE-037).

## Starred (favorite) nodes — IMPLEMENTED & TESTED (pytest + vitest)
- Additive `nodes.starred` column (instrument DBs; migration `0002_nodes_starred`,
  idempotent; existing rows default 0; no data rewritten).
- Research metadata ONLY: excluded from `spec_hash`, versions, validation,
  compilation, lineage. Toggling never forks a version or changes semantics.
- `GET /api/v1/instruments/{id}/nodes?starred=true|false` — server-enforced
  scope; response carries `total` (unfiltered, stable contract),
  `starred_total`, `filtered_total`. A frontend-only filter is NOT a boundary:
  batch consumers must use the query param.
- UI: star toggle per registry row (SVG, theme tokens, optimistic flip with
  rollback), shared All Nodes / Starred Only scope (`StarScopeProvider`,
  localStorage `hero1.nodeScope`) wired in Manual Nodes, DeepTesting,
  Fine Tuning and Live Testing; selection (where selection exists) is limited
  to the filtered set; counts always shown ("Showing N starred nodes out of M").

## Trading research pipeline — AUDITED (docs/PIPELINE.md)
Legacy stage order recovered and mapped onto Hero1 (spec → validation →
deterministic compile → instrument/timeframe resolve → market-data integrity
→ indicators (Phase 3 skeleton) → screening → backtest → OOS/robustness →
qualification → paper → MT5). Only stages 1–4 + spec validation are
implemented; executors for 6–12 are future phases and are NOT claimed. See
docs/PIPELINE.md for the full matrix, boundaries and risks.

## Multi-timeframe strategy nodes — IMPLEMENTED & TESTED (pytest + vitest) — HERO V1.4.0

Timeframe is a property of the NODE, never a global app setting. The global
instrument dropdown (TopBar) is unchanged; there is deliberately NO global
timeframe control anywhere. State lives in:

- per-node `timeframe` column (instrument DBs; migration `0003_timeframe_unique_name`,
  additive + idempotent; backfilled ONLY from stored spec_json identity — never
  guessed; drafts keep NULL),
- per-node fingerprint: `sha256(semantic_canonical(spec))` excludes presentation
  fields (identity.name / identity.description) and INCLUDES identity.timeframe +
  identity.instrument (compiler 1.1.0),
- per-instrument sequential C-names (`^C(\d+)$`, first-available gap reuse),
  raced-guarded by the additive UNIQUE index `uq_nodes_instr_name`
  (`(instrument_id, name)`).

### Save flow (one strategy → N nodes)
`POST /api/v1/instruments/{instrument}/nodes/generate` — body: `spec` + `timeframes[]`
(supported set only, no duplicates, else 422 TIMEFRAME_UNSUPPORTED/TIMEFRAME_DUPLICATE)
+ optional `name` (honoured ONLY for single-timeframe saves) + optional `description`.
The server, in ONE all-or-nothing transaction per instrument database:
1. re-validates the spec (authoritative), recomputes the canonical fingerprint
   for the REFERENCE timeframe,
2. for each requested timeframe, clones the spec with identity.timeframe re-stamped,
   re-fingerprints, creates an independent node row (own id, own version 1,
   lineage root = itself, parent NULL for roots),
3. allocates sequential first-available C-names (server-side; unique index makes
   concurrent allocation safe — one retry on conflict, else 409),
4. returns `nodes[]` (created, in requested-timeframe order) — the client never
   sends N specs and never allocates names for multi-timeframe saves.

### Fingerprint migration (one-off, 2026-11-06)
The fingerprint definition changed (compiler 1.0.0 → 1.1.0) so previously stored
spec_hashes no longer match deterministic recomputation. `hero1_storage.fingerprint_migrate`
/ `scripts/migrate_fingerprints.py` recompute from stored spec_json: dry-run by default,
collision-refuses (two nodes with same (hash, version) → NOTHING written, both listed),
backup-then-apply per DB, idempotent. Preview DATA root: 1 draft node, no spec →
nothing to rewrite (verified by dry-run). Live databases with real specs MUST run the
dry-run before the next deploy; see docs/DATA_LAYOUT.md § backups.

### Frontend
`TimeframeMultiSelect` (themed listbox multi-select; registry-restricted values;
Select All / Clear All / counts / readable summary / no-timeframe validation; full
keyboard + ARIA contract) in the node strategy creation flow. Default = registry
default (`instruments.default_timeframe`, XAUUSD=M15) else M15. Multi-TF saves POST
`/nodes/generate` (names allocated server-side; UI labels the button `Generate N
nodes`); single-TF saves honour the explicit name input. Display name is
INDEPENDENT of id and fingerprint: renames are metadata-only PATCH (Enter/Escape,
trim/≤160/dup-per-instrument) and never fork versions or change hashes.

Registry: TF badge column, timeframe filter chips (server `?timeframe=` filter,
per-TF counts from `timeframe_counts`), "Group by timeframe" view toggle, and a
`TIMEFRAME UNASSIGNED` treatment for spec-less drafts. Shared persisted scope
(`TimeframeScopeProvider`, localStorage `hero1.timeframeScope`) drives Manual Nodes
AND the batch-scope panels of DeepTesting / Fine Tuning / Live Testing — Select All
always applies to the currently visible (filtered) set only.

### Honesty gates
Update (2026-11-07, ISSUE-049..051): a canonical market-data store DOES now
exist (`DATA/MARKET_DATA/market_data.db`, CSV baseline + MT5 incremental
fetch; see docs/DATA_LAYOUT.md). The DATA navigation tab hosts the
historical market-data panel (moved out of Manual Nodes) above the
generated-nodes dashboard (per-node timeframe, saved-spec direction badges,
parent/child lineage). Fetch conflicts are classified
(exact/representation/genuine) and isolated — stored candles are never
overwritten, valid new candles still append, and operations are traced by
operation id through the persistent registry event store surfaced in the
LOG tab. Update (ISSUE-052): fetches execute as background operations with
queryable state (start/status/latest endpoints, duplicate-start
protection, per-timeframe MT5 timeout), lifecycle stage events carry full
stack traces on failure, and the LOG tab paginates server-side with
COPY ALL / SAVE TO TXT full-dataset export. The stale Phase-4 note below
predates this:

No market-data store exists; saved multi-timeframe nodes are DRAFT/PENDING and render
`AWAITING MARKET DATA · DATA UNAVAILABLE — Phase 4` in the inspector. No backtest,
validation, qualification or live-readiness claim is made anywhere in this flow.
W1/MN1 are not in the supported registry and are deliberately not selectable.

### Verification
- pytest 103 passed / 1 skipped (tests/test_multitimeframe.py: single/3/7-TF saves,
  gap C-number reuse, per-instrument isolation, hash-vs-rename invariance, list
  422s, timeframe_counts, migration tool incl. collision refusal)
- vitest 40/40 (src/__tests__/multitimeframe.test.tsx: generate fan-out contract,
  no-TF validation gate, server-side chip filter, group-by, inline rename, keyboard)
- Live preview (BACKEND :8788 + vite :5173): list contract incl. timeframe_counts,
  422 TIMEFRAME_UNSUPPORTED / TIMEFRAME_DUPLICATE, /health identity echo; migration
  applied to preview DATA (alembic 0002→0003 ×3 instrument DBs; fingerprint dry-run
  = no-op); backups in BACKUPS/pre-0003-2026-11-06/.
- Windows .bat files remain UNTESTED on Windows (no Windows in this environment).
- REPAIR (2026-11-07, fbd4a45973bc86402919b38853e80bf8095e8261): the canonical candle timestamp is now
  UTC epoch seconds end-to-end (CSV server wall AND MT5 rate time are each
  converted from broker server epoch to UTC exactly once at ingestion via
  hero1_mt5_adapter.server_time - GMT+2/+3 on America/New_York DST dates).
  Before ISSUE-059 the fetch path stored MT5 server epochs verbatim, which
  shifted every fetched candle +2-3h and manufactured the 15 seam
  conflicts (proven byte-identical 3h earlier in stored history);
  scripts/repair_mt5_timeshift.py re-labels already-shifted mt5-fetch
  candles explicitly (backup first, stored candles never overwritten). FETCH DATA requires a stored baseline, overlaps the latest
  candle by two bars for dedupe, and availability is a per-timeframe registry
  (status/source/updates/gaps) read from the canonical DB + manifest. Real
  Windows MT5 fetch remains unverified; mocked tests are not terminal proof.
