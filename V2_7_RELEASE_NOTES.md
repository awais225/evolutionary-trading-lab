# Evolutionary Trading Research Lab V2.7 Release Notes

## Overview
Evolutionary Trading Research Lab V2.7 establishes a permanent, persistent data architecture, strict data reuse with incremental updates, real multi-level progress tracking with stall detection, persisted hardware resource governance (CPU target & GPU toggle), structured activity logging with diagnostic export, and an interactive Data Inventory UI.

---

## 1. Permanent Persistent DATA Architecture
- **Root Location**: `PROJECT_ROOT/DATA` (`DATA_ROOT`).
- **Version-Independent Folders**:
  - `DATA/MT5/raw/` — Permanent raw market data in Parquet format.
  - `DATA/MT5/normalized/` — Canonical normalized data with DST session labels.
  - `DATA/MT5/datasets/` — Master dataset views.
  - `DATA/MT5/metadata/` — Dataset partition metadata.
  - `DATA/nodes/` — Evolutionary nodes and lineage checkpoints.
  - `DATA/genomes/` — Strategy genome definitions.
  - `DATA/features/` — Precomputed feature caches with schema versioning (`core_v1`).
  - `DATA/datasets/` — General materialized datasets.
  - `DATA/research/` — Human-readable experiment and research summaries.
  - `DATA/cache/` — Fast in-memory/disk caches.
  - `DATA/manifests/` — Relative-path metadata manifests.
  - `DATA/metadata/` — Global metadata.
- **Manifest System**:
  - `DATA/manifests/datasets.json` — Maps all discovered and ingested datasets with relative paths, row counts, and date ranges.
  - `DATA/manifests/features.json` — Maps all persistent feature caches keyed by `(dataset_id, feature_set_id, schema_version)`.
  - `DATA/manifests/research.json` — Research memory, nodes, and experiments.
  - All paths in manifests are stored **relative to `DATA_ROOT`** for complete disk and directory portability.

---

## 2. Data Reuse & Incremental Updates
- **Data Reuse Engine**:
  - Discovers existing datasets in `DATA/` on startup and query.
  - When the requested range is already present, the system reuses the data and logs explicitly:
    `ACTION: REUSING EXISTING DATA`
  - Zero unnecessary network requests to MT5 or the simulator.
- **Incremental Synchronization**:
  - When a date range is partially missing, the engine identifies the missing window and requests ONLY the missing delta.
  - Missing bars are validated against integrity rules (monotonicity, OHLC consistency, spread checks), appended safely, deduplicated, and manifests are updated.
- **Persistent Feature Caching**:
  - Precomputed features are saved in `DATA/features/{dataset_id}__{feature_set_id}__{schema_version}.parquet` using stable schema versioning (`core_v1`).
  - Keyed by `(dataset_id, feature_set_id, schema_version)`.
  - When restarting the laboratory, features are reloaded from disk with `ACTION: REUSING EXISTING FEATURES ({feature_set_id})` and are never recomputed.

---

## 3. Real Progress Tracking & Heartbeats
- **Real Progress Computation**:
  - Calculated directly from actual work done (`processed_rows / total_rows`, `completed_features / total_features`).
  - Zero synthetic timer loops or fake progress increments.
- **Multi-Level Progress Hierarchy**:
  - **Overall Job** (e.g. `Sync XAUUSD M15 Market Data`)
  - **Stages** (e.g. `Fetch` -> `Validate` -> `Normalize` -> `Persist`)
  - **Tasks** (e.g. `request_bars`, `integrity_check`, `apply_sessions`, `append_partitions`)
  - **Workers** (Worker ID, task, progress %, heartbeat timestamp)
- **Real ETA Computation**:
  - Calculated using: `elapsed * (1 - prog) / prog`.
- **Periodic Heartbeats**:
  - Long-running operations emit structured heartbeats:
    `[HEARTBEAT] Job <id>: <pct>% | ETA: <eta>s | <message>`

---

## 4. Stall Detection
- **State Distinctions**:
  - `ACTIVE` — Actively progressing; recent progress timestamp received.
  - `WAITING` — Queued for resources or batch allocation.
  - `COMPLETED` — Successfully completed 100% of required work.
  - `FAILED` — Exited with an error or exception.
  - `STALLED` — Incomplete with no progress for > threshold (default 60s).
- **Diagnostics**:
  - Reports `stalled_duration_s` and `last_progress_at`.
  - Automatically transitions back from `STALLED` to `ACTIVE` when progress resumes.
  - Does NOT falsely mark stalled jobs as completed.

---

## 5. Persisted CPU Utilization & GPU Control
- **CPU Utilization Target**:
  - 10% to 100% in 10% increments (default 60%).
  - Dynamically resizes worker pool concurrency based on logical core count (e.g. 32 cores @ 60% = 19 workers).
  - Persisted in `CONFIG/settings.json` and survives restarts.
- **Hardware Acceleration (GPU)**:
  - Toggle persisted in `CONFIG/settings.json`.
  - Honest reporting: Standard indicator precomputation runs on CPU (numpy/pandas); GPU processing state is reported honestly (`GPU: ENABLED`, `GPU Processing: IDLE`). Never displays fake GPU utilization.

---

## 6. V2.7 Feature Precomputation Stall Fix & Architecture
- **Root Cause & Resolution**:
  - 9 indicator generator specs (`price`, `stoch:14:3`, `bb:20:2.0`, `volatility:20`, `volume:20`, `prev_day`, `time`, `regime`, `sessions`) produce multi-column outputs with canonical names (e.g., `close`, `stoch_k:14:3`, `bb_upper:20:2.0`, `rolling_vol:20`, `hour`, etc.) rather than matching the generator spec string.
  - Implemented `is_spec_cached(spec, cached)` mapping generator specs to their primary column outputs so all 91-92 cached features are recognized instantly on startup.
  - Features cached in `DATA/features/*.parquet` are recognized immediately, logging `ACTION: REUSING EXISTING FEATURES (core_v1, core_v1)` with 0ms stall.
- **Feature Task State Machine (`backend/app/features/tasks.py`)**:
  - States: `QUEUED`, `RUNNING`, `COMPLETED`, `REUSED`, `SKIPPED`, `FAILED`, `STALLED`, `CANCELLED`.
  - Every task has a unique `task_id` (e.g. `FP-000001`), `correlation_id`, `worker_id`, dataset, timeframe, row counts, and timestamps.
  - Terminal conditions strictly enforced: every task resolves to `COMPLETED`, `REUSED`, `FAILED`, `CANCELLED`, or `STALLED`.
- **Parallel Worker Dispatch**:
  - Missing features are precomputed across the dynamically-sized worker pool according to the CPU target % rather than single-threaded sequential execution.
- **Watchdog & Heartbeat**:
  - Periodic heartbeat (1-2s) logging rows processed, worker, CPU, GPU (honestly `IDLE`), and RAM.
  - Multi-stage watchdog: Stage 1 diagnostic log -> Stage 2 mark `STALLED` -> Stage 3 recover or cancel.
- **Safe Stop / Start**:
  - Stopping the research loop cleanly cancels all active feature tasks and resets current task to `IDLE`.

---

## 7. Main Dashboard UI & Workflow Progress
- **Compact Top Status Row (`TopStatusRow.jsx`)**:
  - Single compact horizontal bar across the top of the interface.
  - Clickable interactive popovers with real system diagnostics:
    - **WiFi**: Network interface, IP, latency, connection state.
    - **MT5**: Terminal status (Simulator / Real), account number, broker/server, latency, feed rate.
    - **CPU**: Physical/logical cores, CPU target %, actual %, effective workers, active workers.
    - **GPU**: Device model, enabled/disabled state, VRAM allocation, actual %, processing state (`IDLE` vs `ACTIVE`).
    - **RAM**: Used GB, total GB, utilization %, configured limit.
    - **DATA**: Root path, dataset count, total market rows, cached Parquet files, persistent reuse mode.
    - **DB**: Path, user_version (3), 371 strategies, lineage integrity.
    - **RESEARCH**: Mode, generation, active population, target ceiling.
    - **PROGRESS**: Milestone name, route fill %, active task description.
- **Main Milestone Progress Path (`MilestoneProgressPath.jsx`)**:
  - Metro / bus route with 7 stops:
    `System Ready` ── `MT5 Connected` ── `Data Sync` ── `Feature Precompute` ── `Validation` ── `Evolution` ── `Complete`
  - Connecting line fills continuously according to actual workflow progress.
  - Interactive clickable milestone stops opening a diagnostic flyout with progress bar, elapsed time, current task, completed work items, and individual subprocess breakdown per dataset/timeframe.
- **Live Activity Collapsible Sidebar (`LiveActivitySidebar.jsx`)**:
  - Docked on the right side of the workspace.
  - Arrow collapse/expand toggle (`▶` to collapse, `◀` to expand).
  - Slim 38px vertical strip when collapsed with unread count badge.
  - Expanded view includes active task diagnostic, category filter pills, search, and one-click "📋 COPY LOG" button.
  - Event feed strictly sorted **newest first**.
- **Log Tab Clipboard Copy**:
  - "📋 COPY TO CLIPBOARD" button at the top of the Terminal Log tab with instant visual confirmation ("✓ COPIED TO CLIPBOARD").

---

## 8. Strict Invariants Preserved
- `frontend/src/pages/EvolutionTree.jsx`: 100% untouched.
- `DATABASE/lab_state.db`: 371 strategies preserved, schema user_version 3 intact.
- Persistent `DATA/`: preserved across builds, restarts, and runs.
  - Dynamically calculates effective workers: `max(1, round(logical_cores * target_pct / 100))`.
  - Dynamically resizes the `ProcessPoolExecutor` worker pool upon setting changes.
- **GPU Acceleration Toggle**:
  - ON / OFF toggle on Dashboard and Settings page.
  - When ON, probes hardware via `nvidia-smi` and utilizes GPU acceleration if available; cleanly falls back to CPU if no GPU hardware is detected.
  - When OFF, runs in 100% CPU mode.
  - No fake GPU metrics; reports real utilization and VRAM usage.
- **Apply Settings**:
  - Settings page features an **"APPLY SETTINGS"** button.
  - Immediately validates settings, applies them to running services, and persists to `CONFIG/settings.json` (and `CONFIG/lab_config.yaml`).

---

## 6. Detailed Activity Feed & Diagnostic Copy Log
- **Default Ordering**: Newest events displayed at the top by default.
- **11 Filter Controls**:
  - `Newest` / `Oldest`
  - `Errors` / `Warnings` / `Active`
  - `MT5` / `Features` / `Workers` / `Dataset` / `Research` / `System`
- **Copy Log Button**:
  - Generates a full formatted diagnostic log string containing system hardware specs, resource metrics, database verification status, persistent data paths, and recent event logs.
  - Copies to clipboard and flashes visual confirmation: **"✓ LOG COPIED"** for 3 seconds.

---

## 7. Data Inventory UI & APIs
- **Dashboard Section**:
  - Located on the Overview page under Computational Resource Monitor.
  - Displays discovered datasets, row counts, stored time ranges, broker/source tags, cached feature counts, and file sizes.
  - Highlights `REUSABLE · NO RE-FETCH` status badges.
- **APIs**:
  - `GET /api/jobs` — Tracked jobs with multi-level progress and stall detection.
  - `GET /api/jobs/{job_id}` — Single job details with stages, tasks, and heartbeats.
  - `GET /api/data/inventory` — Comprehensive summary and itemized inventory of datasets, features, and manifests.
  - `GET /api/settings` — Current configuration merged with `CONFIG/settings.json`.
  - `POST /api/settings` — Validates, applies dynamically, and persists to `CONFIG/settings.json`.
  - `GET /api/logs/diagnostic` — Formatted diagnostic text for clipboard export.

---

## 8. Critical Invariants Verified
- `frontend/src/pages/EvolutionTree.jsx`: **100% untouched** (original components, React Flow lineage, handles, and styling preserved).
- `DATABASE/lab_state.db`: **100% preserved** (schema user_version = 3, 371 strategies verified intact).
- All batch startup scripts (`START.bat`, `start.bat`, `pre-requisite.bat`) preserve `DATA/`.
- Full automated test suite: **116 passed out of 116 tests**.

---

## 9. V2.7 Final Orchestration State Machine & Activity Restoration
- **Deterministic Pipeline Stage Flow**:
  $$\text{DATA SYNC} \rightarrow \text{DATA VALIDATION} \rightarrow \text{FEATURE DISCOVERY} \rightarrow \text{FEATURE PRECOMPUTATION} \rightarrow \text{FEATURE VALIDATION} \rightarrow \text{DATASET READY} \rightarrow \text{RESEARCH / NODE GENERATION} \rightarrow \text{BACKTESTING} \rightarrow \text{VALIDATION} \rightarrow \text{QUALIFICATION} \rightarrow \text{EVOLUTION} \rightarrow \text{NEXT RESEARCH CYCLE}$$
- **Explicit Stage Transition Logging**:
  - Dual logging (`log.info` + `activity.info`) on every hand-off:
    `[SUCCESS] [FEATURES] Feature precomputation completed`
    `[SYSTEM] [ORCHESTRATOR] Transitioning FEATURES → FEATURE_VALIDATION`
    `[INFO] [FEATURES] Feature validation started`
    `[SUCCESS] [FEATURES] Feature validation completed`
    `[SYSTEM] [ORCHESTRATOR] Transitioning FEATURE_VALIDATION → DATASET_READY`
    `[SUCCESS] [DATA] Dataset ready for research`
    `[SYSTEM] [ORCHESTRATOR] Transitioning DATASET_READY → RESEARCH`
    `[INFO] [EVOLUTION] Starting research generation`
- **Active Task Watchdog (`backend/app/orchestrator/stages.py`)**:
  - Continuous inspection for active stages ($>30$s idle threshold).
  - Reports: current stage, task, item, percentage, last op, elapsed, worker status, CPU %, GPU %, RAM GB.
  - Distinguishes `WORKING — no progress event received` from worker termination, preventing false failure flags.
- **Verification Before Advancing**:
  - Precomputation completion verified across 5 checks (artifact exists on disk, columns exist, row count matches dataset, Parquet integrity, task completed) before transitioning to research.
- **Population Deficit Calculation Fix**:
  - Evaluates active pipeline candidates (`BORN`, `BACKTESTING`, `SURVIVED`, `VALIDATING`) rather than all historical `QUALIFIED` strategies.
  - Spawns candidate batches toward the target ceiling (500 nodes) without ever stalling.
- **Activity Progress & Subtask Ticks Restored**:
  - Task name, progress (`21 / 21`), percentage (`100%`), horizontal progress bar, status icons (`✓`, `●`, `⚠`, `✕`), current operation, elapsed time, throughput (`2,781 bars/s`), last activity age (`updated 0.4s ago`).
  - Major pipeline overview bars for Data Sync, Feature Precompute, Feature Validation, Node Generation, and Backtesting.
  - Collapsed strip shows live active count / percentage badge (`LIVE ACTIVITY • 3 ACTIVE`).
- **Unified Task State API**:
  - `GET /api/workflow/task_state` exposes authoritative backend task state consumed simultaneously by Activity sidebar, Metro progress path, status row, and dashboard.
