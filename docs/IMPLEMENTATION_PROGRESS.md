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

## 2026-11-07 — G6: START.bat persistent outer launcher + standalone diagnostic (owner report: "STILL closes on Windows")
- Owner rejected the TASK 10 in-place hardening as insufficient. Delivered the
  architectural split instead of a third in-place patch:
  - START.bat = thin PERSISTENT OUTER shell (prints its own path/size/modified/
    certutil SHA-256/git HEAD; executes scripts\START_INTERNAL.bat inside
    `cmd /k call`; prints final status block and pauses after the inner
    procedure ends, however it ends).
  - scripts\START_INTERNAL.bat = rebuilt 10-stage procedure; uniform banners;
    console+startup.log same lines; single :STAGE_FAILED (rc/reason/reproduce-cmd/
    log-tail + pause); monitor loop routes to the same handler; token generated
    before any service window starts.
  - START_DIAGNOSTIC.bat = standalone read-only diagnostic (7 sections; PRAGMA
    integrity_check via mode=ro; diagnostic.log; ALWAYS pauses; touches no DB).
- Audit + launcher tests rewritten for the trio (outer cmd /k + certutil + pause;
  internal 10 stage markers + :STAGE_FAILED; diagnostic pause + PRAGMA + exit /b 0).
- Verified (Linux): audit PASS 6/6; pytest 110 passed / 1 skipped; PRAGMA
  one-liner executed against the live preview DATA root (all DBs ok); ASCII/CRLF
  verified via file(1). NOT VERIFIED: Windows execution - owner must re-test.
- Scope kept: no DATA reads/writes beyond read-only checks; no strategy/schema/UI.
Changed: START.bat (rewritten), scripts\START_INTERNAL.bat (NEW),
START_DIAGNOSTIC.bat (NEW), scripts/audit_launcher.py, tests/test_launcher_bat.py,
docs (ISSUES-037, WINDOWS_SETUP 0.1, CHATGPT TASK 11, this entry), CHANGELOG.

## 2026-11-07 — G6 record: commit + push (verified)
- Feature commit a7d435b27fab5f778070c58e3d4944f668aa611d pushed to origin main
  (11 files, +775/-376). Verified via ls-remote AFTER push:
  a7d435b27fab5f778070c58e3d4944f668aa611d refs/heads/main.
- Committed file hashes (repo-relative, as the owner will pull them):
  START.bat                 1ec65669809741f868a4ec5950405da4ab9eef020784f8fd0b9288b2663b95e8
  START_DIAGNOSTIC.bat      d988b61e10892c1b8bba2556a1c830c00aa2e3072ada31c0f3042b0678b28c9f
  scripts/START_INTERNAL.bat 2779e855eeba5282871ca3a139474373ddcfb473ee973cda9aa038239877719f
- Still NOT proven: Windows execution (owner must re-test). Everything else
  holds as recorded in G6 above.

## 2026-11-07 — TASK 12: broker metadata + MT5 selection, market-data CSV importer, launcher stage-10 + TROUBLESHOOTING (HERO V1.4.2)
- Broker metadata pipeline: `packages/mt5_adapter` gained `connection.py`
  (terminal discovery, single session lock, account snapshot — missing fields
  stay None, never fabricated), `metadata.py` (snapshots per broker/server+
  symbol under DATA/MARKET_METADATA/<server>/ with manifest + SHA-256; servers
  never overwrite each other) and `sizing.py` (legacy digits rule: pip = 10
  points on 3/5-digit feeds else point; tick rounding; volume normalize; stops
  check). MT5 is NEVER simulated: no terminal → `MT5_UNAVAILABLE` refusals.
- API: `GET /api/v1/instruments/{s}/metadata`, `POST .../refresh` (503 without
  terminal), `POST .../import` (201; validate then turns VALID), `GET
  .../metadata/all`; `GET /api/v1/mt5/status|terminals`, `POST .../connect|
  disconnect`. TopBar MT5 selector records the active terminal for Live Testing
  ONLY — instruments, nodes, fingerprints and DATA are untouched by selection.
- Validation integrity: validate/compile answer NEEDS_METADATA until a snapshot
  exists for the exact broker/server/symbol; never forced VALID. Spec
  `identity.direction` is fingerprinted; directional example strategies keep
  LONG/SHORT (never stamped BOTH; BOTH = new blank strategies only).
- Defects found by the new tests and fixed: ISSUE-038 (sizing passed NaN
  through; volume_step=0 silently accepted) and ISSUE-039 (importer --timeframe
  silently overriding a conflicting filename; overwrite count included plain
  inserts).
- Market-data importer `scripts/import_market_data.py` (stdlib-only): MT
  History-Center CSV → DATA/MARKET_DATA/market_data.db (candles+datasets) +
  import_manifest.json. All-or-nothing per dataset; exact duplicates collapse
  (counted), conflicting duplicates FAIL; gaps reported never filled;
  VOL/TICKVOL/SPREAD separate; naive broker-server timestamps (never
  converted); filename/flag mismatch refuses. Live evidence on a SCRATCH data
  root (real DATA untouched): owner 7 XAUUSD exports → 419,013 candles, 7/7
  AVAILABLE after PASS; idempotent re-run 7 skipped exit 0; sampled candles
  match source CSVs exactly; M1/M5/M15 hit the MT 100k-row export cap (noted).
- Launcher: START_INTERNAL.bat stage 10 now captures the browser-launch rc and
  warns (manual URL) instead of claiming success — a browser failure never
  fails healthy services; :STAGE_FAILED + START_DIAGNOSTIC.bat point to the new
  repo-root TROUBLESHOOTING.txt (14 verified-only entries, search-first).
  START.bat byte-untouched. Audit extended with stage-10/TROUBLESHOOTING
  invariants (ISSUE-040).
- Legacy audit: docs/MIGRATION_MAPPING.md maps 15 legacy capabilities to Hero1
  destinations (legacy inspected read-only at 85b79b1c, v10.4-throughput;
  worktree removed; nothing copied wholesale).
- Verified: backend pytest 146 passed / 1 skipped (was 128: +19
  test_metadata_mt5, +15 test_import_market_data, +2 launcher); frontend vitest
  43/43; `npm run build` clean (272.5 kB); launcher audit PASS 6/6; metadata
  wiring live-checked against the read-only preview DATA root (GET 200
  available:false; refresh 503 MT5_UNAVAILABLE; validate NEEDS_METADATA).
- NOT verified: Windows + real MT5 terminal (refresh/collect, selector against
  live terminals) and Windows stage-10 double-click — owner machine required.
Changed: packages/mt5_adapter (+connection/metadata/sizing), services/api
(metadata_service, routers metadata+mt5, spec_service, main, routers init),
packages/strategy_spec spec.py, apps/web (Mt5Select, TopBar, ManualNodes,
client/types, theme, setup/tests, strategy workspace), scripts/
import_market_data.py (NEW), scripts/START_INTERNAL.bat, START_DIAGNOSTIC.bat,
scripts/audit_launcher.py, TROUBLESHOOTING.txt (NEW), tests (test_metadata_mt5
NEW, test_import_market_data NEW, test_launcher_bat), docs (ISSUE-038/039/040,
CHANGELOG, ARCHITECTURE, WINDOWS_SETUP, MIGRATION_MAPPING NEW, CHATGPT TASK 12,
this entry).

## 2026-11-07 — TASK 12 record: commit + push (verified)
- Four commits pushed to origin main; remote verified via ls-remote AFTER push:
  1ee39c734c2559b8738fbe0b3a7fe48156961f0e (metadata/MT5),
  91e9cafa63d9274cf93161890889505ac14c84c5 (importer),
  2019c090c0b35ac317b6d11aec6ddef2b9c522f6 (launcher stage-10 +
  TROUBLESHOOTING), 022ae49e47906e89e179bd0ad146950af82147c2 (docs).
  This record commit follows.
- Suite at push: backend 146 passed / 1 skipped; frontend 43/43; build clean;
  launcher audit PASS 6/6; importer live evidence 419,013 candles (scratch
  root). Preview backend restarted onto current code with identical env and
  live-checked (metadata 200 available:false; refresh 503 MT5_UNAVAILABLE;
  validate NEEDS_METADATA). Live DATA root untouched by all checks.
- Still NOT proven: Windows + real MT5 terminal; Windows stage-10 double-click.
## 2026-11-07 — TASK 13: Windows MT5 lifecycle, market-data deployment path, FETCH DATA, node metadata activation (HERO-V1.5.0)

- Starting SHA `a84279646421f883707ceaada6450898b4eccb88`; implementation
  commit `78f63ae96e0d0377d5378a13a5c358441b0f529f`. The annotated
  `HERO-V1.5.0` tag points at the following documentation commit and is
  explicitly **awaiting Windows acceptance**.
- Root causes fixed: ISSUE-041 (adapter declared no MetaTrader5 dependency),
  ISSUE-042 (initialize-only connect had no launch/lifecycle/login-required
  state), ISSUE-043 (stage-10 browser launch lacked fallback/duplicate-tab
  handling), ISSUE-044 (historical OHLC and broker metadata were not separated
  in the node workflow; no incremental fetch existed).
- Added shared `packages/market_data`, canonical availability/fetch API,
  Manual Nodes HISTORICAL DATA + FETCH DATA, MT5 lifecycle states/account
  permissions, `server_time.py`, and exact-interpreter diagnostics.
- Verification labels: backend **188 passed / 1 skipped** (Linux verified);
  frontend **44/44** and build clean (Linux verified); launcher audit PASS
  (static audit); fake-terminal lifecycle/fetch tests (mocked); fresh `/tmp`
  import of all seven CSVs = 419,013 candles with 7/7 idempotent skip (Linux
  verified, temporary trial removed; scratch retained). Real Windows terminal,
  account, XAUUSD metadata, Windows DATA, live fetch, and browser double-click
  are **not yet verified**.
- Canonical Windows deployment and the 14-point acceptance checklist are in
  `docs/WINDOWS_SETUP.md`. Do not delete `/home/user/hero1-scratch-data` until
  the owner verifies that Windows deployment.
## 2026-11-07 — Historical CSV import, market-data registry and MT5 incremental fetch repair

- Workspace cleanup first: removed only regenerable artifacts (frontend
  `dist`, pytest cache, project `__pycache__`). After canonical deployment was
  verified, the redundant old scratch store (server-epoch convention) was
  removed; supplied CSVs remain preserved in `/home/user/uploads` and are
  referenced by SHA-256 in the manifest.
- Imported all seven supplied XAUUSD CSVs into the canonical Linux DATA root
  `/home/user/hero1-workspace-data`: 419,013 candles, 7/7 AVAILABLE, rerun
  7/7 SKIPPED, SQLite `integrity_check` ok, zero duplicates/invalid OHLC,
  first/middle/last samples match source CSVs. Stored ranges are UTC (see
  TROUBLESHOOTING ISSUE-045..048).
- Repaired FETCH DATA to the UTC convention (no-baseline refusal, overlap,
  invalid-timestamp rejection, COMPLETE/PARTIAL/FAILED) and made the
  dashboard read the canonical registry. Backend 190 passed / 1 skipped;
  frontend 44/44; build clean. Genuine Windows MT5 fetch is NOT verified.
- Commit `fbd4a45973bc86402919b38853e80bf8095e8261`; HERO-V1.5.0 tag target is unchanged
  (awaiting Windows acceptance) - this repair is on `main` after the tag.

- ISSUE-049 (permanent storage): the seven supplied CSVs now live permanently
  at `DATA/MARKET_DATA/IMPORTS/XAUUSD/` in the project and are tracked in git
  (narrow `.gitignore` exception; DBs/manifests still ignored). The importer
  defaults to the project DATA root and discovers the IMPORTS CSVs (SHA-256
  early-skip); the API lifespan (`ensure_baseline`) and START_INTERNAL stage 6
  import them into each installation's own canonical
  `DATA/MARKET_DATA/market_data.db`; START_DIAGNOSTIC step 5b reports the
  store. The LMS installation was migrated into the project DATA directory -
  419,013 candles rebuilt from the repo CSVs, verified identical to the old
  external store, restart-persistent, dashboard==SQLite. Windows NOT VERIFIED
  (owner: git pull, START.bat, START_DIAGNOSTIC step 5b).

- ISSUE-050/051: DATA tab added (HistoricalDataPanel moved out of Manual
  Nodes above the new NodeDashboard: timeframe + colored spec-direction
  badges, parent/child lineage, filters); fetch conflicts classified and
  isolated (representation diffs counted, genuine conflicts recorded with
  stored-vs-MT5 diffs, stored candles preserved, PARTIAL status); fetch
  operation ids traced through the persistent event store; LOG tab is now
  a real filterable view of that store. Backend 199 passed / 1 skipped
  (200 collected); frontend 50/50; build clean; launcher audit PASS.
  Genuine Windows terminal conflict values still NOT VERIFIED.

- ISSUE-052: fetch operations are background, stateful and resumable
  (start/status/latest endpoints; duplicate-start protection; DATA tab
  polling); per-timeframe MT5 timeout; canonical lifecycle stage names
  with stack-trace events; unhandled exceptions recorded in the event
  store; LOG pagination (total/offset/order) + COPY ALL LOGS + SAVE LOGS
  TO TXT (full filtered export). Backend 204 passed / 1 skipped (205);
  frontend 51/51; build clean. Windows exception from the original hang
  was never captured by the old code - NOT VERIFIED there.

- ISSUE-053 (2026-11-07): fetch commit hang root-caused to a per-candle
  full-table timestamp SELECT; fixed with a single pre-transaction lookup,
  batch `executemany` inserts and WAL + 30 s busy timeout. 21,337 mocked M1
  rates now commit in 0.88 s on a DB copy (was ~24 min). Dashboard horizontal
  scroll fixed (grid track min-width/wrapping). Backend 206 tests 0 failures,
  frontend 51/51, build clean. Windows genuine-MT5 fetch NOT VERIFIED.

- ISSUE-054 (2026-11-07): durable fetch-conflict audit + controlled
  per-record resolution (DATA tab panel); dependency-checked node deletion
  (single/bulk, descendants opt-in, dependent records block); Duplicate &
  Edit (new independent node, parent=source, existing fingerprint rules).
  Backend 220 tests 0 failures, frontend 57/57, build clean. The 12 seam
  conflicts of the latest Windows fetch are classified UNRESOLVED pending
  Windows-side value inspection; no stored candle was overwritten.

