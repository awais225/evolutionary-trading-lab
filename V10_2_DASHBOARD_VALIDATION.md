> Historical local-tree validation record, recorded before publication. Its missing-Git/no-push status and earlier resource skips are superseded by **V10_2_RELEASE.md** and the GitHub branch containing this document.

# V10.2 dashboard optimization — validation and manual testing

Date: 2026-10-28. Source: `/home/user/v95-app`.

**Dashboard/source changes are implemented and validated locally. No commit was created and nothing was pushed. The Windows checkout has not been updated by this session.** This is a validation record, not another patch package.

## Changes implemented

- Overview now displays authoritative generation execution state, persisted node/target/remaining counts, and five-minute trailing persisted research nodes/min. A below-target population is not treated as proof that generation is running. Target reached is explicitly distinguished from completion of evaluation.
- Resource reporting distinguishes configured/effective capacity, reported active workers, owned tasks, reported jobs, DeepTest configured/effective process limits and local controller admission. Admitted jobs can be queued, preparing, computing or persisting; they are not reported as observed busy CPU processes. Observed active DeepTest CPU processes remain **N/A**, not an invented estimate.
- Read-only DeepTest telemetry does not create pools or start jobs. Existing queue admission and memory enforcement are unchanged.
- Application CPU sampling retains child-process baselines instead of recreating a zero-first-sample baseline each time. Initial/failed application samples are unavailable, not fabricated zeroes; repeated calls within 0.5 seconds reuse the process sample. CPU percentages across processes can exceed 100% across multiple cores.
- GPU telemetry is explicitly device-level. The status-row backend now forwards scope, application-compute verification and historical compute backend rather than converting device activity into application processing. Device probes refresh at most every 30 seconds. Historical compute remains NumPy/pandas CPU; application GPU compute is not verified.
- Overview polling is stable across event changes, nonoverlapping and hidden-tab-aware. It reuses `/api/status` resources, refreshes inventory/runs more slowly, and uses revision changes to avoid unchanged authority/ranking requests. App/status-row errors and relevant dashboard errors are visible instead of silently retaining apparently current values.
- DeepTest page polling is nonoverlapping and uses current state without timer dependency churn. Failed preflights/start requests no longer become permanent busy entries. Pending starts are guarded against duplicate submission and premature removal while their request is still in flight.
- DeepScenarioPanel loads **all** persisted trade pages in 500-row chunks, with actual loaded/total counters, stale-response/unmount guards, full-population equality checks and visible failures. It does not substitute partial results. Search/filter/sort operates on the complete loaded population; membership uses a Set and filtering/sorting are memoized. Page jump reaches trade 3001 directly without refetching the history. CSV still exports the complete saved result.
- Inspect shows selected symbol/timeframe, requested versus actual tested range, persisted bars and genuine scenario status. Running scenarios poll status rather than loading a changing trade population as though it were final. Per-scenario processed/eligible bar counters are explicitly unavailable when not supplied; existing batch counters remain backend-provided. No elapsed-time progress was fabricated.
- Both node drawer paths expose results, complete DeepTest trades, genome/raw data, ancestry/children, existing export, and the existing historical-backtest configuration panel. Opening configuration does not execute a backtest. Standalone Run Validation is explicitly unavailable because no corresponding per-node API exists; validation records remain viewable. Main drawer requests and Trading Info ignore stale node responses.
- Settings retains the actual implemented `resources.deep_test_workers` field. Default **1 = serial** is unchanged; 2–64 is explicit opt-in subject to existing CPU/resource limits. Invalid widths are rejected before mutating settings, including before partially applying the all-settings request. No speculative historical controls, presets, resource quota changes or automatic worker increases were added. Native restart/testing limits are explained.

### Large-history limitation

History transport and rendering are progressive/paginated, but global client-side search/filter/sort still requires keeping the complete trade population in browser memory. This is not server-side bounded-memory search. An already in-flight HTTP request is not actively aborted on unmount; response guards prevent it from changing the selection or issuing further history pages.

## Fresh validation of final source

All builds, test databases, dependencies and validation logs were kept in the external staged environment, not authoritative DATA:

`/var/tmp/v102-dashboard-y7j3j5_7`

The tested modified/new source bytes were checked against the delivered source bytes.

| Validation | Final result |
|---|---|
| Frontend production build | PASS, 5.63 seconds; existing >500 kB chunk warning remains |
| New dashboard component harness | 8/8 behaviours PASS |
| V10 fixture UI harness | 6/6 PASS |
| V4.7 render smoke | 35/35 PASS |
| V4.8 render smoke | 54/54 PASS |
| V6.5.1 node UI | 16/16 PASS |
| V9.1 UI | 6/6 PASS |
| V9.2 UI | 6/6 PASS |
| V9.3 UI | 11/11 PASS |
| V9.5 UI | 5/5 PASS |
| Eight requested existing suites + V10.2 processing + new dashboard backend tests | **236 passed, 3 failed, 4 skipped**, 243 cases, 34.81 seconds |
| V10.2 processing suite, included above | 44 passed / 4 skipped of 48 |
| New dashboard backend suite, included above | 18 passed / 0 failed |

New UI checks cover authoritative states and unavailable resources, complete 3,015-trade loading, trade 3001 page jump without refetch, global sorting beyond 3000, visible incomplete-history error, unmount stopping follow-on pages, and Overview event rerenders not restarting inventory requests or duplicating status resources.

Fresh synthetic M1/M5 pipeline tests exercised actual preflight/start/engine/persistence/API/CSV paths. The M1 test asserts more than 6,000 saved trades and trade 3001 at offset 3000. These are deterministic regression fixtures, **not real-market performance evidence**. The original DeepTest unlimited/data-bounded execution path and complete export were preserved; 500 is a transport page size, not an execution/result cap.

### Failures, skips and unavailable testing

The three failed assertions require preserved research history absent from the isolated test DATA root:

1. `test_v5_1a_node_populations.py::test_22_api_populations_endpoint_matches_the_module` — expected nonempty research population.
2. `test_v5_1a_node_populations.py::test_23_api_deep_plan_exposes_the_union_and_progress` — expected at least one stored node.
3. `test_v6_8_authority_and_workflow.py::TestNodeDataAuthority::test_node_718_identity_metrics_and_true_ranking` — expected stored ranking records.

These were also documented in prior V10.2 validation. They were not skipped, weakened or supplied fabricated research history. This final run is not fully green.

Four existing process tests skipped at lines 238, 261, 361 and 403 of `test_v10_2_processing.py`: current OS/resource CPU capacity is below two effective workers. On this two-logical-CPU environment the unchanged 60% target yields one effective worker. No quota or target was raised to force a pass. This run therefore does **not** re-prove actual two-process execution.

The opt-in real-data UI harness was attempted without its required isolated-backup authorization/API/IDs and refused to run (`Explicit isolated-backup API authorization required`, exit 1). No real-data engine/UI test was executed. No authorization flag was fabricated to bypass that guard.

Native Windows/Python 3.14.6, MT5, real-market throughput, application CUDA execution, Windows launch success and hardware speedups were not validated. The previous failed native preflight remains preserved and is not represented as repaired. Existing Starlette/httpx, legacy React test/key/graph-container warnings and the Vite chunk warning remain; dependencies were not upgraded.

Two regressions caught during the first dashboard iteration (an unmapped toolbar class and changed coverage-label ordering) were corrected without weakening tests. The final theme and coverage UI tests pass.

## Preservation and scope audit

- All **61 protected evidence files** rehashed unchanged, including prior native-preflight evidence.
- Of 450 baseline non-data source files: **437 unchanged, 13 intentionally modified, none missing**. Four new component/test files were added, plus this report.
- Evolution engine, backtest engine, DeepTest scenarios/store/runtime/bulk are byte-for-byte unchanged from the dashboard starting point. RNG/selection/mutation/lineage, stopping and persistence semantics were not modified.
- Existing tests were not edited. No new generation campaign, broker order, authoritative DB generation or authoritative backtest was run; campaign budget consumption was zero.
- No patch/archive was rebuilt, no DATA tree was copied, and no protected evidence was cleaned up.
- Persistable non-generated workspace footprint is approximately **123.9 MiB**, below 128 MiB; platform-excluded caches are not included.

### Intended source/test files

Modified:

- `backend/app/api/routes.py`
- `backend/app/config.py`
- `backend/app/deeptest/parallel.py` — read-only telemetry helper only; execution path unchanged
- `backend/app/resources/manager.py`
- `frontend/src/App.jsx`
- `frontend/src/components/DeepScenarioPanel.jsx`
- `frontend/src/components/NodeDetailDrawer.jsx`
- `frontend/src/components/SelectionControls.jsx`
- `frontend/src/components/StrategyDrawer.jsx`
- `frontend/src/components/TopStatusRow.jsx`
- `frontend/src/pages/DeepBacktest.jsx`
- `frontend/src/pages/Overview.jsx`
- `frontend/src/pages/Settings.jsx`

New: `frontend/src/components/ProcessingStatus.jsx`, `frontend/tests/v10_2_dashboard_ui.jsx`, `frontend/tests/v10_2_dashboard_ui.mjs`, `tests/test_v10_2_dashboard.py`, this report.

## Git: exact blocker, no publication

The existing `.git` file still contains:

```text
gitdir: /var/tmp/v95-recovery/repo.git
```

That directory does not exist. Root, branch, remote and status probes all fail:

```text
fatal: not a git repository: /var/tmp/v95-recovery/repo.git
```

The bounded search for an existing valid repository/worktree reference found none. No Git metadata was recreated or repaired. A Git diff/index review, staging, commit and push could not safely be performed; source-level review and the baseline hash audit were used to verify scope instead.

The existing authenticated read-only `ls-remote` check of the known repository URL returned:

```text
b2e31a301d2532d076725e4191f7115393dbf8f8  refs/heads/main
```

This is the published main SHA, **not** a V10.2 dashboard commit. Actual local branch and configured remotes cannot be verified from the missing metadata. Requested commit title `V10.2 optimize dashboard and enable manual testing` remains **uncreated**. No stage/commit/push, force push, reset, clean or history replacement was attempted.

## Windows update and manual launch

There is currently **no dashboard commit to pull**. These read-only/check commands are safe from CMD:

```bat
cd /d E:\evolutionary-trading-lab
git rev-parse --show-toplevel
git branch --show-current
git status --short
git remote -v
git fetch origin
git log -1 --oneline origin/main
```

Only after the actual dashboard commit has been published and verified, and only when the Windows tree is clean and already on `main`, update with:

```bat
git pull --ff-only origin main
```

Do not reset, clean, stash or overwrite unrelated local changes. If the tree is dirty or the fast-forward fails, stop rather than forcing it. A fetch/pull now does not install these unpublished dashboard changes.

Once the verified source is actually present on Windows, launch the existing guarded application:

```bat
cd /d E:\evolutionary-trading-lab
call start.bat
```

The launcher verifies the database and source/build identity and rebuilds a stale frontend before serving port 8787. Do not bypass a failing database/native guard or delete DB sidecars/evidence. Launch success is not asserted here.

Manual checks, using existing research rather than starting a new campaign:

1. Overview: compare persisted/target/remaining and execution state to backend status; observe paused/stopped/running only when that state genuinely exists. Confirm five-minute throughput is not labelled instantaneous speed.
2. Resource cards: configured/effective versus reported active/admitted counts are separate. DeepTest observed busy processes show N/A. GPU probe/device activity must not claim application acceleration.
3. Open a stored completed DeepTest with at least 3,001 trades. Check symbol/timeframe and requested/actual ranges. Wait for complete-load confirmation, clear exclusions/search, sort by trade number ascending and jump to page 301. Verify trade 3001 and compare complete CSV row count with the persisted total.
4. Open a node from both navigation paths. Check results, genome, lineage/children, export and complete DeepTest trades. Open rerun configuration without submitting it. Verify unsupported Run Validation is disabled/explained, not a fake successful action.
5. Settings: confirm default DeepTest processes remains 1 unless explicitly previously changed. Do not change CPU targets, quotas, broker/account or risk settings merely to make a test run faster. Any opt-in process trial still requires native validation and explicit execution approval.

## Validation-log receipts

Full artifacts are external and may not survive workspace restoration. SHA-256 receipts from the final run:

- Regression log: `8ec7748e69c2319dff41e044ace63292169e4194f79b00c605148280cc4e072d`
- JUnit: `d39a34c12b0afecb0ddb719db93b5511fc5cdafa2564ab353cdea69a5792b380`
- Frontend harness log: `10dd4981d22a57552f15e08a286a9e165e673059fb8b57aaa9a7f55615503e1e`
- Build log: `a2c50e6c7e6f470d2f398cfad27205e225b24fa9c005777a7215a50d1bbb1bac`
