# Monte Carlo async integration notes

## Existing HERO1 mechanisms inspected (2026-11-06)

- `packages/storage/src/hero1_storage/registry_models.py::DashboardTask` is the authoritative task record in the registry database. It persists task ID, category, status, unit counts, current unit, summary/error, payload, acknowledgement and timestamps.
- `services/api/app/task_service.py` creates/updates/finishes these records and calculates percentages from completed/total units. Its category allowlist currently needs a `MONTE_CARLO` entry. It treats `COMPLETED`, `CANCELLED`, `FAILED`, and `BLOCKED` as terminal.
- `services/api/app/api/routers/tasks.py` exposes shared task list/detail/acknowledgement; `apps/web/src/state/tasks.tsx` polls this same endpoint every two seconds; `apps/web/src/components/TaskProgress.tsx` renders independent task bars and applies the existing five-second completed/cancelled acknowledgement lifecycle and persistent failed-task details.
- `services/api/app/deeptesting_service.py::run_batch_async` is the existing background-thread/session pattern, using fresh registry and instrument sessions in the worker and `task_service` for progress/terminal state. `services/api/app/api/routers/deeptesting.py` shows creation and dispatch from the API.
- `services/api/app/api/routers/monte_carlo.py` currently creates MonteCarloRun records and calls `simulate` inline. `services/api/app/monte_carlo_service.py` owns the existing simulation math.
- There is currently no generic task cancellation endpoint or persisted cancellation-request field in DashboardTask. Integration will store the cancellation request in the task's existing JSON payload and have the worker check it between bounded simulation batches; the Monte Carlo API will expose a task-specific cancel route. The MC run record's existing `result_json` will be updated with the latest partial aggregate after each completed batch.

## Intended integration points

Reuse DashboardTask / task_service, the existing global polling UI, and the existing daemon-thread worker pattern. Use one DashboardTask and one MonteCarloRun per selected node/method configuration. Associate their IDs through the DashboardTask payload and MonteCarloRun config JSON (no competing task store). Workers must open independent DB sessions. On server startup, any persisted MONTE_CARLO tasks left QUEUED/RUNNING are classified as interrupted/FAILED, not silently resumed. Cancellation is observed between bounded batches. The normal web flow will submit async work, retain task IDs through backend state/history, and refresh the shared task list.
