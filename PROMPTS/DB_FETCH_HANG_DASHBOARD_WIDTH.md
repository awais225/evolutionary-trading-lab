# ISSUE-053 — CRITICAL DATABASE FETCH HANG INVESTIGATION, DASHBOARD WIDTH FIX & FETCH RECOVERY

(Condensed from the session memory — the full verbatim prompt text was not
preserved across context compaction; this records the task requirements
faithfully as given.)

Root-cause fix, not UI-only patch; do not spend time on MT5 connectivity or
the fetch button without new evidence. Evidence: Windows ops
FETCH-20261107-094926-639B45 (ALL) and FETCH-20261107-095048-7883B5 (single
M1) both received 21,337 M1 candles, 0 invalid, then stopped after
DATABASE_TRANSACTION_STARTED with no insert/commit/rollback event; app then
returned 500s.

1. Preserve state: record branch/SHA; inspect tree, market-data service,
   recent fixes, TROUBLESHOOTING; preserve DB/candles; verified backup before
   write tests; record per-timeframe counts. Never delete/recreate the
   canonical DB, change CSV sources, node generation, or lineage.
2. Fine-grained DB instrumentation at real boundaries
   (DATABASE_CONNECTION_ACQUIRED, EXISTING_CANDLE_LOOKUP_*, comparisons,
   NEW_RECORDS_IDENTIFIED, INSERT_BATCH_*, DATABASE_COMMIT_*,
   POST_COMMIT_COUNT_VERIFICATION, DATABASE_CONNECTION_RELEASED) with
   operation ID, instrument, timeframe, elapsed ms, records processed, DB
   path, transaction state. Periodic progress for long stages; never
   per-candle events; never log a stage complete before it completes.
3. Investigate SQLite locking/performance on the real implementation: lock
   holders, open transactions, cross-thread connection sharing, per-candle
   queries, repeated full scans, nested-loop comparisons, indexes, event-loop
   blocking, swallowed exceptions. 21,337 candles must not need 21,337
   full-table searches; set-based/batch approach compatible with the existing
   schema; inspect journal mode/busy timeout before assuming WAL.
4. Safe recoverable writes: exact duplicates / genuine conflicts / valid new;
   conflicts preserved and never block unrelated valid candles; append valid
   new; commit; verify counts; release connections/locks. On failure: safe
   rollback, exact stage logged, records actually committed stated, operation
   FAILED/PARTIAL accurately. Never report UPDATED when nothing committed.
5. Capture the actual 500 exception/stack; check DB locked/busy-timeout/
   connection misuse/SQL/schema/rollback/serialization/event-loop causes.
   Backend stays responsive during fetch; LOG and other tabs accessible.
6. Controlled M1 test first (real stored DB + real MT5 on Windows; do not
   fabricate records or use a temporary replacement DB for the acceptance
   test): before/after count+latest, received/duplicates/conflicts/new/
   inserted, per-stage times; verify DB directly; restart persistence; only
   then ALL-timeframe.
7. Dashboard horizontal scroll fix: find the actual overflow source; no
   page-level horizontal scrollbar; dashboard fits viewport; node table fits
   container; long strings wrap/truncate with a way to inspect full value; no
   global overflow hiding; table scrolling confined to the table. Test DATA
   at 1280/1366/1920px; check other tabs for regressions; keep dark/amber
   theme.
8. Acceptance: fetch/DB, logging and layout checklists; run backend tests,
   frontend tests, production build. Never claim Windows/MT5 passed unless
   run on Windows with MT5.
9. Record starting SHA; only required fixes; update TROUBLESHOOTING.txt,
   CHATGPT.txt, CHANGELOG.md, docs/ISSUES_AND_FIXES.md, docs/ARCHITECTURE.md,
   docs/IMPLEMENTATION_PROGRESS.md, PROMPTS/ exact prompt; commit/push main,
   verify remote SHA; do not modify legacy-archive; no release tag until
   verified. Final report (16 items). Do not close if logs still stop at
   DATABASE_TRANSACTION_STARTED.

---

## OUTCOME (ISSUE-053)

Root cause proven by profiling: `market_data_service.py` evaluated
`_stored_ts_set(...)` (a full `SELECT ts FROM candles`) once per incoming
candle inside the write transaction — 0.069s x 21,337 ~ 24.4 min for the
owner's M1 fetch; the transaction never committed, and the dangling write
lock surfaced as 500s on other tabs. Fixed: stored timestamps are read once
before the transaction; classification uses that single set; inserts run as
one `executemany` batch; the store now opens WAL + `busy_timeout=30000` +
`synchronous=NORMAL`. Fine-grained DB stages with elapsed ms are emitted
(EXISTING_CANDLE_LOOKUP_COMPLETED, DUPLICATE/CONFLICT_COMPARISON_COMPLETED,
NEW_RECORDS_IDENTIFIED, INSERT_BATCH_STARTED/COMPLETED, DATABASE_COMMIT with
transaction_elapsed_ms).

Controlled test on a copy of the real DB with 21,337 mocked M1 rates:
21,335 appended, 2 duplicates, 0 conflicts, 0.88s total; M1 100,057 ->
121,392; integrity ok; zero duplicate timestamps. Canonical DB untouched
(419,013 candles, integrity ok). Regression test
`test_large_fetch_is_not_quadratic` added. Dashboard overflow fixed at the
market-data grid/meta tracks (minmax(0,1fr), min-width:0, overflow-wrap);
frontend 51/51, build clean; backend 206 tests, 0 failures, 1 skipped.
Genuine Windows MT5 fetch (M1 then ALL) remains NOT VERIFIED — owner's test.
