# V9.1 MT5 history audit — pre-implementation

Baseline a717884604fd260024c45b9d77e56d18c11dc3fa. Traced DeepBacktest duration → api.deeptestFetch → route → fetch_job._run → resolve_period/explicit_period → sync_master(request_start,request_end) → copy_rates_range → integrity → MasterStore append → persisted coverage.

## Concrete remaining faults beyond V7.0
- engine._sync_master_locked stops after five initial empty chunks. Long range with only later terminal history never reaches available candles.
- On no range results, fallback copy_rates(recent up to20k) can fetch an unrelated recent subset; explicit-range operations must not use it.
- Initial walk trusts chunk timestamps/order, reports count before filtering explicit bounds, and reports only total chunk count, not each chunk's result/error/out-of-range count.
- Missing interval selection considers head/tail only, not internal material gaps. Reuse/discovered cache considers extremes, not internal gaps, and discovered cache checks requested head but not tail.
- No fetch intervals still falls through to no_data/fallback despite valid cache.
- Final completeness uses received extremes rather than persisted union; start allowance is 3,750 M15 bars (~39 days), so materially incomplete history may be marked complete.
- Concurrent engine callers wait and blindly reuse another request's result, even for a different goal. fetch_job also joins any active pair job regardless of period.
- Existing coverage module distinguishes material >96h gaps from smaller session/weekend-quality warnings; reuse that conservative convention rather than inventing an exchange calendar or claiming every gap is missing history.

## Planned evidence
Nine periods, explicit UTC ranges; fixture 3Y well above2,683 bars with date checks, deterministic merge, per-chunk counts and persisted range counts; empty-prefix later-availability regression; cache head/tail/internal holes; unavailable/errors/out-of-range response handling. Existing V7/V6.7 tests remain in gate. Simulator/mock evidence is not real MT5 retrieval.

Operator observation `XAUUSD,M15:100841 bars, limited by charts settings!` is not proof of application retrieval or in-range history. Do not change terminal settings or fabricate candles. Windows/Edge/MT5 terminal acceptance remains unperformed here.

## Implemented follow-up

- Walk continues past empty prefixes. Each chunk records requested UTC bounds, attempts, raw/accepted/unique counts, rejected out-of-range bars, first/last timestamps, empty/error state. Active goals are frozen into explicit UTC bounds at job creation; joining a different active range is refused visibly, not silently coalesced.
- Successful but capped prefix/suffix responses schedule the unreturned head/tail and material internal portions. Empty subranges terminate locally. Overall900s/512-chunk bounds and cancellation remain between native bridge calls; the native call itself is not forcibly interrupted.
- Explicit-range history never uses recent-bar fallback. Results are sorted before integrity checks; identical timestamp duplicates are quarantined/deduped through existing integrity/store behavior, and stored conflicting values are not overwritten.
- Cache reuse derives from persisted **in-range timestamps**, not received extremes. Short cache, missing head/tail and >96h internal holes trigger missing-interval reads. Valid no-fetch cache is a truthful cache result. Concurrent store writes serialize, then each explicit caller reevaluates its own goal against the persisted union.
- Real MT5 history calls retain the existing list-return bridge contract but expose read-only `last_history_result` diagnostics: disconnected/error/None-return are no longer confused with an empty successful array by the fetch engine. No account/session/order methods or terminal settings were changed.
- Final reports count actual persisted in-range unique timestamps, total store rows and first/last separately. Boundary gaps are partial (market closures are not assumed to explain them). >96h internal gaps are material partial history; smaller holes are explicitly **unverified possible session/market closures OR short data gaps**. `COMPLETE` means observed span plus no material gap under that disclosed existing convention, not proof of an exchange-calendar-perfect series. Warnings remain visible; no filling or interpolation.

### Completed mocked evidence
All nine duration keys are covered, including calendar3Y across a leap day. The3Y persisted fixture exceeds70,000 bars; a separate real Parquet store is reopened to verify unique sorted timestamps, requested dates, persisted count and36+ monthly partitions. A terminal fixture returning at most2,683 bars **per call** is walked to the full available3Y union. Empty-prefix (five+ empty chunks before later history), short-cache extension, internal-hole recovery, out-of-range/unsorted/duplicate responses, empty explicit ranges and retry-safe candidate tests are executable in `tests/test_v9_1_registry_and_history.py`.

This proves application behavior against controlled fixtures, **not** retrieval of the operator's reported100,841 bars. Windows/Edge/MT5 and live native-call latency/settings constraints remain operator acceptance items; no real orders were placed.
