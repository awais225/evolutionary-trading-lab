# Changelog — Hero1
All notable stages of this project are recorded here. Rule (permanent):
every release lists version, date, phase, features, fixes, tests, limitations,
exact commit SHA and tag. Claims only for what was implemented AND verified.

---

## [Unreleased] — 2026-11-07 — HERO V1.4.1 — launcher persistence (ISSUE-037)

**Added**
- `START_DIAGNOSTIC.bat` (repo root): standalone, read-only startup diagnostic.
  7 sections (venv + python version; required files; backend import;
  `init_db.py --check`; SQLite PRAGMA integrity_check via `file:...?mode=ro`;
  port ownership 8788/5173; environment). Prints its own path/root/data dir,
  tees ALL output to `<DATA_ROOT>\LOGS\diagnostic.log`, and ALWAYS keeps the
  window open (success or failure). Never creates, modifies, migrates or
  deletes any database.
- `scripts/START_INTERNAL.bat`: the startup procedure, rebuilt as 10 uniform
  stages ([STAGE n/10] banner, echoed command, cwd, resolved paths, captured
  exit code compared as a STRING — signed native crash codes cannot read as
  success —, PASS/FAIL). Console and `DATA\LOGS\startup.log` receive the same
  lines. Every external call captures its rc immediately; every failure routes
  to a single `:STAGE_FAILED` handler (failed stage, reason, rc, reproduce
  command, working dir, last 30 lines of startup.log) then pauses. The
  post-launch backend monitor routes to the same handler.

**Changed**
- `START.bat` rewritten from a monolith into a thin PERSISTENT OUTER launcher:
  prints its absolute path, size, modified time, certutil SHA-256 and git HEAD;
  runs the internal procedure inside `cmd /k call` so NOTHING the inner
  procedure does (missing label, syntax error, Python exception, native crash,
  DB error, missing dependency, unexpected termination) can close the window;
  after the inner procedure ends (however), the outer prints a final status
  block and pauses. No relaunch loops; no behavior change to strategy, schema,
  or UI.
- `scripts/audit_launcher.py` + `tests/test_launcher_bat.py` extended to the
  three-file architecture (outer must use `cmd /k call` + certutil + pause;
  internal must carry all 10 stage markers + :STAGE_FAILED(pause); diagnostic
  must keep pause + PRAGMA + `exit /b 0`; ASCII/CRLF/label checks retained for
  all 6 launchers).
- Docs: ISSUE-037 (register), WINDOWS_SETUP §0.1, ARCHITECTURE launcher
  contract, CHATGPT.txt TASK 11, IMPLEMENTATION_PROGRESS G6.

**Fixed**
- Launcher vanish class: inner failure could previously take the whole console
  with it; now the outer `cmd /k` session survives every internal outcome, and
  the outer always ends at a `pause`.

**Tests**
- pytest 110 passed / 1 skipped; audit PASS on all six launchers; ASCII/CRLF
  verified; PRAGMA one-liner executed OK against the live read-only preview
  DATA root (registry + all instrument DBs -> integrity_check ok).

**Known limitations**
- NO Windows execution was performed (no Windows/Wine in this environment).
  Double-click behavior on the owner's machine remains UNVERIFIED until the
  owner runs it; acceptance is delivered as design + static proof only.
  Exact owner commands are in docs/WINDOWS_SETUP.md §0.1 and the handoff.

---

## [Unreleased] — 2026-11-06 — Hero1 multi-timeframe strategy nodes (HERO V1.4.0)

**Added**
- Fingerprint definition 1.0.0 → 1.1.0: `identity.name`/`identity.description` are
  presentation and EXCLUDED from the semantic fingerprint; `identity.timeframe` +
  `identity.instrument` remain IN it (`semantic_canonical` / `semantic_fingerprint`).
- `nodes.timeframe` (instrument DBs; migration `0003_timeframe_unique_name`, additive,
  idempotent, spec-backfilled only — never guessed; drafts keep NULL) + unique index
  `uq_nodes_instr_name (instrument_id, name)` + index `ix_nodes_instr_timeframe`.
  Upgrade REFUSES on pre-existing duplicate names (lists them; nothing renamed).
- `POST /api/v1/instruments/{id}/nodes/generate` — one validated strategy → N
  independent node rows (one per selected timeframe), all-or-nothing, server-side
  sequential first-available C-names with gap reuse; explicit name only for
  single-timeframe saves; race-safe via the unique index (one retry, else 409).
- `GET .../nodes?timeframe=M1,M15` (unknown → 422 TIMEFRAME_UNSUPPORTED; duplicates →
  422 TIMEFRAME_DUPLICATE); list response gains `timeframe_counts` (starred-scope
  aware) + `unassigned_timeframe_total`.
- Fingerprint migration: `hero1_storage.fingerprint_migrate` +
  `scripts/migrate_fingerprints.py` (dry-run default, collision-refuses,
  backup-then-apply, idempotent).
- Frontend: `TimeframeMultiSelect` (themed multi-select listbox; supported-registry
  values only; Select All / Clear All / counts / readable summary / no-timeframe
  validation; full keyboard + ARIA contract) — replaces the single-timeframe
  selector in the node strategy creation flow; default = registry default else M15.
  TF badge column, per-TF timeframe filter chips (server-side filter) with per-TF
  counts, "Group by timeframe" registry view, `TIMEFRAME UNASSIGNED` draft treatment,
  inspector TF badge + `AWAITING MARKET DATA · Phase 4` honesty line (no market-data
  store exists). Inline rename of saved nodes (click name → Enter/Escape) — renames
  are metadata-only PATCH and never touch spec_hash/versions/lineage.
- Shared persisted timeframe scope (`TimeframeScopeProvider`,
  localStorage `hero1.timeframeScope`) across Manual Nodes, DeepTesting, Fine Tuning
  and Live Testing batch-scope panels.
- vitest: `src/__tests__/multitimeframe.test.tsx` (10 tests) + mock `/nodes/generate`.
  pytest: `tests/test_multitimeframe.py` (12 tests). Test counts:
  pytest 103 passed / 1 skipped · vitest 40/40 · tsc clean · vite build green.

**Changed**
- Manual Nodes save button: `Generate N nodes` in multi-TF mode (names allocated
  server-side, sequential; single-TF keeps the explicit name input).
- Select-All in the registry applies only to the currently visible (filtered) set.
- START.bat stage-4 hardening (owner report: window vanished after "Verifying
  database integrity (read-only check)..."): captured-RC string-compare for EVERY
  external call (negative native-crash codes can no longer read as success); rc
  echoed per call; database check report printed to the console (tee to log);
  `:STARTUP_FAILED` prints exit code + failed command + last 30 lines of
  startup.log and always pauses; `scripts/audit_launcher.py` + pytest guards
  launcher invariants (ASCII/CRLF/labels/pause-on-failure/no-powershell-pipes);
  all launcher files normalized to pure ASCII. init_db.py now has rc 0/1/2
  (healthy/unhealthy/crashed) + unconditional flush. Windows execution still
  UNPROVEN - see ISSUES_AND_FIXES ISSUE-036.

**Migration / DATA**
- Live preview DATA root migrated 2026-11-06 (alembic 0002 → 0003 ×3 instrument DBs;
  registry unchanged at 0001; 1 legacy draft row kept NULL timeframe).
  Fingerprint migration dry-run = nothing to rewrite (single spec-less draft);
  backups in `BACKUPS/pre-0003-2026-11-06/`. Databases with real specs must run
  `scripts/migrate_fingerprints.py` (dry-run first) after upgrading.

**Known limits**
- Windows .bat files remain UNTESTED on Windows (no Windows in this environment);
  first Windows run is a smoke test (`cmd /k START.bat`, LOGS\startup.log).
- No market data exists: multi-timeframe nodes are DRAFT/PENDING; nothing anywhere
  claims backtest/validation readiness. W1/MN1 are not supported (not selectable).
- Indicator VALUES remain unavailable (Phase 3). Browser E2E absent (ISSUE-011).

---

## [Unreleased] — 2026-11-06 — Hero1 critical recovery: proven legacy launcher, theme consistency, starred nodes, pipeline audit (HERO V1.3.0)

**Added**
- `scripts/check_port.py`, `scripts/wait_ready.py`, `scripts/verify_instance.py` —
  stdlib-only launcher utilities adapted from the legacy v10.4-throughput tools:
  port ownership with Hero1 identity proof (0 free / 10 verified-Hero1 / 20 foreign),
  schema-verified HTTP readiness polling, and post-launch instance verification
  (Hero1 `/health` signature + `project_root` + per-run `HERO1_START_TOKEN`).
- `/health` additive identity echo: `start_token` / `project_root` (set only by the
  launcher; localhost-only; no secrets). `HealthOut` carries both as optional fields.
- Starred (favorite) nodes, research metadata only: additive `nodes.starred` column +
  idempotent instrument-DB migration `0002_nodes_starred`; `starred` on NodeCreate patch
  path / NodeOut; `GET /nodes?starred=true|false` with `starred_total` + `filtered_total`
  (`total` stays unfiltered — stable contract); server-enforced scope for every consumer.
- UI: per-row star toggle in Manual Nodes (SVG, theme tokens, optimistic flip with
  rollback), shared All Nodes / Starred Only filter (`StarScopeProvider`, persisted) in
  Manual Nodes, DeepTesting, Fine Tuning, Live Testing; Select-All limited to the
  filtered set; live match counts ("Showing N starred nodes out of M total nodes").
- docs/PIPELINE.md — legacy→Hero1 pipeline recovery + forward-compatibility audit
  (Part 5 deliverable; boundaries; risks; future phases explicitly not implemented).
- `PROMPTS/CRITICAL_RECOVERY_LAUNCHER_STARRED.md` (task prompt archive).
- vitest: `src/__tests__/starred.test.tsx` (4 tests). pytest: starred persistence/filter/
  counts + health identity fields (+3 tests). Test counts: pytest 91 passed/1 skipped,
  vitest 28/28.

**Changed**
- `START.bat` rebuilt on the proven legacy stage architecture (root detect → venv →
  import → DB → frontend deps → port ownership → persistent `cmd /k` service windows →
  real HTTP readiness → instance identity verification → browser → foreground monitor
  loop). Every failure path prints diagnostics and pauses; nothing closes silently.
  Frontend↔backend connectivity is verified through the vite proxy before the browser
  opens. Re-launching reuses identity-verified instances (never duplicates, never kills
  unrelated processes). `cmd /k START.bat` keeps full output visible for debugging.
- `scripts/run_backend.cmd` / `scripts/run_frontend.cmd`: single module identity
  `app.main:app` with cwd `services\api` (legacy semantics; previously worked only via
  the editable finder and double-loaded app modules); PowerShell `Tee-Object` pipes
  removed; services stream to their own persistent windows.
- `apps/web/vite.config.ts`: `strictPort: true` (vite fails fast on occupied 5173
  instead of silently moving to 5174).
- Theme: native `<select>` integrated into the dark theme (custom SVG chevron, amber on
  focus), `accent-color` on checkboxes/radios, tinted time/date indicators, new
  `.star-btn`/`.scope-line`/`.selection-chip` tokens-only styles; new batch scope UI
  reuses `.segmented`/`.card`/`.btn` — no separate visual system.

**Fixed (ISSUES_AND_FIXES.md ISSUE-025..032)**
- Launcher accepted any HTTP 200 as "already running"; frontend 200 could mask a dead
  or missing proxy→backend link; vite port drift; PowerShell-pipe fragility; dual app
  module identity; theme regression on Visual Builder/JSON Editor native controls;
  scope-line test-unfriendly split text nodes.

- Graphical-theme consistency fix (ISSUE-033): the last three grey/white,
  non-themed controls are now on the graphical tier. Global instrument selector
  is a themed custom listbox (`components/InstrumentSelect.tsx`) — trigger +
  `role=listbox`/`option`, `aria-expanded`/`aria-controls`/`aria-activedescendant`,
  ArrowUp/Down/Home/End/Enter/Space/Escape, outside-click close, disabled
  unavailable options never selectable; amber gradient chevron panel, grouped
  Available / Not available sections — **no native OS dropdown popup remains**.
  Data/persistence unchanged (state/instrument.tsx: registry, `hero1.instrument`,
  XAUUSD default). `.segmented` (Visual Builder/JSON Editor + All Nodes/Starred
  Only + AND/OR + exit/risk toggles) is the shared graphical recipe: amber
  gradient active + glow + inset highlight + dark text; themed hover for
  inactive; focus-visible/active/disabled matrix. Starred count is the
  `.scope-count` amber-soft badge (flips dark-on-amber on the active segment).
  Permanent standard documented in **docs/DESIGN_SYSTEM.md** (tokens, tier
  rules, state matrix, shared components) — binding for all future UI work.
- vitest rewritten instrument specs (listbox keyboard/selection/persistence/
  disabled-option) + builder spec interaction; tests: pytest 91/1-skip,
  vitest 30/30.

**Verified**
- Linux (LMS): backend + frontend run against the REAL code; `/health` ok; proxy
  connectivity verified; identity checks rc-tested (reuse, fresh, wrong-token);
  preview DATA migrated 0001→0002 (additive; data untouched); all 8 tabs serve;
  pytest 91/1-skip, vitest 30/30, tsc+vite build green; new modules served 200
  via the preview (theme.css contains .instrument-panel/.scope-count).
  NOTE: no pixel-level browser screenshot was captured in this environment —
  see ISSUE-033 / CHATGPT.txt; theme verification = jsdom interaction tests +
  served-module/CSS audit, not visual proof.

**Limitations / honest status**
- Windows execution of the .bat files remains UNPROVEN (no Windows environment here);
  first Windows run must be a smoke test with `cmd /k START.bat` + LOGS\startup.log.
- Phases 3 (indicator engine), 4 (screening/backtest/OOS), 5 (paper/MT5), 6–8 are NOT
  implemented and NOT claimed; pipeline preparation documented in docs/PIPELINE.md.
- Browser E2E (Playwright) still absent (see ISSUE-011).

## [Unreleased] — 2026-11-06 — Hero1 dashboard completion: global instrument selector, visual strategy builder, canonical DATA, launcher repair

**Added**
- Global instrument selector in the top-right status cluster of every tab
  (backend-registry list, disabled/greyed entries for symbols the registry does
  not enable, `GBPUSD/USDJPY/ETHUSD` shown as not-in-registry; persisted via
  `hero1.instrument`, default always XAUUSD; selection passed explicitly to all
  instrument-scoped API calls; instrument context shown on all placeholder tabs).
- Manual Nodes: Visual Builder as a first-class mode beside the JSON editor —
  searchable indicator library MIRRORING the backend registry exactly (SMA, EMA,
  RSI, MACD, BBANDS, ATR, VWAP, ROC, STOCH, ADX; WMA/SMMA/CCI/StdDev/OBV/PSAR
  shown disabled as not supported), condition rows with LHS/operator/RHS,
  AND/OR groups, one nesting level + read-only fallback for deeper JSON,
  duplicate/remove/add, backend-limit guards, exits (SL/TP/max-hold/time-exit,
  never invented), risk sizing with explicit distance units (NEEDS_METADATA
  preserved), sections 1–4, indicator preview panel with explicit
  `INDICATOR ENGINE NOT AVAILABLE` (Phase 3 dependency; no fake values),
  one-line node summary, full visual↔JSON round-trip with invalid-JSON safety
  (read-only + revert).
- Registry-driven auto naming `C1…Cn` per instrument (refresh-proof, max+1,
  no reuse), manual override, metadata-only PATCH rename for saved nodes.
- `scripts/run_backend.cmd` / `scripts/run_frontend.cmd` — persistent visible
  service windows with pause-on-exit and tee logging to canonical logs.
- PROMPTS/INSTRUMENT_SELECTOR_BUILDER_BATS.md (task prompt archive).

**Changed**
- Canonical DATA root is now `<PROJECT_ROOT>\DATA` (git-ignored): storage
  engine accepts exactly `<repo>\DATA` in-checkout (others still refused),
  creates DATABASE/LOGS/BACKUPS/CACHE/MARKET_DATA/RESEARCH/ARTIFACTS, and the
  default `HERO1_DATA_ROOT`= unset falls back to `<repo>\DATA`.
- PREREQUISITES.bat / START.bat rewritten around the canonical root (single
  DATA_ROOT variable, all subdirectories + logs, %~dp0, CRLF/ASCII, nested-quote
  defect fixed, service windows launched via the static wrappers, readiness
  polling + browser-after-ready preserved, duplicate-instance detection kept).
- web tests 9 → 24 (builder suite, selector persistence, auto-naming,
  JSON↔visual sync, no-fake-values); environment test updated to the canonical
  DATA policy (docs/ISSUES_AND_FIXES.md ISSUE-020..024).

**Verification status**
- pytest 88 passed / 1 skipped (.venv); web vitest 24/24; production build clean.
- **Windows .bat files NOT executed on Windows** (none available here). Static
  audit + Linux verification of every embedded command only; see CHATGPT.txt.
- Live dashboard re-verified in the LMS workspace with
  HERO1_DATA_ROOT=/home/user/hero1-workspace-data (outside git) — see delivery
  report for the actual preview URL.

**Commit:** this commit — exact SHA in CHATGPT.txt (record commit follows).
**Tag:** none (no release)

## [Unreleased] — 2026-11-06 — Windows two-file launcher

**Added**
- `PREREQUISITES.bat` — one-time Windows setup (detection, .venv reuse/create,
  editable installs, import + build + DB verification; logs to
  `D:\Hero1\DATA\LOGS\prerequisite.log`).
- `START.bat` — everyday launcher (persistent visible backend/frontend
  windows, real HTTP readiness polling, opens the dashboard automatically;
  duplicate-instance safe; logs to startup/backend/frontend.log).

**Changes:** none to app code, schema, compiler, UI or API. Docs: WINDOWS_SETUP
(§0), README, DEVELOPMENT updated. Prompt archived as
PROMPTS/WINDOWS_LAUNCHER.md.

**Verification status:** underlying commands Linux-verified from scratch (clean
venv, fresh DATA root, `npm ci` + production build). **The .bat files have not
been executed on Windows** — first Windows run is a smoke test; see
docs/WINDOWS_SETUP.md §0.

**Commit:** this commit — exact SHA recorded in CHATGPT.txt (record commit
follows, as in prior phases)
**Tag:** none (no release)

---

## [HERO-V1.1.0] — 2026-11-06 — Phase 2 — Strategy specification, validation & deterministic compiler

**Features introduced**
- `packages/strategy_spec` (new, version 1.1.0): principle — a strategy is
  structured DATA, never executable Python. SCHEMA_VERSION 1.0.0; typed
  expression trees (AND/OR/NOT, comparisons gt/gte/lt/lte/eq/neq/cross_above/
  cross_below; candle / forming-candle / indicator / constant operands;
  discriminated unions); 10 indicators (metadata + parameter constraints ONLY —
  no math); explicit exits (stop-loss / take-profit / explicit exit /
  max-holding candles / UTC time exit — required; never silently invented);
  risk config with explicit distance units (absolute/points/ticks/pips/ATR-
  multiple; ATR-multiple requires atr_period) and fixed-risk OR fixed-volume
  sizing; per-direction overrides; execution intent (entry timing,
  completed-vs-forming candle with default completed + forming forbidden
  unless explicitly permitted, market/pending intent, position handling) —
  declarative ONLY, no execution semantics.
- Validator: layered checks (structural → identity/instrument/timeframe →
  expression tree/safety → indicators → risk → execution); severities
  VALID/WARNING/NEEDS_METADATA/INVALID; structured issues
  {code,path,message,severity,remediation}; warm-up = max(indicator warmups +
  offset, 1); XAUUSD/EURUSD/BTCUSD instrument scoping; unknown schema versions
  rejected; unbounded trees rejected (depth ≤ 8, nodes ≤ 64).
  Missing broker metadata (pip/tick/point sizes) ⇒ NEEDS_METADATA — NEVER
  assumed or defaulted; absolute/ATR distances need no metadata.
- Compiler: canonical JSON (sorted keys, compact separators, NaN refused) →
  deterministic IR (IR_VERSION 1.0.0) with SHA-256 fingerprint; insertion-order
  independent; identical specs ⇒ identical fingerprints; distinct ⇒ distinct;
  records schema/compiler/IR versions; refuses INVALID and NEEDS_METADATA
  (WARNING compiles); NO eval/exec/dynamic imports anywhere in the package.
- Reference fixture `bullish_engulfing(instrument, timeframe)` + XAUUSD
  M1/M5/M15 example specs: bearish prev candle → bullish current → body
  engulf → high/low break; entry next bar open after signal close; SL below
  signal low (10 pips via EXTERNAL broker metadata); TP 2R; completed candles.
- Node versioning (immutability fix): semantic spec change ⇒ validate →
  compile → NEW node version linked via parent_node_id (+ node_lineage row);
  parent never modified; next_version = max(lineage component)+1;
  cross-instrument parents rejected (422); lineage component is instrument-
  scoped (root recursion + BFS descendants, 1000-hop cap).
- API: POST /nodes/validate (no persistence), POST /nodes/compile, GET
  /nodes/{id}/lineage, POST /nodes/{id}/versions; NodeOut gains `spec_json`;
  NodePatch is metadata-only (extra=forbid); NodeCreate `spec_hash` kept as
  documented-ignored compat shim; server is the ONLY fingerprint authority
  (canonical JSON persisted, fingerprint recomputed); broker_meta accepted
  for validation only, never persisted; duplicate (instrument, spec_hash,
  version) → 409 SPEC_DUPLICATE. EventBus NODE events for
  validate/compile/create/version/validation-failure.
- Frontend (Manual Nodes): spec workspace — timeframe select, bullish
  engulfing loader, JSON editor, broker metadata inputs (never assumed; blank
  ⇒ server NEEDS_METADATA), Validate/Compile/Save/Save-as-new-version,
  structured issue panel (severity/code/path/remediation), IR + fingerprint
  display, registry version/hash columns, lineage viewer; structured 422/409
  error bodies rendered with server issue details. No redesign; no fake
  results — nothing claims to be backtested.
- Tests: +59 backend (34 spec/validator + 12 compiler/fingerprint/security +
  15 API integration) and +3 frontend workspace tests. Full suites:
  **backend 86 passed, 1 skipped** (Ollama unreachable, by design);
  **frontend 9 passed**; production build OK (`tsc --noEmit && vite build`).
- Live smoke (2026-11-06, temp data root, uvicorn :8799): validate w/o meta →
  NEEDS_METADATA; validate w/ meta → VALID (warmup 1); compile → fingerprint
  eb132930ec… (== unit-test value); create → 201 server-hashed; new version →
  v2 parent 1; lineage → [v1, v2].

**Bugs fixed** (details: docs/ISSUES_AND_FIXES.md ISSUE-012…019)
- NodeOut omitted spec_json (ISSUE-014) — saved specs were unreadable
- PATCH could rewrite spec_json/version in place — semantic edits bypassed
  validation/fingerprinting (ISSUE-015)
- Client-supplied spec_hash was persisted verbatim — now server-recomputed
  (ISSUE-016)
- Duplicate identical spec → sqlite IntegrityError → 500; now 409 SPEC_DUPLICATE
  (ISSUE-013)
- Undiscriminated LogicNode union → exponential backtracking (DoS); now
  discriminated (ISSUE-012)
- API client flattened structured FastAPI errors to `HTTP 422` (ISSUE-018)

**Known limitations / honest scope**
- Unit tests ≠ trading performance, ≠ Windows/RTX 4090/MT5 verification.
- Frontend↔backend covered by mocked-fetch vitest + manual smoke; no browser E2E.
- Node DB schema unchanged by design (immutability is application-level);
  UNIQUE(instrument_id, spec_hash, version) kept — duplicates are conflicts.
- Fingerprint covers the full spec including identity (name/description/
  metadata) — cosmetic-only edits DO produce a new fingerprint; clients must
  route semantic edits through /versions regardless.
- Push depends on owner-configured secure auth; CHATGPT.txt records remote
  verification status honestly.
- Phase 3 (indicator math, candle store) NOT started.

**Baseline:** `396a71e4de7ff96994da2342a38157a53a24a7d0` (HERO-V1.0.0 record commit)
**Commit:** `ef6742e916a02e9fdf938cbb2fea531523f507fe` (this release commit)
**Tag:** HERO-V1.1.0 → ef6742e91 (annotated tag object `f64bb18be15cba16b7550fdfb40631cc7da3dd72`)
Record commit (this file's SHA pin) follows on main, as in Phase 1.3; the tag
stays on the release commit.

---

## [HERO-V1.0.0] — 2026-11-06 — Phase 1.3 — HERO V1 (migration release)

**Identity change:** Hero1 becomes the active project of the EXISTING GitHub
repository `https://github.com/awais225/evolutionary-trading-lab` (PUBLIC, by
owner decision 2026-11-06). `main` = HERO V1 (full Hero1 history preserved).
Evolutionary Trading Lab preserved on branch **`legacy-archive`**
(`d96ec4baae56054729b3dd961aea9b7f0b067114`, original legacy main; verified
remotely). Legacy branches preserved: v10.2-dashboard-manual-testing,
v10.3-speed-diagnostics, v10.4-throughput. Legacy tags preserved:
archive-v5.4-line-d9ea8e7, V5.3.1-V6-BASELINE. Repository DATA and Git LFS
objects were NOT deleted, migrated or regenerated.

**Features introduced:** none (release/migration only). Release identity
**HERO V1**.

**Bugs fixed:** none. **ISSUE-010 resolved** (migration path replaced the
new-repo publishing path).

**Technical changes:** `main` replaced via lease-protected push
(`git push --force-with-lease=main:d96ec4ba… origin main:main`); full Hero1
history intact (840bf71 → ddce564 → 2471da6 → HERO V1 release commit); tags
v0.1.0/v0.1.1/HERO-V1.0.0 pushed.

**Tests performed:** `python3 -m pytest` → 27 passed, 1 skipped (local Ollama
unavailable in this environment — integration test skips by design). Frontend
unchanged since v0.1.0 (6/6 vitest, clean build).

**Known limitations:** repository is PUBLIC (explicit owner decision);
Windows/RTX 4090/MT5 still unverified; local Ollama currently unreachable
(integration test skipped); Phase 2 not started; post-push verification record
is a follow-up docs commit on top of HERO-V1.0.0 (tags immutable).

**Commit:** `eaf02eb97341322a1bc443c43bbd588f4c8fdf12` (tag HERO-V1.0.0)
**Tag:** HERO-V1.0.0 → eaf02eb97 (annotated e7cfba090c47bb7fbe744f9d184976c9212d6a8f)

**Remote verification (2026-11-06):** `git ls-remote origin` — refs/heads/main
= eaf02eb9…; refs/heads/legacy-archive = d96ec4b…; legacy branches
(v10.2-dashboard-manual-testing @ f5935f3, v10.3-speed-diagnostics @ 8abf304,
v10.4-throughput @ 85b79b1) and legacy tags (archive-v5.4-line-d9ea8e7 @
d9ea8e7, V5.3.1-V6-BASELINE @ 11415f6) intact; v0.1.0 → 840bf71, v0.1.1 →
ddce564, HERO-V1.0.0 → eaf02eb9. Initial push rejected by GitHub (PAT lacked
`workflow` scope; remote unchanged, verified); retried with a workflow-scoped
token — success. A post-push record commit follows this entry on main
(tags remain on eaf02eb9; tags are not moved).

---

## [v0.1.1] — 2026-11-06 — Phase 1.1 — GitHub publishing & documentation handoff

**Features introduced**
- Mandatory documentation system established:
  - `CHATGPT.txt` — primary LMS→ChatGPT handoff (18 sections + append-only log)
  - `docs/ISSUES_AND_FIXES.md` — structured issue register (preserved history)
  - `PROMPTS/` — permanent prompt archive (exact task prompts + outcome sections)
  - `CHANGELOG.md` — this file (versioning convention established: v0.1.x until Phase 2)
- `docs/ARCHITECTURE.md` — converted to status-tagged implementation truth
  (Implemented / Partial / Planned / Unverified / Not supported)
- Permanent commit/push protocol codified (see CHATGPT.txt §17–19 and this file):
  CHATGPT.txt + CHANGELOG (+ISSUES_AND_FIXES when relevant) updated and committed
  in the SAME commit as the code; docs-only changes still follow the protocol.

**Bugs fixed:** none (documentation release).

**Tests performed**
- `python3 -m pytest` → 28 passed (re-verified at this stage)
- Frontend build/tests unchanged since v0.1.0 (6 vitest passed, tsc+vite build OK)
- Repo hygiene test re-run as part of the suite (no DATA/credentials tracked)

**Known limitations**
- GitHub push BLOCKED (no secure auth available in this environment) — repository
  not yet published. All v0.1.1 work is committed locally; tags v0.1.0/v0.1.1
  exist locally only.
- Windows / RTX 4090 / MT5 unverified (see CHATGPT.txt §12/§14).

**Commit:** `ddce56450ed8e354e8a5e7e8a3ede89aaa5a7d81`
**Tag:** v0.1.1 (local)

**Publication attempt (Phase 1.2, 2026-11-06) — NOT published**
- Owner supplied a fine-grained PAT for publishing. Verified: token authenticates
  as `awais225` (GET /user → 200). Target repo does NOT exist (404).
- Repo creation FORBIDDEN for this token: REST 403 and GraphQL FORBIDDEN —
  "Resource not accessible by personal access token".
- No repository was created or modified; nothing was pushed; no remote configured.
- Tags remain local. See ISSUE-010 and PROMPTS/PHASE_1_2_GITHUB_PUBLISH.md.

---

## [v0.1.0] — 2026-11-06 — Phase 1 — Foundation

**Features introduced**
- Monorepo structure: apps/web (React 18+TS+Vite, new visual identity, 8 tabs,
  instrument switcher, collapsible global LIVE FEED with WS replay),
  services/api (FastAPI: /health, /api/v1/instruments|settings|events|nodes,
  WS /ws/v1/events), services/worker (placeholder), packages/{storage, eventbus,
  strategy_spec, exec_semantics, indicators, ai_orchestrator, research, mt5_adapter}
- Storage: global registry DB + per-instrument SQLite DBs; Alembic baselines;
  FK enforcement; composite indexes; explicit instrument scoping; runtime
  refusal of inside-repo DATA roots
- Events: durable GlobalEvent records; WS live delivery + since_id replay;
  no database polling by consumers
- Safety: `doctor` environment report; origin taxonomy
  (CUSTOM/WWW/INTERACTIVE/FINE_TUNED); approvals schema (separate from testing
  qualification); .env.example dummies only; strict .gitignore
- CI: GitHub Actions (backend pytest; frontend build + vitest)

**Bugs fixed (all verified, see docs/ISSUES_AND_FIXES.md)**
- ISSUE-001 init hang: storage engine deadlock (Lock→RLock)
- ISSUE-002 instrument Alembic baseline created registry tables per instrument
- ISSUE-003 WS event fanout dead from sync endpoints (attach_loop +
  call_soon_threadsafe; drop-oldest queue policy)
- ISSUE-004 false-positive isolation test (id-space collision) — assert on
  instrument_id/name instead
- ISSUE-005 test invoked non-existent doctor --check flag
- ISSUE-006 pip system-install permission failure → --user installs
- ISSUE-007 frontend files misplaced (pages/) + wrong import + tsc -b on
  non-composite tsconfig → moved/corrected/build script adjusted

**Tests performed & results**
- `python3 -m pytest` → **28 passed**
- `apps/web`: `npm test` → **6 passed**; `npm run build` → OK (dist ~190 kB JS)
- Live smoke vs uvicorn 127.0.0.1:8788: /health OK; node & event creation OK;
  invalid instrument → 404; WS replay + live delivery OK
- Ollama structured-output probe (qwen2.5:0.5b) → PASS (pipeline check only)

**Known limitations**
- No strategy compiler, backtester, research agents, fine-tuning or live
  execution (Phases 2–9 — see docs/PHASE_ROADMAP.md)
- Windows/MT5/RTX 4090 unverified; MT5 = availability probe only, no order code
- Frontend↔backend covered by mocked-fetch vitest + manual smoke (no browser E2E)

**Commit:** `840bf71d9d3adbb556be1bf19771b63d98ae3e57`
**Tag:** v0.1.0 (local; remote push blocked at tag creation time)
