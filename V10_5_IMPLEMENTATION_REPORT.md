# V10.5 — DeepTesting integrity and scenario management

## Release status

Targeted source changes are implemented and regression-tested against the published V10.4 parent `ea58dc9dad6592779c35a78f367bbb37631880d6`, on the existing `v10.4-throughput` branch. This is **not** certification of the unavailable native example, native Windows environment, MT5 account, or GPU acceleration. The publication receipt records the final commit and independently verified remote objects.

**Most important limitation:** run `dt-9edaa1dcbd0d42e7` is not present in the available authoritative evidence. Neither #4325 nor #4315 can be certified as its canonical node. The supplied original/Inspect/Monday-Asia figures are documented as observations, not fabricated corrected results. See [reconciliation evidence and native procedure](V10_5_RECONCILIATION.md).

## Architecture and permitted scope

The implementation was inspected before modification: `deeptest/store.py` holds immutable run/trade snapshots; orchestration and durable worker ownership live in `scenarios.py`/existing bulk services; `analytics.py` performs trade summaries; `inspect_calendar.py` and versioned filter rules provide entry-time membership; named filter templates/assignments are shared with Live schedules; existing persistent `shortlist` is the authoritative star state. Live evaluation uses the existing schedule service. The V10.4 durable writer, execution engine, database implementation, evolutionary logic and market parsing remain unchanged.

No authoritative DB/DATA was reset, migrated destructively, copied wholesale, regenerated or mass-backtested. No production nodes/trades or dummy evidence were created. All synthetic DBs, tests, dependencies and benchmark output were outside the project workspace under a temporary external root. Native failed-preflight evidence, sidecars, backups, logs, research and original data were preserved. No orders, native campaigns, worker activation, MT5 login, GPU/power/affinity/risk changes were performed.

## 1. One saved-trade metric authority

New `backend/app/deeptest/metric_service.py` captures actual run headers and all persisted trade rows in one read snapshot, then performs CPU aggregation **outside** the database lock. It normalizes table/headline, Inspect, saved preference response, scenario comparison, filter analysis, shared historical projection and metrics JSON onto the same backend calculation. Engine snapshots remain unchanged in storage.

Key corrections:

- Read recorded run `settings.total_capital` before compatible explicit capital fields; remove the filtered route's invented 10,000 fallback. Zero/missing is not replaced with a plausible balance.
- Net P&L uses compensated summation of eligible saved net observations. Costs already incorporated in net P&L are not charged twice. Profit factor, win rate, average and expectancy use the same eligible population.
- Realised equity sorts by exit timestamp and deterministic tie keys, includes starting capital as an initial peak, and reports **drawdown amount + percentage of initial capital** from the same curve. No 100% clamp or silent peak-relative substitution.
- Explicit N/A reasons cover missing expected rows, unavailable positive capital, invalid chronology and undefined ratios. Summary-only historical fixtures no longer masquerade as real zero-trade performance.
- Sharpe/Sortino use the same documented per-trade return convention and explicit assumed 252-trades/year annualisation. This is not calendar-derived daily return annualisation.
- Original/included/excluded trade populations are independently aggregated and equity rebuilt. Filtering remains **post-trade exclusion, not re-simulation**; historical sizing/fees remain. Counts and P&L reconcile.
- Provenance includes run/node, method version, trade snapshot hash, capital and source, recorded currency or unavailable, timestamp, available/eligible/expected count, execution/coverage and available bar/range metadata. PARTIAL stays partial.
- Inspect prominently pairs recorded-currency drawdown amount with its matching percent and initial-capital denominator; no hardcoded USD. Comparisons carry the amount and denominator. Inspect links the authoritative `metrics.json` export. Existing complete CSV/trade retrieval and pagination remain uncapped, including beyond 3,000.

### Cache/throughput safeguards

The read-only cache is bounded to 512 entries / 8 MiB per live Database object, weakly referenced. Keys include connection identity, external SQLite data version, local write revision, run ID and calculation version; filtered keys also include normalized rules and legacy/versioned semantics. Invalidation is intentionally conservative: an unrelated SQLite write can cause recomputation, but cannot silently reuse stale metrics. Snapshot loads are batched over at most eight runs, with O(n) caller metadata lookup. Financial CPU work does not hold the SQLite lock, as demonstrated by a concurrent-reader regression.

Ordinary warm rendering does not reread/recompute every trade. Cold complete-trade calculations still have a real cost; no universal latency promise or speculative parallelism was introduced.

## 2. Assigned scenario replaces Live day/session state

`filter_scenarios.assign` persists an immutable authoritative assignment snapshot (rules plus version). The Live configuration API projects replacement `days`/`sessions` from that snapshot on every load, rather than merging old selections. Assignment alone does not rewrite the raw fallback base configuration; the stored assignment is the effective schedule authority while assigned. Risk, activation, node identity and other settings are preserved. Daily matrices project their selected union to legacy controls; exact day/session pair restrictions remain in the authoritative rules. For non-matrix rules the existing versioned predicate determines eligible days/sessions. An empty legacy session list means unrestricted sessions; it is not a hidden “select none”.

Live evaluation marks assigned rules so they are not intersected with stale legacy day/session controls. The assignment response updates parent and child UI state immediately and survives reload/reopen. A Live config save cannot overwrite assigned schedule compatibility fields with stale values. Named schedule edits carry the expected assigned scenario ID; stale editors receive **409** rather than silently replacing another selection. Assignment never starts a worker or trading.

## 3. Explicit All Nodes / Starred Nodes

`LiveNodeIndex.jsx` exposes a plainly labelled selector backed exclusively by existing persistent `shortlist`, including locally created and AI-origin nodes. Reloaded authoritative state adds newly starred rows and removes unstarred rows. It composes with existing index search/sort/pagination and the existing refresh/event mechanisms; no second star database or automatic activation was added. Existing filter-persistence behavior is retained, not expanded into a new preference store.

## 4. Scoped, confirmed DeepTest deletion

New `DeepScenarioManagement.jsx` provides top-level **Delete All Scenarios**, selected-node bulk deletion, individual selection, multiple selection and current-page selection. Server preview supplies affected run/trade/preference/definition counts and retained-definition counts before typed confirmation. Existing individual scenario deletion remains available. The legacy Inspect management endpoint retains its narrower compatible meaning; the new bulk controls request explicit DeepTest-owned definition cleanup.

Backend deletion uses the existing management service, ownership guards, transaction and tombstones:

- Cancel local affected runs and await their Futures before deletion; terminal DB status alone is not sufficient. A still-stopping writer, foreign owner or concurrent owner causes a useful refusal, not partial unsafe cleanup.
- Cancel an affected local bulk controller only when its nodes are wholly covered by the deletion scope; refuse a selected-node request that would interfere with unrelated nodes in the same batch. The wait is bounded; retry is explicit.
- Delete only selected DeepTest run-owned trade/summary/history/preferences and proven DeepTest-owned templates/assignments. Transaction exceptions roll back the entire deletion and tombstones. Existing tombstone writer guards prevent resurrection.
- **Preserve Live/shared/unverified templates.** Node ownership alone does not prove DeepTest ownership. An unassigned Live schedule and ambiguous legacy template are retained. Trusted server call sites mark newly saved schedule domain; client-provided domain flags cannot widen deletion scope. Existing deep assignments or matching canonical run provenance can establish DeepTest ownership. Live-referenced or out-of-scope shared definitions are retained.
- Preserve strategies, full genomes, ancestry/generation, stars/shortlist, market/features, ordinary backtests, Live configuration and AI records. Clear only relevant run/runtime/derived metric caches after successful commit; cache revisioning also guards stale reads.
- Reports/UI state retained definitions honestly rather than promising destruction of shared Live configuration. Logs report useful scoped outcomes without credentials, full genomes or private account contents.

## 5. Validation — not an all-green claim

| Check | Result |
|---|---|
| Unchanged V10.4 broad backend baseline | **369 passed, 17 failed, 4 errors** |
| V10.5 same broad selection + new regressions | **403 passed, 17 failed, 4 errors**, 48.47 s |
| Exact nonpassing identity comparison | **All 21 match baseline; zero new nonpassing identities** |
| New V10.5 backend regressions | **34 passed** in focused run, 3.10 s |
| New V10.5 UI harness | **6 behavior checks passed** |
| Existing UI harnesses | V10.4, V10.3 SPEED LOG, V10.2 dashboard, V9.3 all passed |
| Production frontend build | Passed, 5.85 s; existing bundle-size warning |
| Diff whitespace | `git diff --check` passed |
| Original/protected preservation | **462 + 61 file hashes unchanged** |
| DATA tree | `68225e768709dcd9fb9123642a5164e4e68e79ab`, unchanged |

The baseline failures/errors depend on unavailable historical/real fixture inventory, including old node-specific evidence; their exact identities are retained in `V10_5_VALIDATION.json`. They were not removed or relabelled as passing. Three assertions in two legacy test files were **strengthened**: summary-only records now require missing-evidence N/A and raw snapshot preservation, instead of asserting fabricated performance without trade rows. Development full-suite validation caught and corrected a transient start-response enrichment regression before this final run.

New tests cover capital authority, conflicting stored metrics, chronology and deterministic ties, monetary/% drawdown, missing/zero capital, missing timestamps, zero/winner/loser/negative-equity populations, PARTIAL/missing-trade disclosure, full retrieval >3,000, filter membership/count/P&L reconciliation, cross-connection invalidation, cache reuse, DB-lock release, replacement/reopen/stale schedule saves, local/AI shortlist, atomic protected-data deletion, rollback, writer/batch cancellation, unrelated-owner refusal and ambiguous/Live template preservation. The UI tests use jsdom behavior harnesses, not a claim of native visual certification.

### Measured, isolated performance

Final **synthetic 15,277-trade** cache benchmark (not the example run):

| Operation | Seconds |
|---|---:|
| Cold original complete calculation | 0.643990 |
| Warm original median (20 calls) | 0.000351 |
| Cold filtered/calendar calculation | 2.016006 |
| Warm filtered median (10 calls) | 0.004907 |

Warm original calls issued **zero full trade SELECTs**. Synthetic net −15,286 = included −14,605 + excluded −681. This measures cache reuse, not improved real trading results.

The unchanged-output V10.4 runner was also compared over three isolated rounds, one worker, 12,000 bars / 100 births: serial median 1.805154 → 1.745870 seconds (~3.3%, noise-sensitive). Settings, records, RNG, complete trades and metric digests matched. No execution/strategy optimization or GPU claim is based on that small timing difference.

## 6. Preservation and workspace

The 462 original files under the inherited app and 61 protected evidence files were SHA-256 checked before and after implementation, all unchanged. Three generated Git-status text logs were **explicitly user-authorized** for lossless compression; decompressed hashes match, retaining the `.txt.gz` files and saving **4,916,355 bytes**. No research/protected evidence was deleted. A generated pip cache was relocated outside the persistent workspace. Final eligible persistent bytes and report hashes are recorded in the small publication receipt; allocated `du` blocks are not confused with snapshot content bytes.

Source and small reports are published normally to the existing intended branch. No force push, reset, clean, history rewrite, credential commit or extra source/patch/handoff archive is used. Generated databases, dependencies, logs, test profiles and benchmark artifacts remain outside the project workspace; only small measured results are persisted.

## 7. Remaining acceptance work requiring real native evidence

- Locate the exact run, resolve its actual node ID and recorded capital/currency, and independently reconcile the complete original and Monday-Asia closed-trade populations. Corrected exact-run drawdown and ratios remain unavailable here.
- Check the deployed native UI visually for the actual schedule, star and deletion workflow, and perform safe operator-supervised native shutdown/worker-owner checks.
- Windows / Python 3.14.6 / RTX 4090 / MT5 / native failed 144424 preflight database and sidecar behavior are **not validated by this Linux sandbox**. No native environment changes or failed-evidence cleanup was attempted.

Use the existing-backend read-only procedure in `V10_5_RECONCILIATION.md`; it does not start a new historical run or trading session. Missing records must remain missing evidence, not be replaced with a plausible run or inferred balance.
