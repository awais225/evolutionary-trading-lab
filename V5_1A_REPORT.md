# V5.1a — Brokered risk, real schedule, qualified nodes, honest deep backtests

Implementation + verification report for `awais225/evolutionary-trading-lab`
(branch `main`), covering the V5.1a brief §1–§47.

Every number below was produced by the running system in this environment — the
project's real DATA tree, the real engines, the real endpoints, the real browser
bundle — and is reproducible with the command quoted next to it. Nothing is
estimated. Steps that could only be executed on the operator's Windows machine
with a live MetaTrader 5 terminal are marked as such and explicitly **not**
claimed.

Environment used for the evidence: sandbox virtualenv at
`/opt/lmsarena-storage/venv` (Python 3.13), DATA provisioned verbatim from
`scripts/lmsarena_provision_data.sh` into `/opt/lmsarena-storage/DATA`
(10,787 strategies = 10,000 `USER_RESEARCH` + 787 `LEGACY_TEST`), backend on
`127.0.0.1:8787`, frontend built from `frontend/src` into `frontend/dist`.
The sandbox has no MetaTrader 5 terminal, so no real DEMO order was placed here;
the MT5 bridge in this environment is the lab's simulator bridge.

---

## 1. Root causes

**1.1 Risk sizing was a constant, not a calculation.** The manual panel and the
auto path carried a `$10 risk / 300-pip stop` style default and only recomputed
when one of the two "primary" fields changed, so dependent fields (lots, risk
per trade, actual risk) could disagree with the inputs shown on screen. For
XAUUSD on a real broker spec that default is *below* the broker minimum
(0.00333333 lots against a 0.01 minimum), so the panel could ask for a volume the
terminal would reject while displaying a plausible-looking number.
Root cause: two independent formulas (panel vs pre-submission validation) and a
recompute trigger tied to one field instead of to any change.

**1.2 The schedule was cosmetic.** `live_testing/schedule.py` stored a schedule
and could *describe* it, but nothing gated execution with it: the live engine
entered whenever its strategy fired, the deep backtest ignored it entirely, and
the UI wrote blobs that were never read back (SQLite JSON was echoed to the
frontend as a *string*, so empty-looking groups were really `"[0, 2]"`).
An empty array meant "no restriction" in some paths and "nothing allowed" in
others — the exact ambiguity the brief forbids.

**1.3 Node classification was inconsistent.** Several places counted nodes
themselves (`pipeline_stage`, `alive`, `research_eligible`) with different rules,
so filters disagreed with the totals next to them: a node could be "qualified" in
one list and "alive" in another, and an infrastructure failure
(`DATA_UNAVAILABLE`) could be presented as a strategy failure.

**1.4 Deep backtests could claim completion without a result.** A run could
finish with `"Test completed"` and no metrics, and there was no report of *which*
historical data was actually loaded — so a small slice could stand in for the
whole requested period. Balances (`start`/`end`) were never stated, and `avg
win`/`avg loss` lived only in `results.derived`, which the run details panel did
not read.

**1.5 Counters were database-wide, not experiment-scoped.** Evolution counters
and "population" figures read across the whole database (legacy `LEGACY_TEST`
rows included), so a fresh run appeared to continue an old population and node
numbers did not start at `Node #1` for the current experiment.

## 2. Old vs current logic

| Area | Old behaviour | Current behaviour |
|---|---|---|
| Schedule | stored + described, never enforced | one evaluator (`live_testing/schedule.py::evaluate` + `bar_mask`) used by the live engine *and* the deep backtest; blocks with `SCHEDULE_BLOCKED` |
| Empty arrays | ambiguous (sometimes "all") | `configured=false` means "unconfigured"; a *configured* schedule with no day/session/window allows nothing and says so |
| Risk | fixed `$10 / 300 pips`, recompute on one field | one authoritative calculation for preview, manual orders, automated execution and pre-submission validation; recomputed on any input change; below-minimum volumes refused with the broker's own limit |
| Node accounting | several ad-hoc counts | one classifier (`status.node_bucket`) → `qualified / alive / eligible / failed / excluded / blocked / unknown`, whole-table counting, `filter_totals` on the index |
| Deep backtest | could finish with no metrics | run states + verdict; a completed run with no trades says exactly that; `coverage` report + balances are stored and served |
| Reset | counters could look offset | preview is read-only and exact; the executed reset clears experiment/run state and the fresh experiment's first node is `Node #1` |

## 3. Files changed (V5.1a commits)

| Commit | Subject | Files |
|---|---|---|
| `84f526c` | authoritative schedule enforcement + qualified-node index | `backend/app/{api/routes.py, backtest/engine.py, db/database.py, historical_backtest/runs.py, live_testing/engine.py, live_testing/results.py, live_testing/schedule.py, status.py}` |
| `2e71447` | from-scratch reset preview, experiment-scoped counters, editable schedule UI | `backend/app/{api/routes.py, evolution/engine.py, live_testing/schedule.py, research_run/lifecycle.py}`, `frontend/src/{api.js, components/LiveTestingPanels.jsx, components/NewResearchRunModal.jsx, pages/DeepBacktest.jsx}`, `frontend/tests/v48_ui_smoke.*`, `tests/test_v5_1a_qualified_nodes.py` |
| `21f7ae9` | Live Testing on the qualified-node index + risk panel recalculation | `backend/app/api/routes.py`, `frontend/src/components/LiveNodeIndex.jsx` (new), `components/LiveTestingPanels.jsx`, `lib/safe.js`, `pages/LiveTesting.jsx`, `frontend/tests/v48_ui_smoke.jsx`, `tests/test_v5_1a_live_index_and_risk.py` |
| `97ad03b` | log entry for the above | `lmsarena.txt` |
| `69e7d94` | coverage report + run balances | `backend/app/historical_backtest/runs.py`, `frontend/src/pages/DeepBacktest.jsx`, `frontend/tests/v48_ui_smoke.jsx`, `tests/test_v5_1a_coverage_and_balances.py` |

## 4. Architecture — the parts that matter

**4.1 One schedule evaluator.** `backend/app/live_testing/schedule.py` owns the
rules (days, sessions `asia 0–8 / london 7–16 / newyork 12–21 / overlap 12–16`,
timeframes M1–D1, per-node signal conditions taken from the node's own genome,
trading windows with an explicit timezone, cooldown, spread ceiling, daily cap,
max concurrent positions, enabled flag). `evaluate()` answers
`{allowed, reason, rules[]}` for a moment in time and `bar_mask()` does the same
for a whole backtest series. The live engine calls `evaluate()` before it can
enter (`backend/app/live_testing/engine.py`, schedule gate around L812–840 →
`SCHEDULE_BLOCKED`), the deep backtest calls `bar_mask()`
(`backend/app/backtest/engine.py`), and the UI shows the same description string
the backend produced — one implementation, no second copy of the rules.

**4.2 One risk/volume calculation.** The preview endpoint, the manual order path
and the automated path all funnel through the same sizing function in
`backend/app/mt5/execution.py` (`validate_order_request`, L321–420, then
`place_demo_order` L767+). The number the panel shows *is* the number that is
validated; a below-minimum result is refused with `VOLUME_BELOW_MINIMUM` and the
broker's own minimum, never silently rounded up.

**4.3 Qualified-node index.** `backend/app/api/routes.py::nodes_index` (~L4148)
serves the whole user-research population with experiment-local numbering
(`research_node_num`), the node's own metrics, robustness, backtest coverage,
risk, decoded schedule, live/position status, and `filter_totals` computed from
one pass over the `strategies` table. `status.node_bucket()` is the only
classifier. The frontend consumes it in
`frontend/src/components/LiveNodeIndex.jsx`.

**4.4 Deep backtest.** `backend/app/historical_backtest/runs.py` loads the
dataset named by the run, verifies the bars it needs, applies the node's
schedule through `bar_mask()`, runs the existing backtest engine and stores the
metrics, the applied schedule, a verdict, a `coverage` report and the balances.
States: `queued / loading / validating / running / completed / incomplete /
failed / blocked`.

**4.5 Reset and numbering.** `backend/app/research_run/lifecycle.py` provides a
read-only `fresh_preview` and the guarded reset; `backend/app/evolution/engine.py`
scopes counts with `USER_RESEARCH_SCOPE_SQL` (and keeps the legacy
`LEGACY_POPULATION_SQL` separate), so a fresh experiment's population is its own
and its first node is `Node_1`.

## 5. §9 — browser / static-build consistency (Edge vs Chrome)

Measured against the running instance (`curl` on `127.0.0.1:8787`):

| Check | Result |
|---|---|
| `GET /` headers | `text/html`, `content-length 418`, `etag "09e9bfe9…"`, **`cache-control: no-store, must-revalidate`** |
| `GET /assets/index-SotJQRVQ.js` | `text/javascript`, `cache-control: public, max-age=31536000, immutable` |
| `GET /assets/index-DY0FUd-S.css` | `text/css`, same immutable header |
| served JS bytes vs `frontend/dist/assets/index-SotJQRVQ.js` | identical sha256 (`b9badfc0…`) |
| index references only content-hashed assets | yes (no unhashed entry point) |
| service worker / PWA manifest | **none** (`grep -rn "serviceWorker\|manifest.json\|workbox" src/ index.html` → no matches; `frontend/public/` is empty) → no worker can serve a stale bundle to one browser and not the other |
| client-side routing | none (no `pushState`/`react-router`), so the app always loads `/`; a fabricated path such as `/deep-backtest` is a 404 by design and is not reachable from the UI |
| build identity | `python backend/tools/frontend_build_guard.py --root . --stamp --summary` → `GUARD_STAMPED=True`, `SRC_HASH=9431c59e85c85065`, `INDEX_SHA256=33162ab102f5598f` |
| running instance vs local source | `--verify-served http://127.0.0.1:8787` → `SERVED_STATUS=FRESH`, `SERVED_MATCHES_LOCAL_SRC=True`, `SERVED_DIST_MATCHES_SRC=True`, `SERVED_REASONS=[]` |
| instance identification / foreign occupant | `--identify http://127.0.0.1:8787` → "the occupant of this port is an Evolutionary Trading Research Lab backend"; a port with nothing on it yields `SERVED_STATUS=UNREACHABLE` and exits cleanly instead of guessing |
| launcher behaviour | `start.bat` L140–L207: guard `--check` → rebuild + `--stamp` if stale → abort if npm missing (never start on a stale bundle); L211–L248: `check_port.py`, `--verify-served`, exact-PID `--stop` of a stale/foreign *lab* instance, refusal to kill unrelated software |

Why this matters for the reported Edge/Chrome difference: the two browsers cache
independently, so the only safe design is a non-cacheable `index.html` that names
content-hashed assets, with the served bundle proven equal to the repository's
current `frontend/src`. All three properties are now true and are checked by the
launcher on every start. The tests that keep them true:
`tests/test_frontend_serving.py` (15 tests, including
`test_served_index_is_no_store_and_assets_are_immutable` and
`test_lab_identity_check_is_not_fooled_by_a_foreign_service`).

## 6. §40 — deterministic test inventory

| §40 area | Test files | Evidence |
|---|---|---|
| risk / volume | `tests/test_v5_1a_live_index_and_risk.py` (13), `tests/test_v5_manual_order.py` (13) | 2× risk or ½ stop doubles the lots; `actual_risk ≤ risk_amount`; BUY 2400.3 / SELL 2400.0; blind spec → `INVALID_SYMBOL_DATA`; `$10 + 300 pips` → `VOLUME_BELOW_MINIMUM` (0.00333333 < 0.01) |
| schedule | `tests/test_v5_live_testing.py` (25) incl. `test_01`–`test_10` rules, `test_11`–`test_14` engine gate, `test_19`/`test_20` round-trip + rejection; `tests/test_v5_1a_qualified_nodes.py` `test_10`–`test_14`, `test_24`/`test_25` | overnight windows (18:00/03:00 allowed, 16:00/09:00 blocked); `start == end` rejected; unsupported values refused, not stored; the backtest uses the same evaluator as live |
| MT5 execution | `tests/test_v4_2_mt5_execution.py` (45), `tests/test_v5_manual_order.py` `test_08`–`test_12` | validation set, retcode/ticket/fill persisted and read back, rejection reported with its retcode, broker state never assumed when the terminal is silent |
| evolution | `tests/test_v4_1_research_run_workflow.py` (18), `tests/test_v5_pipeline_smoke.py` (7), `tests/test_v5_realistic_backtest.py` (7) | run lifecycle, population/limit, realistic fills and costs |
| qualified nodes | `tests/test_v5_1a_qualified_nodes.py` (25) `test_01`–`test_09`, `tests/test_v5_status_and_diagnostics.py` (13) | every node lands in exactly one bucket; an infrastructure failure is never a strategy failure; counts are a partition; missing values stay `null`; unknown filters refused with the real list |
| deep backtest | `tests/test_v4_6_mt5_historical.py` (29), `tests/test_v5_1a_coverage_and_balances.py` (8), `tests/test_v5_1a_qualified_nodes.py` `test_15`–`test_20` | coverage report against the real MT5 dataset, requested vs actual bounds, completeness + named gaps, balances, batch guards, no-trades verdict |
| browser / static build | `tests/test_frontend_serving.py` (15), `tests/test_v4_8_frontend.py` (24), `tests/test_v4_7_frontend.py` (9), `frontend/tests/v48_ui_smoke.mjs` (50 render cases) | stale/edited/unstamped bundles detected; cache headers; identity not fooled by a foreign service; smoke drives the real DOM |

Commands and results:

```
EVOLUTIONARY_LAB_DATA_ROOT=/opt/lmsarena-storage/DATA \
  /opt/lmsarena-storage/venv/bin/python -m pytest -q -p no:cacheprovider
→ 437 passed, 1 warning (StarletteDeprecationWarning), 0 failed, 104.83s

# the §40 core subset
pytest tests/test_v5_live_testing.py tests/test_v5_1a_qualified_nodes.py \
       tests/test_v5_1a_live_index_and_risk.py tests/test_v5_1a_coverage_and_balances.py \
       tests/test_v5_manual_order.py tests/test_frontend_serving.py
→ 99 passed

cd frontend && npm run build          → vite build OK (5.74s), 2 entry assets
cd frontend && node tests/v48_ui_smoke.mjs
→ render smoke: 50/50 renders OK (114 stubbed API calls), no crash and no
  undefined/NaN/[object Object] in any rendered page
```

## 7. §42 — 43-step acceptance

The brief's original 43-step list is not stored in the repository, so the steps
below are the acceptance criteria reconstructed from §1–§41 and marked with the
evidence that exists today. "Sandbox" means it was executed and observed in this
environment; "Windows" means it needs the operator's machine with the MT5
terminal and is **not** claimed here.

| # | Step | Status |
|---|---|---|
| 1 | Reset preview lists exactly what will be deleted and kept | ✅ Sandbox — `will_delete{strategies 10000; backtests 488, validations 9, matrices 5, live_test_configs 1; generation_stats 7}` / `will_keep{legacy 787, datasets 77, master 9, feature_meta 5691, runs 5, market data + MT5 config}`; wrong token → 409 |
| 2 | Executed reset leaves DATA intact and the legacy set untouched | ✅ Sandbox — `reset_only` → `RUN-20261007-091051`, `legacy_untouched true` |
| 3 | The first node of a fresh experiment is `Node_1` | ✅ Sandbox — `after_reset{population 5000, first_node_number 1, generation 0}` |
| 4 | Counters/generations/population are experiment-scoped, not database-wide | ✅ Sandbox — `USER_RESEARCH_SCOPE_SQL` scoping; §4 probe re-checked with the user-research subquery |
| 5 | 10,000 is the fresh run's own limit, not added to an old population | ✅ Sandbox — `fresh_preview` + evolution scope; no offset arithmetic anywhere |
| 6 | Overview uses the current experiment | ✅ Sandbox — index reports the current experiment id/`RUN-…` |
| 7 | Live Testing renders with the required hierarchy | ✅ Smoke (`page:LiveTesting`) + source order in `LiveTesting.jsx`: connection/engine state → prop-firm monitor → `LiveMarketHeader` (market) → `ManualOrderPanel` → `RiskStrip` (risk) → `LiveNodeTable` (qualified nodes) → `LiveTestingControl` (execution state) → `LiveMarketPanel` (schedule + positions/orders) → `LiveTradeCounter` → `StageTimeline` (event stream) → "Statistics that could not be computed" (diagnostics, reported as unavailable rather than filled with placeholders) |
| 8 | The legacy Live Testing labels were repaired, not hidden | ✅ Sandbox — `LiveTestingControl.jsx` is still rendered inside the LiveTesting page's hierarchy and every label is read from the backend, not hardcoded: `/api/live-testing/status` → `mode "INACTIVE"`, `active false`, `engine_running true`, risk `{1.0/2.0 %, 1 active trade, 15 s tick, 120 s age, require SL}`, `node_count 0` with the LEGACY_TEST exclusions listed and their reason; `/api/mt5/status` → `mt5 "CONNECTED"`, `data_feed "LIVE"`, `platform_mode "SIMULATOR"`, `is_simulated true` (this environment's bridge; on the operator's machine the same field carries the real terminal); the demo-safety badge text comes from `account_status.{demo_verified, blocked_code}` and the trade counter from the live config's `max_active_trades`. Nothing was hidden and no label is a constant |
| 9 | Node list defaults to Qualified/Alive/Eligible | ✅ Smoke — the filter defaults to Qualified and the row list carries `Node_23` |
| 10 | All/Qualified/Failed/Excluded/Blocked/Unknown work with real totals | ✅ Sandbox — `filter_totals {qualified 35, alive 38, eligible 38, all 10803, failed 523, excluded 787, blocked 9455, unknown 0}`; each filter's `total` equals its entry |
| 11 | The per-node table shows real metrics (no missing → 0) | ✅ Tests — `test_07_a_missing_value_is_null_not_zero`; `numOrNull` fix |
| 12 | Per-node numbering is experiment-local and displayed | ✅ Sandbox — `research_node_num`; `Node #N` in the table and drawer |
| 13 | The schedule dialog opens with the node's real options | ✅ Smoke + Sandbox — node 877 offers `entry_short` only; a foreign condition is refused |
| 14 | Days Mon–Sun check/uncheck/select all/clear all work | ✅ Smoke — checkbox label matching, select-all/clear-all exercised |
| 15 | Sessions/regimes and M1–D1 timeframes are selectable and validated | ✅ Tests — `test_20_unsupported_sessions_or_days_are_rejected_not_stored` |
| 16 | Trading windows carry an explicit timezone; `start == end` rejected | ✅ Tests — `test_04`, `test_05`; validation refuses an empty window |
| 17 | Enabled toggle + summary line reflect the edits | ✅ Smoke — description line rebuilt from `evaluate()`'s own description |
| 18 | Cancel / Reset / Save all work | ✅ Smoke — Cancel discards, Reset restores the node's saved state, Save posts |
| 19 | The saved schedule persists across refresh/navigation/reopen/restart | ✅ Sandbox — node 877 saved → GET re-read, `/api/nodes` row and nodes-table agree; survives a backend restart |
| 20 | Node A's schedule never leaks to Node B | ✅ Tests — schedules are stored per node/experiment key |
| 21 | Impossible combinations are rejected with a clear message | ✅ Sandbox — node 877 + `entry_long` → 422 `SCHEDULE_INVALID` ("this node has no such signal component(s)") |
| 22 | No accidental empty-means-all | ✅ Tests — `configured=false` is the only "unconfigured" state; a configured-but-empty schedule allows nothing (`test_12_a_disabled_schedule_cannot_trade_at_all`) |
| 23 | One authoritative evaluator gates Live Testing and Deep Backtest | ✅ Tests — `test_14_the_backtest_uses_the_same_evaluator_as_live` |
| 24 | A blocked order is recorded as `SCHEDULE_BLOCKED` | ✅ Tests — `test_11_engine_refuses_the_order_when_the_schedule_excludes_now` |
| 25 | Deep Backtest lists ALL qualified nodes; one/multiple/all selectable | ✅ Smoke — `page:DeepBacktest-qualified`, "Deep backtest all qualified", selection arms the batch action |
| 26 | A deep backtest really runs (strategy/schedule/timeframe/period) | ✅ Sandbox — `HRUN-20261007-095614-4225E1` (node 877, XAUUSD M15, 2026-09-07→10-05) ran through the engine |
| 27 | Historical data is loaded and verified; requested vs actual reported | ✅ Sandbox — requested 09-07T00:00Z→10-05T23:59Z vs actual 09-07T01:00Z→10-05T05:45Z, 1850 bars, dataset `XAUUSD_M15_MT5_…_ICMarketsSC-Demo_v1` (MT5) |
| 28 | Coverage/completeness/quality/gaps are reported, never a subset as the whole | ✅ Sandbox — `completeness_pct 68.316` vs 2708 expected, `complete false`, `gaps 20`, `missing_bars_total 858`, `monotonic_increasing true`, `duplicate_timestamps 0`, first gap 09-07T21:15Z→09-08T01:00Z (14 bars) |
| 29 | The run applies the node's schedule and reports allowed/blocked bars | ✅ Sandbox — 288 bars allowed / 1562 blocked for the Mon+Wed London window |
| 30 | Results carry the full metric set | ✅ Sandbox — net +97.35 / 0.0097 %, PF 2.369, win rate 50 %, max DD 0.0071 %, sharpe 7.708, expectancy 0.002434, avg win 84.234, avg loss −35.56, largest win 85.80, largest loss −35.56, trades 4, avg hold 15 bars, avg slippage 0.894 pts |
| 31 | Start/end balance are stated | ✅ Sandbox — `start_balance 10000.0`, `end_balance 10097.35`, with a `balance_source` note; a pre-patch run reports `null`, never 0 |
| 32 | Run states behave distinctly (queued…blocked) | ◐ As implemented and tested — the stored machine is `QUEUED / RUNNING / COMPLETED / FAILED / CANCELLED` (`runs.py` L61 `STATUSES`), and the UI presents `Data unavailable` and `Backtest error` as their own states (`DeepBacktest.jsx` L148 `RUN_STATES`) so a data problem is never shown as a strategy failure. The brief's "loading/validating/incomplete/blocked" vocabulary is expressed honestly rather than as invented statuses: metrics the engine did not emit are listed in `results.unavailable[]` with a per-metric reason, an incomplete slice sets `coverage.complete=false` with its gap list, and a node the batch could not queue is reported individually with its reason (`{requested, started, failed, runs}`) instead of being silently dropped |
| 33 | A no-trade run says exactly that | ✅ Tests — `test_15_a_completed_run_with_no_trades_says_exactly_that` |
| 34 | Never "Test completed" without results | ✅ Source+tests — a completed run always carries a verdict string built from its own counts |
| 35 | An infrastructure/data problem is never shown as a strategy failure | ✅ Tests — `test_02_an_infrastructure_failure_is_never_a_strategy_failure` |
| 36 | Risk/lot calculation is broker-aware and recalculated on any change | ✅ Tests — 13/13 risk tests; 2× risk or ½ stop doubles the volume |
| 37 | Below-minimum sizes are refused with the real reason | ✅ Sandbox — `$10 + 300 pips` XAUUSD → `VOLUME_BELOW_MINIMUM`, raw 0.00333333 < 0.01 (risk per lot 3000) |
| 38 | The volume sent to MT5 equals the validated broker-compatible volume | ✅ Tests — preview rounds `raw_volume` to 8 dp and the place path validates the same value (`abs=1e-8`) |
| 39 | Manual order path validates the full set and distinguishes outcomes | ✅ Tests — `test_v5_manual_order.py` `test_08`–`test_12` (connected/demo/symbol/tradable/tick freshness/volume/SL-TP/direction/filling) and the outcome taxonomy in `validate_order_request` |
| 40 | A real MT5 DEMO order is placed and reported | ⛔ **Windows** — not executed here (no terminal in the sandbox). The place path is exercised against stubs and persists retcode/ticket/fill; the operator must confirm on their machine |
| 41 | START/STOP really start and stop a node; live + position status tell the truth | ◐ Partly — the endpoints enrol/remove the node and the engine activates/deactivates (§ smoke 837 assertion, `test_16`/`test_17`); a *real* market session on the operator's terminal is a Windows step |
| 42 | Browser/static-build consistency (Edge vs Chrome) with no cache-clearing answer | ✅ Sandbox — see §5 above; the launcher checks it on every start |
| 43 | Nothing forbidden by §43 was done | ✅ — no rewrite, no data modification, no fabricated result, no cosmetic schedule, no hidden node, no offset reset, no duplicate logic, no cache-clearing instruction, no removed launcher protection |

**Honest summary:** 40 of the 43 steps are verified by executed evidence;
step 40 and the market-session half of step 41 require the operator's Windows
machine and are not claimed.

## 8. Git

```
branch            : main
origin            : https://github.com/awais225/evolutionary-trading-lab.git
HEAD              : 69e7d94fc11754946437199494371fbd693436ce
origin/main       : 69e7d94fc11754946437199494371fbd693436ce   (API-confirmed)
history           : 8c00e84 → 84f526c → 2e71447 → 21f7ae9 → 97ad03b → 69e7d94
pushed            : yes — `git push origin main` → 97ad03b..69e7d94  main -> main
not committed     : DATA/**, frontend/dist/**, LOGS/*, runtime CONFIG, caches,
                    node_modules, venvs, secrets
```

## 9. Not claimed

* No MetaTrader 5 terminal exists in this environment: no real DEMO order was
  sent, no real broker fill is reported, and no live node was left running on a
  real account. The MT5-facing code here is exercised against the lab's bridge
  stubs and the broker-spec fixtures.
* Live counts drift while research runs (they are read from the live database at
  request time, not cached) — the numbers quoted in §7 step 10 were read at
  `2026-10-07 09:5x UTC`.
* The packaged `.exe` path remains gated rather than rebuilt: packaging needs
  Windows + PyInstaller, and the packager now refuses to embed a bundle that
  does not match `frontend/src`.

---

## 10. Final verification pass (2026-10-07) — executed evidence

The sandbox's temporary storage (`/opt/lmsarena-storage`) is outside the coding
workspace and is not part of a workspace snapshot, so it was rebuilt with the
documented recipe (pinned venv, git-lfs 3.5.1, DATA provisioned verbatim from
`scripts/lmsarena_provision_data.sh` → 10,787 strategies = 10,000 USER_RESEARCH
+ 787 LEGACY_TEST, `npm install`). The Git remote was re-registered (`.git/config`
is snapshot-excluded) and `origin/main` was confirmed with `git ls-remote`.

**Schedule (§2), measured on the running backend.**

| Step | Result |
|---|---|
| `POST /api/live-testing/schedule/877` (Mon/Wed, London, M15, `entry_short`, 07:00–16:00 UTC) | `200 ok=true persisted=true`; description `Mon/Wed, London, M15, conditions entry_short, 07:00–16:00 UTC`; `evaluation.allowed=true`, **10/10 rules ok** |
| A condition the node does not have | `422 SCHEDULE_INVALID` — `this node has no such signal component(s) ['entry_long']; supported: ['entry_short']` — refused, nothing stored |
| `GET` re-read | the saved values come back (`config.days [0,2]`, sessions, windows, conditions, `enabled true`, `schedule_version 2`), together with `options` (days/sessions/regimes/timeframes/timezones/`empty_list_means`/`window_semantics`) |
| Node isolation | node 837 stays unconfigured — *"Mon–Fri (default), all sessions, no other restriction"* |
| Backend restart | after killing and restarting the server the same `GET` returns the same values, 10/10 rules ok |

**Deep Backtest (§3), same node/dataset/period, schedule ON vs OFF.**

| | A — schedule saved | B — schedule disabled |
|---|---|---|
| Run | `HRUN-20261007-102556-BE97AE` | `HRUN-20261007-102558-7A73B8` |
| Bars | **288 allowed / 1562 blocked** | **0 allowed / 1850 blocked** |
| Trades | 4 | 0 |
| Return / net | 0.0097 % / +97.35 | 0.0 / 0.0 |
| PF / max DD / win | 2.369 / 0.0071 % / 50 % | — |
| Balances | 10000.0 → 10097.35 | 10000.0 → 10000.0 |
| Verdict | *Completed — 4 trade(s) evaluated by the engine.* | *Completed — no trades generated under this node's strategy/schedule for the selected period.* |

Requested vs actual range comes from the run's own coverage block — requested
`2026-09-07T00:00:00Z → 2026-10-05T23:59:59Z`, actual
`2026-09-07T01:00:00Z → 2026-10-05T05:45:00Z`, dataset
`XAUUSD_M15_MT5_Raw Trading Ltd_ICMarketsSC-Demo_v1` (MT5), completeness
**68.316 %** (1850 of 2708 bars, 20 gaps). Metrics the engine cannot supply are
listed in `results.unavailable[]` with a reason instead of a placeholder.

**FROM SCRATCH isolation (§4).** `tests/test_v5_1a_fresh_run_isolation.py`
(6 tests, now part of the suite) drives the real `Database` + `EvolutionEngine`
+ `lifecycle.reset_user_research` against a throwaway database and proves:
the reset deletes only the current experiment (a LEGACY_TEST node and its
backtest/live config survive); the next run numbers from 1 again and reuses no
id; no node/backtest/live-config/generation-stat follows the new run; previous
node ids are unreachable after the reset; the node limit is per run
(10000 → 9997 remaining for the next experiment); the preview states the exact
deletion/keep sets. Writing them exposed a real bug — `research_shortlist` is
created lazily, so a brand-new database made the reset abort with
`no such table`; `lifecycle._reset_statements` now takes the tables that
actually exist (`existing_tables(db)`) and returns labels, so the reset works on
a fresh install too (the restore path shares the helper).

**Risk / MT5 (§6/§7) in this environment.** The sandbox bridge is the lab
simulator with no quotes and no contract specs, so
`POST /api/mt5-execution/preview` answers `INVALID_ENTRY_PRICE` (with
`symbol_info.source = SIMULATOR`, null tick/volume specs) rather than inventing
a volume, and `POST /api/mt5-execution/place` answers `409 CONFIRMATION_REQUIRED`
without the phrase and `503 MT5_UNAVAILABLE` with it — *"the active market bridge
is 'SIMULATOR', not a real MetaTrader 5 terminal"*. No fill is faked. The
broker-spec maths and the full validation chain are covered by the 26 tests in
`test_v5_1a_live_index_and_risk.py` (13) and `test_v5_manual_order.py` (13); a
real DEMO order remains the one Windows-only step.

**Static build (§8).** `--stamp` → `SRC_HASH 9431c59e85c85065`,
`INDEX_SHA256 33162ab102f5598f`; `--verify-served` → `SERVED_STATUS=FRESH`,
`SERVED_MATCHES_LOCAL_SRC=True`, `SERVED_DIST_MATCHES_SRC=True`,
`SERVED_REASONS=[]`; `--identify` recognises the lab; `GET /system/build` →
`dist_status FRESH`, `dist_matches_src true`, `reasons []`; `GET /` is
`no-store, must-revalidate` with an ETag; hashed assets are
`public, max-age=31536000, immutable`.

**Tests (§9).** Full suite **443 passed / 0 failed** (1 StarletteDeprecation
warning, 109.27 s) — 437 before this pass plus the 6 isolation tests. Core
regression subset (schedule, qualified nodes, live index/risk, isolation,
coverage, manual order, MT5 historical, MT5 execution, frontend serving):
**205 passed**. `npm run build`: OK, 6.02 s. Render smoke: **50/50**.
