# V6.6 — Deep Testing Workspace + §12 Statistics Priority — Release Report

Release commit chain (branch `main`, in order):

| # | sha | purpose |
|---|-----|---------|
| 1 | `08821f3c` | tests: `test_v5_live_testing::test_13` trade-age robustness (pre-existing Sunday flake, fixed at root) |
| 2 | `6456ad30` | V6.6 content: Deep Testing workspace redesign + §12 statistics-priority integration |
| 3 | `16e9e228` | V6.6 content fix: `live_test_trades` additive migration tolerant of interleaving (§14) |
| 4 | (this commit) | docs: V6.6 publication record + `BUILD_FINGERPRINTS.json` (records `--release V6.6 --commit 16e9e228`) |

Fingerprint record: `SRC_HASH=0a7c19bec60fff66…`, `CODE_HASH=ade22c4d0894eb90…`,
`INDEX_SHA256=8afdc7a7afea0cd9…`, entry assets `index-kZu7aUtH.js` / `index-DG45pVtK.css`.
`build_fingerprints.py --match` identifies the tree as **V6.6 (16e9e22)**.

---

## A. Git state

- Branch: `main`. Starting HEAD: `42c740eb` (V6.5.1). Final HEAD: the commit adding this file (see publication record commit).
- Changed files (content commits): see §"Files by purpose" below. **Zero DATA paths in any commit**; `LOGS/lab.pid` and a restore-ritual `chmod` on `backend/tools/mt5_demo_forensics.py` were excluded from the commits.
- Push status: see the publication record commit message / chat evidence (remote HEAD match verified at push time).

## B. Regression results (2026-10-11, fresh scratch DATA copies, sha `c159b071…` verified)

The full suite is run as two sequential 31-file segments (this 2 GB sandbox OOM-kills a
single 62-file process; the split is disclosed). Commands, from repo root:

- Gate A (strict DeepTest, independent fresh copy):
  `EVOLUTIONARY_LAB_DATA_ROOT=/var/tmp/evo-v66-SCRATCH/DATA .venv/bin/python -m pytest tests/test_v6_6_deeptest.py -q`
  → **51 passed / 0 failed / 0 skipped (exit 0)** — `tests/test_v6_6_deeptest.py`
- Gate B (seg1 = first 31 test files, fresh copy):
  `EVOLUTIONARY_LAB_DATA_ROOT=/var/tmp/evo-v66-REG1/DATA .venv/bin/python -m pytest $(ls tests/test_*.py | head -31) -q`
  → **614 passed / 1 skipped / 0 failed (exit 0)**
- Gate C (seg2 = last 31 test files incl. `test_v6_6_deeptest.py`, fresh copy):
  `EVOLUTIONARY_LAB_DATA_ROOT=/var/tmp/evo-v66-REG2/DATA .venv/bin/python -m pytest $(ls tests/test_*.py | tail -31) -q`
  → **478 passed / 0 failed / 0 skipped (exit 0)**

**Full suite: 1092 passed / 1 skipped / 0 failed** (62 test files; the 51 strict V6.6
tests are counted inside seg2's 478; Gate A re-runs them independently on fresh data).

The single skip is disclosed: `tests/test_v5_1a_coverage_and_balances.py:120` —
"this installation carries no simulator dataset" (environment-dependent, same skip
present in the V6.5.1 baseline gate 1041/1/0).

Failures found and fixed at root during the gate (each with its evidence):

1. `test_v5_3_manual_order_capability::test_01` — my `deeptestCsvValidate` used raw
   `URLSearchParams`, breaking the qs()-only contract that keeps `"undefined"` out of
   URLs. Fixed in `frontend/src/api.js` (qs()), re-verified 18/18 in that file.
2. `test_v5_live_testing::test_13` — **pre-existing Sunday calendar flake**, reproduced
   identically at V6.5.1 base `42c740eb` (worktree at base, fresh DATA). The autouse
   weekday clock sits up to 2 days behind real time on a Sunday while
   `record_today(hours_ago=26)` wrote real-clock timestamps, so the cooldown rule saw a
   "future" trade (−1320 min) and blocked. Fixed at test level (trade aged 26+48h;
   assertions unchanged). No product code touched.
3. `test_v5_1a_live_test_order_path::test_02` — order-dependent flake in the shared
   `live_test_trades` additive migration: `_ensure_live_trade_columns` (app/db/database.py)
   raced with itself across two connections (PRAGMA snapshot, then ALTER) and lost the
   trade record on "duplicate column name: sl_requested". The same flake family was
   present at base (a different member, `test_v5_1a_launcher_identity::test_01`, flaked
   in the base seg1 run). Fixed at root: each ALTER now tolerates exactly "duplicate
   column name" (documented additive/idempotent migration, now concurrency-safe — §14).
   Deterministic interleaving regression test added.

Pre-existing failures distinguished from V6.6 regressions: item 2 and the item-3 flake
family are pre-existing (base reproductions kept in `/tmp/gateBASE.log`, base worktree
at `/var/tmp/v66-base-wt`). No V6.6-introduced failure remains.

Frontend smoke harnesses (against the production build + source):
`v48_ui_smoke.mjs` 56/56 renders OK · `v6_5_1_nodes_ui.mjs` 16/16 behaviours OK ·
`v47_render_smoke.mjs` 40/40 renders OK.

## C. DeepTest engine verification (live app, fresh run root)

Real startup path (`RUN_BACKEND.bat` = `python -m uvicorn app.main:app`, port 8787),
scratch root `/var/tmp/evo-v66-RUN/DATA` (pristine copy, db sha `c159b071…`):

- Run `dt-e5c5daa3ad72425a` — node 837 (real qualified node), XAUUSD M15, requested 3Y
  (2023-10-11→2026-10-11 UTC), actual tested range 2026-08-28→2026-10-05 (2,859
  validated candles), status **PARTIAL / PARTIAL COVERAGE** (missing start+end vs
  requested; never labelled complete; confirmed fingerprint
  `7cbe77a4ca24ab8b…` bound to node+dataset+coverage+period+settings).
- Trades: **61** persisted (`deeptest_trades`), sample coherent: side/entry_ts/price/
  exit_ts/price/volume 2.0/SL 2893.469/TP 2912.509/exit_reason TP/hold 225900s/
  net_pnl 2731.1423/session newyork/dow 4. Reconciliation ok (trade-level sum vs
  summary net: delta 0.001 rounding). Deterministic: two independent runs of the same
  request produced identical metrics (61 trades, net 48,212.76).
- Summary: initial 200,000 → final 248,212.76 (net +48,212.76, return 24.1064%),
  PF 2.761, win rate 52.46%, Sharpe 7.266, max DD 4.2795%, breakdowns by day-of-week/
  session/direction present, cost model itemised (spread 2,764.50, slippage 54.07,
  commission/swap 0.00 for this genome).
- **No broker orders**: `mt5_historical_runs` = 0 rows, `live_test_trades` = 0 rows in
  the run root after all runs; provenance `run_policy` = "historical simulation only —
  no broker orders"; engine adapter = "MT5->pandas historical adapter v1 (no broker
  orders)"; the bridge reported SIMULATOR mode and Live Testing stayed INACTIVE across
  server shutdowns ("New orders blocked; existing positions are NOT closed").
- Failed/unconfirmed runs are never presented as valid: an unconfirmed run request is
  refused with "the requested history is incomplete — confirm the coverage warning
  before this run may start"; a FAILED run observed earlier (bad engine import) was
  persisted as FAILED with its error and never preferred.

## D. Data / CSV verification

- Coverage: `pair_coverage` reports real store state only (bars/first/last/gaps/fingerprint);
  5 weekend session-closure gaps reported as warnings; material gaps (≥96 h) drive
  PARTIAL; zero-bar windows refuse.
- Coverage tests (strict suite): gap policy, missing-start/end, fingerprint binding
  (incl. "a preset's confirmation must not expire with the 'now' anchor"), pair
  coverage never fabricates, unknown pairs reported not guessed.
- CSV (live + tests): MT5 headerless joint-datetime parsed; naive timestamps →
  `needs_timezone_confirmation`, commit refused without an explicit timezone
  ("the timezone must be confirmed before committing this file"); explicit mapping
  reported (column→field), coverage taken from rows never the filename; row-level
  OHLC/dupe/order/gap validation; **timeframe mismatch refused** ("the rows are spaced
  like 900s bars but this import claims M5 (300s) — refusing a timeframe mismatch") —
  no silent conversion; **symbol/timeframe mismatch claims at commit refused** ("this
  file was validated for XAUUSD — it cannot be committed as EURUSD"); the merge is
  transactional with "stored data wins" conflict policy and full provenance
  (`deeptest_candle_sources`).
- Live probe note: a validate call may register a 0-row master-store entry for the
  claimed pair (shared `get_master` behaviour). No bars are created; it is shown as
  0 bars everywhere.

## E. Statistics priority + LIVE TESTING protection

Response shape (verified live):

- `GET /api/nodes` row for node 837: `metrics_source: "deeptest_preferred"`,
  `metrics_source_label: "DeepTest (preferred scenario)"`, `metrics` = the frozen
  9-key shape from the preferred scenario, `generation_metrics` preserved alongside,
  `deeptest.preferred_run_id` + `deeptest.coverage_status: "PARTIAL"`.
- `GET /api/deeptest/nodes`: nested `preferred` object (run_id/status/coverage_status/
  metrics/analytics/duration_key/finished_at/preference_source) + `scenario_count`,
  `valid_scenario_count`, `statuses`, `last_run` — the fields the earlier diagnostic
  looked for at top level were wrong keys; the real shape is the nested `preferred`.
- Precedence proven live and in tests: preferred DeepTest → latest valid DeepTest →
  generation → N/A. Explicit preference set to `dt-e5c5daa3ad72425a` survived a NEWER
  completed run (`preference_source: "explicit"` preserved; the newer run became
  `last_run` only). FAILED/CANCELLED runs never displace valid metrics (tested).
  A node without DeepTest results (e.g. node 791) shows `metrics_source: "generation"`,
  `metrics == generation_metrics` (unchanged).
- Partial marking: PARTIAL carried in `deeptest.coverage_status`, in the preferred
  object, and in the run record; metrics are never mixed across scenarios.

**Explicit list of LIVE TESTING-facing changes** (the only one):
`frontend/src/components/LiveNodeIndex.jsx` — the performance-metrics source tooltip
on the existing return-% cell (title text only; no layout/behaviour/execution change).
Live operational metrics (live P&L, positions, balance/equity, execution status,
schedules, MT5 connection) are untouched live values. `tests/test_v5_live_testing.py`
changed only the test-data age in `test_13` (pre-existing flake fix, assertions
unchanged) — no live execution logic changed. All other tabs untouched.

Shared-file changes (each unavoidable, behaviour preserved for other consumers):

- `backend/app/api/routes.py` — +16 `/api/deeptest/*` endpoints, the `nodes_index`
  §12 projection hook (additive fields only), and CSV commit pair-mismatch refusal.
- `backend/app/backtest/engine.py` — optional V6.6 `BacktestRequest` fields (scenario
  capital/risk/limits/SL-TP offsets); unset defaults keep the historical-research
  path byte-identical (V6.5 offset semantics preserved).
- `backend/app/backtest/execution.py` — additive `lots_for_amount()` helper (Mode B).
- `backend/app/db/database.py` — `_ensure_live_trade_columns` interleaving tolerance
  (§14; final schema byte-identical).
- `backend/app/versions.py` — `PRODUCT_RELEASE = "V6.6"`.
- `frontend/src/api.js` — additive deeptest client (relative urls, qs()).
- `frontend/tests/v48_ui_smoke.jsx` — smoke contract: obsolete V5 `page:DeepBacktest-
  qualified` block replaced by the V6.6 `page:DeepTesting-workspace` contract block
  (disclosed assertion-suite change).

## F. Production build

- Build: `cd frontend && npm run build`, stamped via
  `python backend/tools/frontend_build_guard.py --stamp` (also the `start.bat` path,
  which rebuilds/re-stamps if stale). Guard `--check` passes: "frontend/dist was built
  from the current frontend/src".
- Served fingerprint (verified over HTTP on the running app): `index.html` references
  `assets/index-kZu7aUtH.js` + `assets/index-DG45pVtK.css`;
  `build-info.json` `built_at=2026-10-11T05:28:35Z`, `index_sha256=8afdc7a7afea0cd9…`,
  `src_hash=0a7c19be…` (identical to the recorded SRC_HASH — the dist is from this
  exact source). Hash history: `DbffDyTi` was the pre-qs-fix build of 2026-10-10 and
  is stale; `kZu7aUtH` is the current one (rebuild after the api.js fix, deterministic
  across rebuilds).
- Lazy chunk `assets/DeepBacktest-CX434IDP.js` (served over HTTP) carries the V6.6
  markers ("Deep Testing", "DEEPTESTED NODES", "GET MT5 DATA").
- Clean restart: stop/start of the real startup path serves the same build and
  re-reads persisted scenarios/preference (verified).

**Browser acceptance: NOT VERIFIED — no browser is available in this environment.**
Evidence above is server-side (HTTP responses, served asset contents, smoke harnesses
that render the app in jsdom). Exact manual verification steps are in the chat report.

## G. Verdict

**PASS — V6.6 REGRESSION GATE COMPLETE** (with the browser acceptance item explicitly
**NOT VERIFIED** and pending manual verification, as disclosed above).

Outstanding / disclosed:

1. Browser-visible acceptance pending manual verification (steps in the chat report).
2. The `Mt5Backtest.jsx` `setPPage` bug (frozen file) remains as-is, per the frozen-file
   rule (carried over from V6.5.1 disclosure).
3. Per-trade `symbol`/`gross_pnl`/`commission`/`swap_cost` columns are present but null
   where the engine does not track them per trade (net P&L includes costs; itemised
   costs live in the analytics `costs` summary). Honest nulls, never fabricated zeros.
4. Windows MT5 demo §13 acceptance (incl. node-718 recovery) remains operator-side.
