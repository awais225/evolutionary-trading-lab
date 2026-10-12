# V7.0 — Release Report (2026-10-12)

Focused corrective release on V6.9 (`b97e5df3`): MT5 historical fetching
retrieves the **selected duration range**, the DeepTesting Inspect area is a
first-class themed dashboard surface, and the LIVE TESTING Manual Trading
Panel is restored to its proven V6.4 position and bridge. Root causes and
pre-change measurements: `V7_0_RECONCILIATION.md`.

## A. Commit chain (branch `main`, in order)

| # | sha | purpose |
|---|-----|---------|
| 1 | `5a5c0869d8781a79d893338c895f1352ced192ae` | V7.0 content (11 files, +965/−145): requested-range fetching end to end, Inspect theming + interactions, Manual Trading Panel restoration, 21 new tests |
| 2 | (this commit) | docs: V7.0 publication record (`V7_0_RECONCILIATION.md` + this report + `BUILD_FINGERPRINTS.json`, recorded `--release V7.0 --commit 5a5c086`) |

Production build fingerprint (recorded at `5a5c086`):

- `SRC_HASH=a831f231812796d5ead9ff1caa37318da0e3b3e1df58d0c1d2db7d66c899a533`
- `CODE_HASH=660ed848af4f31444d6369d1cb6f1ab2594d759da1e1ff6ed7187c99793c6349`
- `build_fingerprints.py --match` identifies the tree as **V7.0 (5a5c086)**.

## B. §2 — range fetching: requested range vs actual retrieval (evidence)

**Root cause** (traced end to end): the selected duration never left the
frontend (`onFetch` posted only `{symbol, timeframe}`), the route discarded
it, `fetch_job._run` ignored its own `period` field, and `sync_master` derived
its window from `CONFIG.settings.json history_months {"M15": 1}` — one month
≈ 2.1–2.9k bars ≈ the user's “2,345 bars” for EVERY duration button. The
bridge itself (`mt5_real.copy_rates_range`) passes start/end faithfully.

**Fix**: the duration (or explicit UTC bounds) travels
UI → route → job → `sync_master(request_start, request_end)`; the goal window
is fetched as genuinely-missing intervals (older HEAD + newer tail) in the
bounded chunk walker; reuse shortcuts require the store to cover the goal;
cache identity is coverage-based (per symbol/TF/profile with timestamp
dedupe); `fetch_report` carries requested/received/persisted ranges + named
uncovered portions; COMPLETE only when persisted bars cover the request.
Legacy no-request sync stays strictly incremental (V6.7 contract preserved).

**Measured evidence** (mocked terminal holding two years of weekday M15 bars
— `tests/test_v7_0_range_fetch.py`, 11/11):

| request | requested window | retrieved | status |
|---|---|---|---|
| 1D / 7D / 1M / 6M / 1Y / 2Y / 3Y | 7 DISTINCT strictly-increasing windows, each `requested_start/end` == the documented `resolve_period` UTC bounds | — | — |
| **2Y** | 2-year window | **≈50k bars (the full two-year history) — not 2,345** | COMPLETE |
| **3Y vs 2y fixture** | 3-year window | the available 2 years | **PARTIAL** with the uncovered portion named by real timestamps |
| 1M + re-run | 1-month | 1-month; second run adds 0 (dedupe) | COMPLETE |
| 1-month cache + 2Y request | 2-year | head gap fetched; store grows past 20k bars | COMPLETE (no false cache hit) |
| M15 vs **H1** | same 2Y semantics | ~12k H1 bars vs ~50k M15 | COMPLETE (not hardcoded to M15) |
| failing terminal | any | 0 bars | `no_data_from_bridge`, never success |

Returned vs persisted counts agree (unique timestamps), earliest/latest
persisted timestamps recorded, and the frontend refreshes node-table
coverage (`loadNodes` + `loadCoverage`) when the job reaches a terminal
state — asserted by test.

## C. §3 — Inspect panel (evidence)

Statistics now render as themed **Kpi statistic cards** on the existing
design system (Return %, Net P&L, PF, Max DD, WR, Trades, Avg trade,
Expectancy, Sharpe/Sortino with unavailable-when-invalid labels, bars tested,
status + scenario id), each labelled with its source; the trade table is the
shared `tbl` design with full-set sorting (entry/exit/P&L/hold/side/exit
reason/session, direction indicators, missing-last, trade_no tie-break) and
formatted cells; exclusion filters render as themed chips with unmistakable
selected state. The jsdom smoke now MOUNTS THE REAL INSPECT INTERACTIONS:
stat cards render, the P&L sort flips `aria-sort`, unchecking Thursday
recomputes (FILTERED badge + kept-count + changed numbers from the posted
exclusions), Reset restores the originals — **61/61 renders**.

## D. §4 — Manual Trading Panel restoration (evidence)

**Root cause**: `1e47735d..HEAD` moved the panel below the nodes table and
wrapped it in a collapsed-by-default toggle (“Manual Demo Trade … collapsed”)
— invisible on the operator's screen. `backend/app/mt5/execution.py` is
byte-identical to V6.4; the bridge (`/mt5-execution/state|preview|validate|
place` → `place_demo_order` → `send_market_order`) is intact.

**Restored**: “Action controls — Manual Trading Panel” at the V6.4 position
(before the node table), **open by default** (collapse retained, never resets
the form), on the identical request path (`mt5ExecutionPreview` →
`mt5ExecutionPlace` with the V6.4 payload: symbol/side/volume/sl/tp/confirm/
client_order_id).

**Mocked-bridge tests** (`tests/test_v7_0_manual_trading.py`, 10/10; no real
order is ever sent): result reporting (ticket 555001 surfaced), broker
rejection never shown as success, disconnected terminal refused clearly,
repeated submission refused (client_order_id idempotency — exactly one send),
the SL safeguard enforced on the risk-sizing path, and research/DeepTest rows
byte-identical before/after a successful place. The pre-existing V4.2 suite
(account safety, validation matrix, fill path) passes unchanged; a static
guard test asserts zero drift in execution/bridge/live/qualification modules.

## E. Regression gate (canonical DATA sha `c159b0718c43b43f…` verified per batch)

| run | result |
|---|---|
| half 1 (34 files, one process) | **692 passed, 1 skipped, 0 failed** (3:46) |
| half 2 batch a (16 files) | **226 passed, 0 failed** (0:43) |
| half 2 batch b (17 files) | **258 passed, 0 failed** (2:12) |
| launcher identity + frontend suites | **37 passed** |
| jsdom render smoke incl. Inspect/Manual interactions | **61/61** |

**Totals: 1 176 passed, 1 skipped, 0 failed** (67 files; 1 155 from V6.9 + 21
new). The one skip is the same environment-gated skip as V6.6–V6.9. Half 2
runs as two batches because the single-process run was **OOM-killed by the
sandbox cgroup** (kernel log: `Out of memory: Killed process … python,
anon-rss:754MB`; machine RAM 1.9 GB) — the two incomplete attempts are NOT
counted; each batch ran to a complete summary on its own fresh canonical
database (the v6_5_1 cluster stays in one process). One pre-existing
timing-sensitive test (`test_v5_2_deep_testing_data::test_15`) was made
deterministic by making its own engine double hold the running window —
assertion unchanged.

## F. Verification matrix — performed vs not performed

| check | status |
|---|---|
| full regression gate (complete summaries per batch) | **performed** — 1 176 / 1 / 0 |
| range-fetch evidence table (§B) | **performed** (mocked terminal) |
| Inspect interactions in the production bundle path (jsdom) | **performed** (61/61) |
| manual trading mocked flows (10 tests) + V4.2 bridge suite | **performed** |
| production build + fingerprints + launcher identity (13/13) | **performed** |
| real MT5 terminal / real order placement | **NOT performed** (no MT5 on this Linux build host) — mocked/simulator only; see §G for the operator procedure |
| interactive Windows browser click-through | **NOT performed** (no browser in this sandbox) |

## G. Operator acceptance (Windows + real MT5) — manual, never automated

1. **Range fetch**: open DeepTesting → pick an M15 node → select **2Y** →
   GET MT5 DATA. Expect: the fetch report names the 2-year requested range,
   progress climbs in bounded chunks, and the node-table coverage shows the
   persisted bar count (tens of thousands for 2Y M15, not ~2.3k) with
   first/last timestamps. Selecting **3Y** against limited terminal history
   must offer the PARTIAL-coverage confirmation (proceed/cancel/CSV) naming
   the uncovered span.
2. **Manual Trading Panel**: LIVE TESTING → “Action controls — Manual Trading
   Panel” is visible without expanding. Enter risk $10, SL 300 pips — the
   preview shows the backend-sized lot; BUY → confirm dialog shows account
   login/type → the result reports the terminal's own order ticket/fill or its
   rejection. Repeat-submitting the same form must not create a second order.
3. **Inspect**: DeepTesting → INSPECT on a completed scenario — themed stat
   cards; click P&L to sort; uncheck Thursday — the stats/monthly/exits/trade
   list change and are marked FILTERED; Reset restores.

## H. Data preservation

Canonical LFS database (`c159b0718c43b43f…`) restored byte-identically after
every batch and at release (verified); zero `DATA/` paths committed; no
secrets in code, tests, logs or commits; V6.9 canonical identity/qualification/
schedule/metric-layer behaviour untouched (regression suites green; static
guard test enforces no drift in frozen modules).
