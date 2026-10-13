# V8 — Backend Purge Audit (dependency map + deletion inventory)

## 1. Initial repository state

| item | value |
|---|---|
| branch / remote | `main`; `origin/main` = `691f6e3203322b087719fe3f2ec72a25791a5472` (V7.0 publication record) |
| local HEAD at session start | `5e42c270` (V6.6) + uncommitted V6.7-era diffs (workspace snapshot restore pattern) |
| reconciliation | worktree matched published V7.0 except snapshot-lost files (7 reports, `node_data.py`, 5 test files) + 2 tool mode bits — all restored from `origin/main`; `main` moved to `691f6e32`; worktree clean before edits. Nothing discarded. |
| canonical DB | LFS `c159b0718c43b43f…` (10 000 rows) — backup `/tmp/lab_state.canonical.db`, hash recorded |
| uncommitted user work | none (clean tree after baseline reconciliation) |

## 2. The five modules and their dependency maps

### Final Testing (`pages/FinalTesting.jsx`, 1 245 lines)
- APIs: `researchFilter, researchFacets` (shared with StrategyLab), `savedShortlist` (shared with **App.jsx**), `strategy` (shared with StrategyDrawer), `toggleShortlist` (shared), `strategyRerunBacktest`, `strategyRunValidation`, `strategyTrades` (**exclusive**).
- Backend: `POST /strategies/{sid}/backtest` (reruns `lab._run_batch` + `fitness`/`death_check`, inserts a `backtests` row), `POST /strategies/{sid}/validate`, `GET /strategies/{sid}/trades`.

### Backtest Matrix (`pages/BacktestMatrix.jsx`, 368 lines)
- APIs: `researchMatrix` (shared with **StrategyLab**), `population` (shared with Overview), `strategy` (shared), `mt5HistoricalRuns` (shared with **StrategyLab's HistoricalBacktestPanel / NodeEconomics**), `generateMatrix` (**exclusive**).
- Backend: `POST /strategies/{sid}/matrix` (computes `full_matrices` + INSERT `matrices`).

### MT5 Backtest (`pages/Mt5Backtest.jsx`, 812 lines)
- APIs: `mt5HistoricalCapabilities/Runs` (shared with **StrategyLab / NodeEconomics**), `researchStrategies` (shared with StrategyLab), `dataSync` (shared with **LiveActivityPanel**), `toggleShortlist` (shared), `authoritativeStrategy`, `mt5BacktestResults`, `mt5BacktestRun`, `mt5StrategyBacktests` (**exclusive**).
- Backend: `GET /mt5-backtest/results`, `GET /mt5-backtest/strategies/{sid}`, `POST /mt5-backtest/strategies/{sid}/run` (no frontend consumer even before V8), `GET /strategies/{sid}/authoritative`.
- Components: `HistoricalBacktestPanel` (**shared** with StrategyLab — kept), `HistoricalRunResults` (shared).

### MT5 Demo Trading (`pages/Mt5DemoTrading.jsx`, 554 lines)
- APIs: `mt5DemoStatus/StartAll/StartShortlist/StopAll/PauseAll/Schedule/SaveSchedule` (**exclusive**), `toggleMt5Demo` (**shared — NodeEconomics**), `liveTestingNodesTable` (**shared — LiveTestResults**), `researchFilter` (shared), `ManualOrderPanel`/`Mt5AccountSelector` (**shared — Live Testing**), `DemoOrderPanel` (**exclusive component**).
- Backend: `/mt5-demo/status|start-all|start-shortlist|stop-all|pause-all|schedule GET/POST/DELETE` (**exclusive orchestration**); `POST /mt5-demo/strategies/{sid}/toggle` + `mt5/demo_schedule.py` + `mt5_demo_configs` persistence (**shared** — NodeEconomics toggle + demo-schedule gate semantics).

### Paper Trading (`pages/PaperTrading.jsx`, 384 lines)
- APIs: `paperStatus/Start/Stop/Trades/executions/calibration/recalibrate/killSwitch` (**exclusive UI**), plus `paperCandidates/Promote/Demote/BatchPromote` (**shared — Overview's candidates panel**).
- Backend: `GET /paper/status|promoted|trades|executions`, `POST /paper/start|stop`, `GET /calibration`, `POST /calibration/recalibrate` (**exclusive**); `/paper/candidates…` (**shared — Overview**).

## 3. Category A — exclusive and obsolete (DELETED)

| item | evidence of exclusivity |
|---|---|
| `frontend/src/pages/{FinalTesting,BacktestMatrix,Mt5Backtest,Mt5DemoTrading,PaperTrading}.jsx` | only referenced by their App.jsx tab entries (plus tests updated to the new contract) |
| `frontend/src/components/DemoOrderPanel.jsx` | sole importer: Mt5DemoTrading.jsx |
| App.jsx lazy imports + 5 tab entries | the tabs themselves |
| api.js: `generateMatrix, strategyTrades, strategyRerunBacktest, strategyRunValidation, authoritativeStrategy, mt5BacktestResults, mt5StrategyBacktests, mt5BacktestRun, paperStatus, paperStart, paperStop, paperTrades, paperPromoted, executions, calibration, recalibrate, killSwitch(client), mt5DemoStatus, mt5DemoStartAll, mt5DemoStartShortlist, mt5DemoStopAll, mt5DemoPauseAll, mt5DemoSchedule, mt5DemoSaveSchedule` | consumer scan: only the five pages (killSwitch/paper* exec client-side) |
| `POST /strategies/{sid}/matrix`, `GET /strategies/{sid}/trades`, `POST /strategies/{sid}/backtest`, `POST /strategies/{sid}/validate`, `GET /strategies/{sid}/authoritative` | frontend consumers were the removed pages only (grep across retained pages/components: none); zero test files call these routes |
| `GET /mt5-backtest/results`, `GET /mt5-backtest/strategies/{sid}`, `POST /mt5-backtest/strategies/{sid}/run` | same (the `/run` route had no consumer even before V8) |
| `GET /paper/status|promoted|trades|executions`, `POST /paper/start|stop` | only PaperTrading.jsx |
| `GET /calibration`, `POST /calibration/recalibrate` | only PaperTrading.jsx |
| `GET /mt5-demo/status`, `POST /mt5-demo/start-all|start-shortlist|stop-all|pause-all`, `GET/POST/DELETE /mt5-demo/schedule/{sid}` | only Mt5DemoTrading.jsx |

## 4. Category B — shared and required (PRESERVED, with reason)

| item | required by |
|---|---|
| `paper/engine.py` (incl. `library_feature_spec`, status, the engine's loop) | `live_testing/engine.py` imports `library_feature_spec` (Live Testing); `orchestrator/lab.py` autostart drives the PAPER qualification tier (node generation semantics — frozen by §8); `power.py` shutdown; `routes` status `paper_running` |
| `paper/calibration.py` | `orchestrator/lab.py` (live calibration aggregation) |
| `/paper/candidates`, `/paper/candidates/{id}/promote|demote`, `/paper/candidates/batch_promote` | **Overview** candidates panel (promote/demote/batch) |
| `mt5/demo_schedule.py` + `POST /mt5-demo/strategies/{sid}/toggle` + `mt5_demo_configs` table | **NodeEconomics** toggle; the demo-schedule gate the toggle enforces |
| `mt5/execution.py`, `mt5/bridge.py`, `mt5/mt5_real.py`, `ManualOrderPanel`, `Mt5AccountSelector` | **Live Testing** (V7.0 manual trading + engine) |
| `strategies/authoritative.py` + `GET /strategies/{sid}/economics` | **Node Economics** tab |
| `specialization/matrix.py` (`full_matrices`, `propose_specializations`) | `GET /strategies/{sid}` (StrategyDrawer Matrices tab auto-generates); `ai_researcher/analyzer.py` (**Research AI**) |
| `/mt5-historical/*` + `HistoricalBacktestPanel`/`HistoricalRunResults` | **Strategy Lab**, **Node Economics**, Deep Testing data layer |
| `/research/strategies|facets|filter|matrix`, `researchStrategies/Filter/Facets/Matrix` | **Strategy Lab** |
| `/research/shortlist/saved`, `toggleShortlist` | **App.jsx** + drawers |
| `liveTestingNodesTable` route | **Live Test Results** tab |
| `dataSync` route | **LiveActivityPanel** (Overview/Live) |
| `/risk/kill-switch` + risk manager kill-switch | **C→preserve**: global live-risk safety control (§8 “risk controls”); its only UI button sat on the removed tab — a safety switch is never deleted |
| db: `matrices`, `backtests`, `calibration`, `executions`, `mt5_demo_configs`, `paper_*` tables + `get_mt5_backtests`/`get/set_mt5_demo_config` | historical records (§7) + retained readers (stats, authoritative, toggle, drawer) |

## 5. Category C — uncertain / intentionally retained

- `/api/mt5/manual_order` + `api.manualOrder` — superseded V5 manual-order route with **no frontend consumer**, but it is a live order-execution surface; per §5/§8 live execution routes are not touched in a cleanup release. Retained with this note; a future release may deprecate it explicitly.
- `COMPONENT_STATES["paper_trading"]` + status keys `paper_running`, `counts.PAPER` — shared status payload shape (Overview/tests), untouched.
- `paper/engine.py` autostart from the generation pipeline — retained: it produces PAPER-tier node status (qualification semantics, frozen §8) and removing it would change node qualification.

## 6. Database policy (§7)

No schema changes. All tables preserved (records displayed by retained tabs: `backtests` (generation stats), `matrices` (StrategyDrawer), `mt5_backtests` (stats/authoritative), `paper`/`executions`/`calibration` (live calibration + history), `mt5_demo_configs` (NodeEconomics)). The `mt5_backtests`/`paper_trades` tables lose their display tabs but remain as historical records — data cleanup is explicitly out of scope.

## 7. Workload removed (§6 — measured where possible)

- 5 lazy page chunks (3 363 lines of page code) + DemoOrderPanel (423 lines) removed from the frontend bundle; the two interval loops in Mt5DemoTrading and PaperTrading no longer exist. FinalTesting, BacktestMatrix and Mt5Backtest had no `setInterval`; their on-demand fetch/refresh/trigger controls were removed with those pages.
- Triggerable heavy computations removed: rerun-backtest (`lab._run_batch` + fitness per click), run-validation, standalone matrix generation, mt5-backtest run, demo batch orchestration (start-all over all qualified nodes), paper engine manual start/stop + its UI polling, calibration recalculation.
- No shared worker, timer, or startup registration was removed: paper engine (shared), live testing workers, monitor, bootstrap — untouched.
- Startup/shutdown registrations unchanged: shared services remain registered; the deleted API handlers are no longer callable, and no new worker or poll was added.

## 8. Strict-preservation notes (§8)

V8 changes no generation/qualification/ranking/sorting/statistics logic, no Deep Testing data semantics, no Live Testing execution semantics (the Manual Trading Panel and its V7.0 bridge stay byte-identical), no Strategy Lab processing, no Research AI behavior. Only the five modules' exclusive surfaces are removed; shared components (ManualOrderPanel, Mt5AccountSelector, HistoricalBacktestPanel, BacktestMatrixTable, RiskStrip, …) stay mounted where retained tabs use them.


## 9. Final measured before/after (same sandbox, same node_modules/Vite)

| Measurement | V7.0 baseline (`691f6e32`) | V8 |
|---|---:|---:|
| Route decorators in routes.py | 235 | 211 |
| Hashed assets emitted in assets/ | 33 | 24 |
| Sum of asset bytes | 1,497,939 | 1,328,902 |
| Sum of gzip-compressed asset bytes (gzip level default, mtime=0) | 425,677 | 379,731 |
| Intervals in the five deleted page sources | 2 | 0 |
| Shared retained page intervals (Overview / DeepTesting / LiveTesting / StrategyLab / AI) | 1 / 1 / 0 / 0 / 1 | unchanged |
| Generated theme utility declarations | 247 (baseline) | 108 |

Baseline build: `git archive HEAD frontend` into a disposable directory, link
same installed dependencies, `npx vite build`; post build: same command against
the V8 working tree. Deleted CSS consists only of generated utility classes no
longer referenced by surviving components; shared theme rules are unchanged.

No claim of measured idle CPU/RSS/startup/latency improvement: those comparisons
were not reliably captured. The five pages were already lazy-loaded, so an
unopened deleted tab had no mounted polling interval. The defensible result is
less distributed code, fewer active endpoint surfaces/on-demand computation
triggers, and removal of two possible mounted page polling loops—not a promise
of a particular dashboard speedup. Shared paper autostart remains deliberately
unchanged because the user froze retained generation behavior.

## 10. Final review additions

- 24 exclusive API handlers (~521 lines including dead calibration import)
  deleted; no separate exclusive backend worker was proven safe to delete.
- NodeEconomics integration only: remove dead quick-navigation destinations;
  send existing analysis actions to Strategy Lab and live actions to Live
  Testing. Its stored economics, stage metrics and node selection are unchanged.
- Smoke harnesses no longer import/render deleted pages. Removed tests were
  exclusive deleted-page/route contracts; retained runner, node, risk,
  qualification and execution assertions remain. New purge tests require
  absence and shared-operation contracts (12 tests).
- First absence-test run returned 405 after real-app startup because
  DashboardStaticFiles catches unmatched POST paths. Router-only tests assert
  strict 404 and source registration tests independently assert no deleted
  route implementation remains; production static serving is tested separately.
- All canonical historical database tables and access code with retained
  consumers preserved. No schema object or user record dropped.
