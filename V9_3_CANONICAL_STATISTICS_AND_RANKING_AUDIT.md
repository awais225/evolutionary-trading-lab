# V9.3 forensic audit / dependency map (pre-implementation)

## Verified starting state
Remote main:836df1ade134ba911d8388a82dc488ffee98e8b4. Original local main:642cd3b1b24737ff223b6d201da61a573e894165, with substantial tracked/untracked V9.2/user diagnostic work. Original checkout untouched; status and binary patch saved outside it. Implementation clone `/home/user/v93-app` starts clean on the actual remote main. No reset/clean/force push.

Downloaded original versioned research database:139132928 bytes, SHA256 c159b0718c43b43f97609881bd3be196dad76274029949a9fb4c4e710f106a12. **Direct read-only raw SQL (logical DB including committed WAL) contains83 QUALIFIED records**, not the70 described as raw in earlier reports. The raw fixture has no deeptest_scenarios table. Initialization and restoration effects must be distinguished from raw bytes; earlier reports are not accepted as evidence. Protected backup is outside the implementation tree. All Git LFS history objects were retrieved without regenerating data.

## Dependency map
- `DeepBacktest.jsx` → `/api/nodes` → `nodes_index` → canonical strategies PK / research aliases + `_gen_metrics_from` + `stats/deeptest_projection`.
- `LiveNodeIndex.jsx` → the SAME nodes endpoint/projection, plus separately labelled runtime/live execution namespaces and unchanged eligibility/risk/worker controls.
- Deep main table displays `GenerationCells` and separately gets preferred scenarios through `/deeptest/nodes`; Live primary performance columns use `row.metrics`, already the preferred/latest Deep projection. These **are not the same columns/populations**. Deep defaults node-ID ascending; Live defaults fitness descending. Different top nodes therefore do not prove corrupted win-rate calculations. New canonical historical columns/ranking must use the very same returned projection, without letting lazy Deep summaries overwrite it.
- `deeptest/scenarios.py` → coverage/preflight → effective signature → scenario/trade tables → unchanged Backtester; preset ranges currently include wall-clock seconds in signature, defeating unchanged-click dedupe. Assignment snapshots also include irrelevant name/assignment timestamps. Reuse check and insert are separate, unprotected operations.
- `filter_scenarios.py` durable named rules/assignments → `filter_rules.py` → historical and Live gates; `ScheduleDialog` still shows independent legacy whole-day/session controls and a scenario-name/rules dump. Scenario application does NOT hydrate a28-cell shared schedule.
- Legacy Live schedule service defines fixed UTC Asia00–08, London07–16, NewYork12–21. V9.2 Inspect v1 rules use exchange-local historical-DST memberships instead. These are two versioned meanings; preserve existing records and make any new schedule boundary convention explicit.
- `coverage.py` → persistent MasterStore + indexed range reads → `HistoryLatest` / CSV validation-token-full-frame / source archive / transactional partition merge. Deep loads range counts initially but catches errors silently and retains old maps; no range/request guard on that independent response, and generic snapshot coverage is an unsafe fallback.
- `DeepScenarioPanel`/`InspectCalendar` use persisted trades/analytics; redesign only below the existing node table.
- `LiveTesting.jsx` mounts the manual panel unconditionally; preserve the order panel itself, put a default-collapsed disclosure around it.

## Identity / metric rules to preserve
Canonical strategy PK ≠ research-local number ≠ research run ID ≠ Deep scenario ID ≠ log event ID ≠ order/deal/position ticket. Metrics remain fractions internally (UI×100), not percent strings. Generation engine definitions stay immutable. Deep analytics uses eligible finite realised net P&L: wins>0/all closed, PF gross positive/absolute gross negative (undefined if zero loss), net/initial capital, realised peak-to-balance maximum loss divided by initial capital. This established Deep drawdown is not silently rewritten into the generation engine's equity drawdown. Live execution aggregates remain a different namespace. Missing stays missing.

## Existing acceptance gates
Current repository includes72 backend files and five functional jsdom harnesses, production Vite build, launcher/build-fingerprint checks, permanent V8 AST protections with narrowly requested V9.2 exceptions, canonical-data and import tests. Actual new run results will be recorded after implementation; prior claimed pass totals are not reused.

## Final forensic correction: main-file bytes versus logical WAL state

Opening the **versioned main file alone** (`immutable=1`) gives QUALIFIED70, SURVIVED22, RETIRED4731, FAILED3843, KILLED1289, BACKTEST_ERROR45. Opening the original SQLite database **with its versioned WAL** read-only gives QUALIFIED83, RETIRED4740, FAILED3843, KILLED1289, BACKTEST_ERROR45. Both total10000. Main-file SHA remains c159b0718c43b43f97609881bd3be196dad76274029949a9fb4c4e710f106a12. The early private initialization trace copied only that main file: its70 is NOT proof that initialization changed logical83 into70. A subsequent actual operational audit uses SQLite's backup API against the read-only logical DB, including committed WAL state, before initialization/restoration. It begins with83 and reconciles actual preserved research to99. Neither count is a constant in product code.

Original DB/WAL/SHM and tracked startup log bytes are restored exactly after incidental test-side checkpoints; the incidental checkpoint/log state is archived outside the checkout, not discarded or committed. Original dirty checkout remains untouched. No normalized candle, CSV, generation history or original database replacement is part of the V9.3 commit.

## Exact node traces and the apparent win-rate discrepancy

`verification/V9_3_BEFORE_EXACT_NODE_TRACE.json` filters broad identity searches by **exact canonical primary ID**, never research aliases. Before new Deep tests, stored generation observations were:

| Canonical ID | Research alias | TF | Generation WR | Generation return | Trades |
|---|---|---|---:|---:|---:|
|718|missing|M1|77.24%|148.10%|905|
|726|missing|M5|78.27%|72.70%|336|
|728|missing|M5|78.64%|52.57%|323|
|735|missing|M5|77.98%|71.33%|336|
|737|missing|M5|77.91%|71.00%|335|

These are observations, not truth constants or fixtures embedded in ranking. In particular, **72.70% on726 is the stored return, not its78.27% win rate**. Different columns and former fitness/node-ID sorts explain the observed different leaders without inventing a new performance formula. IDs are restored from actual research history; aliases remain NULL.

## Implemented single historical contract

`stats/canonical.py` attaches schema2 `historical_statistics` to `/api/nodes`. Shared `HistoricalStatistics.jsx` renders precisely that contract in Deep and Live, with source/status/scenario/range provenance and unavailable values, not locally hydrated Deep overrides. Shared source preference supports effective / generation-only / Deep-only. Effective retains the established explicitly preferred valid result, otherwise latest valid COMPLETED/PARTIAL result with analytics, otherwise labelled generation. PARTIAL is never called complete, and ranks below complete observations; missing numeric WR ranks last in either direction. Equal values break by canonical ID. Invalid non-finite/out-of-range WR is unavailable, never clamped into an apparently valid statistic. Generation values stay separately recorded.

The full filtered registry is loaded (`limit=0`), sorted before DOM pagination, with superseded/truncated responses prevented from replacing a complete population. Both tabs default to descending `canonical_win_rate` using new V9.3 sort preference keys; explicitly changing sort remains possible. Overview keeps its explicitly labelled generation metrics and uses the same registry authority; it does not relabel those as Deep or live results.

Generation provenance now includes the saved backtest PK/stage, actual stored dataset reference/window metadata, creation time, fingerprint/version and engine-version metadata when recorded. **Exact generation timestamp coverage remains unavailable when absent in the original record**; a currently imported/fetched dataset range is never substituted. Live execution P&L/status remain separate namespaces.

Deep: unique finite eligible closed `(run_id,trade_no)` records; anonymous legacy rows are not guessed/deduplicated. WR=wins/closed including breakevens in the denominator; PF=gross profit/absolute gross loss, unavailable without losses. Return=realised net/actual scenario initial capital. DD=maximum chronological realised equity decline/initial capital, preserving the established Deep definition, not silently replacing the generation engine's definition. Calendar and aggregate percentages are recomputed from their unique trade sets, not averaged from percentages.

## Actual historical end-to-end proof

The WAL-aware private-copy audit imported all seven exact originals, queried the operational normalized store and ran canonical718's actual strategy against4,131 M1 bars from2026-10-13T12:32Z through the last available2026-10-16T12:31Z, end exclusive12:32Z. The initial range endpoint also returned4,131. The test explicitly used max40 trades, initial capital200,000:40 closed trades,28 wins, WR70%, net4,725.85, return2.3629%, PF1.792, realised DD1.3719%. These differ legitimately from generation905 trades/77.24%; they are not substituted into that historical generation record. A repeat reused the same scenario ID without engine rerun. Qualified authority stayed99 before/after import/Deep. Full evidence and exact run ID are in `verification/V9_3_ACTUAL_DATA_AUDIT.json`.

See `V9_3_REPORT.md` for final completed test/build/publication evidence and limitations. No native MT5 or real/demo orders were used.
