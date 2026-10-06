# Evolutionary Trading Research Lab V2.7 — BAT Architecture Restoration & 413 Node Generation Fix Report

**Project Root:** `E:\evolutionary-trading-lab`  
**Date:** 2026-09-26  
**System Lineage:** `DATABASE/lab_state.db` verified intact with 371 strategies (`PRAGMA user_version = 3`)  
**Evolution Tree Invariant:** `frontend/src/pages/EvolutionTree.jsx` strictly untouched  
**Test Suite:** 122 / 122 tests passing (100% PASS)  
**System Doctor:** DIAGNOSTIC VERDICT: ALL SYSTEMS OPERATIONAL (PASS)

---

## 1. BAT Architecture Restoration Status

All five launcher and maintenance scripts have been restored to their exact previous working architecture without any experimental shell detection, external sub-process polling loops, or extra console windows:

- **`PRE-REQUISITE.BAT` (and `Prerequisite.bat`):** `restored/preserved`  
  Pure Windows Command Prompt syntax. 8-stage hardware discovery, Python verification, virtualenv creation, dependency installation, and database verification. All experimental inline sub-process polling removed.
- **`DOCTOR.BAT`:** `restored/preserved`  
  Pure CMD virtual environment and system path resolution. Directly invokes `backend/app/doctor.py` without extra window flashing.
- **`REPAIR.BAT`:** `restored/preserved`  
  Clean 4-stage deep repair script. Re-verifies dependencies, rebuilds frontend assets, and verifies database integrity. Zero data loss.
- **`RUN_BACKEND.BAT`:** `restored/preserved`  
  Starts Uvicorn backend on port 8787 using native CMD syntax and isolated parenthesized execution.
- **`START.BAT` (and `start.bat`):** `restored/preserved`  
  Standard 8-stage launcher. Uses standalone Python helper utilities (`backend/tools/check_port.py` and `backend/tools/wait_ready.py`) to eliminate command-prompt flickering, handle existing port instances, launch the backend console, wait for `/health` readiness, and launch the default browser.

**Flickering & Window Handling Confirmation:**
The existing flickering prevention architecture (which uses standalone Python scripts instead of CMD `for /f ('powershell ...')` loops) is completely preserved. No flashing console windows, no repeated command prompts, and no altered browser invocation.

---

## 2. Root Cause Analysis: The 413 Node Generation Stop

A forensic trace through the execution pipeline isolated the exact application and orchestration conditions that caused node generation to stall at ~413 nodes when `TOTAL NODES = 1000` was requested:

### Root Cause A: SQLite Schema Mismatch in `_validation_batch` (Primary Fatal Crash)
- **Defect:** In `backend/app/orchestrator/lab.py::_validation_batch()`, the SQL statement:
  ```sql
  INSERT INTO validations (strategy_id, verdict, robustness_score, walk_forward, stress, monte_carlo, regime_holdout, perturbation, min_trades, created_at, fingerprint) ...
  ```
  attempted to insert into non-existent columns (`verdict`, `walk_forward`, `stress`, `monte_carlo`, `min_trades`). The actual SQLite table definition created in `database.py` is:
  ```sql
  CREATE TABLE validations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      strategy_id INTEGER NOT NULL UNIQUE,
      oos TEXT, walkforward TEXT, perturbation TEXT,
      spread_stress TEXT, slippage_stress TEXT, montecarlo TEXT,
      regime_holdout TEXT, robustness_score REAL, passed INTEGER,
      notes TEXT, created_at REAL NOT NULL, fingerprint TEXT, ...
  );
  ```
- **Consequence:** When the laboratory evaluated the first generation batch on top of the 371 existing strategies (371 + ~42 = ~413 nodes), the candidate strategies passed screening and detail testing and entered `_validation_batch()`. The execution immediately crashed with:
  `sqlite3.OperationalError: table validations has no column named verdict`
  This unhandled exception crashed `_tick()`. On every subsequent loop iteration, `_validation_batch()` retried the stuck candidate and crashed again, permanently halting node generation at 413 nodes.
- **Resolution:** Replaced the erroneous insert with the authoritative table column mapping:
  ```python
  self.db.x("""INSERT OR REPLACE INTO validations
               (strategy_id,oos,walkforward,perturbation,spread_stress,slippage_stress,
                montecarlo,regime_holdout,robustness_score,passed,notes,created_at,fingerprint)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["id"], jd(v.get("oos")), jd(v.get("walkforward")),
             jd(v.get("perturbation")), jd(v.get("spread_slippage_stress")), None,
             jd(v.get("montecarlo")), jd(v.get("regime_holdout")),
             v.get("robustness_score"), 1 if passed else 0,
             jd(v.get("death_reasons", [])), time.time(), val_fp))
  ```

### Root Cause B: Population Cap Culling Generation Pipeline Candidates
- **Defect:** `EvolutionEngine.enforce_population_cap()` compared the count of `ACTIVE_STATES` against `population_size` (10 or 30). Because `ACTIVE_STATES` previously included permanent historical achievements (`QUALIFIED`: 113, `PAPER`: 7), the active count was already > 120, producing an artificial excess > 90.
- **Consequence:** `enforce_population_cap()` retired every newly screened `SURVIVED` candidate at the end of every tick, preventing survivors from advancing to detailed testing or validation.
- **Resolution:** `ACTIVE_STATES` is now strictly scoped to in-flight generation pipeline candidates: `("BORN", "BACKTESTING", "SURVIVED", "VALIDATING")`. Permanent catalog strategies (`QUALIFIED`, `PAPER`) and terminal nodes (`FAILED`, `KILLED`, `RETIRED`) are never retired by the generation population cap.

### Root Cause C: Reproduction Shortfall on Duplicate Collisions
- **Defect:** In a mature database (371 strategies), mutations and crossovers on elite strategies frequently generated genome hashes identical to existing strategies. When `try_insert()` rejected duplicates, the shortfall was discarded, causing `reproduce()` to return fewer candidates than needed (or 0 candidates).
- **Resolution:** Implemented an active shortfall fill loop in `reproduce()`. If mutations collide with existing genomes, the deficit is automatically backfilled via randomized exploration until the quota is satisfied.

### Root Cause D: Generation Watchdog & Pipeline Replenishment
- **Defect:** If all active candidates completed evaluation within a generation before the target was reached, the pipeline candidates count dropped to 0 while waiting for `_tested_this_gen >= target`.
- **Resolution:** Added `_generation_watchdog_check()` in `lab.py`. If `running == True` and `total_nodes < target` and pipeline candidates reach 0, the watchdog detects the empty queue and immediately replenishes candidates via `_ensure_population()`.

---

## 3. Hard Target Requirement & Explicit Stop Reasons

The stopping condition is strictly enforced: as long as `current_persisted_nodes < target_nodes` and the process is not explicitly paused, stopped by the user, or halted by a fatal error, the generation controller continues producing candidates.

When the system halts or pauses, it emits an authoritative diagnostic message:
- `NODE GENERATION STOPPED: TARGET REACHED` (when `persisted_nodes >= target`)
- `NODE GENERATION STOPPED: USER REQUEST` (when the user presses Stop)
- `NODE GENERATION STOPPED: FATAL ERROR` (with explicit exception trace)
- `NODE GENERATION PAUSED: NO ELIGIBLE PARENTS` (if elite pool is empty)
- `NODE GENERATION STOPPED UNEXPECTEDLY` (if loop terminates while below target)

---

## 4. Node Generation Diagnostics & Heartbeat

### Explicit Diagnostic Block
Emitted in Python logs and tracked in the system activity feed:
```text
[NODE GENERATION DIAGNOSTICS]
  TARGET NODES: 1000
  CURRENT PERSISTED NODES: 413
  CURRENT GENERATED NODES: 413
  REMAINING NODES: 587
  CURRENT GENERATION / BATCH: Gen 4
  ACTIVE WORKERS: 4
  PENDING TASKS: 0
  COMPLETED TASKS: 42
  REJECTED TASKS: 11
  DUPLICATE TASKS: 5
  FAILED TASKS: 131
  ELIGIBLE PARENTS: 20
  CHILDREN GENERATED: 42
  CHILDREN REJECTED: 11
  STOP CONDITION: ACTIVE
```

### Selection Basis Transparency
Emitted at the beginning of each reproduction cycle:
```text
[SELECTION BASIS]
  Fitness formula: Multi-objective weighted blend of 8 normalized metrics (profitability, risk-adjusted, drawdown, profit_factor, consistency, oos, robustness, complexity penalty) with expectancy damping (*0.25 if <=0)
  Return weight: 0.20
  Drawdown penalty: 0.15 (hard limit: 30%)
  Trade count requirement: screen >= 3, detail >= 5
  Validation requirement: min_trade_duration >= 120s, robustness >= 0.60, walk-forward majority positive, MC > 30%
  Other constraints: max indicators 8, tournament size 6, species cap 25%
```

### Live Activity Sidebar
- Shows `NODE GENERATION 413 / 1000` with `41.3%` numeric indicator and live animated progress bar.
- Shows stage progress for `Node Generation`, `Backtest`, `Validation`, and `Qualification` with completion checkmarks `✓`.

---

## 5. Persistence, Resume & Reusable Data Architecture

- **Resume Without Regrowth:** When the user configures `TOTAL NODES = 1000`, the orchestrator reads the existing 371 (or 413) persisted strategies from SQLite. It calculates `remaining = 1000 - 413 = 587`, seeds no redundant generation 0 strategies, and continues generating the remaining 587 candidates until 1000 is reached.
- **Data Reuse:** All existing Parquet files in `DATA/MT5/raw/` and cached indicator features in `DATA/features/` are preserved and reused. Zero refetching or recalculation occurs for existing datasets.

---

## 6. Minimum Trade Time Control & Postprocessing Filter

### Dashboard Control
- Located directly beside `TOTAL NODES` on `frontend/src/pages/Overview.jsx`.
- **Label:** `Minimum trade time (minutes):`
- **Default Value:** `2` (editable input).
- Persisted to `CONFIG/settings.json` under `backtest.min_trade_duration_seconds = 120`.

### Separation of Postprocessing vs Execution Recomputation
- **Execution Rule:** If `min_trade_duration_seconds` is changed in backtest settings, backtest executions enforce the minimum duration during the evaluation stage via `death_check()`.
- **Postprocessing Filter:** The `POST-EVALUATION FILTER & RESEARCH SHORTLIST` panel queries `/api/research/filter` and filters persisted historical results in SQLite without recomputing historical backtests (`recomputed: false`, sub-millisecond execution).

---

## 7. Verification Summary

1. **Launcher BAT Files:** Clean, verified no inline PowerShell commands or flickering loops.
2. **Database Integrity:** Exactly 371 historical strategies preserved, schema `user_version = 3`, `integrity: ok`.
3. **Evolution Tree:** `frontend/src/pages/EvolutionTree.jsx` strictly untouched.
4. **Automated Test Suite:** 122 of 122 tests passing.
5. **System Doctor:** `DIAGNOSTIC VERDICT: ALL SYSTEMS OPERATIONAL (PASS)` across all 10 categories.
6. **Frontend Bundle:** `frontend/dist/index.html` built and verified (418 bytes).
