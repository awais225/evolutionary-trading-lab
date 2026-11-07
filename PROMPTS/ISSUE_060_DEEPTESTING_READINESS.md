# ISSUE-060 — Finalize Market Data Readiness for Practical DeepTesting

> Archived per repository rule (user brief, condensed faithfully).

Goal: existing data usable + trustworthy for DeepTesting/statistics;
usable beats perfect. Do NOT re-run repair_mt5_timeshift.py --apply
without proving it has not already run; never modify the canonical DB.
User's Windows dashboard: 446,994 candles, 7/7 TFs, 2020-09-30..
2026-11-06, 7 unresolved conflicts, 158 unverified gaps (~2,815 est).

Verify first: repair already applied? timestamps consistently UTC?
duplicates? OHLC validity/chronology? what are the 7 conflicts? do the
158 gaps materially affect backtests? If the current Windows DB is not
in LMS, state that limitation.

Readiness rules: BLOCK only for material problems in the SELECTED
instrument/timeframe/period (bad timestamps, duplicates, invalid OHLC,
non-chronology, differing-OHLC conflicts inside the period,
insufficient coverage). WARN (not block) for small/unverified gaps
(possibly holidays/breaks), conflicts outside the period, metadata-only
diffs (tick_volume/volume/spread), small boundary imperfections. Never
fabricate/fill. Conflicts: identify the 7 exactly; never blindly accept
incoming; canonical retained where established; ambiguous candle inside
a selected test blocks that range, not the database.

Transparency: per-test summary (instrument, timeframe, actual range,
candles used, excluded candles, unresolved conflicts in range,
unverified gaps in range, READY / READY WITH WARNINGS / BLOCKED).
Preserve all strategy/indicator/sizing/metric logic; data-readiness
scope only. Protect DATA; audit copies only in /tmp; preserve untracked
apps/web/127.0.0.1 and `backend)`. Tests for the 9 enumerated cases;
run suites + build; commit code-only to origin/main; 8-item report.

---

## OUTCOME (ISSUE-060)

Verification (evidence rebuilt in /tmp/hero1_market_audit): the owner's
numbers reproduce EXACTLY from the ISSUE-059 repair simulation
(446,994 candles; 158 unverified intervals; ~2,815 estimated candles),
so the repair HAS been applied on Windows; the 7 remaining unresolved
conflicts are exactly the repair-generated final-export-bar collisions
(D1 2026-10-15 21:00, H4 10-16 09:00, M15 10:45, H1 12:00, M5/M30 12:30,
M1 12:31 UTC), canonical CSV values retained, incoming preserved. The
current Windows DB file itself is not in LMS (stated limitation);
repaired-mirror checks: all 7 TFs strictly chronological, 0 duplicate
timestamps, 0 invalid OHLC/timestamps. The 158 gaps are minor and
range-dependent (largest = holiday-scale ~28.5h; ~0.6% of candles).

Readiness (data_quality v2): three states READY / READY_WITH_WARNINGS /
BLOCKED, scoped per instrument+timeframe+requested period. BLOCKS:
coverage shortfall, no candles in period, duplicate timestamps, invalid
timestamps (incl. ts/ts_iso mismatch), invalid OHLC, unresolved
conflicts whose differing fields include OHLC inside the period.
WARNS: unverified gap intervals; conflicts differing only in
tick_volume/volume/spread. Report adds checks{}, blocked_reasons,
warnings, candles_used, excluded_candles, candles_before_range.
Frontend: compact DeepTesting readiness chip for the selected timeframe
in the DATA panel. Live evidence on the repaired mirror: seam week
BLOCKED (conflicts), clean 2024 H1 READY_WITH_WARNINGS (2 small gaps,
1,332 candles usable). 9 new regression tests; backend 259+ and
frontend 66/66 + build clean; Windows NOT VERIFIED.
