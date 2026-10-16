# V9.1 — Canonical tables, durable candidates and range-aware history

Date: 2026-10-16. Workflow: **Generate → Qualify → Overview → Deep Testing → Star Candidates → Live Testing**.

## Release identity and safe baseline

- Verified initial origin/main: `a717884604fd260024c45b9d77e56d18c11dc3fa` (V8).
- Final production source/content commit: `887508ac890a755521c9fdbbab4834f9dc002d8c`.
- Publication adds this report and its fingerprint record; the build identifies the content commit above, not a self-referential documentation commit.
- Initial SRC/CODE/INDEX hashes: `82964c8c393f079280e4515bd5dbb6e6430085abb5020fbead0328b90198e4fa` / `1a9c1e3d3bc311fc3b7af31a2d84a8aedd9f4e4fd4b55ea518ec7cce2f061541` / `2a02233db611c41b57879d5d116095102599ace37838341d2eb94d83c4634837`.
- Final SRC: `ad99bd70fb4b5aca66166be0cbd1389a180eda59811807e1a4fda1c64f4ee5b4`.
- Final CODE: `4eb2f34ebec4a18bce7f334b22813e288bbf9af4f538630e07dc418f8797b528`.
- Final INDEX: `811767c5b13bd6157dbd091757895bbd2eeeb5fb9a100423ddc89072ccc6676b`.
- Entry assets: `index-DEFOYBBx.js`, `index-DYygB5sD.css`; `PRODUCT_RELEASE = V9.1` outside engine-generation manifest digests.

The workspace snapshot began at V6.6 with restored V8 changes. Preserved those in a Git stash and `/home/user/V9_1_INITIAL_WORKTREE.patch`; preserved colliding untracked files separately after byte-equality checks against V8; safely fast-forwarded to V8. Initial DATA was preserved separately. **No reset, clean, force-push or discarded user changes.** Remote publication uses ordinary main push, with a post-push `ls-remote` SHA equality assertion against local HEAD. Exact final publication SHA is emitted by that verification and in the completion response; it necessarily differs from the source commit because report/fingerprints are appended.

## What changed

### Canonical registry and shared tables

Overview, Deep Testing and Live Testing consume `/api/nodes` and render through **one `NodeTable` implementation**, with tab-specific headers/actions injected. `NodeIdentity`, `GenerationCells` and `CandidateStar` are shared too. All three display the same saved Gen Return/Net/PF/DD/WR/Trades; scenario/effective/live results remain separate labelled layers. Live's existing effective-statistics cells and all its controls remain in place.

Canonical identity is persisted `strategies.id`. Research-local number and saved run provenance remain separate; missing aliases are not replaced by canonical numbers or run-position guesses, including the detail drawer getter. Sorting coerces raw numeric strings numerically, handles negatives/zero, puts missing/nonfinite values last in either direction, and breaks ties by canonical id. Sorting happens over the full matching dataset before paging. The shared render page mounts at most100 node rows; selection remains across pages, with truthful page navigation.

Default qualified selection is **stored QUALIFIED + PAPER over complete saved history**, including preserved runs. The unchanged broader node_bucket/VALID inventory is retained explicitly as `gate_passed`, not renamed qualification. Overview Top Strategies is a labelled top15 registry view with qualified/all/starred scopes and full-scope server ranking; it no longer calls a separate population serializer or fires an old-closure sort refetch.

### Durable candidate stars

`PUT /research/shortlist/{canonical_id}` sets a boolean desired state in the existing `research_shortlist` table. It rejects nonexistent ids and nonboolean input; retries do not invert the result or replace existing bookmark notes/time. Deep/Overview/Live use the same bookmark component, updating only the affected candidate and shortlist context without a population request. Live's Starred filter reads those exact saved canonical candidates independently of research qualification, showing existing ineligibility reasons. Stars never enroll/start workers, change metrics, create scenarios, place trades or bypass safety.

### History completeness and truthful coverage

The selected symbol/timeframe/duration becomes explicit UTC bounds at fetch-job creation. All nine durations are tested: 1D/7D/14D/1M/3M/6M/1Y/2Y/3Y. Different active goals are not silently joined. A waiting engine caller reevaluates its own goal after serialized store writes.

The walker continues past empty old chunks and adaptively retrieves unreturned portions of capped successful responses. Every chunk reports bounds, attempts, raw/accepted/unique counts, rejected out-of-range bars, dates and empty/error state. Explicit ranges never fall back to an unrelated recent subset. Timestamps are sorted; existing integrity/quarantine/conflict handling and master deduplication remain authoritative. Native MT5 list-return compatibility is retained while read-only diagnostics distinguish disconnected/error/None returns from successful empty arrays.

Cache checks and final coverage use the **persisted in-range union**, including missing head/tail and material internal holes, not merely received extremes or store freshness. Short cached data cannot satisfy a long goal. Valid no-fetch cache results are labelled as cache reuse. Deep's persisted range-bar recount now runs when actual rows arrive and when duration changes, fixing its old empty-node closure.

Coverage policy is explicit: boundary shortfalls are partial; internal gaps >96h are material partial history; smaller holes are unverified possible session/market closures **or short data gaps**, not silently called known closures. COMPLETE means observed span/no material gap under that disclosed convention, **not** exchange-calendar-perfect history. No candles are interpolated/fabricated, no goal is silently shrunk, and no MT5 settings were changed. Cancellation/deadline checks occur between native calls; an active native MT5 call itself cannot be forcibly interrupted here. The walk is bounded by900s/512 chunks.

## Preserved semantics and controls

Generation/import/qualification/survival algorithms, thresholds, strategy logic, persisted generation metrics, eligibility, sizing/risk, account selection, schedule semantics and order execution were not changed. The strengthened safety regression compares frozen modules against V8 even after commit, and compares ASTs of protected database qualification/reconstruction/config-write methods and MT5 account/order/session methods. Shared database changes are additive revision triggers, identical config decoding for bulk reads, and read-only alias projection corrections.

Deep's periods/fetch/run/inspect/scenarios/settings controls remain. Live's start/stop/STOP ALL, schedule, inline risk modes, max trades, SL/TP offsets, manual panel, account and execution/order monitoring remain. Strategy Lab remains functional and canonical-id based. V8's five removed tabs remain removed; shared services are retained. No real broker orders were initiated by this work.

## Saved-data verification anchors — not constants

The unchanged restoration engine, run over the verified original DB plus genuine saved research files, yielded **10,809 rows / 99 QUALIFIED+PAPER**. Canonical **Node #718** remains the saved top-return node: XAUUSD M1, SURVIVED, return1.481 (148.1%), PF2.545, DD.0405, WR.7724, trades905. SURVIVED is not silently promoted into the qualified default; it is accessible in All history.

Importantly, its **persisted research_node_num is NULL** in this restored dataset. The earlier detail display “research #318” came from the removed run-rank fallback, not a persisted alias. It now remains missing. Collision tests use explicit aliases in isolated fixtures and do not manufacture operator records. See `evidence/v9_1/restored_history_anchor.json`.

## Measured performance

Final measurements use two sequential calls per operation on separate copies of the same SHA-verified original10,000-row LFS database, same installed environment; V8 source is extracted from origin/main. They are direct route timings with SQLite trace counts and uncompressed JSON bytes, **not HTTP/network/Edge paint or percentile benchmarks**. Raw evidence is committed under `evidence/v9_1/benchmark_{before,after}.json`.

| Operation | V8 cold / warm | V9.1 cold / warm | SQL statements before → after |
|---|---:|---:|---:|
| All history,100 returned rows,10,000 total |5.132 /2.590s |3.581 /1.098s |50,032–50,033 →36 |
| Authority, full diagnostic snapshot |1.806 /.00491s |.356 /.000094s |26 /2 →23 /1 |

The warm same-scope node-list measurement improved about58%; SQL statement count fell about99.93%. Small sample sizes and cold page-cache/import variation preclude percentile or production-latency claims.

- Full100-row payload **increased**391,677 →420,992 bytes because provenance/eligibility fields were added. No blanket payload-size win is claimed.
- Optional compact100-row registry view:329,294 bytes, deferring signal/coverage detail; Overview uses compact mode. Deep/Live retain required detail/control fields.
- Routine authority summary: **26,457 bytes**, versus the old2,509,675-byte full identity-map response; the full diagnostic endpoint remains available.
- Qualified-default sample: old broader bucket92 rows /348,895 bytes versus new frozen research-qualified70 rows /281,653 bytes on the initialized original fixture. **These are different predicates**, not a like-for-like count or performance win. The separately restored complete-history verification is99, not a hardcoded target.
- Shared persisted generation summaries avoid reloading saved trade/equity arrays; bulk config/validation/dataset reads remove N+1s. Additive revision triggers catch direct SQL/foreign-connection status/alias/metric changes; stars do not invalidate generation authority. Existing concurrent GET coalescing is reused, with no new polling/eager job.
- Star interaction: one desired-state PUT, zero node-population reloads; asserted by mounted interaction test.
- Synthetic jsdom1000-row ×8-cell fixture: full-map282.16ms /1000 rows /8000 cells versus shared paged40.49ms /100 rows /800 cells. Includes20ms settle; not actual rich-page layout/Edge paint. Full sorted dataset remains accessible. Raw fixture in `ui_acceptance.json`.
- Production assets:25 files /1,332,278 bytes /381,804 gzip bytes versus V8's previously recorded24 /1,328,902 /379,731. Small bundle growth is disclosed, not presented as a bundle reduction.

## Completed regression gate

All **69 repository test files** completed in resource-safe fresh-original-DB batches; no failed or incomplete run contributes to the total. Batches3/4 first run the existing genuine-history restoration because their historical read-only assertions require those persisted records. Each batch restores verified original configs; no qualification fixture is synthesized to satisfy totals.

| Batch | Files | Passed | Skipped | Failed |
|---|---:|---:|---:|---:|
|1|7|167|0|0|
|2|7|140|1|0|
|3|7|92|0|0|
|4|7|165|0|0|
|5|7|132|0|0|
|6|7|117|0|0|
|7|7|85|0|0|
|8|7|60|0|0|
|9|7|156|0|0|
|10|6|86|0|0|
|**Total**|**69**|**1,200**|**1**|**0**|

The single existing skip is the simulator-dataset availability case in `test_v5_1a_coverage_and_balances.py`; no new skip was added.26 new V9.1 acceptance cases cover canonical aliases, newest-detail precedence, frozen qualified scope, numeric-string sorting across pages, bookmarks/retries/durable storage, revision invalidation, constant bulk query count, all9 periods, empty prefixes, capped2,683-bar responses, cache extension/internal holes, duplicate/out-of-range data, native None-versus-empty, differing active goals and errors.3Y exceeds70,000 unique bars; a real Parquet fixture is reopened and checked for dates, sorting, unique counts and36+ monthly partitions. These are controlled mocked-terminal tests, not real terminal retrieval.

Completed commands:

```text
.venv/bin/python -m pytest -q <each file list in evidence/v9_1/gate_batches.json>
# 10 completed subprocesses; every exit0; TMPDIR/SQLITE_TMPDIR on disk
.venv/bin/python frontend/tools/gen_theme_layer.py --check
cd frontend && npm run build
node tests/v47_render_smoke.mjs       #35/35
node tests/v48_ui_smoke.mjs           #54/54
node tests/v6_5_1_nodes_ui.mjs         #16/16
node tests/v9_1_nodes_ui.mjs           #6/6
.venv/bin/python backend/tools/build_fingerprints.py --record --release V9.1 --commit 887508ac890a755521c9fdbbab4834f9dc002d8c
.venv/bin/python backend/tools/build_fingerprints.py --match
```

Production serving/launcher/build checks are included in the gate; frontend build completes with the existing large-chunk warning. Existing FastAPI duplicate-operation-id and React/jsdom warnings are disclosed, not suppressed. Development runs exposed stale old-contract tests, an identity-link selector regression and a temporary SQLite VACUUM disk-full error; these were resolved, and failed/interrupted runs were excluded. Test storage moved from the small `/tmp` tmpfs to disk. Assertions were updated to verify both explicit gate-passed inventory and frozen research qualification; no safety assertion was weakened to obtain a pass.

## Data preservation

Original database restored SHA256: `c159b0718c43b43f97609881bd3be196dad76274029949a9fb4c4e710f106a12`.

After tests: **all12,462 LFS objects match their original SHA256**. Four simulator/feature caches mutated by existing tests were preserved separately, then restored from their original LFS objects.50 changed tracked regular DATA/config originals were also preserved separately and restored byte-identically. Initial DATA and initial worktree checkpoints remain available. No DATA/config/secrets are staged in this release; no synthetic database replaces canonical data. See `evidence/v9_1/data_preservation.json`.

## Real-environment limits and operator acceptance

Windows/Edge/real MT5 were not available in this Linux sandbox. The reported “100841 bars, limited by charts settings” remains an operator observation, **not** a verified retrieved or3Y-in-range count. No terminal settings or real account/order actions were performed. Operator acceptance still needs:

1. Select the intended existing MT5 demo account; verify symbol and timeframe.
2. Fetch each of the9 durations; compare explicit UTC bounds, terminal availability, per-chunk results and persisted in-range counts/dates. A genuine chart/server limit must show partial coverage/reason, never fabricated full history.
3. Fetch3Y after a short cache; inspect old head, recent tail, gaps and closures. Refresh/restart and verify persisted unique counts and candidates.
4. Star/unstar in Deep; navigate to Live Starred and verify exact canonical ids and ineligibility notes without worker starts.
5. Manually verify retained schedule/risk/max-trades/account/manual panel controls in Windows/Edge under the existing demo-only operator checklist. Never bypass gates or autonomously place orders for acceptance.

The complete technical traces and policy boundaries are in `V9_1_CANONICAL_TABLE_AUDIT.md` and `V9_1_MT5_HISTORY_AUDIT.md`.

### Post-content-commit publication check

After recording the final source commit in the fingerprint manifest, the serving,
launcher/identity, whole-dataset pagination contract, manual safety and V8-purge
publication subset completed again: **66 passed /0 failed**. Final fingerprint
match resolves V9.1 to `887508ac890a755521c9fdbbab4834f9dc002d8c`; theme check
reports0 unmapped classes and `git diff --check` succeeds. These66 are an
additional publication check, not added to the1,200-test full-gate total.
