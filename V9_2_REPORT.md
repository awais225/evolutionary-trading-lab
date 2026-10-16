# V9.2 final implementation and verification report

**Published to GitHub main; final gates and targeted fresh-clone data verification passed.**

Implementation/report publication verified at `4c75059198ab0a1fa51cadf21d29c35eb8ebb125`. The follow-up publication-evidence commit changes documentation/JSON evidence only; its final main head is reported in the delivery message.

Reviewed executable source commit: `9718d79f941daca9d9f9a6351cb8833b75a06bf0`. Final documentation/evidence commits do not change executable code. The published main head is verified separately after pushing; no force push is used.

## Delivered

- Permanent7-file XAUUSD baseline: **419,013 unique rows**; M1 **100,057**, M5 **100,012**, M15 **100,856**, M30 **71,441**, H1 **35,738**, H4 **9,349**, D1 **1,560**. Exact originals are LFS-versioned;370 normalized monthly parquets,7 metadata JSONs and the historical manifest are intentional release data, not temporary UI caches.
- Full retained CSV frames, historical head/internal merges, readback-backed counts, explicit timezone revalidation, original-before-merge archiving, per-store thread/process locks, failed/interrupted transaction recovery and truthful post-commit receipt errors. Old profile data is preserved, not silently reinterpreted.
- Shared Overview/Deep `FETCH LATEST MT5 DATA`: explicit async IC Markets-only service, incremental overlap, bounded/retried/adaptive capped requests, progress/cancel/partial reports, local permanent storage, no automatic Git push and no trading side effects.
- Canonical population ready before the first usable view; durable metadata-only subsequent startup, one canonical identity registry, whole-set ordering and preserved tab/table preferences. No constants for83/99/718.
- Independently signed TP/SL directions, legacy-compatible additive migration, unchanged broker stop/freeze/precision/risk constraints; truthful event/node/order/deal/position identities and verified-success colors.
- Versioned UTC weekday/session/calendar Inspect, overlap-safe unique totals and5+-week ranges; durable global/node scenarios with Save popup and the existing SCHEDULE assignment/removal integration, shared with Deep and Live entry rules.
- Optional integer-day **TIME RANGE BACKTEST DURING GENERATION**, default OFF, using the existing worker/Backtester and actual persistent bars; separate result storage/provenance/cache, never replacing original qualification, generation, Deep or live statistics.

## Acceptance evidence

**Full final gate:72 files,1,233 passed,1 pre-existing environment-dependent skip,0 failures**, in11 completed resource-safe batches on an isolated matching source worktree/private fixture DATA tree. Every batch exited0. New feature suites contain33 passing tests. Earlier failed/interrupted development attempts are excluded, including the deliberately stopped run superseded by the completed final run.

Functional jsdom harnesses additionally completed **35/35 +54/54 +16/16 +6/6 +6/6** renders/behaviors. These are117 internal UI checks, **not**117 additional pytest test cases; their wrappers are already represented in the pytest gate. No assertions/skips were added to hide failures. The live frozen gate still AST-compares every other live function and the original protected DB/MT5 methods against V8, allowing only the explicitly requested offset/rule/log behavior.

`npm run build` completed in4.77s; the existing large-chunk advisory remains nonfatal. Linux launcher/build/serving/identity contracts are included in the complete gate. Build guard status is **FRESH**, release fingerprint match is **V9.2/latest**:

- SRC `2a20715e9d9152788b5df6b51b6ecb3837dda743794edef11ee523adc4fb7f20`
- CODE `5ee28556c27183baca184f1a980e1558f4e01999ce67bd8835c48c1fb306e709`
- built index `8403f789a1311192d7f0d44881ed4f4266cb8fe68bff31d2c186c0b9bbafefb3`

### Actual CSV → HTTP → disk → new engine
The exact attached M15 file was exercised through the actual raw-body HTTP validation/commit routes against an isolated2,698-row recent overlap drawn from that same file, not fabricated OHLC or a claimed copy of the absent customer terminal:

-100,856 valid,0 rejected; **98,158 added +2,698 identical +0 conflicts =100,856 persisted**.
- Validation **0.769s**, commit including archive/store/readback **1.107s**.
- Original bytes/SHA verified; reopened store and fresh engine read100,856 bars, with active-profile independence and canonical snapshot v2.
- Separate-process independent parsing checks **every UTC/OHLC/tick-volume/real-volume/spread value** for all7 persisted baselines.

### Actual Node718 / generation-window / Inspect proof
The real saved Node718 genome completed an actual Deep simulation of **4,131** persistent M1 bars in **0.668s** on the final source. It did not require an MT5 terminal. Its original strategy row/generation backtest count remained unchanged; authoritative qualified population stayed **99→99**.

The optional3-day window traversed the real `bt_worker` plus dedicated persistence writer over4,131 source bars; a separate record was verified and original strategy/backtests remained unchanged. Repeat strategy metrics were deterministic; runtime timing is explicitly excluded from equality comparisons. A new production generation campaign was not started merely to demonstrate this feature.

Inspect aggregated **872 actual saved trades** in **23.356ms** in this run. Multi-week/no-trade/missing/overlap behavior also has deterministic fixture tests.

## Performance and explicit tradeoffs

The before/after API benchmark uses the **same raw published original database** before restoration, same requests and same70-qualified fixture; otherwise comparing70 raw rows to99 restored rows would conflate data changes with implementation cost. Values below are the second warmed sample of2, not statistically controlled guarantees or browser paint timings.

| Query | Before → after ms | SQL statements | JSON bytes |
|---|---:|---:|---:|
| 100-row normal node query | 1139.659 → 1075.511 | 36 → 36 | 420,992 → 428,792 |
| 100-row compact node query | 1156.513 → 989.056 | 35 → 35 | 329,294 → 337,094 |
| Qualified query, same70-row fixture | 344.446 → 297.589 | 35 → 35 | 281,653 → 287,113 |
| Lightweight authority summary | 0.104 → 0.059 | 6 → 6 | 26,457 → 26,457 |

The normal node payload grows7,800 bytes per100 rows for the added direction/version fields; compact grows2.37%. The lightweight authority stays26,457 bytes and preserves V9.1's advantage over the2.51MB full authority object. No eager all-tab/full-candle load was introduced.

Under the same jsdom fixture/status provider,5 measured post-warmup Overview mounts were median **85.62→78.86ms**, with **11→13** requests for that component scope. This is not a whole-browser navigation/WebSocket count. Bounded100-row/8-column table mounts were **18.99→15.61ms** and retain100 DOM rows for1,000 records. Full1,000-row tables remain much more expensive. The benchmark measurements completed; its disposable harness needed termination afterwards because imported module timers kept the process alive. This is not counted as a passing test and does not claim Edge paint/layout performance.

Persistent indexed3-day M1 range reads averaged **5.753ms** over10 reads; no CSV parser is involved. Final native-cap mocks cover >2,683 bars, first/last2,683 caps,6,000 new bars, repeat dedupe, corrections, pair isolation, cancellation and failures. There is **no real-terminal fetch timing** to report.

First historical reconciliation is a deliberate one-time migration cost: earlier6.035s, final **11.354s under concurrent verification load**, real SQL70→99, not a hardcoded83→99. An unchanged subsequent authority check took **14.4µs**. This is not described as universally faster cold startup; correctness now precedes the first usable population.

Optional generation-window context preparation took **40.84ms**. Five alternating warm worker samples were median **26.38ms normal vs 53.43ms enabled**, approximately2× in this controlled4,131-bar screen/window case. It deliberately adds a real backtest; no zero-overhead claim is made. Default OFF runs no additional window backtest.

## Preservation / publication / remaining environment limits

The pristine original DB backup remains139,132,928 bytes, SHA-256 `c159b0718c43b43f97609881bd3be196dad76274029949a9fb4c4e710f106a12`. Its actual initialized fixture is70 QUALIFIED/PAPER, not the operator's separate historical83 observation. Real established restoration produces99. No synthetic canonical database, node-ID list, hardcoded count, reset/clean or force push was used. Initial local source changes/untracked collisions and DATA were preserved separately; unrelated/obsolete MT5 diagnostic edits were not silently reapplied over the published V9.1 baseline. Preserved local/untracked runtime paths remain uncommitted intentionally.

Frozen signal logic, qualification/survival thresholds, original fitness, order/account/risk execution methods, Manual Trading Panel and existing V7 bridge contracts remain regression-protected. V8-removed tabs were not reintroduced. No real MT5 order was submitted. Stars/scenario assignment/filter saves/fetch clicks do not enable or start Live/generation workers.

Independent authenticated download from GitHub LFS verified **377/377 objects,46,370,888 bytes**, including all7 original CSVs and370 monthly parquets. Normal main push succeeded, and a new GitHub clone at `4c75059198ab0a1fa51cadf21d29c35eb8ebb125` used native `git-lfs pull` for the targeted baseline. All377 payload SHA-256 hashes and all7 operational counts matched. A cold3-day M1 query returned4,131 bars in 38.55ms without parsing CSVs. This is a targeted baseline/source sparse-checkout verification, not a claim that the entire older historical-project LFS corpus was newly downloaded or Windows app startup was run. Ongoing fetches stay local until an explicit reviewed manual Git commit/push.

**Not verified:** real IC Markets terminal retrieval/chart-limit behavior, Windows PowerShell/batch launch execution, native Windows locking, Edge visual/performance behavior or live trade execution. Broker historical gaps/timezones are distinguished from user confirmation in the timezone audit. Native MT5 calls cannot be forcibly interrupted. File-lock/journal recovery is tested for process interruption and failure, not a formal fully-fsynced power-loss or cross-SQLite/filesystem ACID guarantee. Post-commit receipt failure returns an explicit partial state and safe retry instructions.

Supporting audits: `V9_2_DATA_IMPORT_AND_TIMEZONE_AUDIT.md`, `V9_2_MT5_HISTORY_AND_GITHUB_DATA_AUDIT.md`, `V9_2_QUALIFIED_NODE_AND_IDENTITY_AUDIT.md`, `V9_2_INSPECT_SCENARIO_AND_GENERATION_REPORT.md`. Machine-readable scoped evidence: `docs/V9_2_VERIFICATION.json`.


## Publication verification

The completed final gate is the only final pytest total reported. Superseded incomplete development runs and unsuccessful sparse-clone initialization attempts are excluded. The successful fresh-clone proof used a normal shallow Git clone, an explicit sparse source/baseline checkout, and native git-lfs retrieval; it did not use local original CSVs/parquets as substitute download objects. Independent prior LFS batch downloads and the separate native clone both verified377 objects. No automatic fetch-to-Git synchronization was introduced.

Final publication uses reviewed normal fast-forward pushes only. Source/build hashes and data contents are unchanged by this publication-evidence update. The final remote main head is compared to local HEAD before delivery; reports, normalized metadata and exact original data pointers are part of main.
