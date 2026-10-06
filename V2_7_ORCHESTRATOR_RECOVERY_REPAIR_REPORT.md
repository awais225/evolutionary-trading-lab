# Evolutionary Trading Research Lab — Orchestrator, Watchdog & Frontend Recovery Report

**Version:** V2.7 / V2.8 / V2.9 Stabilization  
**Date:** September 27, 2026  
**Status:** ALL ISSUES SURGICALLY RESOLVED · 40/40 AUTOMATED TESTS PASSING  
**Verified SHA256 (`EvolutionTree.jsx`):** `62017667f4568816916285c42d6ba7ed98db2eccbc45f971c222ab48213061f4` (STRICTLY UNTOUCHED)

---

## 1. Executive Summary & Problem Diagnosis

During research runs reaching ~371 to ~413 nodes, the research laboratory experienced a cascading failure characterized by:
1. **The Orchestrator Crash:** `TypeError: 'str' object is not callable` in `backend/app/orchestrator/lab.py` during population screening and candidate reproduction.
2. **The 10 Hz Infinite Failure Storm:** The orchestrator loop caught the exception, logged the traceback, but immediately re-attempted execution at `0.1s` intervals (`loop_interval_s`) without consecutive failure tracking or exponential backoff.
3. **Recovery Counter Inflation (`4118 -> 4130 -> 4134 -> 4142`):** In the tight failure storm, watchdog recovery routines repeatedly flipped strategies between `BACKTESTING`/`VALIDATING` and `BORN`/`SURVIVED` on every tick. The recovery counter tracked the number of state flips, inflating by dozens every few seconds without advancing research.
4. **Database & API Freeze:** The 10 Hz loop hammered SQLite with write transactions, holding locks and causing incoming FastAPI API requests (`/health`, `/status`, `/api/data/clear/*`) to stall or time out.
5. **DELETE DATA Stuck on `Processing...`:** Because research and workers were not paused prior to deletion, and because threads contested SQLite write locks, clear operations hung indefinitely waiting for locks.
6. **Frontend Black Screen:** `useRef` was used in `App.jsx` without being imported from React, triggering `ReferenceError: useRef is not defined` when mounting in the browser. In the absence of a top-level React Error Boundary, React 18 destroyed the DOM tree, resulting in an unrecoverable blank screen.
7. **Master Log Disappearance & Flooding:** The logging subsystem wrote disk logs to `ROOT_DIR / "lab.log"` rather than `LOGS/lab.log`, while repeating errors flooded the in-memory activity ring and obscured diagnostic history.

---

## 2. Root Cause Analysis & Architectural Flaws

| Subsystem | Root Cause | Cascade Effect |
| :--- | :--- | :--- |
| **StageManager Contract** | `current_stage` was defined as `@property def current_stage(self) -> str:`, but a caller at line ~1005 called it as a method `sm.current_stage()`. | Raised `TypeError: 'str' object is not callable`, aborting `_ensure_population` before candidates were born. |
| **Orchestrator Tick Loop** | `_loop` in `lab.py` caught exceptions in a `try/except` block, logged traceback, and executed `self._stop.wait(0.1)` in a `while True` loop. | Ran 10 failed ticks per second (600/minute), generating high CPU spikes and SQLite write lock contention. |
| **Strategy Watchdog Recovery** | `_check_generation_watchdog` ran `UPDATE strategies SET status='BORN' WHERE status='BACKTESTING'` without updating timestamps or tracking attempt limits. | Evaluations that failed were immediately reset to `BORN`, re-queued by `_screen_batch()`, failed again, and re-recovered, inflating counter by 4118, 4130, 4134... |
| **Data Clearance Endpoints** | Clear endpoints did not acquire a mutual exclusion lock or pause the orchestrator and worker pool prior to deleting files and purging database tables. | File handles remained open by workers, and transactions deadlocked on SQLite write locks, leaving frontend showing `Processing...`. |
| **Frontend Initialization** | `useRef` was referenced on line 70 of `App.jsx` without importing it on line 1 (`import React, { createContext, ... } from "react"`). | Browser threw `ReferenceError: useRef is not defined`, crashing React render tree into a blank black screen. |
| **Diagnostic Logging** | File logging wrote to `ROOT_DIR / "lab.log"` instead of `LOGS/lab.log`, and `ActivityManager` had no deduplication for repeated errors. | Error floods emitted thousands of identical events into memory and obscured diagnostic log entries. |

---

## 3. Surgical Technical Solutions Implemented

### A. StageManager Contract Dual Compatibility (`stages.py`)
`StageManager.current_stage` is defined as a property returning `CallableString(self._current_stage)`:
```python
class CallableString(str):
    """String subclass that allows callable invocation `s()` returning str(s)."""
    def __call__(self, *args, **kwargs) -> str:
        return str(self)
```
- Property access (`sm.current_stage == "RESEARCH"`, `f"Stage: {sm.current_stage}"`) returns `str`.
- Method access (`sm.current_stage()`) returns `str` without raising `TypeError`.
- `sm.get_current_stage()` provides an explicit method contract.
- All callers in `lab.py` and `engine.py` access `sm.current_stage` consistently.

### B. Controlled Orchestrator Retry, Backoff & `SAFE PAUSED` State (`lab.py`)
Replaced the tight `while True` failure loop with consecutive failure tracking and safe failure state transitions:
1. **1st Failure:** Logs full traceback, publishes warning, and waits `2.0s` (controlled backoff).
2. **2nd Failure:** Logs warning, publishes warning, and waits `5.0s` (increased backoff).
3. **3rd Consecutive Failure:** Transitions orchestrator into `SAFE PAUSED` mode:
   ```python
   with self._lock:
       self.paused = True
       self.safe_paused = True
       self.safe_paused_reason = f"Repeated orchestrator failures ({self._consecutive_failures} consecutive). Last error: {self.last_error}"
   ```
4. Emits stop diagnostic: `ORCHESTRATOR STATUS: SAFE PAUSED — REPEATED FAILURES`.
5. Broadcasts `orchestrator_safe_paused` WebSocket event.
6. Research loop pauses safely without hammering SQLite. The dashboard, `/health`, `/status`, `/logs`, and management APIs remain 100% active and responsive.
7. Resuming via `lab.resume()` or `lab.start()` clears `self.safe_paused = False` and resets `_consecutive_failures = 0`.

### C. Idempotent Strategy Recovery & Attempt Thresholding (`lab.py`)
Replaced blanket SQL updates with idempotent recovery and candidate thresholding:
1. Replaced raw `UPDATE strategies SET status='BORN'` with `self._recover_stuck_strategies(force=True)`.
2. Queries strategies stuck in `BACKTESTING` or `VALIDATING` past 120 seconds (`updated_at < (now - 120)`).
3. Deduplicates candidates via `self._recovered_strategy_ids` so the same strategy is never recovered twice in a single cycle.
4. Tracks per-strategy attempts in `self._strategy_recovery_attempts[sid]`:
   - If `attempts < 3`: Updates status to `BORN`/`SURVIVED` and updates `updated_at = time.time()`.
   - If `attempts >= 3`: Marks strategy `status="FAILED"`, `creation_reason="Failed recovery: evaluation timeout threshold exceeded"`, permanently removing it from the evaluation cycle.
5. Emits distinct recovery ID (`REC-YYYYMMDD-XXXXXX`).
6. Throttles candidate pipeline starvation checks in `_generation_watchdog_check()` to at most once per 5.0 seconds.

### D. Safe, Non-Blocking Data Management (`routes.py`, `Settings.jsx`, `api.js`)
1. Implemented mutual exclusion cleanup lock: `_CLEAR_LOCK = threading.Lock()`.
2. Automatically pauses active research and cancels running feature tasks before file deletions or database updates:
   ```python
   lab = get_lab()
   if lab.running and not lab.paused:
       lab.pause()
       time.sleep(0.3)
   ```
3. Background execution with 5-second initial wait and background job tracking via `JobManager`.
4. In `frontend/src/pages/Settings.jsx`, if a clear operation takes longer than immediate return (`res?.status === "processing"`), it polls `api.jobDetail(res.job_id)` with a 15-second timeout, resetting the button upon completion or timeout.
5. Added explicit `AbortSignal.timeout(20000)` to clear API calls in `frontend/src/api.js`.

### E. Frontend Crash Protection & Visible Initialization Error Screen (`App.jsx`, `ErrorBoundary.jsx`, `main.jsx`)
1. Fixed missing `useRef` import on line 1 of `frontend/src/App.jsx`.
2. Created `frontend/src/components/ErrorBoundary.jsx`:
   - Catches any React rendering or initialization exception.
   - Probes backend health (`fetch("/health")` and `/api/status`) to determine `backendStatus`: `CONNECTED` (green) or `UNAVAILABLE` (red).
   - Displays required fallback screen:
     - Header: `🧬 EVOLUTIONARY TRADING RESEARCH LAB`
     - Subtitle: `The dashboard could not initialize.`
     - Badge: `Backend status: CONNECTED`
     - Error box: `<actual error>`
     - Action buttons: `[↻ RETRY]`, `[OPEN HEALTH STATUS]`, `[VIEW DIAGNOSTIC LOG]`.
3. Wrapped `<App />` in `main.jsx` and `<ActivePageComponent />` in `App.jsx` with `<ErrorBoundary>`.
4. Built production bundle (`npm run build`) without errors.

### F. Master Log Disk Persistence & Activity Deduplication (`activity.py`, `logging_setup.py`)
1. Pointed logging `FileHandler` to authoritative path: `LOGS/lab.log`.
2. Implemented error throttling & deduplication in `ActivityManager.emit()`: identical error/warning strings within a 2.0-second window are counted in `_throttled_error_count` and suppressed from spamming WebSocket/memory.
3. Bounded in-memory activity ring buffer to 500 entries (`MAX_ACTIVITY_ENTRIES = 500`).
4. Enhanced `generate_diagnostic_log()` to read the tail of `LOGS/lab.log` alongside resource metrics and activity items for COPY LOG functionality.

---

## 4. Test Suite Execution & Verification

All automated test suites were run and passed completely:

```text
============================= test session starts ==============================
platform linux -- Python 3.13.14, pytest-9.0.3, pluggy-1.6.0
collected 40 items

tests/test_v2_7_regression_repair.py ......                              [ 15%]
tests/test_v2_9_nonblocking_and_stability.py .......                     [ 32%]
tests/test_v2_8_clean_data_and_execution.py ...........                  [ 60%]
tests/test_scenarios_a_to_f.py ........                                  [ 80%]
tests/test_final_testing_and_orchestrator_loop_fix.py ........           [100%]

======================== 40 passed in 3.44s ========================
```

### Constraint Audit:
- **`frontend/src/pages/EvolutionTree.jsx`:** SHA256 verified unchanged (`62017667f4568816916285c42d6ba7ed98db2eccbc45f971c222ab48213061f4`).
- **Minimalistic Top Status Row (`TopStatusRow.jsx`):** Untouched.
- **Collapsible Live Activity Panel (`LiveActivitySidebar.jsx`):** Preserved and functional.
- **LMS 128 MB Footprint:** Total persisted project directory is ~57 MB (excluding `node_modules` which does not persist across snapshots).

---

## 5. Conclusion

The Evolutionary Trading Research Lab V2.7/V2.8/V2.9 architecture is now stable, resilient, and fault-tolerant. Orchestrator exceptions trigger controlled retry backoff and safe pausing rather than infinite recovery storms. Strategy recovery is strictly idempotent, data management endpoints cannot hang, and the frontend is shielded by a comprehensive React Error Boundary.
