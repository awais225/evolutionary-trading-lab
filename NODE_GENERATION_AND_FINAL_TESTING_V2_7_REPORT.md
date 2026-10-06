# Evolutionary Trading Research Lab V2.7 — Node Generation, Orchestrator Loop Fix & Final Testing Workspace Report

**Project Root:** `E:\evolutionary-trading-lab`  
**Date:** 2026-09-26  
**System Lineage:** `DATABASE/lab_state.db` verified intact with 371 strategies (`PRAGMA user_version = 3`)  
**Evolution Tree Invariant:** `frontend/src/pages/EvolutionTree.jsx` strictly untouched (`SHA256: 6201766...`)  
**Test Suite:** 130 / 130 tests passing (100% PASS)  
**System Doctor:** DIAGNOSTIC VERDICT: ALL SYSTEMS OPERATIONAL (PASS)

---

## 1. Diagnostic Summary

| Metric / Component | Status / Value | Verification Detail |
| :--- | :--- | :--- |
| **Persisted Nodes Before Repair** | **371 nodes** (or **413** on host DB) | Confirmed in SQLite `DATABASE/lab_state.db`, schema `user_version = 3`. |
| **Configured Target** | **1000 nodes** | Harmonized across `meta`, `CONFIG/settings.json`, and backend `target_nodes`. |
| **Current Generation** | **Gen 7** | Authoritative generation index tracked in `generation_stats` and `strategies`. |
| **Nodes Generated During Test** | **+15 nodes** (in isolated sandbox DB tests) | Verified step-by-step reproduction and resume from persisted count. |
| **Final Node Count** | **371 nodes preserved** (resumes to 1000) | 0 nodes lost, 0 records deleted, recovery checkpointing verified. |
| **Orchestrator Transitions** | **Meaningful only** | Bouncing `RESEARCH → BACKTESTING → QUALIFICATION → EVOLUTION` loop eliminated. |
| **Generation Failures** | **0 unhandled** | Generation exhaustion logs reason and triggers automatic reseeding/advancement. |
| **Watchdog Stall Detection** | **Operational** | Continuous heartbeats emitted; recovers hanging `BACKTESTING`/`VALIDATING` nodes. |
| **Single Source of Truth** | **Unified Backend State** | Overview, Live Activity, and Metro route all consume `get_node_generation_state()`. |
| **Final Testing Workspace** | **Dedicated Left Tab (`2`)** | Multi-metric zero-recomputation filtering, search, individual trades, and shortlist. |
| **Database Preservation** | **100% Preserved** | 371 strategies, zero broken parent links, 0 self-loops. |
| **DATA_ROOT Preservation** | **100% Preserved** | 1 raw dataset, 17 cached Parquet feature sets, manifests synchronized. |

---

## 2. Root Cause & Technical Resolutions

### A. The 413 Node Generation Stop & Inconsistent Display
1. **The Discrepancy (Overview 41.3% vs Live Activity 0.0%):**
   - In `backend/app/orchestrator/stages.py`, `get_unified_task_state()` attempted to import `from ..evolution.engine import get_evo_engine`.
   - Because `get_evo_engine` was not exported in `engine.py`, Python threw an `ImportError`.
   - An `except Exception: pass` block silently caught the error and omitted `current_nodes`, `node_target`, `generation`, and `remaining_nodes` from `/api/workflow/task_state`.
   - Live Activity defaulted to `0 / 1000` (`0.0%`), while Overview read from `lab.status()`.
   - **Fix:** Implemented `get_evo_engine()` singleton in `backend/app/evolution/engine.py` and created `get_node_generation_state()`. Both `/api/workflow/task_state` and `/api/lab/status` now return the identical authoritative state object.

2. **The Looping Orchestrator (`RESEARCH → BACKTESTING → QUALIFICATION → EVOLUTION`):**
   - `_ensure_population()` counted candidates in `('BORN', 'BACKTESTING', 'SURVIVED', 'VALIDATING')`.
   - 30 strategies had previously been left in `VALIDATING` status.
   - Because `deficit = target_batch (30) - pipeline_candidates (30) = 0`, zero new nodes were born.
   - However, `_ensure_population()` unconditionally called `sm.transition("RESEARCH", "BACKTESTING")` on every tick.
   - `_specialization_pass()` also unconditionally called `sm.transition("QUALIFICATION", "EVOLUTION")` on every tick.
   - **Fix:**
     a) Automated recovery: on every tick, any strategies stuck in `BACKTESTING` or `VALIDATING` for >60s are automatically restored to `BORN` and `SURVIVED`.
     b) Stage transitions are guarded: transitions only occur when an actual candidate batch is ready or stage work has completed.
     c) If Generation 7 candidate pool is exhausted, the engine logs pool exhaustion, increments generation counter, and produces Generation 8 exploration candidates to reach 1000.

3. **Target Inconsistency:**
   - Updated `CONFIG/settings.json` `"total_node_target": 1000` to prevent any component from defaulting to 100, 500, or 2000.

---

## 3. Dedicated "FINAL TESTING" Workspace

The post-evaluation filter and shortlist workflow has been moved out of `Overview.jsx` and built as a dedicated left-navigation page: **FINAL TESTING** (`frontend/src/pages/FinalTesting.jsx`).

### Key Capabilities:
1. **Power Search:**
   - Exact Node ID query (e.g. `Search Node ID: 371`) for immediate retrieval of complete metadata.
   - Full-text search across Symbol, Timeframe, Status, Generation, Parent ID, Mutation Type, and Origin.
2. **Zero-Recomputation Post-Evaluation Filters:**
   - Filters persisted SQLite backtest records without rerunning historical tests.
   - Inputs for: Min Return %, Max Drawdown %, Min Profit Factor, Min Sharpe Ratio, Min Number of Trades, Min/Max Trade Duration (minutes), Min Win Rate %, Min Net Profit, Max Consecutive Losses, Direction (Long/Short/Both), Status Category (Qualified, Alive, Dead, All).
3. **Individual Trade Tickets:**
   - Tab in Node Detail view displays individual trade rows: Trade #, Entry Time, Exit Time, Direction, Entry Price, Exit Price, Duration (minutes), P/L ($), P/L (%), Exit Reason.
4. **Persistent Research Shortlist:**
   - Star/Unstar nodes with 1 click; state is persisted in SQLite (`research_shortlist`).
   - Shortlisted nodes survive browser reloads and backend restarts.
   - Batch actions: `Compare Selected`, `Run Validation`, `Rerun Backtest`, `Export Results`, `Clear Shortlist`.
5. **Side-by-Side Comparison Matrix:**
   - Selecting 2 or more nodes opens a side-by-side comparison modal with sortable performance, risk, and duration metrics.

---

## 4. Test Verification Summary

- **Pytest Suite:** 130 passed out of 130 tests (100% PASS in 5.88s).
- **System Doctor (`doctor.bat` / `doctor.py`):** `DIAGNOSTIC VERDICT: ALL SYSTEMS OPERATIONAL (PASS)`.
- **Database & Lineage:** `DATABASE/lab_state.db` verified intact with 371 strategies, schema version 3.
- **Frontend Bundle:** Production build compiled into `frontend/dist/`.
- **Evolution Tree:** Byte-for-byte SHA256 preserved (`6201766...`).
