# DeepTesting and Monte Carlo improvement phase — implementation notes

## Scope and verification status

This phase is in progress. No strategy logic, historical candle data, databases, or `DATA/` paths were changed. No commit or push was performed. Existing local workspace edits were present before this phase and have been preserved.

## DeepTesting progress

The engine reports completed candles to the async task callback at its existing 512-candle cadence. The task record now derives the global percentage from completed nodes plus the active node's authoritative candle percentage. Active work is capped at 99%; the task reaches 100% only through successful terminal completion. The node detail and global dashboard bars are sourced from the same task record.

## DeepTesting STOP / cancellation

`POST /api/v1/tasks/{task_id}/cancel` requests cooperative cancellation for a live DeepTesting task only. The worker checks its cancellation event every 64 candles and raises the engine's explicit `CANCELLED` error. The run is persisted as `CANCELLED`, the task retains partial progress, and no queued later node in that task is started. Terminal task updates cannot change the task back to running/completed. STOP acts on the active task ID rather than a node inferred from the current screen.

Cancellation is currently process-local: tasks that survived a server restart do not have a live worker event to signal. Cross-process worker cancellation is not yet implemented.

## Monte Carlo settings

The interface defaults to 1,000 simulations, 1,000 random/chart points, 10 displayed paths, 200,000 initial capital, 1% risk, one max active trade, block size 10, stress multiplier 1.5, and a generated seed when blank. Settings are persisted in browser local storage. APPLY validates values, persists the applied snapshot and does not start a run. Submission is blocked while settings are dirty or invalid and uses the applied configuration.

Risk, max-active-trades, and capital inheritance from a selected DeepTesting scenario is not yet wired into scenario-specific selection. The current eligible-node API still selects the latest DeepTesting run per node, not an independently selectable saved scenario.

## Performance investigation

A reusable standard is recorded in `docs/benchmarks/deeptest-xauusd-m1-engulfing-v1.json`. The exact source CSV was found and audited read-only. Its SHA-256, row count, raw/converted range, duplicate count, gap count, and importer compatibility are recorded there. The dry-run passed OHLC validation and did not write anything.

The requested complete DeepTesting measurement is blocked pending verified broker tick metadata and a persisted default-engulfing benchmark node: the current XAUUSD DB has a draft placeholder node with no strategy spec, and the registry has no broker metadata snapshots/instrument registrations. Do not substitute the placeholder or guessed sizing values. Consequently, no actual benchmark runtime, trade count, statistical output, CPU/GPU sample during a run, or profile is claimed.

The current engine prepares indicator series once per invocation, then evaluates each candle. It creates immutable decision-evidence events for the full evaluation. Evidence persistence was changed from bounded batches of 256 to 2,048 rows to amortize SQLite transaction overhead while retaining every event and candle. This remains a hypothesis to measure, not a verified resolution of the slowdown. The original engine implementation and historical identical-run record are not available for a verified before/after comparison; no claim of reaching the reported historical 5–10 second range is made. A comparable benchmark must use the same node/version, data fingerprint, timeframe, range, execution assumptions, and resource conditions before a performance claim is made.

## System monitoring

The persistent shell strip polls `/api/v1/system/metrics` every 2 seconds and identifies the source as the API host/LMS sandbox. CPU and RAM come from psutil; GPU values are supplied only when NVML is accessible. GPU absence is rendered as unavailable, not zero. The current LMS sandbox reports 2 logical threads / 1 physical core and about 2.08 GB total RAM; GPU telemetry is unavailable. These are sandbox resources, not the user's local Windows machine. The API and frontend preview use an isolated `/tmp/hero1-preview-data` database root with `HERO1_SKIP_BASELINE_IMPORT=1`; the user's DATA databases were not used or modified.

## Monte Carlo limitations

The existing Monte Carlo page continues to disclose that START_POINT rotates the completed trade sequence and is not historical candle replay. Scenario subrows with exact scenario/run identity, per-node TEST/DELETE management, and the requested MAX/MIN/DEFAULT/DEEPTESTED curve set are not implemented by this partial phase. Existing historical Monte Carlo run persistence and exports have not been deliberately changed.

## Manual checks added to the guide

See `MONTE_CARLO_MANUAL_TESTING.md` for existing workflows. Add/perform the following before calling this implementation complete:

1. Start a long DeepTesting run and verify both global and page progress are synchronized, incremental, and remain below 100% until persisted completion.
2. Cancel during candle execution; verify the exact task/run becomes CANCELLED, partial progress remains visible, queued nodes do not start, and another new task can start.
3. Refresh during execution and verify progress recovers from the persisted dashboard task.
4. Change Monte Carlo inputs, verify unapplied state, APPLY without submission, invalid input rejection, persistence after reload, and submission of the applied snapshot only.
5. Benchmark the exact historical XAUUSD M1 engulfing baseline against a controlled prior implementation/build and verify identical input fingerprint, candle count, trade ledger, and relevant statistics before reporting timings.
