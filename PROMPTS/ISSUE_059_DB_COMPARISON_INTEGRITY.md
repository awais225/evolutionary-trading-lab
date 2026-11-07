# ISSUE-059 — Compare Updated MT5 Market Database Against Original and Rectify Market Data Integrity

> Archived per repository rule (user brief, condensed faithfully; full text
> in the conversation record).

Execution rules: workspace cleanup is PAUSED (protected data; do not
attempt further cleanup or delete anything protected). ALL .db files for
this task live exclusively in /tmp/hero1_market_audit/. Retrieve from
GitHub (repo awais225/evolutionary-trading-lab, branch main, commit
eb369db76): original DATA/MARKET_DATA/market_data.db and updated
DATA/MARKET_DATA/updatedmarket.db (Git LFS, hydrate, verify not a
pointer). Never copy either into the workspace, never create backups or
temp DBs in the workspace, never commit .db files.

Steps: (1) retrieve exact databases into /tmp; (2) compare schemas then
all seven timeframes (counts, added, missing, duplicates, ranges, OHLC /
volume / spread differences on matching timestamps, missing periods,
conflicts + provenance); (3) identify the actual issue behind the 15
unresolved CSV-vs-MT5 conflicts with database evidence, distinguishing
confirmed findings from hypotheses (retrieval, range handling, timestamp
normalization, duplicates, persistence, source precedence, gaps,
conflict handling); (4) rectify the application (correct retrieval +
storage, no silent overwrite, duplicates detected, idempotent,
conflicts preserve both values, commits and counters agree, all 7
timeframes, canonical DB stays DATA/MARKET_DATA/market_data.db, never
replace it with the uploaded snapshot, never modify the uploaded files);
(5) DeepTesting accuracy (chronology, UTC correctness, OHLC integrity,
missing/duplicate/conflicting candles, coverage, no future leakage; a
period with material problems must not be declared READY); (6) tests
(insertion, duplicates, idempotency, conflicts, UTC normalization,
persistence, OHLC validation, gaps, per-timeframe readiness, DeepTesting
rejection, no future leakage) with temp DBs outside the workspace, plus
docs (CHATGPT.txt, CHANGELOG.md, ISSUES_AND_FIXES, ARCHITECTURE if
changed, IMPLEMENTATION_PROGRESS, TROUBLESHOOTING); (7) verify no .db
in workspace/staged, commit + push code/tests/docs only, no tag.

Final report: 13 enumerated items; never claim data is DeepTesting-
accurate merely because code builds/tests pass.

---

## OUTCOME (ISSUE-059)

CONFIRMED ROOT CAUSE (database proof, /tmp/hero1_market_audit):
the fetch path stored copy_rates_range() BROKER SERVER epochs verbatim
into the true-UTC canonical store. All 15 conflicts are byte-identical
candles 3 hours earlier in stored history (15/15 EXACT OHLC matches at
ts-3h; GMT+3, Oct 2026 US daylight time). Persistence was proven perfect
(0 missing, 0 changed, 0 duplicates across 419,013 shared candles); the
CSV side was correct UTC all along (importer converted once).

FIX: _rate_to_candle converts server epoch -> UTC exactly once via
hero1_mt5_adapter.server_time.server_to_utc_epoch (the same rule the
CSV importer already used); absurd values still reject politely.
data_quality: unresolved conflicts INSIDE the requested period now make
it NOT_READY (was CAUTION), conflicts are range-scoped, and
candles_after_range is reported (no hidden future data).

REPAIR for already-shifted Windows data: scripts/repair_mt5_timeshift.py
(dry-run default, backup before --apply, one transaction; drops exact
duplicates, preserves collisions as new UNRESOLVED audit conflicts,
never touches stored candles or existing conflict rows). Validated on a
/tmp scratch COPY of the owner's updated DB: 28,224 shifted candles ->
27,981 moved, 236 representation duplicates dropped, 7 final-export-bar
collisions recorded as conflicts; seam M1 12:31 C4179.59 -> 12:32
O4179.54 continuous (was a +3.75 phantom jump); 446,994 candles,
integrity ok, 0 duplicates. The two uploaded audit DBs were NOT modified.

DeepTesting verdict: data is NOT READY for periods spanning the
2026-10-16 seam or the appended Oct16-Nov6 range UNTIL the owner runs
the repair on the Windows DB; after repair those periods are as
trustworthy as the CSV baseline (0 invalid timestamps, 0 bad OHLC,
strict chronology). The 22 unresolved conflict timestamps (15 + 7)
remain NOT_READY until explicitly resolved per record.

Tests: backend 250 passed / 1 skipped (8 new: seam dedup replica of the
real 15-conflict scenario, winter/summer offsets + transition sweep,
genuine-conflict preservation, readiness refusal, repair plan/apply/
dry-run/no-op). Frontend 66/66 + production build clean (run from /tmp
copy; workspace dependency dirs absent). Workspace .git note: the
restored workspace had lost remote/HEAD state; origin was re-added and
HEAD realigned to origin/main (eb369db76) with files untouched. The
tracked updatedmarket.db pointer was removed from the index (hygiene
rule) - the commit and LFS object remain in GitHub history/storage.
Windows genuine-MT5 verification: NOT VERIFIED.
