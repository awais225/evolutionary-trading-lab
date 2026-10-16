# V9.3 — data import, bar-count initialization and MT5 latest audit

## Source and baseline preservation

All seven uploaded files were compared byte-for-byte with `DATA/HISTORICAL/baseline`; SHA-256 values below match the versioned originals. Existing LFS conventions continue to version exact CSVs, operational partitions and intended metadata. No original, normalized partition or canonical DB content is staged as a V9.3 data change. No artificial bars, OHLC filling, baseline regeneration, reset or automatic fetch-to-Git push was performed.

The user already explicitly confirmed IC Markets server interpretation: UTC+2/+3 using historical US DST / NewYork close, represented as `ICMARKETS_NY_CLOSE`. This confirmation is not presented as conclusive inference from CSV gaps or filenames. The validator receives explicit XAUUSD/TF mapping plus this confirmed timezone, checks actual schema/content/spacing, normalizes once to UTC, and rejects ambiguous/nonexistent local times rather than guessing. Native MT5 epoch timestamps stay UTC and are not broker-shifted twice. Existing safeguards require revalidation when timezone changes; a stale token cannot be silently reinterpreted at commit.

## Actual full-file pipeline verification

Executed on an isolated COPY of the actual normalized store and a SQLite backup of the original **logical DB including committed WAL state**. Raw logical status counts begin QUALIFIED83, RETIRED4740, FAILED3843, KILLED1289, BACKTEST_ERROR45. Initialization/restoration uses actual preserved research; qualified authority is99 before tab access and remains99 after all imports and Deep. No private acceptance result is substituted for the versioned production DB.

For each file: exact source selection → full HTTP raw-body validation with explicit pair/timezone → explicit commit → original-byte archive readback → persistent dedupe merge → actual MasterStore query → actual DataEngine materialization/query → fresh interpreter reopening. All rows were validated, not a preview/sample. The existing store already contains these originals, so this repeat audit correctly reports **new0, updated0, conflict0, reject0; all rows retained as identical duplicates**, not a fabricated fresh ingestion claim.

| File / TF | Read = valid | Rejected | File duplicates | New | Updated | Identical retained | Conflicts | Queryable after restart | UTC first → last | Gap observations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
|XAUUSD_M1.csv / M1|100057|0|0|0|0|100057|0|100057|2026-07-07T18:41:00Z → 2026-10-16T12:31:00Z|75|
|XAUUSD_M5.csv / M5|100012|0|0|0|0|100012|0|100012|2025-05-21T07:35:00Z → 2026-10-16T12:30:00Z|372|
|XAUUSD_M15.csv / M15|100856|0|0|0|0|100856|0|100856|2022-07-12T22:00:00Z → 2026-10-16T10:45:00Z|1106|
|XAUUSD_M30.csv / M30|71441|0|0|0|0|71441|0|71441|2020-09-30T22:00:00Z → 2026-10-16T12:30:00Z|1563|
|XAUUSD_H1.csv / H1|35738|0|0|0|0|35738|0|35738|2020-09-30T22:00:00Z → 2026-10-16T12:00:00Z|1561|
|XAUUSD_H4.csv / H4|9349|0|0|0|0|9349|0|9349|2020-09-30T21:00:00Z → 2026-10-16T09:00:00Z|325|
|XAUUSD_Daily.csv / D1|1560|0|0|0|0|1560|0|1560|2020-09-30T21:00:00Z → 2026-10-15T21:00:00Z|319|

Total419,013 unique operational candles. Header mapping for all files: explicit date/time parsing, `<OPEN>/<HIGH>/<LOW>/<CLOSE>`, `<TICKVOL>/<VOL>/<SPREAD>`; validator `timestamp_mode=mt5_or_iso`, `timeframe_mismatch=null`. Gap observations include normal market/session closures and unverified missing periods; they are listed, never assumed to prove complete broker history or interpolated.

### Exact source SHA-256

- `XAUUSD_M1.csv`: `840eeb06b0669a65251179702e2d8eafc848804d4e1f63245b2be59a8bcd73cb`
- `XAUUSD_M5.csv`: `7bdfeddbb119f9c0f910e1436e7c14beec8390a8379291b4a5f065ce410c1896`
- `XAUUSD_M15.csv`: `aef59992b465d45ac7adc068ceace2ed5c58cf66ceb7d379e41e2b98a70def87`
- `XAUUSD_M30.csv`: `f3293d536a6ffd79c7298412e746e0f792e473f7539013a27bfbef21c128985f`
- `XAUUSD_H1.csv`: `c6403ee874ea3e6a2953edc250d4ccd8075400cc4211c58de0afe202bda04445`
- `XAUUSD_H4.csv`: `a1b2f93cd30ac42de422783c6660d6ca84ba49e247d4e8d38cfd7d08dc59c338`
- `XAUUSD_Daily.csv`: `2cfc0fcf53c17c4892bc03fab87701aa6597bc9c1374678cf3501d3966cdb314`

## Initial bar-count root cause and fix

Two independent issues were present: coverage failures silently retained a stale map/fell back to unrelated research snapshot coverage; more importantly, `/deeptest/coverage/pairs` returns a **keyed object**, while the UI consumed it through an array-only helper. The existing initial effect therefore could make a request yet display no operational count. Both array and actual keyed-object contracts are now accepted. A request/range sequence guard discards older results; duration changes clear old counts; failed records become unavailable with an error instead of default0 or a stale count.

Opening/refreshing Deep requests real operational stored coverage after the canonical rows arrive, without first pressing GET. Symbol/TF is the key; range counts use unique finite timestamps in `[start,end)` to match actual historical execution. Total bars remain separately labelled. Boundary/internal gaps mark PARTIAL/NONE or OBSERVED_SPAN with explicit calendar limitations. Import/node refresh and shared latest-fetch completion invalidate UI coverage; returning to a fresh backend reads persistent data, not browser state. A functional deferred-response test verifies that a late3M response cannot overwrite a newer3Y range.

Actual first-load acceptance: XAUUSD/M1 UTC2026-10-13T12:32 through exclusive2026-10-16T12:32 returned4,131 stored in-range candles. The actual canonical718 historical backtest used exactly4,131 from the normalized v1 dataset, completed with its real strategy, and a repeated request reused the same scenario ID. Its deliberately explicit max40-trade experimental setting is not the generation905-trade population. Full details: `verification/V9_3_ACTUAL_DATA_AUDIT.json`; fresh-process all-seven query: `verification/V9_3_FRESH_BACKEND_QUERY.json`.

## MT5 latest path and2683 investigation

Preserved shared Overview/Deep explicit `HistoryLatest` workflow, IC-only bridge guard, closed UTC candles, incremental two-bar correction overlap, bounded requests/retries/deadline/chunk count, normalization/validation, dedupe/correction receipts, persistence and coverage refresh. The button never enables generation/Live or submits orders. Existing service reports request/chunk ranges, received, rejected, added, updated, identical/conflicts, actual stored range and complete/partial/error state; it does not invent unavailable bars.

Trace: `HistoryLatest.jsx` → `/history/latest` → `data/history_latest.py` → native `mt5_real.copy_rates_range` → shared normalized MasterStore. Latest requests are5,000-bar chunks with adaptive genuinely unreturned head/tail requests; the existing Deep range engine uses bounded15,000-bar chunks and also walks partial/capped results. A native per-call2683/chart-history return is not treated as the entire requested3Y range. Existing deterministic bridge tests prove traversal of a2683-per-call cap and persistence/reopen behavior; these are explicitly **fake-bridge tests, not proof of terminal retrieval**. No native MetaTrader5 Python package/Windows terminal was available here, so neither successful native IC retrieval nor the broker's available history/chart-cap setting is claimed.

## Recovery/cache/safety evidence

Existing import tests cover original archive failure, partial receipts, atomic partition journal rollback/recovery, duplicate/conflict preservation, IC UTC/DST conversion, normalization, broker isolation, trusted correction policy, cancelled/latest partial fetch and process locking. V9.3 adds initial keyed-map handling, range stale guards, unique closed-trade statistics and immutable queued data/config checks. Data-only operations never overwrite generation metrics, eligibility, risk/account selection or historical scenarios.

The archived sources and normalized partitions remain authoritative and permanent; the app does not reread CSVs on every tab. Ongoing fetch remains local persistence, not an automatic commit/push. Fresh Git/Windows/LFS provisioning instructions remain inherited; V9.3 data audit verifies restored native LFS objects, exact seven originals and their unchanged operational contents rather than regenerating a replacement baseline.
