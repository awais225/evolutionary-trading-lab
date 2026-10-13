# V8 — Dashboard Purge, Backend Cleanup and Workload Reduction

Date: 2026-10-13. Baseline: `691f6e3203322b087719fe3f2ec72a25791a5472`
(V7.0), verified against origin/main before edits. See
`V8_BACKEND_PURGE_AUDIT.md` for the pre-deletion dependency map, exclusivity
proof, intentionally retained code and measurement procedure.

## Result

Removed Final Testing, Backtest Matrix, MT5 Backtest, MT5 Demo Trading and
Paper Trading as complete dashboard interfaces—not hidden tabs. Their five
page implementations and DemoOrderPanel were deleted, together with 24
exclusive API handlers, 24 frontend API clients and obsolete navigation
shortcuts. No replacement placeholder pages remain.

Other existing tabs were not targeted for deletion. The intended Overview →
Deep Testing → Live Testing workflow remains; Strategy Lab and Research AI
retain their existing implementations.

## Actual deletion inventory

- Six frontend files: FinalTesting.jsx (1,245 lines), BacktestMatrix.jsx (368),
  Mt5Backtest.jsx (812), Mt5DemoTrading.jsx (554), PaperTrading.jsx (384), and
  DemoOrderPanel.jsx (423). Total: **3,786 lines**.
- App.jsx: five lazy imports and navigation entries removed.
- api.js: 24 exclusive helpers removed; used-helper scan finds **zero missing
  API clients** in surviving JSX.
- Backend routes.py: **24 handlers removed (~521 lines)**, including standalone
  generation rerun/validation/matrix triggers, old MT5 backtest result/run
  endpoints, standalone demo batch controls/schedule interface, standalone
  paper status/start/stop/trades/executions, and calibration endpoints.
- The now-unused routes.py calibration import removed. Shared lab calibration
  integration preserved.
- Generated CSS utility layer regenerated: 136 obsolete lines removed; shared
  theme rules untouched; 0 unmapped classes.
- NodeEconomics: only deleted-destination navigation adjusted (analysis →
  Strategy Lab; live actions → Live Testing). Calculations unchanged.
- Frontend smoke fixtures no longer import/render removed pages. Deleted-module
  tests retired; retained/shared-service tests updated to surviving consumers;
  **12 new purge tests** enforce actual absence and preservation.

The full endpoint list and each consumer proof are in the audit. Router-only
HTTP checks require strict 404 for deleted APIs, independent of the production
static-file mount (which returns 405 for unknown POSTs). No handler is kept as
an empty stub.

## Backend workload cleanup and deliberately retained services

Removed **on-demand** execution paths: standalone backtest rerun/validation,
standalone matrix generation, old MT5 tester trigger, demo start-all and
start-shortlist orchestration, paper start/stop controls and calibration
recompute. Two removed pages' mounted polling intervals are gone.

**No shared worker was deleted or disabled.** The five modules did not have a
separate exclusive startup worker that could be safely removed without
violating the preservation contract. Backend cleanup is nevertheless real:
235 → 211 route registrations and the handlers/computation entry points
listed above are deleted, not hidden.

Shared/uncertain code retained explicitly:

- Paper engine: Live Testing imports `library_feature_spec`; lab autostart is
  part of the existing PAPER-stage pipeline. Deleting it would change retained
  generation behavior, which V8 expressly forbids. Shared status and power
  shutdown registrations therefore remain.
- Paper calibration: still used by the lab pipeline.
- Overview paper-candidate APIs: still called by Overview's candidate controls.
- Historical runner and HistoricalBacktestPanel/HistoricalRunResults: required
  by Strategy Lab and Node Economics.
- BacktestMatrixTable and specialization.matrix: shared Strategy Lab comparison,
  node-detail Matrices and Research AI consumers.
- Demo schedule evaluator/config repository and per-node demo toggle: still
  used by NodeEconomics. Global batch/demo-tab orchestration removed, not the
  shared evaluator or live demo-account execution.
- MT5 execution/bridge/monitor, ManualOrderPanel, account selector, all live
  risk and position controls: untouched.
- Risk kill-switch API: retained as a global safety control even though its
  old button lived on Paper Trading. Purge tests inspect its registration and
  never operate this safety switch.
- Older `/mt5/manual_order` compatibility surface: no current frontend
  consumer, but a live-execution interface; retained pending a separately
  authorized deprecation rather than changing order behavior during V8.
- All existing schema, history and repository readers with retained consumers:
  preserved. No database migration or table drop.

## Preservation evidence

16 files compared **byte-identical** to V7.0, including the five protected page
implementations (Overview, DeepBacktest, LiveTesting, StrategyLab, ResearchAI),
ManualOrderPanel's source module, DeepScenarioPanel, node_data authority,
status.py, database.py, data/engine.py, deeptest/scenarios.py/fetch_job.py,
orchestrator/lab.py, mt5/execution.py and live_testing/engine.py.

Only navigation/API integration surfaces and proven-exclusive handlers changed.
No qualification, generation, sorting, ranking, date-range, DeepTest,
risk/execution or retained statistics refactor was bundled into V8.

## Measured performance evidence (same environment/build command)

| Measurement | V7.0 | V8 |
|---|---:|---:|
| API route registrations | 235 | 211 |
| Frontend asset count | 33 | 24 |
| Asset bytes | 1,497,939 | 1,328,902 |
| Gzip asset bytes | 425,677 | 379,731 |
| Intervals in removed page sources | 2 | 0 |

Raw asset bytes reduced by **169,037 (11.3%)**; gzip sum reduced by **45,946
(10.8%)**. Baseline frontend built from a disposable git archive of V7.0 with
identical installed dependencies; V8 built with the same Vite command. These
are distribution-size measurements, not browser initial-load transfer estimates
(the application uses lazy page chunks).

**No reliable before/after CPU, RSS, startup time, request-volume or latency
measurement is claimed.** Unopened tabs already had no mounted intervals.
The measured gains are fewer shipped assets, fewer active API surfaces and
fewer optional polling/on-demand workload triggers. Shared generation/autostart
and retained-tab polling remain unchanged.

## Completed regression gate

All 68 files ran in resource-safe fresh-canonical-DATA batches:

| Batch | Files | Passed | Skipped | Failed |
|---|---:|---:|---:|---:|
| 1 | 34 | 678 | 1 | 0 |
| 2A | 16 | 226 | 0 | 0 |
| 2B1 | 9 | 96 | 0 | 0 |
| 2B2 | 4 | 114 | 0 | 0 |
| 2B3 | 5 | 60 | 0 | 0 |
| **Total** | **68** | **1,174** | **1** | **0** |

The single existing skip is `test_v5_1a_coverage_and_balances.py:120`:
this installation has no simulator dataset. No skip added to hide a regression.
The V7.0 total was 1,176; 14 exclusive/parameterized removed-interface cases
were retired and 12 purge cases added, yielding 1,174.

Two larger development attempts were kernel OOM-killed without summaries
(the later process reached ~821 MB anonymous RSS); **neither is counted**.
Initial absence tests incorrectly expected production-static-fallback 404;
router-only checks now prove endpoint deletion directly. Fingerprint tests
passed once the actual build and V8 record were generated.

Additional verification (not added again to the full-gate total):

- Purge acceptance: 12/12.
- Shared launcher identity + frontend serving + purge: 40/40.
- Retained-page/jsdom smoke: 35/35; live/DeepTesting interaction smoke: 54/54.
- Production index and entry assets served via FastAPI TestClient are
  byte-identical to frontend/dist; index `no-store`, hashed assets immutable.
- Canonical, DeepTest settings, manual execution-state and Strategy Lab facet
  APIs return HTTP 200. No real orders submitted.
- One existing OpenAPI duplicate-operation-ID warning in the DeepTesting
  config route remains (present in V7.0); not modified in this cleanup release.

## Production identity and publication

V8 build record:

- SRC_HASH `82964c8c393f079280e4515bd5dbb6e6430085abb5020fbead0328b90198e4fa`
- CODE_HASH `1a9c1e3d3bc311fc3b7af31a2d84a8aedd9f4e4fd4b55ea518ec7cce2f061541`
- INDEX_SHA256 `2a02233db611c41b57879d5d116095102599ace37838341d2eb94d83c4634837`
- Entries `assets/index-DaL3XIUl.js`, `assets/index-DYygB5sD.css`.

Tested application/content commit:
`74a0f3986493109d64c4cda6b5d4a14ea5106a07`.

Publication chain: V7.0 baseline `691f6e32` → V8 content `74a0f398` →
this reports/fingerprint publication commit. The fingerprint names the exact
application/content commit. The publication commit changes no application
source. Its full local HEAD and verified `origin/main` SHA are emitted by
`git rev-parse HEAD` / `git ls-remote origin main` in the release response
(a committed report cannot contain its own commit hash). No force push.

Post-content-commit publication gate: **41 passed, 0 failed** (fingerprint
index + launcher identity + purge). Full-gate source/build hashes remain
identical to those tested; only fingerprint-record metadata names the new
content commit.

## Limitations and operator acceptance

No real Windows/Edge session, Windows BAT invocation or MT5 terminal was
available in this Linux sandbox. Launcher identity/serving and jsdom interaction
checks passed, but **real Windows and broker acceptance remains unperformed**.
Operator: launch the updated build, confirm the five tabs and old shortcuts
are absent, exercise Overview → Deep Testing → Live Testing, verify Manual
Trading Panel is still expanded/functional in the demo environment, and open
Strategy Lab/Research AI. Automated tests never place real trades.

No feature cleanup remains undocumented: explicitly shared/uncertain services
above remain intentionally. Further removal of paper autostart, demo toggle or
legacy execution compatibility would be a separate behavior-changing release.

Canonical DB backup was preserved and restored byte-identically after every
test batch; canonical tracked configurations restored after test writes. Zero
DATA/database paths or secrets are included in the release commits.
