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
