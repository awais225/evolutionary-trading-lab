# Changelog — Hero1
All notable stages of this project are recorded here. Rule (permanent):
every release lists version, date, phase, features, fixes, tests, limitations,
exact commit SHA and tag. Claims only for what was implemented AND verified.

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

**Commit:** (HERO V1 release commit — see git log; the post-push record commit
carries this SHA)
**Tag:** HERO-V1.0.0

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
