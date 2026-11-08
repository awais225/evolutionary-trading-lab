# DeepTesting failure investigation and diagnostics

## Supplied task ID lookup

The supplied ID `e06363057db94683bc1bfd7b8727843c` cannot be traced in this checkout: the available registry database `DATA/DATABASE/hero1_registry.db` has no `dashboard_tasks` table (it predates that migration); there is no matching task ID in the repository/workspace logs or data files; and global event #187 is not present in the available registry DB. The available per-instrument XAUUSD DB also predates the `deeptest_runs` / `deeptest_run_trades` schema, so it does not contain the associated run or strategy records. The task's traceback, exception, actual config, point/tick values, candle/spread values and M1 strategy are therefore not accessible here. No exact root cause is asserted.

## What the supplied `0 trades` line proves (and does not prove)

Inspection of the code at `920ecf4c852ce610704b137a8806973058bc4794` establishes the event-building path:

- `services/api/app/deeptesting_service.py`, `execute_run`'s exception handlers (then lines 433–449) caught expected engine/service errors and unexpected exceptions, stored `run.status = "FAILED"`, and returned the run. Unexpected errors were reduced to `UNEXPECTED: <type>: <message>` without traceback.
- `run_batch_async` (then lines 731–759) initialized `trades_n = 0` and only populated it if `run.metrics_json` existed. A failed run with no metrics therefore emitted `(0 trades)` even if execution had failed before entry evaluation. It is **not evidence that the strategy generated zero signals**.
- The resulting event had the run ID and status, but no stage/error payload beyond the generic event text.

This identifies the reporting/diagnostic defect, but not the exception in the user's specific task. Its actual failure stage could have been before or after signal evaluation. The exact input record and inner exception are required to name that root cause.

## Diagnostic changes in this revision

- Structured `DEEPUTEST_STAGE` logs now carry UTC timestamp, task/run/node/instrument/timeframe, stage/status, source module/function/file/line, elapsed time, relevant parameters, and exception class/message/traceback plus leaf frame file/function/line.
- Engine callbacks log stage-level summaries, executed signals, rejections, sizing decisions, SL/TP, fills, exits and no-signal bar counts. Per-bar condition detail is opt-in (`detailed_logging`); per-signal/trade event detail is sampled to the first 100 events per run by default to avoid enormous logs, while stage summaries retain aggregate outcomes.
- Error details are kept on failed run/task records and exposed in the DeepTesting UI with node/run, stage/code, expandable traceback and copy action. Validation/readiness failures map to BLOCKED; unexpected exceptions remain FAILED. The task event payload now includes error code, stage, and run error.
- `execute_run` still persists a failed run rather than swallowing the error; on expected and unexpected errors the stored `run.error` now includes stage, full traceback and exact leaf location.

## Reproduction coverage

Controlled engine fixtures verify historical spread fills, spread-missing rejection, NO SPREAD ignoring absent spread while preserving commission, risk sizing, swap, and a persisted API run whose config/assumptions retain `spread_mode`. The supplied C1 M1 node/data/run could not be executed from this checkout, so no claim is made that the original node #2 issue is fixed or that exact C1 strategy behavior has been reproduced. When the source data/database (including the task/run record) is available, inspect the task payload/error and `deeptest_runs.error`, then map its recorded code/traceback to the stage logs before making any further execution change.
