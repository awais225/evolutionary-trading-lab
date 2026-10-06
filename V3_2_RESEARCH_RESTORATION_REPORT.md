# EVOLUTIONARY TRADING RESEARCH LAB V3.2
## TECHNICAL REPORT: COMPLETE DATA RESTORATION, HISTORICAL NODE DISCOVERY & BACKTEST RECOVERY

**Software Version:** EVOLUTIONARY TRADING RESEARCH LAB V3.2  
**Data Schema Version:** 3.2  
**Date:** October 4, 2026  
**Status:** ALL SYSTEMS OPERATIONAL (184/184 TESTS PASSED)  

---

### 1. Root Cause of Historical DATA Loading Failure in V3.1

Prior to V3.2, the laboratory exhibited a critical disconnection between historical persistent research artifacts on disk and runtime operational state:

1. **Reconciliation Disregard of Research Entities:**
   `DataDiscoveryEngine.reconcile_with_database()` in `discovery.py` solely verified `datasets` records against raw market partitions. It contained zero discovery, reading, or reconciliation logic for evolutionary nodes (`strategies`), backtesting records (`backtests`), validation reports (`validations`), or generation milestones (`generations`).
2. **Path & Artifact Structure Scanning Mismatch:**
   Historical research was persisted as both flat strategy JSON files (`RESEARCH/strategies/strategy_000001.json` through `000750.json`) and backtesting subdirectories (`RESEARCH/backtests/strategy_000711/` containing `metrics.json`, `trades.parquet`, `equity.parquet`). The discovery scanner only checked flat `.json` files inside `DATA/nodes/` and `DATA/backtests/`, completely missing all directory-based backtest records and all strategies situated in `RESEARCH/` or `DATA/research/`.
3. **Database Population Gap:**
   `DATABASE/lab_state.db` held only the historical baseline of 371 strategy records. The 379 strategies generated subsequently (IDs 372 through 750), along with 35 completed backtests (strategies 711–749) and 11 passed validation records (strategies 713–744), resided unindexed on disk.
4. **Research Engine Starting from Zero / False Re-Generation:**
   Because startup did not reconstruct runtime state from disk artifacts, `EvolutionEngine.total_nodes()` and `Database.next_strategy_id()` were bounded by the un-reconciled database. Resuming research falsely treated historical nodes as non-existent or attempted to regenerate them from Node 372 or Node 1.

---

### 2. Architectural Modifications & Restorations Implemented in V3.2

#### A. Dedicated Restoration Engine (`backend/app/data/restoration.py`)
Implemented `ResearchRestorationEngine` fulfilling the authoritative 18-step startup sequence (V3.2 §2):
* **Discovery Across All Standard Paths:** Discovers nodes, backtests, validations, and generations across `DATA/nodes`, `DATA/strategies`, `DATA/research/`, `DATA/backtests`, `RESEARCH/strategies`, and `RESEARCH/backtests`.
* **Idempotent Reconciliation:** Resolves duplicate hashes, links parents to children, preserves original IDs (1 to 750), original generation numbers (0 to 4), and original creation timestamps.
* **Lifecycle State Recovery:** Restores nodes with passed validations to `QUALIFIED`, nodes with passing detail backtests to `SURVIVED`, and sub-threshold nodes to `FAILED`/`KILLED`.
* **Full Backtest & Trade History Hydration:** Restores complete metrics (Profit Factor, Win Rate, Total Trades, Sharpe, Drawdown, Net Profit) and links trade history parquet files (`trades.parquet`, `equity.parquet`).

#### B. Startup Prerequisite Sequence (`backend/app/main.py`)
Integrated `ResearchRestorationEngine.restore_all(emit_logs=True)` into the application lifecycle prior to event bus attachment and laboratory research loop activation.

#### C. Full Trade History Streaming API (`backend/app/api/routes.py`)
Enhanced `/api/strategies/{sid}/trades` to load complete trade records from persistent `trades.parquet` whenever available (e.g. 259 complete trades for Strategy #711), falling back to `trades_sample` only if the parquet artifact is absent. Added `/api/research/restoration/report` and `/api/research/restore` endpoints.

#### D. Authoritative DATA Mirroring & Schema 3.2 Manifest
Maintained continuous synchronization to:
* `DATA/manifest.json` (schema 3.2, recording latest persisted node 750, resume point 751).
* `DATA/manifests/research.json`.
* `DATA/database/lab_state.db` (exact mirror of SQLite state).
* `DATA/nodes/node_ledger.jsonl`.

---

### 3. Startup Restoration Diagnostic Report

```
===========================================================
EVOLUTIONARY TRADING RESEARCH LAB V3.2
PERSISTENT DATA RESTORATION
===========================================================
[DATA] DATA_ROOT: /home/user/evolutionary-trading-lab/DATA
[DATA] DATA discovery: COMPLETE
[DATA] Existing manifests: manifest.json, research.json, datasets.json, features.json
[DATA] Existing datasets: 62 datasets
[DATA] Existing feature sets: 64 feature sets
[DATA] Historical nodes discovered: 750
[DATA] Historical nodes restored: 750
[DATA] Generations restored: 5 generations (Gen 0 - 4)
[DATA] Parent/child relationships restored: 313 connections
[DATA] Validation results restored: 11
[DATA] Historical backtests discovered: 35
[DATA] Historical backtests restored: 35
[DATA] Qualified nodes restored: 11
[DATA] Dead nodes restored: 252
[DATA] Pending operations: 0
[DATA] Duplicate records: 0
[DATA] Unresolved records: 0
[DATA] Database reconciliation: COMPLETE (750 strategies synchronized)
[DATA] Evolution Tree reconstruction: COMPLETE (750 nodes, 552 edges)
[DATA] Dashboard synchronization: COMPLETE
[DATA] Data integrity: VERIFIED
===========================================================
[OK] HISTORICAL RESEARCH RESTORATION COMPLETE
[OK] READY FOR NEXT RESEARCH PHASE
```

---

### 4. Verification & Testing Matrix

| Test Suite | Tests | Result | Details |
|---|:---:|:---:|---|
| **Restoration & Recovery (V3.2)** | 8 | **PASS** | Tests A through H (Discovery, Nodes, Backtests, Tree, Dashboard, Idempotency, No Unnecessary Research, Portability). |
| **Non-blocking & Stability** | 12 | **PASS** | Activity non-blocking, PID lease, discovery TTL cache, port checks. |
| **Dataset Eligibility & Contracts**| 14 | **PASS** | Timeframe eligibility, feature contracts, quarantine paths. |
| **Watchdog & Orchestration** | 10 | **PASS** | Dynamic pool concurrency, watchdog recovery, feature verification. |
| **Feature Tasks & Milestones** | 8 | **PASS** | Non-blocking milestones, atomic state transitions. |
| **MT5 Sync & Clean Execution** | 18 | **PASS** | MT5 / SIMULATOR fallback isolation, real data integrity. |
| **Lineage & Evolution Tree** | 14 | **PASS** | 750 nodes rendered across generations 0–4 with intact connections. |
| **Comprehensive Core Suite** | 100 | **PASS** | Genomes, fitness scoring, risk boundaries, data storage. |
| **TOTAL** | **184** | **184 / 184 (100% PASS)** | Complete regression safety. |

---

### 5. Disk Space Optimization Summary

* **LMS Workspace Storage Limit:** 128.00 MB
* **Current Persistent Workspace Usage:** 89.64 MB
* **Headroom Remaining:** **38.36 MB** (safely below 128 MB limit)
* **Optimization Measures:** Redundant temporary backups purged, test cache parquet files cleaned, duplicate artifact hardlinks utilized, node_modules isolated.
