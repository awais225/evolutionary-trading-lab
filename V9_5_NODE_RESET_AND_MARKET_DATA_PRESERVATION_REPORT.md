# V9.5 — current acceptance complete; publication, cleanup and actual reset pending

Evidence date: 2026-10-18. Existing V9.5 continued, not rebuilt. No acceptance run was concurrent with another memory-intensive acceptance suite.

## Actual reset is not yet performed
Original active physical runtime: /var/tmp/v95-recovery/data-archive/DATA/DATABASE/lab_state.db (repository DATA is an external directory link). Direct SQL = 10000, quick_check OK, SHA unchanged. Earlier 0→10809 scratch reset/restore was only isolated evidence. No actual before/after-zero API or normal-launcher resurrection proof exists yet. Permanent original upload/preservation archives remain outside the repository; market data, metadata, timezone, credentials, valid preferences and unrelated history are protected. A new timestamped readable/reconciled backup must precede the actual reset after publication and clean-checkout verification.

## Verified current evidence
- Maintained suite: **1325 passed, 0 failed, 1 pre-existing conditional skip**, 24/24 groups, no deselections. Skip: this installation carries no simulator dataset. No skip was introduced.
- Separate focused active-run recovery proof: **2 passed**; the same assertions fail twice against the published baseline. Do not add overlapping diagnostic reruns to totals.
- Archived comparison: all 31 files each completed; baseline **188 passed / 44 failed**, current **190 passed / 42 failed**. Those 42 are still failures, not an allow-fail PASS: 27 paired pre-existing, 2 historical-artifact expectations, 9 environment/fixture issues, 4 justified run-identity/recovery contracts. Each case, paired trace and rationale is in `verification/V9_5_CURRENT_ARCHIVE_CLASSIFICATION.json`; unresolved = 0.
- Native headless Chromium: 5 checks, no page errors. Actual components + HTTP/SQLite batch engine, **synthetic coverage and results**; not full-dashboard or real-market browser acceptance. Includes all three decisions, prior-consent full-only/partial behavior, unavailable history, retained disclosure/selection, duplicate submissions, range change, and zero-order live review.
- Real normalized-store engine/cache: M1 and M5 completed backtests; M15 truthfully `INSUFFICIENT_HISTORY` (276 < 300). No threshold reduction, synthetic bars, or CSV rereads. The 3-calendar-day window is anchored to latest persisted history for this benchmark, not claimed as current-clock coverage.
- Cold startup: first health 2.481 s, ready 19.352 s. Warm: ready 1.162 s. All seven checked APIs HTTP 200, original strategy statuses unchanged, active qualified 83 (observed, not a constant). Product release V9.5 is separate from frozen engine/schema version 3.6.
- Current frontend build exit 0. Exact commands, per-group exits/counts/RSS/OOM/owned-worker receipts: `verification/V9_5_CURRENT_GATE_RESULTS.json`.

## Safety and pending work
No implementation commit/push or cleanup commit/push yet. No actual node reset or automatic active generation. Actual recovered DB remains 10000 strategies and byte-identical to original LFS SHA c159b0718c43b43f97609881bd3be196dad76274029949a9fb4c4e710f106a12. All canonical market partitions match the retained 419013-bar manifest; seven uploaded CSV originals remain verified in the durable upload archive. Temporary copies are not actual reset backups.

Required order: implementation audit/normal push/remote verification → separate precise tracked-node cleanup, mixed-file preservation and clean checkout → idle-worker verified backed-up actual reset, SQL/API/restart checks. No Git DATA symlink deletion has been staged as cleanup. Native Windows/Edge/FTMO/MT5 and live/demo broker execution remain unverified; no broker orders were submitted.
