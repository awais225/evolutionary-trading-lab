# Changelog — Hero1
All notable stages of this project are recorded here. Rule (permanent):
every release lists version, date, phase, features, fixes, tests, limitations,
exact commit SHA and tag. Claims only for what was implemented AND verified.

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
**Commit:** this commit — exact SHA recorded in CHATGPT.txt OUTCOME and the
annotated tag `HERO-V1.1.0` (record commit follows the tag, as in Phase 1.3)
**Tag:** HERO-V1.1.0 (created after tests/build/verification pass)

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
