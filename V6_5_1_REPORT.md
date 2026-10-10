# EVOLUTIONARY TRADING LAB V6.5.1 — CORRECTIVE RELEASE REPORT

**Release:** V6.5.1 — unified Nodes table lifecycle control, inline risk editing, risk modes,
MT5 account panel, build/serving identity (corrective release for the V6.5 UI defect report).
**Date:** 2026-10-10 · **Base:** V6.5 `68a183b313631c90b4e95b98a2a89470e37b2107`
**Nature:** additive correction. Strategy logic, entry/exit conditions, position sizing
semantics (when modes are unset), broker execution path, DATA and historical records are
preserved. Zero offsets = exact strategy levels. No real broker order was sent.

---

## A. Root cause

Why the UI was unchanged and why START never became STOP:

The unified Live Testing Nodes table (`frontend/src/components/LiveNodeIndex.jsx`) renders its
START/STOP control from `GET /api/nodes` row fields `worker`, `is_active` and
`live.status === "ACTIVE"`. The V6.5 backend added the worker/lifecycle/limits/offsets fields
**only to the legacy projection `GET /api/live-testing/nodes-table`** — never to
`GET /api/nodes` (`nodes_index`, `backend/app/api/routes.py`), which is the endpoint the table
actually consumes. Consequences observed by the operator:

1. After a successful START (the backend really started the worker and set
   `live_test_configs.status="RUNNING"`), the table reloaded and the row carried **no**
   `worker` key and **no** `is_active` key; `live.status` was `"RUNNING"` (not `"ACTIVE"`),
   so the button's `running` test was false and the control rendered **START** again. The
   same applied to any already-running node after refresh.
2. The Risk cell was display-only (no inline editor); per-node risk was only editable in a
   separate RiskStrip table and the schedule dialog — the "unified Nodes table" controls were
   not delivered. SL/TP offsets existed only in the hidden detail expansion; `max_active_trades`
   rendered perpetual defaults (its fields were also missing from `/api/nodes`).
3. No risk-mode concept existed (`compute_risk` sized from `equity * pct / 100` only).

Not a stale-build problem: the served bundle contained the V6.5 UI; the UI was reading a
payload contract the backend did not fulfil on that endpoint. The build/startup safeguards
introduced earlier (frontend_build_guard + `/system/build`) remain in force and were re-verified
at the HTTP boundary (§I).

## B. Actual serving process

* **Startup path (Windows, operator machine):** `start.bat` (or `start.ps1` → `start.bat`) →
  venv resolution → `backend/tools/verify_database.py` → `backend/tools/frontend_build_guard.py
  --check` (rebuilds `frontend/dist` via `npm install && npm run build` when stale, then
  `--stamp`) → port 8787 instance resolution → `RUN_BACKEND.bat` → uvicorn/FastAPI
  `app.main:app` serving `frontend/dist` statically on one port (8787).
* **Working directory:** repository root; **frontend output directory:** `frontend/dist`
  (generated, not committed); the backend mounts it at `/` (`DashboardStaticFiles`,
  `index.html` served `no-store`).
* **Served HTML entry:** `frontend/dist/index.html`, referencing
  `assets/index-BdNiBaR8.js` + `assets/index-DG45pVtK.css` (this release's build).
* **Diagnostics:** `GET /system/build` reports release id, git commit, code fingerprint,
  `dist_status` (FRESH/STALE/…), `index_sha256`, entry assets, process identity and launch
  token; the dashboard's Build chip (TopStatusRow) shows the same and warns when the loaded
  shell is not the served bundle.

## C. START/STOP lifecycle

Files changed: `backend/app/api/routes.py` (`nodes_index` payload + `_lifecycle_of`),
`frontend/src/components/LiveNodeIndex.jsx` (lifecycle control, pending states, poll,
stale-response guard), `backend/app/live_testing/workers.py` (unchanged — the V6.4 state
machine is authoritative and was reused).

State model (backend `worker.state` is the authority):
`STOPPED | STARTING | RUNNING | STOPPING | COMPLETED | ERROR | UNKNOWN | IDLE`.
`GET /api/nodes` now emits per row `worker` (the real worker state) and `lifecycle`
(`_lifecycle_of`: worker state wins; IDLE+not-enrolled → STOPPED; IDLE+enrolled-per-DB with no
live worker (restart) → **UNKNOWN**, never a false RUNNING/STOPPED claim).

UI behaviour: STOPPED → **START**; on click an immediate truthful **STARTING…** (disabled,
duplicate clicks blocked, one POST per gesture) → after the backend confirms
(`worker_running` + reloaded row) → **STOP**; stop → **STOPPING…** → confirmed → **START**;
failures show the explicit error and the control is restored from backend state (a failed stop
keeps showing STOP); UNKNOWN offers both idempotent operations with that explanation; state is
restored on refresh/navigation from `/api/nodes`; polling (8 s, silent) never wipes an action
error and out-of-order responses are discarded (request-sequence guard).

Test evidence: `tests/test_v6_5_1_lifecycle.py` (10 tests: full state machine incl.
STOPPED→STARTING→RUNNING→STOPPING→STOPPED, duplicate START/STOP idempotency, start-failure
handling, unconfirmed stop keeps STOPPING, node independence, index payload regression,
lifecycle mapping, real start/stop endpoints updating authoritative state);
`frontend/tests/v6_5_1_nodes_ui.*` acceptance harness (16 behaviours, run by
`tests/test_v6_5_1_frontend_ui.py`); runtime-verified live over HTTP against the running
backend (§I).

## D. Unified Nodes table

Implemented and visible in the single Live Testing Nodes table row:

| Control | Inline in row | Persistence | Notes |
|---|---|---|---|
| START/STOP | ✅ Action column | backend lifecycle | STARTING/STOPPING pending states, ERROR/UNKNOWN handled |
| Risk (mode + value) | ✅ Risk cell | `POST /api/live-testing/nodes/{id}/config` | click-to-edit, Save/Enter + Esc, saving/error states, saved value re-read from the response |
| Risk mode | ✅ same cell | `risk_mode` / `risk_capital_basis` | "% of capital" (Mode A) / "Constant money" (Mode B) |
| Max active trades | ✅ Max trades cell | `max_active_trades` | Default (inherit) or explicit 1..100 |
| SL offset (pips) | ✅ SL off cell | `sl_offset_pips` | 0 = strategy's own level |
| TP offset (pips) | ✅ TP off cell | `tp_offset_pips` | 0 = strategy's own level |
| Schedule | ✅ Schedule cell | Schedule dialog (structured editor) | existing format; engine-enforced |

Duplicate-editor removal: the RiskStrip per-node table is now a **read-only summary** (it can
no longer write node settings); the Nodes table is the single per-node risk editor. The global
risk editor remains (it edits the global default, not a node setting). Existing research
columns (node identity, bucket, gen, market/TF, fitness, IS return, PF, max DD, win, trades,
robustness, live counts, schedule) are preserved.

## E. MT5 account panel

New `frontend/src/components/Mt5AccountPanel.jsx` on the Live Testing page + read-only
`GET /api/mt5/account`. Shows **Balance / Equity / Free margin** (with currency), account
login/server/type, connection badge (CONNECTED / NOT CONNECTED / SIMULATOR), data-kind badge
(LIVE_ACCOUNT / SIMULATED / UNAVAILABLE), and a freshness line ("as of N s ago") from the
backend's `checked_at`. Unavailable/disconnected/missing-field/error states are explicit:
numbers render N/A with the reason — never 0, never a simulator's figures presented as live
(data_kind SIMULATED when the bridge is simulated). No credentials in the payload
(`tests/test_v6_5_1_account_panel.py::test_06`). Polling 10 s; refresh never places an order.
Tests: connected, simulated, disconnected (no account), missing fields, bridge exception,
credential absence.

## F. Risk modes

* **Mode A — PERCENT (percentage of capital):** allowed loss = capital × pct / 100 where the
  capital basis is explicit: **EQUITY** (default; live MT5 equity — the historical behaviour,
  unchanged) or **BALANCE** (live MT5 balance). Missing/zero basis data refuses sizing
  (`ACCOUNT_EQUITY_UNAVAILABLE` / `ACCOUNT_BALANCE_UNAVAILABLE`); pct bounds enforced
  (`RISK_PCT_ABOVE_MAXIMUM`, blocked not clamped).
* **Mode B — AMOUNT (constant monetary risk):** allowed loss = the configured `risk_amount`
  (account currency) if the stop is hit; validated positive, capped by
  `live_testing.risk_amount_max` (`RISK_AMOUNT_ABOVE_MAXIMUM`, blocked not clamped); pct
  settings do not configure this mode (trace still reports them).
* **Both modes** convert money→volume with the broker's real specification (tick size, tick
  value, volume min/max/step, contract size) in `compute_volume`; volume is normalized **down**
  to the broker step (never rounded up past the risk limit), min/max volume enforced, missing/
  invalid metadata refused (`INVALID_SYMBOL_DATA`), zero/negative SL distance refused
  (`SL_MISSING` / `SL_DISTANCE_ZERO`). The selected mode and amount are shown in the row and in
  the execution trace (`risk_mode`, `capital_basis`, `capital`, `risk_amount`).
* **Documented limitations (not an absolute guarantee):** the stop is assumed to fill at its
  price — gaps, slippage, spread movement, commission and swap are not modelled in the sizing
  math; no account-currency conversion is applied to the instrument's tick value (accounts in
  a different currency than the instrument's profit currency are sized on the raw tick value).
* **Tests:** `tests/test_v6_5_1_risk_modes.py` (16 tests: both modes, both capital bases,
  volume rounding across three representative instrument specs with worst-case SL risk ≤
  requested amount, metadata failures, mode/basis rejection, config validation, persistence,
  per-node isolation, persisted settings reaching the sizing call exactly as `engine.py`
  passes them).

## G. Existing functionality and DATA

* No DATA deletion/reset/recreation; no study resets; no node regeneration. The authoritative
  DATA tree (`/tmp/evolutionary-trading-lab-DATA/DATA`) is byte-identical (published LFS blobs);
  all tests and the runtime verification ran against scratch copies (`/var/tmp/...`).
* Migrations are additive only: `live_test_configs` gains `risk_mode`, `risk_amount`,
  `risk_capital_basis` (same idempotent `ALTER TABLE` mechanism as V5/V6.5 columns). Existing
  rows read as unset → Mode A/EQUITY, i.e. the historical behaviour verbatim.
* Strategy logic, entry/exit conditions, signal generation, order construction, filling modes,
  magic-number scheme, close verification, emergency stop, Deep Testing, manual demo trading,
  the trade ledger and trade history are untouched (diff review §J). `compute_risk` keeps its
  historical call signature working (all pre-existing callers/tests pass unchanged).
* MT5 bridge changes: none this release (the account endpoint is read-only on the existing
  `account_info()` path).

## H. Regression tests

Command: `EVOLUTIONARY_LAB_DATA_ROOT=<fresh scratch>/DATA .venv/bin/python -m pytest tests/ -q`

* **Result (final gate):** see publication record at the end of this file — the full suite is
  green after fingerprint recording (the two `test_v5_2_3` identity tests require the
  `BUILD_FINGERPRINTS.json` entry, recorded after the content commit per the established
  self-reference flow; they are exercised in the final gate).
* New tests: `tests/test_v6_5_1_lifecycle.py` (10), `tests/test_v6_5_1_risk_modes.py` (16),
  `tests/test_v6_5_1_account_panel.py` (6), `tests/test_v6_5_1_nodes_config_api.py` (5),
  `tests/test_v6_5_1_frontend_ui.py` (1 → 16 UI behaviours inside).
* Skip: 1 (pre-existing `test_v5_1a_coverage_and_balances.py:120` — "no simulator dataset";
  unchanged from the V6.5 gate). No test was removed or weakened.

## I. Build and runtime verification

Chain evidence `Git commit → frontend build → generated HTML/assets → running server`:

* `npm run build` + `frontend_build_guard.py --stamp` → `frontend/dist/build-info.json`
  (`src_hash=4bf7ac8e…`, entry `assets/index-BdNiBaR8.js`, `index-DG45pVtK.css`).
* Real backend started from the repository (`uvicorn app.main:app`, port 8787) and probed over
  HTTP at the serving boundary:
  * `GET /` (no-store) references exactly `assets/index-BdNiBaR8.js` + `assets/index-DG45pVtK.css`;
    both assets return 200 and their served bytes hash-match the built files on disk
    (`c197fbbc…` / `6936d0f2…`); served `index.html` sha256 `6aefaeb5…`.
  * `GET /system/build`: `release=V6.5.1`, `dist_status=FRESH`, `dist_matches_src=true`,
    `served.index_sha256` = stamp `index_sha256` = `6aefaeb5…`, entry assets equal the served
    HTML references.
  * `GET /api/nodes` rows carry `worker`/`lifecycle`/limits/offsets/`risk_mode`.
  * Live lifecycle over HTTP (this host, SIMULATOR bridge, scratch DATA, no broker):
    `POST .../start` → `worker_running: true`, index `lifecycle: RUNNING` (button would show
    STOP) → `POST .../stop` → `worker_stopped: true`, index `lifecycle: STOPPED` (button would
    show START) → repeat `stop` → `already_stopped: true` (idempotent), `positions_touched:
    false`.
* Browser-level inspection of the operator's Windows/MT5 instance is **not** possible from
  this Linux host and is NOT claimed. The manual acceptance walkthrough (§12 of the
  directive) is reproducible via the steps in §L against the operator's own instance.

## J. Git publication

See publication record at the end of this file (content commit + fingerprint commit, push
result, fetched remote HEAD). Diff review: no strategy/DATA changes; no credentials; the only
DB change is the additive column migration mechanism; `LOGS/lab.pid` and tool exec-bits
restored before commit.

## K. Remaining blockers

1. **Windows + MT5 manual acceptance (§12 walkthrough) is PENDING** — this host has no
   Windows, no MT5 terminal and no browser access to the operator's instance. Every runtime
   claim above was verified over HTTP against the real backend; browser-visible behaviour on
   the operator's machine must be confirmed with the §L steps. V6.4 §11 / V6.5 §E manual
   checklists remain pending in the same session.
2. Node-718 history-recovery (three UNKNOWN manual trades) still requires MT5 terminal
   history on the operator's machine (V6_5_REPORT.md §E) — unchanged by this release.
3. Sizing limitations of §F (gaps/slippage/currency conversion) are documented, not modelled.

## L. User acceptance

Start the application exactly as usual on the Windows machine: `start.bat` (the launcher
rebuilds the dashboard automatically because this release changes `frontend/src`; the
`frontend_build_guard` refuses to start with a stale bundle). Open `http://localhost:8787`
(the URL you normally use). Then:

1. Top status row → **Build chip**: expand it — `release: V6.5.1`. If the chip is amber,
   the tab is running a cached shell — use its reload link (or Ctrl+F5).
2. **Live Testing** page → **Live test nodes** table. Each row now shows, in one place:
   **Risk ✎** (mode + value), **Max trades ✎**, **SL off ✎**, **TP off ✎**, **Schedule**,
   **Lifecycle** (STOPPED/STARTING/RUNNING/STOPPING/ERROR/UNKNOWN) and the **START/STOP**
   control.
3. On a safe demo node: **START** once → the button immediately shows **STARTING…**, then
   flips to **STOP** once the backend confirms (Lifecycle shows RUNNING). Refresh the page —
   the node still shows STOP/RUNNING while the backend reports it running.
4. Click the **Risk** cell → choose "% of capital" or "Constant money", enter the value
   (and equity/balance basis for %) → **Save**. The cell shows the saved value tagged
   `(node)`; refresh persists it. Esc cancels; a rejected value shows the error and never
   appears as saved.
5. Edit **Max trades**, **SL off**, **TP off** in their cells; edit **Schedule** via the
   Schedule cell (structured dialog). All belong to the clicked node only.
6. **MT5 account** panel: Balance / Equity / Free margin of the connected account, with the
   connection badge and "as of …" freshness. If MT5 is not connected the panel says
   ACCOUNT DATA UNAVAILABLE with the reason — values are N/A, never 0.
7. **STOP** the node → **STOPPING…** → **START** again (Lifecycle STOPPED). Open positions
   are never touched by START/STOP.
8. This host verified the identical chain over HTTP (§I); only the browser-visible step on
   your machine remains for you to confirm.

---

## Publication record

(see final section appended after push — content commit + fingerprint commit, gate counts,
push verification)
