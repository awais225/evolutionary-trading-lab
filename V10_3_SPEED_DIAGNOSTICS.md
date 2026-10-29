# V10.3 — SPEED LOG and complete diagnostic snapshots

## Manual use

1. Start the existing application normally. No worker, broker, risk, power, quota,
   Python or campaign settings need to change. This feature does not start research.
2. Expand the existing Live Activity sidebar. Its separate **SPEED LOG** section
   preserves Activity and the existing logs/theme/navigation. Expand SPEED LOG.
3. Light recording is the default. Start your own normal operation if desired.
   **Detailed 60s** temporarily measures all hooked stages, then returns to light.
   **Recording OFF** disables collection; `LAB_SPEED_PROFILE=off` starts disabled.
4. Search, subsystem/type and local-time range filters, and 50-row pages affect
   display only. **Pause display** does not stop backend recording. **Clear display**
   does not delete backend retention, aggregates, research, bars, trades or files.
5. Click **COPY ALL**. It requests a fresh **complete retained backend snapshot**,
   including all retained events, timing samples/percentiles, counters, resources,
   workers, retention losses, metadata, gaps and evidence summary. Display filters,
   clearing and pagination are not sent to this endpoint.
6. On success, paste the JSON into LMSArena and ask for an evidence-based analysis
   distinguishing measured findings from hypotheses and unavailable measurements.
   If clipboard permission fails or JSON exceeds the 2 MiB clipboard safety limit,
   a **complete JSON download is requested instead**, with explicit feedback. Open
   that file and paste its contents, or attach it. No shortened substitute is copied.
   **Export ALL JSON** is an independent full-download option. A download request is
   not proof that a browser has completed saving; check your browser's downloads.

An unavailable recording status before opening the section is not an idle/healthy
claim. Closing/collapsing the section stops its polling, not backend measurement.
Retention losses are visible and included in the export; COPY ALL is not unlimited
historical logging. Repeated successful copies have distinct snapshot IDs.

## Source map and interfaces

- `backend/app/speed.py`: bounded recorder, fail-open spans/counters/events,
  monotonic nesting, resource sampler, worker IPC ingestion, summary and TEMP flush.
- `backend/app/speed_api.py`: `/api/speed-log/snapshot`, `/events?after=&limit=`,
  `/recording` and numeric-only `/frontend`; pure-ASGI response-through-stream timer.
- Existing `performance.py` timers bridge into the recorder without changing
  candidate construction/selection RNG expressions, ordering or persistence rules.
- Hooks: controller/population/batch/state, candidate construction/selection,
  mutation/crossover/validation/hash/duplicate/insert/commit/cache/export,
  database lock acquisition/SQL/commit, Activity, root logging handlers, data load,
  feature preparation/indicators/signals/loop/metrics, complete trade-list
  serialization, DeepTest scheduling/execution/progress/result writes, research
  exports, CSV stream wall time and API preparation/stream response bytes.
- `SpeedLogPanel.jsx` and `speedLog.js`: compact section within the original
  `LiveActivitySidebar`, independent display controls, bounded nonoverlapping
  polling and complete export/copy helpers. No new runtime/test dependencies.

`GET /snapshot` is coherent under the recorder lock and ignores display query
parameters. It includes raw retained timing sample windows; compact `/events`
polling omits those arrays. `/events` carries aggregates and a bounded incremental
window, its cursor and retention information. No backend DELETE/clear operation is
provided. `/recording` accepts `profile`, `capacity` (100..5000), `sample_every`
(1..1000), `sampling_interval_seconds` (0.5..10) and detailed `seconds` (1..300).
Defaults: 2000 events, hot stages sampled 1/20, resources every 1 second. The UI's
60-second detailed mode never automatically changes any useful-work worker count.

## What the quantities mean

- Durations use `perf_counter`, never wall-clock differences. Wall timestamps are
  for human correlation only. IDs use system UUID entropy, not evolutionary RNG.
- Inclusive time contains nested work. Self time subtracts **recorded synchronous
  same-thread/task children only**. In light mode unsampled children remain inside
  parent self time. Neither is CPU time. Do not sum inclusive stages, cross-process
  stages, concurrent operations, or end-to-end ASGI/CSV wall time into one duration.
- Hot-stage counts/times and percentiles are actual measured subsets, not estimated
  totals. Exact attempted/persisted/invalid/duplicate/processed-bar/trade counters
  are separate. Local call counts expose the sampling denominator. Percentiles are
  unavailable below five measured values; the rolling window holds at most 256.
- Spawned workers send bounded separate diagnostics, popped before research result
  persistence. Run/PID/thread and timing IDs remain attributed to the worker.
  Dispatch IDs are **causal links**, not nested cross-process wall-time subtraction.
  Imports preserve at most 32 actual timing samples per stage/task and 64 retained
  child events. Omitted child events increase reported retention loss; this is not
  silently claimed to be complete worker event history. Worker stage totals and
  counters remain separate from the retained event window.
- Candidate attempts are not inserts. Persisted counters increment on successful
  non-null inserts only. Controller target/current and actual inserted deltas are
  emitted separately. Collision/invalid-candidate entropy reseeds have a retry
  counter; production retry/selection/validation rules are unchanged.
- Bars are not trades. Completed successful tests count their processed bars;
  cancellation does not pretend that the whole requested window was processed.
  DeepTest phase/progress events retain observed processed/eligible bars and closed
  trades. Rates use the entire process-session elapsed window, **including off
  periods**, not a selected run's instantaneous throughput. Test/minute is explicit.
- Resources use actual per-process user+system CPU deltas (one core = 100%, values
  above 100% are valid), system per-core CPU, process RSS and observed descendants.
  The first per-process CPU delta is null. Latest sample timestamp and delta interval
  are supplied. Peak RSS is the observed sampled process-tree RSS sum, not unique
  physical memory. Sampling can miss short-lived processes. Missing values remain
  null/UNAVAILABLE, never fabricated zeros or healthy status.
- Configured/effective capacity, actual process observations, and completed root
  task records are distinct. Root active seconds include waits; idle seconds and
  useful-compute CPU utilization are unavailable. Device GPU metrics do **not** prove
  that this application executed GPU kernels or achieved acceleration.
- The evidence summary ranks measured self-time observations within their stated
  scope, not proven root causes. A median lock/queue delay above 10ms over at least
  five measured calls may yield a **SUSPECTED** investigation note. Missing GPU
  attribution is **UNAVAILABLE**. Low CPU alone never creates a bottleneck claim.

## Bounds, privacy, fault isolation and shutdown

All diagnostics are memory-first, outside authoritative DATA. Event capacity is
hard-bounded at 5000, timing stages/call-count names at 256, counters/workers/active
owners at 128, and latency samples at 256/stage. Display holds 1000 rows and renders
50/page. Drops include event eviction, downsizing and child-transfer omissions;
metric-cardinality rejection counts are attempts, not distinct discarded names.
The resource sampler keeps the latest sample and observed RSS maximum, not an
unbounded process history. Snapshot serialization copies the bounded state.

There is no synchronous diagnostic database/file write per candidate, bar or trade.
Complete trade-list conversion is timed once per backtest, not by adding a profiler
wrapper for every individual trade. Existing required Activity, exports, errors,
duplicate checks and durable database commits remain in place.

Only fixed structured labels, allowed numeric/enum fields, opaque run IDs and
exception class names are recorded. Raw SQL, messages, stack traces, request/response
payloads, genomes, market rows, account fields, credentials and environment dumps
are excluded. Frontend telemetry accepts numeric allowlisted fields only. This
feature inherits the dashboard's existing deployment/access controls; it does not
add authentication or make an internet-exposed deployment private.

Entry/exit/record/worker-report/legacy trace failures are isolated from business
results. Span cleanup restores context/active-operation state even if recording
fails. Real business exceptions still propagate. On shutdown the sampler is stopped,
the bounded final JSON is flushed to the OS TEMP directory under
`EvolutionaryTradingLab/V10.3/speed`, and only the last three owned, UUID-named
snapshots are retained there. Canonical resolved paths inside DATA or the source
root are rejected. Diagnostic failure never replaces a successful research result.

## Known measurement gaps — do not overinterpret

Exact exclusive FastAPI JSON-encoding time, all non-diagnostic frontend requests,
browser paint/chart time, OS I/O wait/hardware counters, thread-idle time, useful-work
CPU, verified app GPU computation and native Windows/MT5/CUDA behavior are not
measured. API response timing includes serialization and streaming send waits; CSV
stream wall includes consumer backpressure. Root logging handler timing covers only
handlers present at startup, not private later/non-propagating handlers. These gaps
are also listed in snapshots. Process-local session aggregates are not a historical
multi-server database. No per-run resource-history reconstruction is claimed.

## Validation

Tests run against explicit fresh external isolated DATA/TEMP, never the preserved
research database. See `V10_3_VALIDATION.json` for exact latest counts, benchmark raw
samples and source/evidence preservation. The expanded regression suite is **not all
green**: its 17 failures + 4 setup errors match untouched V10.2 with the same absent
research/history fixtures. No assertions, research evidence or market data were
changed to make tests pass. The shutdown-cancellation test also has an intermittent scheduling assertion: it
failed in some validation runs and passed in sequential confirmations. A separate
20-repeat scheduler-stress harness called the **unchanged** test and fixtures
against untouched V10.2 and reproduced that assertion twice (18 passes, 2 failures).
It concerns publication of the closing flag before the bulk queue is fully drained.
Production stopping rules and assertions were not changed to mask it. This known
race remains disclosed; this feature is not a native safety certification.

The overhead experiment uses the real historical engine on a deterministic
synthetic 6200-bar, >6000-trade fixture, nine rotated repetitions per mode, without
resource sampling, IPC, research database or real market. Reported light/detailed
deltas are relative to V10.3 OFF (which still dispatches wrappers), not a comparison
with pre-V10.3 or a speedup claim. Near-zero/negative light deltas are timing noise,
not evidence that recording speeds up work. Native workloads and sampler/IPC costs
need actual deployment evidence; no native profiling run is requested or fabricated.
