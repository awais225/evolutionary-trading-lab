# Evolutionary Trading Research Lab — Data Discovery, Startup Fix & Batch Execution Architecture (V2.8)

**Date**: 2026-09-27  
**Status**: COMPLETE & VERIFIED  
**Diagnostic Verdict**: `ALL SYSTEMS OPERATIONAL (PASS)`  
**Test Suite**: 146 / 146 Passed (100%)  
**Evolution Tree Invariant**: Preserved (SHA256: `62017667f4568816916285c42d6ba7ed98db2eccbc45f971c222ab48213061f4`)  
**Database**: 371 Strategies, 371 Nodes, Schema `user_version = 3`

---

## 1. Executive Summary

This release resolves the batch execution environment ambiguity, establishes authoritative physical market data discovery before pipeline progression, eliminates fake progress prechecks, separates MT5 terminal connection logging from historical data fetching, implements explicit stage continuity after feature precomputation, introduces live watchdog heartbeat and stall diagnostics distinguishing worker liveness, and validates all acceptance scenarios (A through F).

---

## 2. Key Architecture & Fixes Implemented

### 2.1 BAT File Execution Environment & PowerShell Interception
- **Explicit CMD Startup Logging**: Added authoritative launcher tracking blocks to all root batch scripts:
  - `START.bat` & `start.bat`
  - `pre-requisite.bat` & `Prerequisite.bat`
  - `doctor.bat`
  - `repair.bat`
  - `RUN_BACKEND.bat`
  ```cmd
  [LAUNCHER] Execution shell: CMD
  [LAUNCHER] Script: <SCRIPT_NAME>
  [LAUNCHER] Working directory: <ROOT>
  [LAUNCHER] Launcher execution confirmed
  ```
- **PowerShell Interception & Delegation Wrappers**:
  - Created companion PowerShell scripts: `start.ps1`, `pre-requisite.ps1`, `doctor.ps1`, `repair.ps1`, `run_backend.ps1`.
  - When executed from PowerShell or Windows Terminal, these scripts detect PowerShell syntax, inform the user, and transparently delegate to `cmd.exe /c` without parser errors or window flickering.

### 2.2 Authoritative Physical Data Discovery & Validation
- **Physical Inspection Before Stage Progress**:
  The pipeline enforces the strict progression:
  $$\text{DATA DISCOVERY} \longrightarrow \text{RAW DATA VALIDATION} \longrightarrow \text{MT5 AVAILABILITY CHECK} \longrightarrow \text{CONTINUE}$$
  - Files are physically verified on disk (under `DATA/MT5/XAUUSD/`, `DATA/MT5/raw/`, `DATA/XAUUSD/raw/`, and `DATA/cache/`).
  - Database rows, strategy counts, manifests, and cached feature arrays are **never** treated as proof of raw market data availability.
- **Physical Dataset Validation Format**:
  For all timeframes (`M1`, `M5`, `M15`, `M30`, `H1`), `validate_timeframe_raw_data()` executes an actual inspection and logs:
  ```text
  [DATA] Checking physical dataset
  [DATA] Dataset path: DATA/MT5/XAUUSD/M15.parquet
  [DATA] Exists: YES
  [DATA] File size: 2,874,210 bytes
  [DATA] Row count: 31,680
  [DATA] First timestamp: 1759248000.0 (2025-09-30 16:00:00 UTC)
  [DATA] Last timestamp: 1790380800.0 (2026-09-25 00:00:00 UTC)
  [DATA] Required columns: ts, open, high, low, close
  [DATA] Null/NaN check: PASS (0 NaN in OHLC)
  [DATA] Duplicate timestamp check: PASS (0 duplicate timestamps)
  [DATA] Timestamp ordering: PASS (strictly monotonically increasing)
  [DATA] Symbol: XAUUSD
  [DATA] Source: MT5
  [DATA] Validation result: PASS
  [DATA] ACTION: REUSING EXISTING DATA
  ```
  If any integrity check fails:
  ```text
  [DATA] Validation result: FAIL (...)
  [DATA] ACTION: EXISTING DATA INVALID/INCOMPLETE
  ```

### 2.3 Independent Database vs. DATA Status Reporting
Implemented `get_startup_status_report()` per spec §12, reporting both layers independently during startup and in `backend/app/doctor.py`:
```text
DATABASE:
  Strategies: 371
  Nodes: 371
  Schema: v3

DATA:
  Raw datasets: 5
  Valid datasets: 5
  Invalid datasets: 0
  Feature sets: 63
  Missing datasets: 0
```

### 2.4 MT5 Fallback Acquisition & Logging Disambiguation
- **Distinct Connection vs. Fetch Logging**:
  - `[MT5] MT5 CONNECTION SUCCESS` is logged when the terminal and bridge initialize.
  - `[MT5] MT5 DATA FETCH SUCCESS` is logged only when historical bars are received.
  - Intermediate milestones confirm: `[MT5] Terminal connected`, `[MT5] Account detected`, `[MT5] XAUUSD symbol detected`, `[MT5] Requesting M1 history`, `[MT5] Received N bars`.
- **Target File Persistence**:
  Raw bars are saved directly to `DATA/MT5/XAUUSD/{TF}.parquet`, verified on disk, and registered with zero overwrite of unrelated valid files.
- **Incremental Delta Checks**:
  When valid data exists, the engine reads the last stored timestamp, requests only the missing range from MT5, and logs:
  ```text
  [DATA] Existing M15 dataset found
  [DATA] Last stored bar: YYYY-MM-DD HH:MM
  [MT5] Requesting incremental delta
  [MT5] Received N new bars
  [DATA] Appending N bars
  [DATA] New total: N
  [DATA] Validation PASSED
  ```
  Or if zero new bars exist:
  ```text
  [DATA] No new M15 bars
  [DATA] Existing dataset remains valid
  [DATA] No rewrite required
  ```

### 2.5 Stage Transition Continuity & Feature Precompute Completion
- Feature computation precomputes indicators (`EMA`, `RSI`, `ATR`) only after raw data is validated.
- Logs per-feature timings and failure tracebacks:
  ```text
  [FEATURE] Computing ema:20... OK (0.04s)
  [FEATURE] Computing rsi:14... OK (0.02s)
  [FEATURE] Computing atr:14... OK (0.03s)
  [FEATURE] Validating computed arrays: PASS
  [FEATURE] Feature cache updated: DATA/features/XAUUSD_M15.parquet
  ```
- **Automatic Unstuck Transition**:
  After feature computation completes, the orchestrator immediately hands off:
  $$\text{FEATURES COMPLETE} \longrightarrow \text{FEATURE VALIDATION} \longrightarrow \text{DATASET REGISTRATION} \longrightarrow \text{RESEARCH INITIALIZATION} \longrightarrow \text{NODE GENERATION} \longrightarrow \text{NODE EVALUATION}$$
  Eliminating post-feature precompute freezes.

### 2.6 Task Watchdog, Heartbeats & Liveness Detection
- Long-running tasks emit periodic heartbeats:
  ```text
  [HEARTBEAT] Task: Feature Precompute (XAUUSD_M15) | 18,400/31,680 rows | Elapsed: 4.2s | Memory: 1.1GB
  ```
- The watchdog checks for tasks exceeding the stall threshold (30s) and distinguishes:
  - `WORKER ALIVE (WORKING - stalled/blocked)`
  - `WORKER DEAD (abruptly terminated)`
  - Stalled tasks are never falsely marked complete.

---

## 3. Acceptance Verification (Scenarios A through F)

All scenarios have been implemented and verified in `tests/test_scenarios_a_to_f.py`:

| Scenario | Condition | Verified Outcome | Test Result |
| :--- | :--- | :--- | :--- |
| **Scenario A** | Empty DB, No Raw Data | Discovery reports missing data $\rightarrow$ MT5 bridge fallback connects $\rightarrow$ Fetches bars $\rightarrow$ Validates $\rightarrow$ Persists to `DATA/MT5/XAUUSD/` | **PASS** |
| **Scenario B** | Empty DB, Valid DATA | Discovery finds M1..H1 $\rightarrow$ Validates files $\rightarrow$ Logs `ACTION: REUSING EXISTING DATA` $\rightarrow$ No download $\rightarrow$ Reconciles DB index | **PASS** |
| **Scenario C** | DB Exists, Corrupt DATA | Corrupt file fails validation $\rightarrow$ Logs `ACTION: EXISTING DATA INVALID/INCOMPLETE` $\rightarrow$ Recovers via MT5 fallback $\rightarrow$ Retains unrelated valid files | **PASS** |
| **Scenario D** | DB & DATA Complete | Validates raw datasets $\rightarrow$ Reads last stored timestamp $\rightarrow$ Fetches incremental delta only $\rightarrow$ No full redownload | **PASS** |
| **Scenario E** | Feature Completion | Logs `FEATURES COMPLETE` $\rightarrow$ `FEATURE VALIDATION` $\rightarrow$ Transitions automatically to `DATASET_REGISTRATION` $\rightarrow$ `RESEARCH_INITIALIZATION` $\rightarrow$ `NODE_GENERATION` | **PASS** |
| **Scenario F** | Worker Stall Detection | Emits heartbeats $\rightarrow$ Detects stalled worker past 30s $\rightarrow$ Reports idle time and `WORKER ALIVE / DEAD` status $\rightarrow$ Never falsely marks complete | **PASS** |
| **Launcher** | Batch Shell Execution | All 7 `.bat` scripts contain CMD headers; companion `.ps1` wrappers delegate to `cmd.exe /c` cleanly | **PASS** |
| **Status §12** | Dual State Report | Database state (371 strategies, schema v3) and DATA state (5 raw, 5 valid, 0 invalid) independently reported | **PASS** |

---

## 4. Invariants & Health Verification

1. **Evolution Tree Invariant**:
   - File: `frontend/src/pages/EvolutionTree.jsx`
   - SHA256: `62017667f4568816916285c42d6ba7ed98db2eccbc45f971c222ab48213061f4` (100% byte-for-byte preserved)
2. **Database & Lineage**:
   - File: `DATABASE/lab_state.db` (371 strategies, 371 nodes, `PRAGMA user_version = 3`)
   - Parent references: 100% valid (0 broken refs, 0 self-loops)
3. **Frontend Production Bundle**:
   - Compiled with Vite: `frontend/dist/index.html` (418 B) and `dist/assets/index-DV39qPP1.js` (892 kB)
4. **Snapshot Storage Budget**:
   - Workspace size: 124.55 MB across 824 files (strictly within 128 MB platform cap)
5. **System Doctor Status**:
   - `python backend/app/doctor.py`: `DIAGNOSTIC VERDICT: ALL SYSTEMS OPERATIONAL (PASS)`
