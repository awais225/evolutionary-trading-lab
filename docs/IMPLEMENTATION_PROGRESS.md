# Implementation Progress — Hero1 (append-only milestone log)

Entries are appended, never rewritten, newest at the bottom. Every entry states
date, scope, verified evidence, and honest gaps.

## 2026-11-06 — G0: baseline (pre–timeframe-architecture task)
- Tasks 1–7 landed earlier today (Phases 1–2, starred nodes, launcher recovery).
- Tests at task start: pytest 91 passed / 1 skipped · vitest 30/30 · tsc+vite build green.

## 2026-11-06 — G1: backend — node-identity timeframe, name uniqueness, multinode generation
Shipped & verified:
- Fingerprint definition change (compiler 1.0.0 → 1.1.0): identity.name/description
  are presentation and EXCLUDED from the semantic fingerprint; identity.timeframe +
  identity.instrument remain IN the fingerprint. Stored specs keep full canonical
  JSON (round-trip safe). Compiler exposes semantic_canonical/semantic_fingerprint.
- `nodes.timeframe` column (migration `0003_timeframe_unique_name`), backfilled
  from stored spec_json (NULL for drafts/unparseable — never guessed).
- UNIQUE (instrument_id, name) enforced at the database boundary
  (`uq_nodes_instr_name`) — upgrade REFUSES on pre-existing duplicates and
  lists them; nothing is renamed silently.
- `GET /nodes?timeframe=M1,M15` filter (unknown values → 422
  TIMEFRAME_UNSUPPORTED), response gains `timeframe_counts` (per-TF counts in the
  active starred scope) and `unassigned_timeframe_total` (drafts).
- `POST /nodes/generate` — one strategy → N independent nodes (one per selected
  timeframe), each with own id/timeframe/spec/fingerprint/lineage root;
  all-or-nothing; sequential first-available C-names (gap reuse) allocated
  server-side, race-guarded by the unique index (one retry, else 409).
- Explicit name honoured only for single-timeframe saves; versions fork with
  first-available C-names (parent display name is not reusable).
- Fingerprint migration tooling: `hero1_storage.fingerprint_migrate` +
  `scripts/migrate_fingerprints.py` (dry-run default, collision-refuses,
  backup-then-apply, idempotent).

Verified: pytest 103 passed / 1 skipped (12 new: tests/test_multitimeframe.py).
Honest gaps: display-name fingerprint change invalidates ALL previously stored
spec_hashes until the migration script is run against live DATA (see G4).

## 2026-11-06 — G2: frontend — multi-select, generate fan-out, TF registry UX, inline rename, shared batch scope
Shipped & verified:
- `TimeframeMultiSelect` (themed multi listbox; registry-only values; Select All /
  Clear All / counts / readable summary / no-timeframe validation; keyboard +
  aria-multiselectable contract) in the node creation flow; default =
  instruments.default_timeframe (XAUUSD=M15) else M15.
- Node save → `POST /nodes/generate` (single-TF honours explicit name; multi-TF
  = server-side sequential names, `Generate N nodes` button label);
  Validate/Compile preview stamps the reference TF (first selected) — documented
  in the UI hint beside the picker.
- Registry: TF badge column (`—` for drafts), timeframe filter chips (All + 7 TFs,
  server `?timeframe=` filter, per-TF counts from `timeframe_counts`,
  unassigned-drafts note), "Group by timeframe" toggle, Select-All limited to the
  visible set, per-instrument inline rename (click name / pencil; Enter saves,
  Escape cancels; trim/≤160/dup-per-instrument), inspector TF badge +
  `AWAITING MARKET DATA · DATA UNAVAILABLE — Phase 4` honesty line.
- Shared persisted scope: `state/timeframeScope.tsx` (localStorage
  `hero1.timeframeScope`) wired into Manual Nodes + BatchScope (DeepTesting /
  Fine Tuning / Live Testing) with live filtered/total counts.
- Instrument registry carries `default_timeframe` into the frontend type.
- Vitest: new `src/__tests__/multitimeframe.test.tsx` (10 tests); mock gained
  `/nodes/generate` + TF-aware list filtering + `timeframe_counts`.
  Result: vitest 40/40 · tsc --noEmit clean.
Honest gaps: pixel-level browser screenshot not captured (no browser/screenshot
tool in this environment); verification = jsdom interaction tests + build + served
modules through the live preview.

## 2026-11-06 — G3: gates — build, live preview DATA migration, preview verification
Verified:
- `npm run build`: tsc --noEmit + vite build GREEN (dist/assets/index-*.js
  266.31 kB). pytest re-run not needed this phase (no backend edits since G1;
  103 passed / 1 skipped stands).
- Live preview DATA (/home/user/hero1-workspace-data, outside git):
  backups → BACKUPS/pre-0003-2026-11-06/ (registry + 3 instrument DBs);
  alembic 0002 → 0003 applied ×3 instrument DBs (timeframe col, ix
  ix_nodes_instr_timeframe, uq uq_nodes_instr_name; 1 legacy spec-less draft kept
  NULL timeframe; 0 backfilled); registry stays 0001; fingerprint migration
  dry-run = nothing to rewrite (draft has no spec) — --apply NOT run (would write
  zero rows); idempotent.
- Preview backend RESTARTED to serve post-timeframe code (was started pre-G1);
  verified live: list carries timeframe/timeframe_counts/unassigned_timeframe_total;
  ?timeframe=M15 filters; ?timeframe=W1 → 422 TIMEFRAME_UNSUPPORTED;
  ?timeframe=M15,M15 → 422 TIMEFRAME_DUPLICATE; /health echoes project_root +
  start_token; vite proxy /health → backend OK; all new modules serve 200.
NOT done / out of scope: Windows execution of .bat files (no Windows available);
live market data, backtests, validation, qualification (Phase 4+; nothing claimed).

## 2026-11-06 — G4: commit + push (verified)
- Feature commit 01ccd599a6c23450a8ba8868e420b39145492615 pushed to origin main
  (35 files, +2130/-101). Remote main verified via ls-remote AFTER push:
  01ccd599a6c23450a8ba8868e420b39145492615 refs/heads/main.
- Record commit follows this entry (docs-only); remote main re-verified via
  ls-remote after it.
- Not done (unchanged, honest): Windows execution of the .bat files; Phase 4+
  market data / backtests / qualification; browser E2E (ISSUE-011).

## 2026-11-06 — G5: START.bat stage-4 diagnosis + launcher hardening (owner report)

Owner report: START.bat reached "Verifying database integrity (read-only
check)..." then the window vanished; dashboard never launched.

Diagnosis (established by inspection + Linux rehearsal, NOT by Windows execution):
- Exact failing command: `"%VENV_PY%" scripts\init_db.py --check >> startup.log 2>&1`
  followed by `if errorlevel 1 goto :DB_CHECK_FAIL`.
- Live preview DBs (read-only): PRAGMA integrity_check = ok (registry + XAUUSD +
  EURUSD + BTCUSD); alembic 0001 registry / 0003 instruments (as applied in G3).
- Healthy DATA copy: rc=0 ok:true · missing root: rc=1 structured JSON report ·
  relative HERO1_DATA_ROOT: rc=1 via UNCAUGHT traceback (outside the function's
  try) · corrupt DB copy: rc=1 + sqlite3.DatabaseError visible only inside the log.
- Defect A (real): `if errorlevel 1` compares signed 32-bit; a native crash rc
  (e.g. 0xC0000005 = -1073741819 signed) is < 1 → the check would be treated as
  PASSED. Fixed: captured RC + string compare everywhere; rc echoed per call.
- Defect B (real): check output redirected ONLY into the log — invisible console.
  Fixed: temp-file tee, full report typed to console, appended to the log, rc printed.
- Defect C (real): init_db.py path-resolution crashes bypassed the rc contract and
  stdout was block-buffered (a crash lost the JSON report entirely). Fixed:
  rc 0/1/2 contract (healthy / unhealthy / crashed-with-traceback) + flush.
- Skew class (not provable from here): on a consistent checkout the window cannot
  disappear at that line (all goto targets resolve; pause guaranteed). The remaining
  silent class is a local file skew/corruption (goto to missing label exits cmd
  with no pause). Fixed for all future states: scripts/audit_launcher.py +
  tests/test_launcher_bat.py enforce ASCII/CRLF/label-resolution/no-duplicate-label/
  pause-on-failure/no-plain-exit/no-powershell-pipes; all launchers normalized to
  pure ASCII (3 em-dashes removed).
- WINDOWS EXECUTION STILL UNPROVEN: the owner must re-run START.bat on Windows;
  the launcher now cannot exit silently on any failure (every path prints command,
  exit code, log tail; every failure pauses).

Changed: START.bat (stage 0-10 rc capture + transparent stage 4 + HERO1_DATA_ROOT
export + init_db.py existence + :PORT_CHECK_FAILED + :STARTUP_FAILED rc/log tail),
scripts/init_db.py (rc contract + flush), scripts/run_backend.cmd + run_frontend.cmd
(ASCII), scripts/audit_launcher.py (new), tests/test_launcher_bat.py (new).
Scope kept: no UI/functionality/DATA changes; databases untouched (read-only checks;
rehearsal used a /tmp copy).
Verified: pytest 107 passed / 1 skipped (4 new launcher tests); audit PASS on all
four launcher files.

## 2026-11-06 — G5 record: commit + push (verified)
- Feature commit 708a894e7f6782611675d3019aa7d4f86f71a3a1 pushed to origin main
  (12 files, +453/-22). NOTE: the environment was re-provisioned mid-task
  (.venv/node_modules rebuilt; pytest parity re-verified; git remote re-added —
  .git/config is intentionally not in snapshots; credential helper persisted).
- Remote main verified via ls-remote AFTER push:
  708a894e7f6782611675d3019aa7d4f86f71a3a1 refs/heads/main.
- Record commit follows this entry (docs-only); remote main re-verified via
  ls-remote after it.
- Still NOT proven: Windows execution of START.bat (owner must re-run; launcher
  now cannot exit silently — every failure prints command + rc + log tail + pause).
