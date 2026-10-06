# Evolutionary Trading Research Lab V2.7 — Data Discovery, Database Reconciliation & Technical Logging Repair

**Project Root:** `E:\evolutionary-trading-lab`  
**Date:** 2026-09-27  
**Database Preservation:** `DATABASE/lab_state.db` verified intact with 371 strategies (`PRAGMA user_version = 3`)  
**Evolution Tree Invariant:** `frontend/src/pages/EvolutionTree.jsx` strictly untouched (`SHA256: 6201766...`)  
**DATA Folder Invariant:** Existing `DATA/` artifacts 100% preserved and untouched (zero destructive actions)  
**Test Suite:** 138 / 138 tests passing (100% PASS)  
**System Doctor:** `DIAGNOSTIC VERDICT: ALL SYSTEMS OPERATIONAL (PASS)`

---

## 1. Architectural Repair: DATABASE != MARKET DATA

The core architectural flaw was that the application previously tied database state directly to market data availability. If `DATABASE/lab_state.db` was new or recreated, the application incorrectly assumed market data was missing and initiated redundant network downloads.

This has been corrected with a layered, decoupled architecture:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        DATA PERSISTENCE LAYER                          │
│             DATA/MT5/ · DATA/{SYM}/raw/ · DATA/features/               │
│                  (Reusable persistent source of truth)                 │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                         [DATA DISCOVERY & VALIDATION]
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        DATABASE INDEX LAYER                            │
│                       DATABASE/lab_state.db                            │
│           (Runtime index/state registered from DATA discovery)         │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        RESEARCH STATE LAYER                            │
│            1000 Target Nodes · Lineage · Features · Backtests          │
└────────────────────────────────────────────────────────────────────────┘
```

### Discovery & Reconciliation Flow
```text
START
  ↓
Load configuration
  ↓
Open/create database
  ↓
Scan DATA_ROOT (MT5, cache, features, nodes, genomes, backtests, manifests)
  ↓
Discover existing datasets (symbol, timeframe, bars, range, integrity)
  ↓
Validate discovered datasets (readability, schema, monotonicity)
  ↓
Register usable datasets into current database index (datasets & master_datasets)
  ↓
Determine missing datasets/features via Decision Tree
  ↓
Fetch ONLY missing deltas if newer bars exist
  ↓
Continue research pipeline
```

---

## 2. Key Technical Implementations

### A. Data Discovery Engine (`backend/app/data/discovery.py`)
- **Explicit DATA DISCOVERY Phase:** Scans `DATA_ROOT`, `MT5/raw/`, `MT5/normalized/`, `{symbol}/raw/{tf}/legacy/`, `{symbol}/raw/{tf}/{profile}/`, `features/`, `nodes/`, `genomes/`, `backtests/`, and `manifests/`.
- **Validation & Reporting:** Verifies readability, column schemas (`ts`, `open`, `high`, `low`, `close`), timestamp monotonicity, and non-negative prices.
- **Structured Logs:**
  ```text
  [DATA DISCOVERY] Scanning DATA_ROOT...
  [DATA DISCOVERY] DATA_ROOT = E:\evolutionary-trading-lab\DATA
  [DATA DISCOVERY] Searching:
    DATA\MT5\
    DATA\cache\
    DATA\features\
    DATA\nodes\
    DATA\genomes\
    DATA\backtests\
    DATA\manifests\
  [DATA DISCOVERY] Found 5 candidate datasets
  [DATA DISCOVERY] Found 15 feature sets
  [DATA DISCOVERY] Found 0 node artifacts
  [DATA DISCOVERY] Found 0 genome artifacts

  [DATA DISCOVERY]
  XAUUSD M5
  Source: MT5
  Bars: 37,272
  Range: 2026-03-29 → 2026-09-25
  Schema: valid
  Integrity: PASS
  Action: REUSE
  ```
- **Database Reconciliation (`reconcile_with_database`):**
  - **Scenario A (DB empty, DATA populated):**
    ```text
    [DATABASE] Existing database contains 0 registered datasets.
    [RECONCILIATION] DATA_ROOT contains 5 usable datasets.
    [RECONCILIATION] Registering discovered datasets into current database...
    [RECONCILIATION] XAUUSD M1 → REGISTERED (31,680 bars)
    [RECONCILIATION] XAUUSD M5 → REGISTERED (37,272 bars)
    [RECONCILIATION] XAUUSD M15 → REGISTERED (24,768 bars)
    [RECONCILIATION] XAUUSD M30 → REGISTERED (12,384 bars)
    [RECONCILIATION] XAUUSD H1 → REGISTERED (6,192 bars)
    ```
    No redownloads, no historical recomputations.
  - **Scenario B (DB populated, DATA populated):** Reconciles existing records, validates disk files, logs `ACTION: REUSE`.
  - **Scenario C (DB empty, DATA empty):** Logs `ACTION: BOOTSTRAP / FETCH REQUIRED DATASETS`.

### B. Feature Precomputation Reusability & Incremental Calculation
- Checks `DATA/features` and `DATA/{symbol}/features` before computing.
- When 100% columns match:
  ```text
  [FEATURE DISCOVERY]
  Dataset: XAUUSD_M5
  Dataset fingerprint: VALID
  Feature version: core_v1
  Existing feature artifact found.
  Columns available: 91
  Columns required: 91
  Compatibility: PASS
  ACTION: REUSE EXISTING FEATURES
  ```
- When a subset of features is missing:
  ```text
  [FEATURE DISCOVERY]
  Dataset: XAUUSD_M5
  Existing features: 89/91
  Missing: 2
  ACTION: COMPUTE ONLY 2 MISSING FEATURES
  ```
  Only missing columns are computed and appended to the matrix.
- **Terminal Completion & Hand-Off:**
  ```text
  [FEATURES] COMPLETE
  [FEATURES] Output validated
  [FEATURES] Artifact persisted
  [FEATURES] Registered in database
  [FEATURES] Downstream dependency released
  [PIPELINE] Transitioning FEATURES → NODE GENERATION
  ```

### C. Single Source of Truth for Progress (`backend/app/orchestrator/pipeline_state.py`)
- Central `PipelineStateManager` tracking:
  `run_id`, `stage`, `stage_index` (1–8), `stage_name`, `task_name`, `task_index`, `task_count`, `overall_completed`, `overall_total`, `overall_percent`, `current_dataset`, `current_node`, `node_completed`, `node_total`, `generation`, `status`, `started_at`, `updated_at`, `elapsed`, `last_success`, `last_error`, `worker_status`, `cpu_usage`, `cpu_target`, `gpu_status`, and `node_accounting`.
- Persists to `DATA/metadata/pipeline_state.json`.
- On restart, checks for previous run and logs:
  `[RECOVERY] Previous run detected: {run_id}. Stage: {stage}. Node progress: {node_completed}/{node_total}. ACTION: RESUME`
- Overview (`/api/lab/status`), Live Activity (`/api/workflow/task_state`), Metro milestones (`/api/workflow/milestones`), and Logs all consume the identical state.

### D. No Silent Blocking & Technical Heartbeats
- Heartbeat emitted every 2 seconds during active research.
- If progress unchanged for >30s:
  ```text
  [WARNING]
  Stage: NODE_GENERATION
  Progress unchanged for 30s
  Last completed: 413
  Current task: Node 414
  Worker: ACTIVE
  Waiting on: active backtest/evaluation worker
  This is NOT necessarily an error.
  ```

### E. Diagnostic Execution Console (`frontend/src/pages/Logs.jsx`)
- **Action Bar:**
  - `📋 COPY LOG` button at the top: exports the complete, un-truncated diagnostic log.
  - Flashes `✓ LOG COPIED` for 2.5s after clicking.
  - Quick Search input and Auto-scroll toggle.
- **Category Filter Chips:**
  `ALL`, `INFO`, `SUCCESS`, `WARNING`, `ERROR`, `DEBUG`, `DATA`, `FEATURES`, `NODE`, `GPU`, `MT5`, `DATABASE`, `SYSTEM`.
  Filtering updates display instantaneously without interrupting background logging.
- **Structured Console Layout:**
  Renders Timestamp (millisecond precision), Level pill, Module (`[DATA.ENGINE]`, `[FEATURES.ENGINE]`, `[EVOLUTION.ENGINE]`), Stage, Dataset, Action, Progress, Worker, Message, and expandable tracebacks for exceptions.

### F. Progress Indicators Restored in Live Activity (`frontend/src/components/LiveActivitySidebar.jsx`)
- **Stage Progress Rows:**
  - `✓ DATA DISCOVERY          100%`
  - `✓ DATA SYNC               100%`
  - `✓ FEATURE DISCOVERY       100%`
  - `▶ FEATURE COMPUTATION      100%`
  - `▶ NODE GENERATION          41.3%`
  - `○ EVALUATION               0%`
- **Symbols:**
  - Completed: `✓` (green)
  - Running: `▶` (blue/green accent, active pulse)
  - Queued: `○` (dim gray)
  - Failed: `✕` (red)
- **Active Task Progress:**
  Displays task title, subtask count, percentage, and character progress representation:
  `▶ XAUUSD M5 FEATURES 14 / 21 66.7% [████████████░░░░░░░]`

---

## 3. Test Verification (Section 35)

All 7 core scenarios were verified with automated unit and acceptance tests:

| Test Case | Scenario Description | Expected Outcome | Verification Status |
| :--- | :--- | :--- | :--- |
| **TEST 1** | Empty/new DATABASE + populated DATA | Discovers 5 datasets, registers them into SQLite, 0 downloads, 0 recomputations | **PASS** |
| **TEST 2** | Empty DATABASE + empty DATA | Detects missing data, initiates bootstrap fetch, registers and persists | **PASS** |
| **TEST 3** | Populated DATABASE + populated DATA | Fast validation of registrations against disk, action: REUSE | **PASS** |
| **TEST 4** | Existing dataset + newer MT5 bars | Existing dataset reused; calculates delta and requests missing delta only | **PASS** |
| **TEST 5** | Feature artifact already exists | Complete feature matrix (91 columns) reused without recalculation | **PASS** |
| **TEST 6** | Feature artifact missing subset | Reuses existing 89 features; calculates only the 2 missing features | **PASS** |
| **TEST 7** | Node generation state agreement | Overview, Live Activity, Log, and PipelineState show the exact same node count | **PASS** |

**Full Pytest Suite:** 138 / 138 tests passed in 8.50s (100% PASS).
