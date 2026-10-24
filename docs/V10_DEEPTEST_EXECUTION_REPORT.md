# V10 — DeepTest execution, progress and TEMP isolation

Date: 2026-10-24. Source baseline: `e56cf8d4579f7c432ed2563523c8d1d62634aec3` (DATA-only update after published V9.6). This is a historical execution fix, not a strategy/qualification/trading change.

## Diagnosis established before editing

- The current authoritative database contains **10,000 strategies and 76 active qualified nodes**. Its 195,448,832-byte LFS object was downloaded outside the workspace, verified SHA256 `66ae751040d74ce1c70f53b0c95d493be92c0e94957e971b0f23bc15370dedee`, opened read-only/immutable, and passed SQLite `quick_check`.
- Both stored 76-node jobs (`batch-a7a93ee166ed42749a5d`, `batch-5c8c4a20b8b948088f4c`) are actually `AWAITING_COVERAGE_DECISION`: full=0, partial=76, unavailable=0. The frontend selected-node action supplied no automatic partial policy; `_prepare` intentionally stopped before scheduling backtests. This is persisted evidence of the blocking boundary, not a claim that a worker deadlock was observed.
- Two DeepTest owner checks used `os.kill(pid, 0)`. This is unsafe on Windows: [CPython's os.kill documentation](https://docs.python.org/3.13/library/os.html#os.kill) states that values other than the console CTRL signals invoke `TerminateProcess`. Both checks now inspect process status through existing psutil, without sending a signal. Native Windows termination was **not** reproduced here.
- Existing architecture already supplied durable batch/scenario tables, explicit resume, deduplication and serial workers. It was retained. Master-store locks are reentrant; no unproven ABBA diagnosis is asserted.
- Additional gaps: sparse progress, no dedicated live execution log, interval-based polling, shared dataset/feature scratch writes, an arbitrary 1,800-second observation deadline, and terminal scenario status being visible before trade insertion finished.

## Focused changes

1. `BulkDeepRequest.auto_partial` defaults to **false**. Only the explicit selected-node frontend action sets it true. The policy and per-node requested/actual coverage are retained durably; usable partial history continues without another popup, missing/invalid data is skipped, and results remain PARTIAL where appropriate. Legacy/per-node consent and existing waiting jobs are not automatically executed. Positive canonical IDs and the 1..5,000 request bound remain enforced.
2. Existing batch executor and scenario executor remain single-worker. Pending executable batch admission is capped at eight; identical active requests are reused, including old requests without the new optional flag. Submission failures retain a recoverable FAILED job. Cancellation stops remaining scheduling, retains completed/shared scenarios, and does not kill a historical worker. Restart shows INTERRUPTED and requires explicit resume.
3. Incremental phases/counters/error/timestamps/log path are stored in the existing batch JSON, not a new job framework or destructive migration. The UI shows job/state/phase, selected/validated/completed/success/failure/skipped, percentage, current node, elapsed time, last update and persistent log path. Polling schedules its next request only after the previous load; same-tick duplicate starts are guarded. Refresh recovers the saved job; old jobs remain discoverable through the existing list.
4. System TEMP is resolved at job execution. Owned UUID attempt directories are created below `EvolutionaryTradingLab/DeepTest/<job-or-run-id>-<attempt>`. Project/workspace paths and escaping TEMP symlinks are rejected, and unavailable TEMP fails visibly, never falling back to workspace. A single timeframe's consistent source view is read under the existing store lock into job TEMP; no full DATA copy or canonical dataset registration/materialization is performed for DeepTest.
5. Full-series warmup and the requested engine window are preserved. A private, injected-data FeatureEngine uses the **same feature functions** with job-local RAM caches, without reading/writing shared feature files, manifests or feature metadata. The normal engine's no-argument constructor and normal cache behaviour are unchanged. No global configuration/environment/trading-engine switch is used.
6. `DATA/logs/DEEPTEST.log` uses standard logging, immediate handler flush and bounded rotation (5 MiB + two backups), propagating to the existing central logger. Entries identify job/phase/node/elapsed, coverage authorization, data/features/engine/persistence, failures/tracebacks, slow progress and cleanup. Default slow-warning cadence is 60 seconds (`LAB_DEEPTEST_STALL_WARNING_SECONDS`); it is not a backtest kill timeout. Known returned failures also retain explicit error events. There is no arbitrary execution observation deadline.
7. Metrics/trades remain in the existing durable scenario/trade persistence, not only scratch. Terminal success/PARTIAL is published **after** trade persistence. Failed/incomplete history remains inspectable; an exact valid replay reuses the saved scenario. Cleanup verifies ownership and removes only this attempt's scratch. Abandoned cleanup requires a recognized marker, dead owner and age over seven days; active, unknown, recent and symlink directories are protected.

## Real-data validation — only two distinct existing qualified nodes

Used the current authoritative snapshot, not the old upload/archive or fabricated fixture. SQLite's **backup API**, not a blind live-file copy, produced `/var/tmp/v10-integration/DATA/DATABASE/lab_state.db`. Only canonical M15 history plus its metadata/manifest were hydrated: 54 source files, 4,812,318 LFS bytes, all rechecked unchanged against Git/LFS hashes. The full source M15 frame has **101,368 bars**.

A real React `SelectionControls` + `DeepBulk` + production `api.js` harness selected only **924 and 1468** from the actual qualified inventory, posted over real HTTP to the actual FastAPI routes, and executed their stored genomes through the actual historical engine and SQLite persistence. The isolated API mounted the existing router without starting the application's evolution/live/bootstrap workers. No orders or population generation occurred.

The same two IDs were validated twice (initial integration and final safety/logging validation): **four new scenarios total**, never more than two distinct real nodes. The first Node/jsdom runner printed success but remained alive because of browser-scheduler ports; its observation shell timed out after the job had already completed in ~2.2 seconds. The owned completed runner was stopped and its final cleanup now explicitly exits. The final runner exited successfully in ~6.4 seconds; actual batch execution was ~2.5 seconds.

Final batch: `batch-754574cd96434f069a7f`, **COMPLETED_WITH_ISSUES**, selected=2, validated=2, completed=2, successful processed=2, failed=0. “With issues” reflects partial coverage, not an engine failure. The frontend visibly showed `2/2 finished`, with one POST and no blocking popup.

| Canonical ID | Verified source status | Stored genome hash | Final run | Result | Bars used | Persisted trades |
|---|---|---|---|---|---:|---:|
| 924 | QUALIFIED | `537381a22d40f5bb` | `dt-43e9c9a239374747` | PARTIAL | 6,026 | 559 |
| 1468 | QUALIFIED | `1ba300e10835d3e2` | `dt-bf09ac84aed14135` | PARTIAL | 6,026 | 559 |

Dataset: `XAUUSD_M15_USER_HISTORY_ICMARKETS_CANONICAL_v3`, fingerprint `fb403f0a823b5761160b07536afa57e5`. Requested UTC range: 2026-07-24 09:50 → 2026-10-24 09:50. Actual selected bars: 2026-07-24 10:00 → 2026-10-23 23:45. Requested/actual ranges, missing periods and limitations remain recorded; no candles were manufactured. Exact frozen-input HTTP replays returned the same two run IDs with reuse=true and no additional computation.

**Limitation:** MetaTrader5 is unavailable in this Linux sandbox. No simulator bars were generated/used: candles came from the committed canonical history. Execution costs/instrument specifications used the unchanged **TABLE**, `specs_verified=false` fallback, not terminal-verified broker specifications. These partial historical outputs are execution-validation evidence, not validated profitability or live-trading readiness.

## TEMP, persistence and preservation measurements

- Resolved real scratch root: `/tmp/EvolutionaryTradingLab/DeepTest`.
- Final batch attempt: `batch-754574cd96434f069a7f-bc3947e2`; child attempts `dt-43e9c9a239374747-2f5dd2b4` and `dt-bf09ac84aed14135-00092dd5`.
- Each single-timeframe view: 4,110,289 bytes. Measured child directory bytes before cleanup: 4,110,374 / 4,110,373; batch marker directory: 92 bytes. All three `after_exists=False`; real scratch root has **zero remaining job directories**.
- Persistent validation logger: `/var/tmp/v10-integration/DATA/logs/DEEPTEST.log`; production semantics remain `<configured DATA_ROOT>/logs/DEEPTEST.log`.
- All original rows were compared over every original column: **10,000 strategies, 16,918 backtests, two old DeepTest scenarios, 3,916 old trades and two old waiting batches remain byte/value-equivalent**. Both old 76-node jobs were left waiting and never scheduled. Original database SHA256 remains unchanged; original database writes=0. New validation results exist only on the isolated backup and in the supplied portable results export, not in the user's authoritative database or Git DATA tree.
- Initial physical workspace: **117.03 MiB** (122,710,954 bytes). Pre-publication audit after relocating download caches: **118.77 MiB**, before final receipts/report/bundle overhead; final exact measurement accompanies the publication receipt and must remain under 128 MiB.
- **Storage exception, corrected:** although installed dependencies and all DeepTest execution scratch were external, package installers initially placed their download caches in snapshot-excluded `/home/user/.npm` and `.cache`. An actual physical audit caught **367.69 MiB** total. Both cache directories were preserved intact under `/var/tmp/v10-dependency-download-caches`, bringing workspace below 128 MiB. No research data/archive/cache was deleted or moved. The physical cap was not maintained throughout installation; it is not represented as if it were.
- All 441 previously surviving worktree files remain present. Exactly five preexisting source files changed (listed below). Unrelated MT5 tool modes, missing tracked DATA paths and prior verification artifacts were not staged or discarded. No CONFIG/version/theme/risk/strategy/account modification was made.

## Tests and boundaries

Final focused regression command (all test roots/dependencies/bundles external):

```sh
EVOLUTIONARY_LAB_DATA_ROOT=/var/tmp/v10-regression/DATA \
PYTHONDONTWRITEBYTECODE=1 python -m pytest \
 tests/test_v10_deeptest.py tests/test_v10_ui.py \
 tests/test_v9_5_repairs.py tests/test_v9_5_ui.py \
 tests/test_v5_2_deep_testing_data.py \
 tests/test_v6_6_deeptest.py::TestPeriods \
 tests/test_v6_6_deeptest.py::TestStore \
 tests/test_v6_6_deeptest.py::TestAnalytics \
 tests/test_v6_6_deeptest.py::TestCsv \
 tests/test_v6_6_deeptest.py::TestCoverageAndConfirm \
 tests/test_v6_6_deeptest.py::TestSharedFileRobustness \
 tests/test_v6_7_deeptest_workflow.py::TestMt5Sync \
 tests/test_v6_7_deeptest_workflow.py::TestScenarioIdentity \
 tests/test_v6_7_deeptest_workflow.py::TestWorkflowAndFilters \
 tests/test_v6_7_deeptest_workflow.py::TestThemeContract \
 -q -p no:cacheprovider --basetemp=/var/tmp/v10-pytest-publication
```

**142 passed**, one upstream Starlette/httpx deprecation warning. The new UI driver checks six behaviours; existing V9.5 UI driver checks five. Tests cover contracts, automatic/legacy coverage, unavailable/invalid IDs, durable progress/logs/error tracebacks, queue admission/submission failure/deduplication, cancellation, interruption, non-signalling ownership checks, TEMP setup/cleanup/foreign protection, returned engine failure, terminal persistence ordering and full-frame feature/trade parity (excluding only measured `runtime_ms`). These are deterministic fixtures, separate from the actual two-node integration above.

Frontend production build passed with output external to workspace; existing >500 kB chunk warning remains. Older broad data-dependent regression attempts stopped on missing preloaded SIMULATOR dataset, empty isolated inventory/Node#718 or absent historical-store prerequisites. Those groups were not repaired by invented data and **are not claimed as full-suite passes**. Native Windows packaging/process execution, a whole 76-node campaign and real MT5 broker execution were not run.

Opt-in real UI harness (requires an isolated backup API; no implicit node generation):

```sh
V10_ISOLATED_BACKUP=YES V10_REAL_API=http://your-isolated-api \
V10_NODE_IDS=924,1468 V10_RECEIPT=/system/temp/ui-receipt.json \
node tests/v10_real_data_ui.mjs
```

The harness uses jsdom/React with real HTTP, not a native graphical-browser rendering test. Its bounded observation limit only stops the harness observing; product workers have no such deadline and are not automatically cancelled.

## Exact intended Git files (13)

Modified existing source:
- `backend/app/deeptest/bulk.py`
- `backend/app/deeptest/contracts.py`
- `backend/app/deeptest/scenarios.py`
- `backend/app/features/engine.py`
- `frontend/src/components/SelectionControls.jsx`

New source/tests/docs:
- `backend/app/deeptest/runtime.py`
- `frontend/tests/v10_ui.jsx`
- `frontend/tests/v10_ui.mjs`
- `frontend/tests/v10_real_data_ui.jsx`
- `frontend/tests/v10_real_data_ui.mjs`
- `tests/test_v10_deeptest.py`
- `tests/test_v10_ui.py`
- `docs/V10_DEEPTEST_EXECUTION_REPORT.md`

Publication must explicitly stage only these files, review the complete staged diff and secret scan, fresh-fetch main, commit `Fix DeepTest execution, progress logging, and temp isolation`, normal-push main without tags/force/history rewriting, then independently verify fetched origin/main and `ls-remote`. The final supplied publication receipt records the actual observed commit/remote SHA; this document does not pre-claim that an unverified push succeeded.

Supplementary non-Git evidence delivered separately: `V10_REAL_INTEGRATION_RECEIPT.json`, `V10_REAL_FRONTEND_RECEIPT.json`, `V10_REAL_DEEPTEST_LOG_EXCERPT.txt`, and `V10_REAL_RESULTS.json.gz` (all four real validation scenarios and 2,236 trades). Runtime databases, data, credentials, dependencies, logs and generated builds are not part of the source commit.
