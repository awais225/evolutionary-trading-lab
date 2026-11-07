# ISSUE-055 — UNIVERSAL TABLE SORTING + MARKET DATA INTEGRITY INVESTIGATION

(Verbatim prompt archived from the task assignment; formatting normalized.)

Two objectives: (1) consistent, persistent sorting on every applicable
table; (2) investigate the latest market-data fetch incl. the 15 unresolved
CSV-vs-MT5 conflicts and recorded gaps. Manual Nodes deletion already
works; do not reimplement. No DeepTesting implementation.

PART A — UNIVERSAL TABLE SORTING. Audit every table (Manual Nodes, DATA
historical, DATA node table, WWW Research, Interactive, DeepTesting if any,
Fine Tuning, Live Testing, LOG, others). Shared reusable sorting. Defaults
by type: numeric metrics desc; profit/return highest first; drawdown by
ABSOLUTE monetary magnitude (-500 before -100); win rate/trade counts desc;
names A-Z; IDs natural numeric ascending (C2 before C10); dates newest
first; text categories A-Z. Two-state click (first = default direction,
second reverses, then alternates), visible indicator, no menus. Correctness:
whole dataset not visible page; works with pagination/filters/search; no
record mutation; stable; null/missing/N/A consistent, never zero; numerics
compared numerically; natural IDs; selection/bulk/row actions unaffected.
Persistence per table across tab switches and restarts via existing
preference architecture. Dark/amber theme, no header widening, no page
horizontal scroll. Tests: numeric desc/asc, negatives, drawdown abs,
alphabetical, natural IDs, dates, nulls, filters, pagination, tab
persistence, restart persistence, selection/bulk after sorting.

PART B — INTEGRITY. Canonical DB E:\...\market_data.db: 447,237 candles,
7/7 TFs, 2020-09-30 21:00 UTC .. 2026-11-06 23:56 UTC, last MT5 update
2026-11-07 14:46:19 UTC; gap counts M1 90, M5 387, M15 1121, M30 1578,
H1 1576, H4 328, D1 322; latest fetch FETCH-20261107-144617-93858F.
15 unresolved conflicts (M1/M5/M15/M30/H1 x3), stored = CSV imports,
incoming = MT5 ICMarketsSC-Demo, stored preserved; never auto-overwrite.
Investigate CSV import/timestamp parsing/timezone/open-vs-close, MT5
copy_rates_range interpretation, UTC conversion, broker server time,
symbol identity, provenance, broker/provider difference, consistent
shift, adjacent-candle correspondence; compare candles before/during/
after. Neither source assumed correct. Classify: different feeds,
revisions, timezone/boundary conventions, instrument specs, incorrect
imports, other. Keep Stored / Accept MT5 per record; propose (do not
apply) bulk rule only if a reliable source-level rule is established.
Gaps: classify Expected closure / Confirmed missing in open session /
Unverified / Invalid alignment using sessions, weekends, holidays,
trading hours; weekend/session closures must not be flagged missing;
never fill; separate confirmed-missing summary. Status semantics: why
"MT5 FETCH required" while 7/7 available + recent update; separate
availability, coverage, latest fetch, conflicts, missing candles, genuine
fetch necessity; fix the underlying logic, not just the label.
Backtesting readiness report (B7): history sufficiency, gaps in range,
conflicts, source used, indicator feasibility without synthetic data,
range suitability. No DeepTesting implementation.

PART C — Regression: table rendering, sorting everywhere, pagination/
filters, persistence, node selection/deletion, Duplicate & Edit, DATA tab
+ canonical DB, MT5 connectivity, fetch logging. Never claim Windows MT5
verification unless actually run there. Update CHATGPT.txt, CHANGELOG.md,
docs/ISSUES_AND_FIXES.md, docs/ARCHITECTURE.md if needed,
docs/IMPLEMENTATION_PROGRESS.md, docs/PROMPTS. Commit + push main. Do not
touch legacy-archive. No release tag. Final report (10 items). Priority:
data integrity first, universal sorting second.

---

## OUTCOME (ISSUE-055)

Part B: gap classifier added (classify_gaps): intervals overlapping a
Saturday = EXPECTED_CLOSURE, weekday evening breaks (same-day, >=20:00
UTC, <=4h) = EXPECTED_SESSION_BREAK, everything else UNVERIFIED with an
estimated missing-candle count (holidays are honestly unverified - no
broker calendar is available). On the Linux baseline copy this reduces
e.g. D1 "319 gaps" to 315 weekend closures + 4 unverified, M30 1563 to
315 weekends + 1203 nightly breaks + 45 unverified. "MT5 FETCH required"
root cause: it fired whenever the latest stored candle was not the
current bar - availability/freshness conflation; now availability,
last-MT5-update, up_to_date/missing_completed_candles (completed bars
only) and unresolved conflicts are separate summary facts, and the chip
reads "MT5 update available" with explanatory wording. data_quality()
endpoint (GET /market-data/quality) returns READY/CAUTION/NOT_READY with
reasons for a timeframe/range. The 15 Windows conflicts remain UNRESOLVED
(their values live on the Windows install; per-record Keep/Accept shipped
in ISSUE-054). Extra local evidence: sampled M1->M5 aggregation of the
CSV baseline is exact (6/6 complete samples), so the CSV side has no
internal timeframe shift; CSV-vs-MT5 root cause remains officially
unknown pending Windows values. Bulk acceptance NOT implemented (no
reliable source-level rule established).

Part A: shared apps/web/src/lib/tableSort.ts (number/absNumber/natural/
date/text comparators, missing-always-last, stable, non-mutating,
first-click-default two-state toggling) + SortHeader; persisted per table
in localStorage (hero1.sort.<id>) like the other preferences. Applied to
Manual Nodes, DATA node dashboard, DATA market-data rows, and LOG
(server-side sort= param on /api/v1/events, applied before pagination;
persisted client pref). No metric/drawdown tables exist yet - the
absNumber comparator ships tested for them.

Tests: backend 227 (7 new), frontend 66/66 (9 new), build clean. Live:
gap analysis + quality endpoint verified on the canonical DB (419,013,
integrity ok). Windows MT5 NOT VERIFIED.
