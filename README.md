# 🧬 Evolutionary Trading Research Lab (V2.1)

A **local Windows application**: an autonomous quantitative research laboratory connected to
MetaTrader 5. It continuously **generates, backtests, analyzes, mutates, validates, and paper-tests**
trading strategies represented as machine-readable genomes — an industrial-grade research lab,
not a black-box bot.

---

## 🚀 Quick Start (Windows Workflow)

### STEP 1: Verify & Install Prerequisites
Run `Prerequisite.bat` from the application root:
* Double-click `Prerequisite.bat` in Windows Explorer, OR
* Run from CMD prompt: `Prerequisite.bat`, OR
* Run from PowerShell: `cmd.exe /c Prerequisite.bat`
```bat
Prerequisite.bat
```
* Verifies Hardware (CPU cores, logical processors, RAM, GPU/CUDA).
* Verifies Python 3.10+, Node.js 18+, npm, and MetaTrader 5 terminal detection.
* Sets up the Python virtual environment (`backend\.venv`) and installs dependencies (`backend/requirements.txt`, `MetaTrader5`).
* **Genuine CMD batch script**: operates via `cmd.exe`, outputs clear stage diagnostics `[1/9]` to `[9/9]`, logs to `LOGS\prerequisite.log`.
* **Safe to run repeatedly** — skips already installed packages and **never deletes or resets DATA, DATABASE, RESEARCH, BACKUPS, or LOGS**.
* Stays open with visible `Press any key to close...` on success or failure.

### STEP 2: Launch the Laboratory
Run `START.bat`:
* Double-click `START.bat` in Windows Explorer, OR
* Run from CMD prompt: `START.bat`, OR
* Run from PowerShell: `cmd.exe /c START.bat`
```bat
START.bat
```
* Validates core files, activates Python virtual environment (auto-invokes `Prerequisite.bat` if missing).
* Starts the backend server in an observable CMD process window (`cmd.exe /k`) on `http://localhost:8787`.
* Waits for the backend health check endpoint (`/health`) with pure CMD+Python polling (zero PowerShell alias errors).
* Confirms frontend readiness and checks market bridge status.
* **Automatically opens the dashboard in your default browser (`http://localhost:8787/`).**
* **Launcher remains open and stable** with an active monitor loop; displays clear `STARTUP FAILED` diagnostics if an error occurs.

---

## 🎯 Total Node Target & RECHECK (V2.1)

### Total Node Target
* **User-Editable Ceiling**: Configure `TOTAL NODE TARGET [ 200 ]` directly in the dashboard.
* **Strict Backend Enforcement**: Every strategy creation path (mutation, crossover, indicator/param/timeframe/session/regime mutation, specialization, random exploration) checks `TOTAL NODES >= TARGET` before creating a node.
* **Non-Destructive Resumption**:
  * If the database contains 173 nodes and target is set to 200, the system creates only `174 → 200`. Historical nodes are never regenerated or deleted.
  * If target is reached (e.g. `200 / 200`), system emits `NODE_TARGET_REACHED` and halts new evolution while preserving all research.
  * When target is later increased (e.g. to 500), evolution immediately continues from `201 → 500`.
* **Dead Node Rule**: Dead strategies (`FAILED`, `KILLED`, `RETIRED`) cannot produce new children. All existing historical descendants remain valid.

### RECHECK
* Click **[ 🔄 RECHECK ]** to instantly reconstruct the entire laboratory state from SQLite without running any backtests:
  * Total nodes, Target, Remaining nodes
  * Alive vs Dead counts (`Alive + Dead == Total Nodes`)
  * Backtesting, Validating, Qualified counts
  * Current generation and next strategy ID
  * Ancestry edges, root nodes, max depth
  * Experiment fingerprints and research export manifests
  * Dataset and feature cache metrics
* Immediately refreshes the dashboard and emits activity events.

---

## 🏛️ Directory Architecture (V2)

All persistent application data lives inside the application root by default:
```
Evolutionary-Trading-Lab/
├── DATA/
│   ├── XAUUSD/
│   │   ├── raw/                  <- Master monthly partitioned Parquet files
│   │   │   └── M15/
│   │   │       ├── SIMULATOR_LAB_SIMULATOR_SIM/
│   │   │       └── legacy/       <- Migrated V1 snapshots (reused, not redownloaded)
│   │   ├── features/             <- Incremental precomputed feature Parquets
│   │   ├── metadata/             <- Master dataset fingerprints & partition manifests
│   │   └── quarantine/           <- Corrupted/rejected bars (integrity gate)
│   ├── BTCUSD/
│   ├── NAS100/
│   └── EURUSD/
├── RESEARCH/
│   ├── strategies/               <- strategy_000001.json
│   ├── generations/              <- generation_000001.json
│   ├── experiments/              <- experiment_HASH.json
│   ├── backtests/                <- Complete trades.parquet + equity.parquet
│   ├── validations/              <- Detailed validation reports
│   ├── paper_trading/            <- Paper execution logs & equity curves
│   └── hypotheses/               <- AI researcher structured proposals
├── DATABASE/
│   └── lab_state.db              <- Authoritative SQLite state (+WAL, migrated to V2)
├── LOGS/
│   ├── lab.log                   <- Full rotating log stream
│   └── migration_error.log       <- Safe migration audit log
├── BACKUPS/
│   └── lab_backup_TIMESTAMP.zip  <- Non-blocking SQLite VACUUM snapshot + manifest
├── CONFIG/
│   └── lab_config.yaml           <- Root-relative portable configuration
├── backend/                      <- FastAPI backend, evolution engine, risk layer
├── frontend/                     <- React + Vite dashboard
├── scripts/
├── tests/                        <- 55 automated unit & integration tests
├── Prerequisite.bat
└── START.bat
```

---

## 🔑 Key V2 Architectural Features

1. **Incremental MT5 Data Engine (spec §4, §5, §6, §7)**
   * Reads last stored timestamp; requests **only the missing delta** from MT5.
   * Incoming bars are verified by the **Data Integrity Gate** (OHLC consistency, non-negative spread, positive price, monotonic timestamps).
   * Bad records are flagged and quarantined — never silently deleted.
   * Identical duplicate bars are discarded; conflicting bars are logged to `data_conflicts`.
   * Master dataset identity includes `(symbol, timeframe, source, broker, server)`.

2. **Deterministic Experiment Fingerprinting (spec §9)**
   * Every experiment is content-addressed: `HASH(genome + dataset_version + engine_versions + costs + parameters)`.
   * Unchanged experiments are **reused immediately**.
   * Modified datasets, costs, or code automatically mark old results **STALE** and re-evaluate.

3. **Incremental Feature Engine (spec §8)**
   * Precomputed feature columns are appended only for new bars with warm-up lookback.
   * Code changes in feature implementations (`algo_hash`) invalidate only the affected feature cache.

4. **Paper Trading Position Recovery (spec §12)**
   * Open positions (with entry price, SL, TP, trailing state, ATR) are persisted in SQLite.
   * On restart, positions resume exactly where they stopped — **no orphan trades**.

5. **Connection & Feed Monitoring (spec §15, §16, §17, §18, §19)**
   * Three independent indicators in header: `INTERNET`, `METATRADER`, `DATA FEED`.
   * Clicking MT5 status displays detailed terminal info, latency, and account masking.
   * Automatic reconnect with exponential backoff on terminal disconnection.

6. **Computational Resource Limits (spec §26, §27, §28, §29, §30, §31)**
   * Real CPU worker concurrency limits for ProcessPool workers.
   * Memory budget gate pauses/queues batches if system memory would be exceeded.
   * Safe GPU probing with automatic, non-crashing CPU fallback.

7. **Persistent Kill Switch (spec §33)**
   * Emergency kill switch survives server and machine restarts (stored in config + db meta).
   * Real money execution requires typed confirmation and fails closed.

8. **Operational Activity Stream (spec §20, §21, §22)**
   * Real-time activity events (`INFO`, `SUCCESS`, `WARNING`, `ERROR`) across all subsystems.
   * Broadcast via WebSockets and rotated in SQLite.

9. **Complete Trade History & RESEARCH Mirroring (spec §10, §11)**
   * Complete trade lists and equity curves persisted as Parquet files in `RESEARCH/`.
   * Database stores transactional state, indexes, and summary metrics.

10. **One-Click Backup Facility (spec §40)**
    * Creates `BACKUPS/lab_backup_TIMESTAMP.zip` using SQLite `VACUUM INTO` for consistent snapshots.

11. **Total Node Target & Fast State Reconstruction (spec §13, §14)**
    * Dashboard-tunable ceiling (`TOTAL NODE TARGET`) universally respected by all strategy creation paths.
    * Existing nodes (e.g. 173) are loaded first; evolution only produces remaining nodes (174 → target).
    * `RECHECK` button reconstructs the entire state (nodes, generations, ancestry, fingerprints, progress) from SQLite in under 20ms without re-running backtests.

12. **Smart Brain Architecture & Scientific Research Memory (spec §20–§28)**
    * Hypothesis-driven exploration with structured proposals (strictly whitelisted genome directives).
    * `research_memory` table logs comparative experiments (parent vs child), changed variables, and fitness delta.
    * Anti-redundancy engine prevents repeating failed hypotheses with identical actions/parameters.
    * Detailed failure reasons recorded across screening, backtesting, and multi-regime validation.

13. **Full Stability, Dependency Verification & Self-Repair Engine (V2.3)**
    * `backend/requirements.txt` explicitly specifies all runtime dependencies including `psutil>=5.9.0`.
    * Dedicated dependency verifier (`backend/app/verify_deps.py`) inspects the exact virtual environment interpreter, performs granular per-package import tests, and automatically executes targeted self-repair if any package is missing.
    * Zero false positives: every `[OK]` represents an actual verified import and real smoke test execution (`from app.main import app`).
    * Clear MetaTrader 5 distinction: distinguishes package installation from terminal connection; automatically falls back to high-fidelity SIMULATOR mode on unsupported Python versions (such as Python 3.14).
    * Guaranteed frontend production compilation: `Prerequisite.bat` compiles and validates `frontend/dist/index.html` (size > 0), eliminating runtime build failures in `START.bat`.

---

## 🚀 Windows Launchers & Desktop Architecture (V2.3)

* **`Prerequisite.bat`**:
  - Genuine CMD syntax (`cmd.exe`), completely isolated from PowerShell.
  - Automatically identifies root using `%~dp0` (works across drives and directories with spaces).
  - Inspects CPU, RAM, NVIDIA GPU/CUDA, Node.js, npm, and MT5 installations.
  - Detects virtual environment (`.venv` or `backend\.venv`), creates it if missing, upgrades pip, and installs requirements.
  - Executes `backend\app\verify_deps.py` for granular dependency verification and self-repair (`psutil`, `fastapi`, `uvicorn`, `duckdb`, `pyarrow`, `numpy`, `pandas`).
  - Installs npm packages and pre-compiles the frontend production bundle (`frontend\dist\index.html`).
  - Writes diagnostics to `LOGS\prerequisite.log` and stays open on error or completion (`Press any key to close...`).

* **`START.bat`**:
  - One-click CMD launcher: validates environment, checks port 8787 conflicts, and verifies backend imports.
  - Confirms `frontend\dist\index.html` exists; builds cleanly with full diagnostic logging if missing.
  - Launches backend in an isolated window via `scripts\run_backend.bat` (`cmd.exe /k`).
  - Polls backend `/health` endpoint up to 30 seconds before declaring readiness.
  - Verifies frontend dashboard readiness, logs MT5 status (`REAL` vs. `SIMULATOR`), opens the browser, and remains in a foreground monitoring loop.

* **Desktop EXE Architecture (`EvolutionaryTradingResearchLab.exe`)**:
  - `desktop/app_window.py`: Embedded desktop shell that launches the local research backend and displays the dashboard in a native desktop window (via `pywebview`) or system browser.
  - `EvolutionaryTradingResearchLab.spec`: Production PyInstaller specification bundling backend, embedded dashboard, and dependencies into a standalone Windows executable.
  - `scripts/build_exe.bat`: One-click batch compilation to produce `dist/EvolutionaryTradingResearchLab.exe`.

---

## 🧪 Automated Testing

All 72 automated tests run via `pytest`:
```bash
pytest -v
```
Test categories covered:
* `tests/test_backtest.py`: 8 tests (reproducibility, costs, sessions, regimes, max hold)
* `tests/test_evolution.py`: 6 tests (ancestry, duplicates blocked, diversity cap, population cap)
* `tests/test_features.py`: 8 tests (indicators, DST sessions, no-lookahead properties, cache hits)
* `tests/test_fitness.py`: 9 tests (multi-objective scoring, death rules, complexity penalty)
* `tests/test_genome.py`: 10 tests (DSL schema, mutation operators, crossover, hash determinism)
* `tests/test_v2_features.py`: 13 tests (V1 migration, incremental master store, deduplication, conflict detection, broker identity, feature invalidation, paper recovery, kill switch, resource limits, backup)
* `tests/test_day1_day2.py`: 1 comprehensive test (Day 1 research cycle -> restart -> Day 2 verification -> incremental new data ingestion)
* `tests/test_v2_1_features.py`: 7 tests (launcher validation, node targets, incremental continuation, dead node reproduction prevention, recheck state reconstruction)
* `tests/test_v2_2_features.py`: 4 tests (launcher scripts, research memory schema, failure/survival reasons, anti-redundancy hypothesis engine)
* `tests/test_v2_3_features.py`: 6 tests (requirements consistency, psutil presence, verify_deps self-repair, BAT script structures, desktop EXE packaging layer, frontend dist validation)
