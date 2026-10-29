# V10.4 — measured generation and DeepTesting throughput

Date: 2026-10-29. Base: `8abf3042a34bd484cdbbf806af5dc00851a9a1be` (V10.3). Intended publication branch: `v10.4-throughput`.

## What actually became faster

These are **isolated synthetic real-engine measurements**, not simulated claims about the user's Windows machine or market profitability. Generation: 24 fresh 100-birth rounds per revision (three successive before/after process pairs, eight rounds each). DeepTest: three rounds per configuration. Medians below. Default Light diagnostics were enabled in the first table.

| Workload | Before | After | Observed improvement |
|---|---:|---:|---:|
| Generation: 100-node seed/birth persistence, nodes/min | 44,321.66 | 49,493.29 | **+11.67%** |
| DeepTest: 4 scenarios, requested/effective workers 1/1 | 5.2998 s | 2.0905 s | **2.535×** |
| DeepTest: 4 scenarios, requested/effective workers 2/2 | 5.2201 s | 1.6676 s | **3.130×** |
| DeepTest: 4 scenarios, requested/effective workers 4/2 | 5.4549 s | 1.6532 s | **3.300×** |

Each DeepTest scenario passes 12,000 deterministic fixture bars through the real historical engine and persists **11,985 trades**. Serial throughput increased from 45.29 to **114.80 scenarios/min**; effective-two-worker throughput reached **143.92 scenarios/min**, **28,748.37 persisted trades/s**. Those trade rates are genuine persisted row counts, not a substitute for bars or tests. Input-bar throughput is reported separately in the validation JSON.

The sandbox has **2 logical CPUs**, Linux 6.1.158, Python 3.13.16. Requested four workers was **not** a four-worker experiment. Both effective-two-worker rows have the same resource ceiling; their difference includes cold-start/scheduling noise, not a meaningful scaling comparison.

### Profiler OFF cross-check

| Workload | Before | After | Observed improvement |
|---|---:|---:|---:|
| Generation nodes/min (24 rounds/side) | 54,445.44 | 55,617.86 | **+2.15%** |
| DeepTest 1/1, batch seconds | 4.7757 | 2.0915 | **2.283×** |
| DeepTest 2/2, batch seconds | 4.1207 | 1.5313 | **2.691×** |
| DeepTest 4/2, batch seconds | 4.0407 | 1.5683 | **2.577×** |

DeepTest gains remain substantial with recording OFF. The generation-only OFF gain is small (+2.15% pooled; individual process-pair gains -0.86%, +0.57%, +3.70%), so it is not evidence of a large universal CPU gain. Light additionally benefits from equivalent precompiled redaction regexes; its three repeated pair gains were +12.42%, +18.35%, +7.19%. OFF/Light were separate runs, not a randomized profiler-overhead experiment or statistical confidence interval. A final three-round Light sample measured **-1.83%** generation change (earlier candidates measured +10.09%/+13.70%); that adverse sample is retained in the JSON and motivated the explicitly longer 24-round generation comparison above. No performance-related backend code changed between that final short sample and the repeated generation validation.

**Benchmark limits:** generation times cover actual seed/birth construction, validation, deduplication, insertion, alias assignment, cache invalidation and required strategy exports on fresh isolated databases. They do not certify sustained fully evaluated 20,000-node evolutionary campaigns. Mutation/crossover/selection/lineage/RNG preservation is independently regression-tested against frozen original insertion code. DeepTest batch wall time includes feature/engine calculation, normalization, existing worker startup/IPC where applicable, durable scenario/trade persistence and count/pagination checks. Synthetic frame construction, real-market acquisition, the full production scenario controller, HTTP/browser rendering and native antivirus/storage effects are not all inside that batch wall clock. Existing controller/API/polling spans cover those boundaries, but no native end-to-end browser claim is made.

## Bottlenecks and implementation

1. **Generation SQL:** removed the separate research-alias COUNT round trip by moving the same scoped `COUNT(*)+1` into the strategy INSERT. Explicit aliases, run/data-source scoping, duplicates, durable commits and node identity remain unchanged. The three-round trace has 100 fewer SELECTs per 100 births. No cached mutable counts, stale config-digest cache or cached sensitive identifiers were introduced.
2. **Diagnostics hot path:** compile the exact existing identifier/redaction regexes once; same acceptance/rejection behavior, no retained raw-string LRU. No per-bar/per-candidate synchronous diagnostics added.
3. **DeepTest persistence:** replace one commit per trade with an immediate durable first-trade checkpoint, then atomic **256-row chunks**. The 11,985-row fixture uses **48 commits**, rather than 11,985. The first-row boundary preserves cancellation responsiveness and existing durability tests. A chunk fault rolls back only that chunk; previous checkpoints survive.
4. **Retry/completion integrity:** replay accepts only identical retained trade evidence, refuses mismatches/duplicates/unmatched historical numbering, and checks exact final persisted row count. No delete-and-reinsert retry and no falsely successful incomplete persistence.
5. **Preparation:** omit a serial TEMP Parquet encoding that was written only for a size message and never consumed. The in-memory frame and full feature history remain the calculation input. Parallel Arrow IPC is retained. No authoritative data/cache or resumable research checkpoint was removed. This controller improvement is not separately credited with the benchmark speedup above.

Default-Light median per-task trade-persistence time fell **0.9053 → 0.1162 s** in serial mode and **0.9842 → 0.1348 s** at effective two workers. Compute time did not receive a claimed algorithmic speedup. After persistence reduction, real strategy execution, trade serialization/aggregation and process startup/IPC become relatively more important. Inclusive/nested profiler spans overlap and hot-stage samples are not exhaustive; do not add those spans as disjoint wall-clock components.

Peak observed DeepTest process-tree RSS (before → after): serial **173,588,480 → 173,223,936 bytes**; effective two **512,970,752 → 513,466,368 bytes**; requested four/effective two **523,935,744 → 526,999,552 bytes**. A 20 ms sampler can miss short peaks/CPU intervals; summed RSS includes shared pages. Whole-host RAM percentages are not attributable application memory. In the 24-round generation validation, process CPU median was 0.14148 → 0.12730 s under Light (OFF: 0.11285 → 0.11244 s). Per-configuration sampled tree CPU and raw timing rounds are in `V10_4_VALIDATION.json`.

## Parallelism, caching and GPU decision

- Retained the existing **reusable bounded spawned DeepTest pool**, memory/CPU admission guards, IPC, stop checks and CPU fallback. Matching full trades/metrics were verified at effective 1 and 2. Default remains **one worker**; opt-in two-worker use is supported by sandbox evidence, not automatically imposed on Windows.
- No parallel generation: the serial RNG stream, candidate order, mutation/crossover distributions, parent selection and identity must remain reproducible. DB commit/export and Python-controlled candidate handling do not justify unvalidated parallel defaults. No process-per-candidate, unbounded queue or nested pool was introduced.
- No new mutable market/feature/config cache. Existing complete-key caches and invalidation behavior remain; avoiding a cache is preferable to a stale or incompletely keyed one.
- **GPU acceleration is NOT IMPLEMENTED/UNVERIFIED.** This environment exposes neither NVIDIA monitoring nor a working CUDA driver. There is no claim of application GPU execution, compatibility with RTX 4090 Laptop/Python 3.14.6, VRAM usage or GPU speedup. No CUDA/PyTorch/CuPy dependency was added.
- Generation is dominated by branching/tree/string/SQLite work; historical trading has stateful entry/exit/position/risk dependencies; SQLite commits/serialization cannot be fixed by moving them to a GPU. Batched numerical features could be a separate future candidate, but require native compatible libraries, transfer-inclusive timing, full-trade equivalence, bounded VRAM/admission, failure fallback and demonstrated **end-to-end** benefit before implementation/default enablement. Device availability or monitoring alone is not acceleration.
- CPU target 100%, automatic worker maximum, memory limit 50%, disabled GPU default, trading/risk/broker settings and campaign ledger limits are unchanged. Benchmark-only simulator inputs/worker sweeps are in-memory and external to authoritative research.

## Attached log: evidence, not invented attribution

- 155 scenario completions were PARTIAL. Per-scenario phase differences give median historical compute **1.20 s**, median persistence **17.61 s**. This is strong evidence to prioritize DB persistence, not GPU utilization. It is not a native before/after V10.4 measurement.
- Three `WinError 10054` traces occur at 15:19:57.266, 15:20:32.322 and 16:01:12.287 in Proactor transport shutdown. The attachment does **not** prove socket origin, affected run ID or that these resets caused PARTIAL. Existing scenario computation is separate from browser response lifetime; a browser disconnect does not issue an implicit cancellation.
- Added safe ASGI reset telemetry with exception class and scenario ID only when the known request path supplies one; exceptions are rethrown without changing worker status. Root warning/error telemetry captures exception class, never arbitrary raw exception messages. Proactor callbacks outside ASGI can remain unassociated; do not invent their run IDs.
- PARTIAL denotes genuinely confirmed incomplete source coverage in the scenario controller. Completed metrics/trades remain retained; cancellation/failure does not erase previous checkpoints.
- **101,678 source vs 70,954 processed bars:** code prepares the full source frame, then uses `searchsorted` to select the requested time window. Source and eligible-window sizes are different quantities. The attachment lacks requested-date/window evidence to assign the exact **30,724 excluded bars** to boundaries or warmup. New bounded `window_accounting` events report source/eligible/processed counts, before/after-window exclusions and requested/actual timestamps. Indicator warmup can further affect first eligible trade, but is not established as this discrepancy's exact cause.

## Existing UI improved in place

- SPEED LOG gains severity and scenario/run filters alongside existing subsystem/type/time/search filters, stages/resources/status/queue observations, bounded retention and drop counts. Pause only freezes display, not recording/computation. Clear only clears visible entries. COPY ALL still takes the **entire retained backend snapshot**, ignoring filters/pages/pause/clear, with existing clipboard fallback/download. No history truncation or secret-bearing raw payload logging.
- Live Testing gains **STARRED** using the existing authoritative shortlist-backed `starred_only` query with filter `all`. No duplicate star truth, activation, orders or replacement result mapping. Immediate unstar updates and empty state are tested.
- **Existing DeepTesting → LiveTesting performance projection is unchanged**, including original function hashes and the shared projection module. No metrics duplication/remapping/rebuild.

## Confirmed deletion, independently scoped by the backend

The scenario inspector provides “Delete selected scenario…” and a distinct “DELETE ALL DEEPTEST DATA…” control. Exact typed confirmations are independently validated server-side. The client cannot widen SQL scope with a supplied table/scope field.

Deletion refuses active scenarios, pending/not-finished Futures, active/awaiting batches, or live foreign owners; the user must explicitly cancel and wait. Shared request/persistence file guards and consistent lock ordering serialize writers. A regression exercises simultaneous writer/cancel/delete to prevent lock inversion. Durable deleted-run tombstones block scenario/trade/preference resurrection.

Selected deletion removes that scenario's trades, preference/claim/header and its cached entries in completed batch results. Delete-all removes DeepTest result headers/trades/preferences/claims/completed batch ledgers, including orphan result rows; **it preserves node configurations, coverage confirmations, candle provenance, workspace settings, named filters/assignments, and deletion tombstones**. These are settings/shared safety metadata, not completed test results.

Only exactly matched, marked, exclusively owned job directories under OS TEMP are eligible for artifact removal. Unverified/shared caches are retained. Cleanup failure reports a warning after refreshing successfully deleted database rows. Nodes, genomes, ancestry, generations, stars/shortlist, market history, unrelated backtests/live trading and configuration remain untouched. No authoritative deletion was performed for validation.

## Validation and preservation

- Expanded baseline: **343 passed, 17 failed, 4 errors**.
- Expanded modified: **369 passed, 17 failed, 4 errors** (49.26 s).
- **Exact failure/error identities match**, not merely counts. These unchanged failures require missing real datasets/nodes/inventory; no fabricated research fixtures, assertion weakening or test removal was used to turn them green. Full identities/messages are in the JSON.
- **26 new backend cases pass**, including real seeded evolution/RNG/lineage reference comparison, durable chunk failure/cancellation, idempotent/conflicting retry, complete trade history/pagination, deletion scope/ownership/tombstones/active writers/batches, transport retention and unchanged live integration.
- Existing focused suite: **85 passed**. Frontend production build passed (existing >500 kB chunk warning); new UI **8 behaviors**, existing V10.3 **9**, V10.2 dashboard **8/8** passed.
- No new 3,000 or replacement execution/trade cap. Regression persists **6,200** rows, tests retrieval after row 3,000; the real-engine benchmark persists **11,985 per scenario** with matching full-trade digests. Existing full-history/CSV behavior remains.
- All settings and every measured generation-record/RNG/full-trade/non-observational-metric digest match before/after in both Light and OFF. Frozen original insertion behavior also matches genuine reproduction metadata and ancestry.
- **462 original files and 61 protected evidence files hash-verified unchanged**. No whole DATA copy, experiment against authoritative research, reset/truncation, force-push or history rewrite. Repository DATA tree remains `68225e768709dcd9fb9123642a5164e4e68e79ab`.
- Native Windows authoritative 144,424-record DB/sidecars, shutdown behavior, real-market timing and CUDA are **not available for certification** here. Existing failed-preflight evidence/backups/logs remain unchanged; this is not a green native preflight.

## Reproduce / publication

`V10_4_WINDOWS_VALIDATION.md` contains exact isolated native commands, hash comparison, safety boundaries and optional real-market/GPU acceptance steps. `scripts/v104_benchmark.py` refuses existing/source-contained DATA roots, uses explicit in-memory simulator credentials/specification policy and never saves broker settings/connects MT5/places orders. Run the same new script against the before/after source worktrees; `scripts/v104_compare.py` fails if settings or outcomes differ.

`V10_4_VALIDATION.json` is the machine-readable measurement/test/integrity record. Benchmarks were taken from dirty worktrees based on the stated parent and include backend-source fingerprints; they were not falsely labeled as already committed measurements. Publication uses the exact title **`V10.4 optimize generation and DeepTesting throughput`**, normal push to the intended new branch, and a separate receipt containing the real final SHA and remote/blob verification. See that receipt/final response for publication status, not a guessed SHA in this self-referential document.
