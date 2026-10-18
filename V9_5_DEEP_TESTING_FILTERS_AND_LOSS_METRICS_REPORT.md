# V9.5 — current acceptance and implementation publication complete; cleanup/reset pending

Evidence date: 2026-10-18. Existing V9.5 continued, not rebuilt. No acceptance run was concurrent with another memory-intensive acceptance suite.

## DeepTesting and metrics
Coverage classification is factual and independent of stored consent. Full-only excludes partial even after previous partial consent; unavailable bars never execute. Identical pending batch requests deduplicate. Loss sequences use eligible trade ordering, negative runs, currency amounts and explicit unavailable/malformed populations, not a count-driven rule change. Disclosure, master/indeterminate/global controls and stored selections were checked in jsdom and native Chromium; the latter used synthetic history.

## Verified current evidence
- Maintained suite: **1325 passed, 0 failed, 1 pre-existing conditional skip**, 24/24 groups, no deselections. Skip: this installation carries no simulator dataset. No skip was introduced.
- Separate focused active-run recovery proof: **2 passed**; the same assertions fail twice against the published baseline. Do not add overlapping diagnostic reruns to totals.
- Archived comparison: all 31 files each completed; baseline **188 passed / 44 failed**, current **190 passed / 42 failed**. Those 42 are still failures, not an allow-fail PASS: 27 paired pre-existing, 2 historical-artifact expectations, 9 environment/fixture issues, 4 justified run-identity/recovery contracts. Each case, paired trace and rationale is in `verification/V9_5_CURRENT_ARCHIVE_CLASSIFICATION.json`; unresolved = 0.
- Native headless Chromium: 5 checks, no page errors. Actual components + HTTP/SQLite batch engine, **synthetic coverage and results**; not full-dashboard or real-market browser acceptance. Includes all three decisions, prior-consent full-only/partial behavior, unavailable history, retained disclosure/selection, duplicate submissions, range change, and zero-order live review.
- Real normalized-store engine/cache: M1 and M5 completed backtests; M15 truthfully `INSUFFICIENT_HISTORY` (276 < 300). No threshold reduction, synthetic bars, or CSV rereads. The 3-calendar-day window is anchored to latest persisted history for this benchmark, not claimed as current-clock coverage.
- Cold startup: first health 2.481 s, ready 19.352 s. Warm: ready 1.162 s. All seven checked APIs HTTP 200, original strategy statuses unchanged, active qualified 83 (observed, not a constant). Product release V9.5 is separate from frozen engine/schema version 3.6.
- Current frontend build exit 0. Exact commands, per-group exits/counts/RSS/OOM/owned-worker receipts: `verification/V9_5_CURRENT_GATE_RESULTS.json`.

## Safety and pending work
Implementation normal-pushed and remote-verified at 274bb151bd8bfc3a3528bbafec4c4b8bd6e1a9c9. Separate cleanup publication and clean-checkout checks are pending. No actual node reset or automatic active generation. Actual recovered DB remains 10000 strategies and all logical tables exactly match the pre-test SQLite backup. A verification connection checkpointed the pre-existing WAL into the base file: original base SHA c159b0718c43b43f97609881bd3be196dad76274029949a9fb4c4e710f106a12, checkpointed base SHA 87ed8c781db1be4bd469a5d1348c995c2ccaaf66e76aae3c7b8ebe7f918327c7; no SQL row changed. All canonical market partitions match the retained 419013-bar manifest; seven uploaded CSV originals remain verified in the durable upload archive. Temporary copies are not actual reset backups.

Required order: implementation audit/normal push/remote verification → separate precise tracked-node cleanup, mixed-file preservation and clean checkout → idle-worker verified backed-up actual reset, SQL/API/restart checks. No Git DATA symlink deletion has been staged as cleanup. Native Windows/Edge/FTMO/MT5 and live/demo broker execution remain unverified; no broker orders were submitted.

## Prepared targeted Git cleanup (not an actual runtime reset)
Verified isolated publication images remove 29555 tracked node/result/state/SQLite sidecar paths and rewrite five mixed payloads (primary SQLite plus four older backup ZIPs). All protected SQL rows and non-node archive member bytes were compared with originals. All 12400 result Parquets were individually schema-read: 6200 equity and 6200 trade tables, no OHLCV. Seven original CSVs/canonical bars are outside deletion scope. The source/config known-good backup ZIP is retained unchanged. Active SQL remains 10000; actual node files have not been removed.

## Canonical cleanup reconciliation
Cleanup initially preserved SQL strategy high-watermark 10787, but independent content parsing of 10809 filesystem strategy records found canonical ID 10814. A separate normal-push data-only correction raises the empty publication SQLite sequence to 10814, never reuses those historical canonical IDs, and changes no source, thresholds or tests. Actual reset must likewise preserve max(SQL sequence, persisted canonical filesystem IDs) = 10814. No active node has yet been deleted.
