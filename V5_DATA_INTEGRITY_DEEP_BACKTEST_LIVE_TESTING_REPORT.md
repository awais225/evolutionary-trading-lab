# V5 — Data Integrity, Deep Backtest, Live Testing, MT5 Integration & Dashboard Shutdown

Implementation + verification report for `awais225/evolutionary-trading-lab`.
Every number in this report was produced by the running system (real DATA, real
engines, real endpoints) and is reproducible with the commands quoted next to it.
Nothing here is estimated, and no result is reported that the lab did not measure.

Environment used for the evidence below: Python 3.13 virtualenv
(`pandas 3.0.6`, `numpy 2.5.3`) inside the sandbox, authoritative DATA tree copied
to `/opt/lmsarena-storage/{v5_testdata,v5_validate,v5_live3}` (DATA itself is never
written to by a probe).

---

## 1. Root cause — why the survivor count looked like a collapse

**Symptom.** The dashboard showed roughly 10,000 generated candidates and only a
handful of "alive" nodes, which read as either a broken evolver or data loss.

**Diagnosis (measured, not inferred).** The authoritative backtest counters
(`GET /api/diagnostics/backtest`) decompose the population:

| counter | value | meaning |
|---|---|---|
| requested / generated | 10,851 | candidates the lab was asked to produce |
| tested | 812 | candidates that actually reached a backtest |
| never_tested | 10,039 | candidates that never reached one |
| skipped | 9,799 | **never tested because their market data was not available** |
| rejected | 399 | tested, refused by a research gate |
| failed | 240 | tested, judged on performance |
| cancelled | 141 | run stopped before evaluation |
| valid_or_alive | 230 | survived to a real in-sample result |
| data_failures | 9,799 | infrastructure outcomes |
| data_corrupt | 0 | — |
| backtest_errors | 0 | — |

Reconciliation flags returned by the same endpoint:
`tested_plus_never_tested_equals_requested = true`, `skipped_is_never_tested = true`,
`alive_is_counted_separately = true`.

The raw status column, however, held `FAILED 10,477` next to `SURVIVED 94`,
`QUALIFIED 129`, `RETIRED 130`, `KILLED 11`, `PAPER 7`, `DATA_UNAVAILABLE 3`.
So the overwhelming majority of "FAILED" rows were **never tested at all**: their
market data could not be resolved in the environment that produced them, and the
single `FAILED` label conflated "the strategy lost money" with "we never got to
judge it". The low alive count was therefore an **infrastructure/misclassification
problem, not a population collapse** — and the fix had to make that visible without
inflating the survivor count.

Two concrete defects produced and hid the problem:

1. **A hard feature-cache gate.** `is_dataset_eligible_for_research(...,
   require_features=True)` (and the per-cycle feature validation) treated a
   *missing or unreadable feature parquet* as a reason to exclude a dataset — or,
   in the orchestrator's pre-flight, to block the entire research cycle
   ("Research is BLOCKED until valid feature artifacts exist"). Feature caches are
   **derived** data: they can be recomputed from the dataset itself, so an absent
   cache is not evidence about market data or about a strategy.
2. **One status for many causes.** A dataset outage, a corrupt artifact, an engine
   exception and a genuine performance rejection all landed in `FAILED`, so the
   dashboard could not distinguish them, and the survivor rate looked catastrophic.

**Fix (V5).** Explicit status vocabulary (§3), separate counters (§4), no hard
feature-cache gate, and per-dataset exclusion instead of a whole-cycle block:

* `backend/app/data/engine.py::is_dataset_eligible_for_research` — a missing feature
  artifact no longer makes real market data ineligible; it is computed on demand and
  a present-but-corrupt artifact is rebuilt (its state is reported through
  `dataset_diagnostics()`), never silently replaced.
* `backend/app/orchestrator/lab.py` — `_verify_and_validate_features()` now returns
  `(usable_dataset_ids, failures)`; the new pure helper `feature_validation_outcome()`
  decides `BLOCK` (nothing usable at all) versus `CONTINUE` (exclude exactly the
  unusable datasets, report each with its real reason, research the rest). Nodes
  belonging to an excluded dataset are data outcomes, never strategy failures.
* `backend/app/status.py` — one place maps a stored row to a V5 status and classifies
  failure *reasons* (`classify_failure()`): dataset/feature/dependency wording →
  `DATA_UNAVAILABLE` / `DATA_CORRUPT`; strategy-gate wording → `STRATEGY_FAILED`.

**Verified, not asserted.** A deliberate mixed case (one dataset with an unusable
feature set, one healthy) now continues on the healthy dataset and reports the
exclusion; a case where *nothing* is usable still blocks, with the real per-dataset
reason attached (`tests/test_v5_pipeline_smoke.py`).

---

## 2. Old vs current logic (what changed and why)

| area | before | now (V5) | why |
|---|---|---|---|
| status of a candidate | `FAILED` for data outages, engine errors and performance rejections alike | `NOT_TESTED / PENDING / TESTING / VALID / STRATEGY_FAILED / DATA_UNAVAILABLE / DATA_CORRUPT / BACKTEST_ERROR / LIVE_ELIGIBLE / LIVE_TESTING / LIVE_COMPLETED / MT5_DEMO` | only a real performance failure may be called one |
| feature cache | missing artifact ⇒ dataset ineligible; cycle blocked until artifacts exist | derived on demand; a dataset that still cannot produce its core features is excluded and reported | the cache is an accelerator, not evidence |
| dataset problems | silent substitution / blocking | reported per dataset with the real reason (`/api/diagnostics/data`, `/api/diagnostics/eligibility`) | DATA is authoritative and never rewritten |
| counters | single "failed" bucket | requested/generated/tested/skipped/rejected/failed/data-failures/backtest-errors/valid/alive/requeued + evolution counters, with reconciliation flags | an operator must be able to decompose 10k → N |
| backtests | session/period assumptions, single-stage fills | explicit signal / execution / position / SL-TP / cost separation, symbol specs (tick size/value, contract size, min/max lot, lot step, sessions), spread/slippage/commission/swap, no lookahead, no unrealistic fills | results must be trustworthy before they are displayed |
| live testing | UI-level start/stop, decorative schedule | START/STOP really drives the engine (confirmation-gated, `positions_touched:false` on stop); schedule (days + sessions) is enforced by the engine and the reason is returned verbatim | nothing cosmetic |
| manual MT5 orders | no real path | real bridge path with sizing from symbol specs, pre-send preview, broker retcode/ticket/fill/SL-TP verification, audit table | demo/test execution is real, never faked |
| dashboard shutdown | process-wide kill | session-scoped process manager, ownership-verified, ordered stop, verify, forced kill only for confirmed-owned PIDs | the browser, the OS and unrelated processes must survive |

---

## 3. Data

* Authoritative DATA is 11,370 files; a probe never writes to it (all validation runs
  use `cp -a` copies under `/opt/lmsarena-storage/`).
* `GET /api/diagnostics/data` (live): `dataset_rows: 77`, `reported: 77`,
  `by_usability: {VALID: 77}` — 77 datasets, none corrupt, none silently replaced.
* `GET /api/diagnostics/eligibility` (live): required = testable =
  `H1, M1, M15, M30, M5` for XAUUSD, `unusable_timeframes: []`.
* `GET /api/mt5-historical/capabilities` (live) resolves per-dataset evidence:
  `XAUUSD M15` source **MT5**, `dataset_id XAUUSD_M15_MT5_Raw Trading Ltd_ICMarketsSC-Demo_v1`,
  1,850 bars, 2026-09-07 → 2026-10-05, broker `Raw Trading Ltd`, server
  `ICMarketsSC-Demo`, fingerprint `8d4d0cb6d3a5f465dec5b43da4048bd2`,
  `eligible: true`, `eligibility_reason: ELIGIBLE`, `min_bars: 300`.
  `rejected_datasets: []`.
* Corruption is *reported*, never repaired silently: a dataset whose artifact cannot
  be read is surfaced as `CORRUPT` by the registry and counted in
  `data_corrupt` (currently 0).

---

## 4. Evolution

`GET /api/diagnostics/evolution` (live): parent candidates 99, eligible parents 97,
duplicate candidates blocked 1,905, unique genomes 10,851, plus a per-mutation-type
histogram (e.g. `random_exploration 3,727`, `crossover 1,191`,
`entry_condition_mutation 1,040`, `indicator_replacement 964`,
`indicator_addition 959`, `sl_mutation 705`, `tp_mutation 635`, `param_mutation 585`).

Per-generation records (`/api/diagnostics/backtest → generations`) show that low
survival is the *normal* consequence of the gates, e.g. generation 12: tested 620,
failed 1,558, survived 8, qualified 136, duplicates blocked 1,236; generation 10:
tested 490, failed 1,090, survived 9, qualified 135. Survival is recorded per
generation from the lab's own statistics, and nodes skipped for missing data are
excluded from the survival denominator because they were never evaluated.

A real-data pipeline validation run on a fresh copy of the authoritative tree
(`scripts/v5_validate_pipeline.py --nodes 40 --json v5_validation_run2.json`):
generated 40, born 40, unique 40, duplicates blocked 1, tested 40, not tested 0,
alive 7 → `VALID 7 / STRATEGY_FAILED 33 / DATA_UNAVAILABLE 0 / DATA_CORRUPT 0 /
BACKTEST_ERROR 0`, distributed over M1 (7), M5 (6), M15 (11), M30 (11), H1 (5).
So with data present, every candidate is tested and the outcome is a *strategy*
verdict, not an infrastructure one.

---

## 5. Deep Backtest

`frontend/src/pages/DeepBacktest.jsx` + `GET /api/mt5-historical/*`:

* one graphical table (star, ID, status, strategy, symbol, TF, IS return, PF, win
  rate, trades, max DD, Sharpe, risk, period, last tested, action) with
  search/sort/filter/pagination, star and watchlist;
* **`Show Failed` is off by default** (§6); when ticked, failed / data-unavailable /
  backtest-error rows appear with their failure class (`STRATEGY` vs
  `INFRASTRUCTURE`);
* date presets 1/3/7/14 days, 1/3/6/12 months and a Custom from/to range that is sent
  to the engine and really controls the run (`start_date` / `end_date` in the run
  payload), never a label;
* run states are the engine's own: `QUEUED → RUNNING → COMPLETED`, or `CANCELLED` /
  `FAILED` / `DATA_UNAVAILABLE` / `BACKTEST_ERROR`; one run at a time
  (`max_active 1`), queue depth 5, identical requests refused while active;
* the dashboard does not freeze: runs execute server-side and the panel polls only
  while a run is active.

---

## 6. Live Testing

`GET /api/live-testing/status` (live): `mode INACTIVE`, `engine_running: true`,
`risk {risk_pct_default 1.0, risk_pct_max 2.0, max_active_trades 1, tick_interval_s 15,
max_data_age_s 120, require_sl true}`, 49 live-testing candidates, and an explicit
`excluded[]` list that names every LEGACY_TEST node and the reason
("LEGACY_TEST node - never a live trading candidate") — nothing is dropped silently.

`GET /api/live-testing/nodes-table` (live, node 837): `v5_status VALID`, `status
SURVIVED`, `market XAUUSD`, `timeframe M15`, `direction both`, `is_return_pct 0.0067`,
`profit_factor 1.769`, `trades_is 11`, `risk_pct 1.0`, `risk_source GLOBAL`,
`global_risk_pct 1.0`, `is_active false`, `schedule {days null, sessions null,
active false}`.

* START/STOP are confirmation-gated (`confirmed != true → 409`, no state change;
  `confirmed:true` → `is_active:true` and `eng.activate(confirmed=True)`); STOP
  reports `positions_touched:false`.
* The schedule is enforced by the engine, which returns the blocking rule verbatim,
  e.g. `sessions: 05:42 UTC against london 07:00–16:00 UTC, newyork 12:00–21:00 UTC`.
* Global default risk is validated against the configured cap (1.0 %, max 2.0 %) and
  every node reports whether its effective risk is the global default or its own
  override — the same value the engine sizes positions with.

---

## 7. Manual MT5 trading

* Sizing comes from real symbol specs: XAUUSD pip 0.10; a $100 risk with a 300-pip
  stop sizes fine; **$10 with a 300-pip stop = 0.0033 lots < 0.01 minimum ⇒
  `ok:false`, `VOLUME_BELOW_MINIMUM`, `estimate.loss_at_sl null`** — the lab refuses
  and says why rather than silently raising the risk.
* Preview returns levels + warnings + `estimate {loss_at_sl, profit_at_tp,
  reward_risk, basis}` and the documented defaults (`sl_pips 300`, `risk_amount 10`).
* Place returns the broker's own answer: `{ok, broker.retcode / message,
  order.ticket, execution {volume, exec_price, sl/tp_requested|broker,
  sl_tp_verified}}`, with 10016 → `ok:false`, DONE-without-position →
  `EXECUTED_UNCONFIRMED`, and unconfirmed orders refused with 409. Every attempt is
  audited in `manual_mt5_orders`.
* `GET /api/mt5-execution/state` (live) is honest about the environment:
  `execution_allowed:false`, `blocked_code MT5_UNAVAILABLE`,
  `account_safety.demo_verified:false`, bridge `{name:"simulator",
  is_simulated:true, connected:true}`. `GET /api/mt5-demo/status` (live) reports
  `DEMO ACCOUNT READY`, banner "DEMO ACCOUNT ONLY — NO REAL MONEY AT RISK",
  balance/equity 10,000.00, free margin 9,500.00 — a simulator account, labelled as
  one everywhere it appears.

---

## 8. Node Details — Trading Info

`GET /api/strategies/{id}/trading-info` translates the stored genome into
human-readable rules (no hardcoded text): e.g. node 837 → long
`break_prev_day_high > 0.5 AND break_prev_day_low > 0.5`, short
`break_prev_day_high > 0.5 AND ADX(21) > 18`, SL 2.93 × ATR(7), TP 1.13 × ATR(7),
hold 3/72 bars, risk 0.50 % equity, `max_concurrent 1`, sessions `null` rendered as
"no session filter". A genome that is already a dict or a JSON string produces the
same output; an empty genome produces `{value: null, reason: …}` for every item
instead of an invented value. Day lists render through `_day_names()`, so an integer
day never reaches the UI as `int("Mon")`-style garbage.

---

## 9. Results (Live Testing Results / prop-firm view)

`GET /api/live-test/results` (live) returns the statistics at the top level:
`starting_balance 10000.0`, `current_balance 10000.0`, `net_profit 0`,
`return_pct 0.0`, `profit_factor null`, `win_rate null`, `trade_count 0`,
`closed_trades 0`, `open_trades 0`, `max_drawdown_pct null`, `sharpe null`,
`sortino null`, `expectancy null`, `exposure_pct null`, an `exit_reasons` block, an
`unavailable[]` list naming every statistic that could not be computed, and
`read_only` / `source`. `null` means "not enough data to compute" and is rendered as
`N/A` with the backend's explanation — never as 0, 1.0 or "PASSING".
`prop_firm` is money-denominated (account size, equity, daily loss limit/used, max
loss limit/remaining, profit target + distance, trading days / minimum trading days,
`rule_violations[]`) with a verdict from
`{NO_DATA, IN_PROGRESS, TARGET_REACHED, BREACHED}`.

---

## 10. Dashboard shutdown (§10)

Verified live against the running backend (session `s-50084751bd41`, backend pid 5009).

* `GET /api/power/session` returns the session id, the backend pid, per-role owned
  processes (with `launched_by`, `external`, `alive`), the phrase `SHUTDOWN DASHBOARD`,
  a 9-step plan and the `never_touched` list.
* `POST /api/power/shutdown` **without the phrase → 409** `CONFIRMATION_REQUIRED`
  (`"send confirm='SHUTDOWN DASHBOARD' (exactly as shown in the panel)"`), nothing
  changes.
* **Dry run** returns the plan with each step's real decision, e.g.
  `stop_research_workers: {targets: [], terminated: [], force_killed: [],
  unverified: []}`, `stop_frontend: {signalled: [], browsers_touched: 0, note:
  "only a frontend process the dashboard itself started is stopped; a browser or a
  dev server started by the operator is never signalled"}`, `stop_scheduler:
  {cancelled_queued: [], left_running: [], deleted_rows: 0}`.
* **Yes** executed all nine steps in order
  `stop_new_tasks → stop_active_tasks → disconnect_mt5 → stop_background_workers →
  stop_research_workers → stop_scheduler → stop_frontend → stop_backend → verify`,
  each `OK`. Evidence:

  * `stop_backend: {pid: 5009, scheduled: true, signal: "SIGTERM", grace_s: 1.0}` — only
    the owned pid;
  * `stop_frontend: {signalled: [], skipped_external: [], browsers_touched: 0}` — the
    operator's dev server and the browser were **not** touched (verified: the Vite dev
    server answered 200 after the backend was gone);
  * `verify: {data_touched: false, browser_touched: false, still_alive: [],
    unverifiable: [], forced_after_verify: []}`;
  * DATA intact afterwards (same file counts before/after);
  * the backend went down (`/health` unreachable) and the dashboard **restarted
    cleanly** into a new session `s-4522419b6a1e` with `accepting_tasks: true`, and the
    new session reports the previous shutdown as `last_report`.

The Power button in the UI asks "Are you sure you want to close the dashboard?"
(Yes/No); **No** performs no request at all, and the dialog states that only the
dashboard is closed — the operating system, the browser, independently started
applications, an independently started MT5 terminal and DATA are untouched.

---

## 11. Testing

| check | result |
|---|---|
| `pytest -q` on a copy of DATA (`EVOLUTIONARY_LAB_DATA_ROOT=/opt/lmsarena-storage/v5_testdata`) | **376 passed, 1 warning in 62.14 s** |
| `pytest tests/test_v4_8_frontend.py` | **24 passed** (static + the JSX render smoke) |
| `node tests/v48_ui_smoke.mjs` (47 checks) | **47/47 renders OK**, no crash, no `undefined` / `NaN` / `[object Object]` |
| `npx vite build` | ✓ |
| real-data pipeline validation (40 nodes, fresh DATA copy) | 40 generated = 40 unique = 40 tested; 7 VALID, 33 STRATEGY_FAILED, 0 DATA_UNAVAILABLE / CORRUPT / BACKTEST_ERROR |
| live HTTP probes (real DATA, `v5_live3`) | `/health`, `/api/power/session`, `/api/live-testing/status`, `/api/live-testing/risk`, `/api/live-testing/nodes-table`, `/api/live-test/results`, `/api/mt5-demo/status`, `/api/mt5-execution/state`, `/api/mt5-historical/capabilities`, `/api/diagnostics/{data,eligibility,backtest,evolution}` all answered with real data |
| shutdown end-to-end | 409 without the phrase, dry run plan, Yes stopped only owned processes, DATA intact, dashboard restarted |
| UI interaction smoke (jsdom) | START calls the node-start endpoint; the schedule dialog shows the engine's own verdict and blocking rule; the Power dialog asks and its No is a no-op while Yes/dry-run sends the phrase; Trading Info renders from the genome; the market header shows bid/ask/spread/indicators; the MT5 Backtest node picker searches, stars (real endpoint) and loads a node; the node-data panel resolves the node's own dataset, shows integrity and fetches through `POST /api/data/sync`, echoing the engine's own answer |
| §19 node-data panel end-to-end | `XAUUSD M15` resolves to the MT5 dataset (1,850 bars, fingerprint, eligible), rejected datasets are shown with their reason, and a fetch returns the engine's verbatim `REUSING EXISTING DATA` |

Notes on test integrity: the suite never signals the pytest process
(`skip=("stop_backend",)`), the shutdown gate test restores
`power_mod._STATE["accepting_tasks"] = True` in a `finally` block, and no test
monkeypatches time on the heartbeat reader.

---

## 12. Git

* Base: `e11ec95` (V4.8) == `origin/main`.
* V5 commit: `________________________________________` (recorded in `lmsarena.txt`
  together with the push result as soon as it exists).
* Excluded from the commit by construction: `DATA/**` (the authoritative tree lives
  outside the workspace and is never committed), database/WAL/SHM files, ZIPs, PIDs,
  logs, caches, `node_modules`, virtualenvs and any secret or credential. The
  runtime-generated `CONFIG/lab_config.yaml` and `CONFIG/settings.json` are reverted
  before committing, and no sandbox-specific path appears in any tracked file.
