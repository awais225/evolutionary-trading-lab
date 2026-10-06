# Evolutionary Trading Research Lab — V2.9 Technical Release Notes

## Overview: Critical Dashboard Freeze, Backend Non-Blocking & Stability Resolution

This release addresses and resolves the root causes of dashboard unresponsiveness observed 3–5 seconds after starting continuous research, optimizes backend resource locking and disk I/O, protects against HTTP socket exhaustion, and hardens the laboratory against dual-instance execution.

---

## 1. Root Cause Analysis of 3–5s Dashboard Freeze

### Cause A: HTTP Socket Pool Exhaustion in Frontend
- **Trigger**: The dashboard components (`TopStatusRow.jsx` 2.5s, `LiveActivitySidebar.jsx` 2s, `MilestoneProgressPath.jsx` 2s, `App.jsx` 3s, `Overview.jsx` 4s, `Logs.jsx` 2s) polled backend REST endpoints independently.
- **Mechanism**: Under HTTP/1.1, browsers maintain a strict maximum limit of **6 concurrent TCP connections per origin**. Standard `fetch` calls in `frontend/src/api.js` had no timeouts (`timeout: undefined`). When backend endpoints slowed down or queued, in-flight polling requests accumulated. Once 6 sockets became blocked, the browser blocked all subsequent HTTP requests from leaving the browser, freezing UI buttons ("STOP", "DELETE DATA", navigation tabs) completely.
- **Resolution**:
  1. Configured request timeout in `frontend/src/api.js` using `AbortSignal.timeout(8000)` with `AbortController` fallback. Any hanging request automatically aborts after 8 seconds and frees the socket.
  2. Implemented request coalescing / deduplication for identical in-flight `GET` requests in `frontend/src/api.js`. Overlapping polls for `/api/status`, `/api/pipeline/state`, etc., share a single in-flight Promise rather than opening redundant TCP sockets.
  3. Added `inFlightRef` locks to all frontend polling loops (`TopStatusRow`, `MilestoneProgressPath`, `LiveActivitySidebar`, `App`, `Logs`, `Overview`) preventing concurrent overlapping ticks.

### Cause B: Heavy Physical Parquet Validation in Polled Status Loop
- **Trigger**: Every poll of `/api/pipeline/state` or `/api/system/status_row` invoked `EvolutionEngine.get_node_generation_state()`.
- **Mechanism**: `get_node_generation_state()` directly executed `DiscoveryEngine.get_startup_status_report(self.db)`, which looped over all 5 core timeframes (`M1`, `M5`, `M15`, `M30`, `H1`) and called `validate_timeframe_raw_data()`. This read 5 full parquet files from disk, validated NaN sums across all rows, checked timestamp monotonicity, and executed 11 separate SQLite queries every 2 seconds.
- **Resolution**:
  1. Added a 20-second TTL cache to `DiscoveryEngine.get_startup_status_report(force_refresh=False)`. Rapid polls return the cached validation status in sub-millisecond memory time.
  2. Added explicit cache invalidation via `disc.invalidate_startup_status_cache()` triggered when data is ingested, synced, or cleared.
  3. Added a 1.0-second TTL cache to `EvolutionEngine.get_node_generation_state()`, collapsing multiple concurrent status queries into a single evaluation and invalidating immediately upon node births.

### Cause C: Lock Inversion Between PipelineStateManager and SQLite
- **Trigger**: `PipelineStateManager.to_dict()` acquired `PipelineStateManager._lock`, and inside that critical section invoked `evo.get_node_generation_state()`, which acquired `Database._lock`. Simultaneously, background evaluation threads held `Database._lock` while updating pipeline stages (attempting to acquire `PipelineStateManager._lock`).
- **Resolution**: Decoupled `evo.get_node_generation_state()` outside the `with self._lock:` block in `PipelineStateManager.to_dict()`, eliminating the lock hierarchy inversion.

### Cause D: Deterministic Seed Duplicate Starvation Loop
- **Trigger**: When starting a clean research run or when candidate deficits occurred, `seed_population()` used a deterministic PRNG seed (`20250925`). If generated genomes collided with existing database hashes, 0 candidates were inserted.
- **Mechanism**: Because 0 candidates were inserted, `cur_nodes == 0` remained true on every orchestrator tick, triggering an infinite tight busy-loop that flooded SQLite with duplicate lookups.
- **Resolution**:
  1. Implemented dynamic entropy salting on duplicate collisions: when a genome hash collides, `_entropy_counter` is incremented and mixed with microsecond timestamps and process IDs (`(int(time.time() * 1000) ^ os.getpid() ^ (counter << 16)) & 0x7FFFFFFF`) to re-seed the generator.
  2. Added retry escalation in `seed_population()` and shortfall guarantees in `reproduce()`, ensuring novel candidate genomes are generated and generation progresses.

---

## 2. Asynchronous Data Management with Dual Confirmation

- **Endpoints**:
  - `POST /api/data/clear/xauusd`
  - `POST /api/data/clear/cache`
  - `POST /api/data/clear/nodes`
  - `POST /api/data/clear/genomes`
  - `POST /api/data/clear/all`
- **Execution Architecture**:
  - All clearance routines execute asynchronously via dedicated background threads through `_run_clear_job()`.
  - Registered with `JobManager` to provide tracking, real status, and stall protection.
  - Return immediate `{ "ok": true, "job_id": "job_...", "status": "completed" | "processing" }` responses.
  - Timeout protection prevents thread pool starvation.
  - Automatically invalidates discovery engine caches upon clearance.
- **Dual Confirmation Support**:
  - Nuclear reset (`/api/data/clear/all`) supports both `"CLEAR ALL DATA"` and `"DELETE THE DATA"`.
  - Modal in `Settings.jsx` allows entering either phrase before enabling the confirmation button.

---

## 3. High-Performance Non-Blocking COPY LOG

- `ActivityManager.generate_diagnostic_log()` previously executed synchronous SQLite queries (`SELECT COUNT(*) FROM strategies`, `PRAGMA user_version`). Under database write locks, this caused `COPY LOG` requests to stall.
- The diagnostic generator now operates 100% in-memory:
  - System resources read via `psutil`.
  - Event trail read from memory ring buffer `_recent_cache`.
  - Zero SQLite locks, zero lab locks, zero contention.
  - Generates full technical log in < 5ms.
- Frontend components (`LiveActivitySidebar.jsx`, `LiveActivityPanel.jsx`, `Logs.jsx`) feature graceful fallback to local memory state so `COPY LOG` never fails.

---

## 4. Single-Instance PID Lease File & Port 8787 Pre-Bind Check

- **Module**: `backend/app/lease.py` (`PidLeaseManager`)
- **Lease File**: `LOGS/lab.pid`
  - Records process ID, port 8787, start time, and heartbeat.
  - Detects live PID via `psutil.pid_exists` / `os.kill(pid, 0)`.
  - Overwrites stale leases from crashed processes while blocking concurrent dual instances.
  - Cleaned up automatically on shutdown.
- **Port Pre-Bind Verification**:
  - `check_port_free(8787)` verifies socket availability before attempting bind.
  - Integrated into `START.bat` via `backend/tools/check_port.py` (Exit Code 0: Free, 10: Existing Lab, 20: Occupied).

---

## 5. Strict Constraints & Integrity Verification

1. **EvolutionTree.jsx**: Unchanged (`62017667f4568816916285c42d6ba7ed98db2eccbc45f971c222ab48213061f4`).
2. **DATABASE/lab_state.db**: 371 strategies preserved, schema version 3 intact.
3. **Frontend Production Bundle**: Built and verified (`frontend/dist/index.html`).
4. **Test Suite Results**:
   - `tests/test_v2_9_nonblocking_and_stability.py`: 7/7 PASSED.
   - `tests/test_v2_8_clean_data_and_execution.py`: 11/11 PASSED.
   - `tests/test_scenarios_a_to_f.py`: 8/8 PASSED.
   - `tests/test_final_testing_and_orchestrator_loop_fix.py`: 8/8 PASSED.
   - **Total**: 34 / 34 tests passed with zero failures.
