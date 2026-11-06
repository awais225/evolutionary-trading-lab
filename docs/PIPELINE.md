# HERO1 Trading Research Pipeline — Legacy Recovery & Forward Compatibility Audit

**Date:** 2026-11-06 · **Scope:** task `CRITICAL_RECOVERY_LAUNCHER_STARRED.md`, Part 5
**Authoritative status:** what exists today vs. what the legacy system had — see the matrix.
**Rule:** preparation is NOT implementation. Nothing below may be read as "Phase 4/5/7/8 are done".

## 1. What was inspected (legacy, read-only)

| Branch | Head commit | Inspected artefacts |
|---|---|---|
| `v10.4-throughput` | `85b79b1c4097b026111262e300e442814664db31` | `backend/app/orchestrator/stages.py` (authoritative stage list), `pipeline_state.py` (INITIALIZATION→EVALUATION milestones), `backend/app/{backtest,historical_backtest/runs.py,deeptest,validation/{engine,battery}.py,evolution,fitness,genome,strategies,features,research,research_run,ai_researcher,paper/{engine,calibration}.py,mt5/*,risk,data,stats,live_testing/eligibility.py,activity,jobs,lease}`, `backend/tools/{check_port.py,wait_ready.py,frontend_build_guard.py,verify_database.py,v5_validate_pipeline.py,verify_mt5_demo_execution.py,verify_mt5_historical_windows.py,benchmark_cpu_scaling.py}`, `start.bat`, `LAUNCHER_FIX_REPORT.md`, `V10_4_THROUGHPUT_REPORT.md` |
| `v10.3-speed-diagnostics` | `8abf3042a34bd484cdbbf806af5dc00851a9a1be` | Same tree family; diagnostics/build-guard lineage confirmed |
| `v10.2-dashboard-manual-testing` | `f5935f335ad53ddc5d329a165821cf367f196000` | Same tree family; dashboard/operator workflows |
| `legacy-archive` | `d96ec4baae56054729b3dd961aea9b7f0b067114` | Archaeology only — not modified |

**Legacy orchestrator stage order (verified from source, not from docs):**
`INIT → DATA_DISCOVERY → DATA_SYNC → DATA_VALIDATION → FEATURE_DISCOVERY → FEATURE_PRECOMPUTATION → FEATURE_VALIDATION → DATASET_READY → DATASET_REGISTRATION → RESEARCH_INITIALIZATION → RESEARCH → NODE_GENERATION → NODE_EVALUATION → BACKTESTING → VALIDATION → QUALIFICATION → EVOLUTION → RESEARCH_COMPLETED | BLOCKED`

Legacy milestone view (`pipeline_state.py`) compresses this to: `INITIALIZATION → MT5_CONNECTION → DATA_DISCOVERY → DATA_SYNCHRONIZATION → FEATURE_DISCOVERY → FEATURE_COMPUTATION → NODE_GENERATION → EVALUATION`.

## 2. Legacy → Hero1 mapping (concept-level, NOT a code transplant)

| # | Legacy stage (v10.4) | Legacy implementation | Hero1 today (main @ this commit) | Status |
|---|---|---|---|---|
| 1 | Strategy definition | genome/templates, WWW research, interactive | **Strategy Specification + deterministic compiler** (`packages/strategy_spec`), JSON editor, **Visual Strategy Builder**, registry persistence with immutable `spec_hash` + lineage | **Implemented** (Hero1's model is deliberately different and stricter: declarative, no inline formulae) |
| 2 | Strategy validation | `validation/engine.py`, `battery.py` | **Spec validation is server-side and authoritative** (`POST .../nodes/validate`): schema, semantic rules, warmup analysis, structural checks | **Implemented** — NOTE this is *spec* validation, not performance validation (boundary preserved, see §4) |
| 3 | Deterministic compile | implicit in backtest engine | **`POST .../nodes/compile`** → canonical JSON + IR + fingerprint; builders/editors can never half-apply a damaged spec | **Implemented** |
| 4 | Instrument & timeframe resolution | `data/integrity.py`, paths | Global instrument selector; per-instrument SQLite DBs (`<DATA>/<SYMBOL>/DATABASE/hero1.db`); registry DB; spec carries `instrument`; cross-instrument lineage forbidden (tested) | **Implemented** (data *files* resolution exists; dataset *verification* is Phase 4) |
| 5 | Market-data availability & integrity | DATA_DISCOVERY/SYNC/VALIDATION stages, `v5_validate_pipeline.py`, raw→verified DATA hierarchy | Canonical `<root>/DATA/<SYMBOL>/{MARKET_DATA/<TF>, NODES/*, DEEPTEST, LIVE_TESTING, RESEARCH}` created by the storage engine; 10k-node history layout documented | **Partially implemented** — layout + per-instrument isolation done; integrity verifier NOT implemented (Phase 4 prerequisite) |
| 6 | Indicator/feature calculation | `features/` + feature cache + `FEATURE_PRECOMPUTATION` stage | `packages/indicators` = Phase 3 **skeleton** by design; spec references indicators declaratively by name/params; compiler validates the reference; UI renders `INDICATOR ENGINE NOT AVAILABLE` until the engine lands | **NOT implemented (by design)** — preparation only, honestly gated in UI |
| 7 | Initial screening | orchestrator NODE_EVALUATION + fitness | Nothing executable yet; tables exist (`backtest_runs`, `deeptest_scenarios`, `deeptest_trades`) | **Future (Phase 4)** |
| 8 | Historical backtesting | `historical_backtest/runs.py`, `backtest/` | Nothing executable yet; table exists (`backtest_runs`) | **Future (Phase 4)** |
| 9 | Robustness / out-of-sample | `deeptest/` (OOS, walk-forward, robustness) | Nothing executable yet; table exists (`deeptest_scenarios`, `deeptest_trades`) | **Future (Phase 4)** |
| 10 | Qualification | `live_testing/eligibility.py`, approval ledger in UI, DeepTest gates | `approval_state` on node rows + `approvals` table exist as **data model only**; eligibility logic NOT implemented | **Future (Phase 5)** |
| 11 | Paper testing | `paper/engine.py`, `paper/calibration.py` | NOT implemented; no paper engine; MT5 adapter ships an honest availability probe only | **Future (Phase 5)** |
| 12 | Controlled MT5 execution | `mt5/{bridge,mt5_real,execution,monitor,demo_schedule,discovery,accounts}.py`, demo-first guardrails | `packages/mt5_adapter` — **availability probe only** (health endpoint reports mt5 status); execution belongs to a later phase with Windows + demo gates | **Future (Phase 5)** — NOT available, NOT faked |
| — | Evolution & lineage | `evolution/`, `fitness/`, `genome/` | Lineage data model implemented (`parent_node_id`, `node_lineage`, immutable versions, deterministic hashing). Evolution/generation logic NOT implemented | **Partially implemented (data model)** — Phase 8 for fine-tuning proposals |
| — | Research logging | events/activity, research_run | `global_events` + `activity_events` + `/api/v1/events` + live feed UI — **Implemented**. WWW Research agent (Phase 7) NOT implemented | Studio for logging **implemented**; agent **future** |
| — | Process management & launch | `start.bat`, monitor loop, build guard | TASK 7: persistent windows, health polling, identity verification, monitor loop (see README/ARCHITECTURE) | **Implemented** (Windows unproven — see report) |
| — | Node/genome store | SQLite nodes + evolution tables | Per-instrument `nodes` table + `spec_hash` unique constraint + immutable versions + lineage — with `starred` (TASK 7, research metadata) | **Implemented** (schema), executors future |

## 3. Boundaries (unchanged, verified by tests where applicable)

1. Strategy specification remains the authoritative source of strategy logic. The Visual Builder edits the spec; it cannot invent semantics.
2. Indicators are declarative references (name + params). Calculation belongs to the indicator/feature engine (Phase 3) — never inside specs, never inside the UI.
3. Market data is instrument-specific (per-symbol DBs and DATA dirs). Nothing crosses instruments implicitly.
4. Backtesting (future) must consume validated specs + the correct instrument/timeframe dataset — the compiler already produces the canonical IR it will consume.
5. Validation ≠ qualification. The spec validator is implemented; performance qualification is a separate future gate.
6. Paper ≠ live MT5. Neither exists yet; both are Phase 5; MT5 additionally requires Windows + demo-first gates.
7. Lineage is intact: child versions link via `parent_node_id`; parent rows are never mutated.
8. Starred status is user research metadata. It is a boolean column excluded from `spec_hash`, excluded from versioning, excluded from validation — proven by `test_starred_toggle_persists_and_filters`.
9. Research results reproducible: deterministic compiler fingerprint; canonical JSON is the persisted artifact.
10. Every operation is instrument-explicit; the global selector is respected by registry lists, spec workspace, and all future batch scopes (starred filter included).

## 4. Architectural risks & watch-items

- **Phase 4 is the keystone.** The compiler + storage preparation is real, but the backtest/OOS/qualification executors are greenfield. Do not let the UI imply otherwise (see placeholders).
- **Indicator engine (Phase 3)** must land before any honest backtest; until then every spec compiles but cannot execute. The UI says exactly that (`INDICATOR ENGINE NOT AVAILABLE`).
- **Data integrity verifier** (legacy `DATA_VALIDATION`) has no Hero1 equivalent yet — a backtest over unverified bars would be unsound. Prerequisite for Phase 4.
- **MT5** is Windows-only and demo-gated; the Linux preview cannot prove execution paths.
- **`starred` migration (0002)** is additive and idempotent; existing deployments upgrade via `python scripts/init_db.py --create` or PREREQUISITES.bat. No data is rewritten.
- **Launcher identity echo** (`HERO1_START_TOKEN`, `HERO1_REPO_ROOT`) appears in `/health` behind explicit env vars; harmless on localhost, documented here and in ARCHITECTURE.md.

## 5. What changed in THIS task (Part 5 relevance)

- Registry: additive `nodes.starred` column (research metadata only) + `0002_nodes_starred` migration.
- API: additive `starred` filter + `starred_total`/`filtered_total` on node lists; PATCH accepts `starred`.
- UI: consistent All Nodes / Starred Only scope (Manual Nodes + DeepTesting + Fine Tuning + Live Testing placeholders), server-enforced filter, selection limited to the filtered set.
- Launcher: proven legacy architecture adapted to Hero1's two-service layout (see START.bat header and README).
- NO future-phase functionality was implemented, simulated or claimed.
