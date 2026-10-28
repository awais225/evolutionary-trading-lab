# V10.2 — dashboard and manual testing

Publication branch: `v10.2-dashboard-manual-testing`.
Base: `b2e31a301d2532d076725e4191f7115393dbf8f8` (`main`).
Commit message: `V10.2 optimize dashboard and enable manual testing`.

This release was assembled in a separate authenticated clone of the existing repository, not by repairing the original missing Git metadata. The current implementation and all 61 protected evidence files were preserved. No database, credential, runtime cache, generated frontend bundle, old verification log or unrelated benchmark adapter was added to the commit.

## Included implementation

- DeepTest execution is data-bounded: no artificial 3,000-trade ceiling or replacement cap. The generic research-engine default remains unchanged. Eligible requested bars, indicator warmup, strategy exits and persisted accounting remain authoritative.
- Complete trade-history retrieval, 500-row transport chunks, complete-population checks, global search/sort/filter, display page jump and full saved-result CSV export.
- Backend optimizations: existence-only dedupe queries, reused population snapshots/combined aggregates, unchanged-revision write suppression, schema-proof caching instead of repeated DDL, heap-based active-exit occupancy with inclusive exit-bar semantics, and optional timing instrumentation disabled by default.
- Default serial historical execution; explicit bounded process parallelism, memory admission, cancellation, shutdown and complete parent-owned persistence. Process workers do not initialize brokers or own authoritative DB writes.
- Dashboard generation/throughput/resource status, truthful GPU scope, DeepTest admission telemetry, stable nonoverlapping polling, visible failures and node result/trade/genome/lineage/export/backtest configuration access.
- Standalone per-node Run Validation remains explicitly unavailable; existing validation records can be viewed. No fake endpoint or success state was introduced.
- Existing regression assertions preserved; new V10.2 processing, accounting, cancellation, lifecycle and dashboard regressions included.

Older unrelated benchmark-only activity/orchestrator adapters were not transferred. In the evolution file, their pre-existing optional benchmark hooks were separated from the intended V10.2 performance edits by a three-way source comparison; normal RNG/selection semantics are covered by the regression fixtures. Original working files were not modified.

## Fresh validation in the publication clone

- Frontend build: **PASS**, 6.53 seconds. Existing >500 kB chunk warning remains.
- Nine deterministic frontend harnesses: **PASS**. Dashboard 8/8; V10 6/6; V4.7 35/35 renders; V4.8 54/54 renders; V6.5.1 16/16; V9.1 6/6; V9.2 6/6; V9.3 11/11; V9.5 5/5.
- Requested eight existing suites plus V10.2 processing and dashboard backend suites: **240 passed, 3 failed, 0 skipped**, 243 cases, 48.87 seconds.
- All **48 V10.2 processing tests** passed, including actual opt-in process execution, full synthetic M1/M5 pipeline persistence/API/CSV and >3,000-trade correctness.
- All **18 new dashboard backend tests** passed.
- The same three failing tests were rerun on untouched base source in a separate isolated environment and failed there too: nonempty population, nonempty Deep plan, and stored node/ranking-history prerequisites (`test_22_api_populations_endpoint_matches_the_module`, `test_23_api_deep_plan_exposes_the_union_and_progress`, `TestNodeDataAuthority::test_node_718_identity_metrics_and_true_ranking`). No assertions were weakened and no fake research population was supplied. The full suite is not claimed green.

Unlike the earlier local validation record, this clone retained remote main's existing 100% CPU target, which permits two effective workers here. No CPU configuration file or quota was changed for this release. `deep_test_workers` still defaults to **1**; no process pool starts merely from telemetry.

All test runs selected external isolated DATA/TEMP paths. No generation or backtest was run against authoritative research data; campaign budget use was zero. Synthetic correctness is not real-market throughput evidence. Native Windows/Python 3.14.6, MT5, real-market performance and application CUDA execution remain **unverified**. Existing native preflight evidence remains unchanged, not declared repaired. `V10_2_DASHBOARD_VALIDATION.md` is the earlier, historical local validation record; this section supersedes its publication/test summary.

## Windows update and launch

After verifying the published commit, from CMD and a clean checkout already on `main`:

```bat
cd /d E:\evolutionary-trading-lab
git status --short
git fetch origin
git pull --ff-only origin main
call start.bat
```

If main integration is unavailable, inspect the feature branch instead (only if it is not already a conflicting local branch):

```bat
git fetch origin
git switch --track origin/v10.2-dashboard-manual-testing
call start.bat
```

Stop if the working tree has unrelated edits, the fast-forward fails or the branch conflicts. Do not reset/clean/force or overwrite work. The existing launcher verifies database/build identity and rebuilds stale frontend output. Do not bypass a failing native/database guard.

Observe existing generation state without starting a new campaign. For a saved DeepTest with over 3,000 trades, verify pair and actual/requested range, wait for complete history, clear filters, sort by trade number ascending and inspect page 301 / trade 3001. CSV must contain the complete persisted result. Node rerun configuration must not execute until explicitly submitted. Native tests or any new research campaign require separate explicit approval.
