# EVOLUTIONARY TRADING LAB V6.5 — IMPLEMENTATION REPORT

**Release:** V6.5 — Live Testing UX, Per-Node Trade Controls, MT5 Trade Ledger, Analytics,
and Experimental SL/TP Offsets (additive on V6.4; strategy logic untouched).
**Date:** 2026-10-10 · **Base:** V6.4 `bf25ae0011070f6ae33f34fcf44016d7598a77e1`

---

## A. Release identity

* **V6.4 base commit:** `bf25ae0011070f6ae33f34fcf44016d7598a77e1` (verified origin/main at start).
* **V6.5 commit SHA(s):** see §"Publication record" at the end of this file (content commit +
  fingerprint commit; filled after recording).
* **Final remote HEAD:** verified live after push (see publication record).
* **Product version:** `backend/app/versions.py` → `PRODUCT_RELEASE = "V6.5"`.
* **Fingerprint:** `BUILD_FINGERPRINTS.json` entry **V6.5** recorded by
  `backend/tools/build_fingerprints.py --record --release V6.5` from the final source + build.
* **Production bundle assets:** `assets/index-BeNlcShL.js`, `assets/index-DG45pVtK.css`
  (live-testing chunks `LiveTesting-BiMfRBSu.js`, `LiveTestingPanels--04DvS7m.js`).
* **Clean-clone build reproduction:** verified (see §C) — a fresh `npm ci && npm run build`
  reproduces the recorded `index_sha256` and entry assets exactly.
* **Frontend build guard:** `frontend_build_guard.py --check` → `GUARD_STATUS=FRESH`.

## B. Changes (files and purpose)

### Trade ledger & MT5 capture (§6 — highest priority)
* `backend/app/live_testing/ledger.py` (NEW) — canonical managed-trade ledger: additive
  columns on `live_test_trades` (position/order/deal tickets kept DISTINCT, entry evidence,
  default/effective SL/TP + offsets, exit evidence, costs, flags) + append-only
  `trade_ledger_events` with `event_uid` UNIQUE ⇒ **idempotent ingestion** of repeated MT5
  history polls. Evidence accretes; NULLs never erase facts; values are never fabricated.
* `backend/app/db/database.py` — additive V6.5 columns (config offsets via the existing
  idempotent `V5_CONFIG_COLUMNS` mechanism); `set/get_live_test_config` carry
  `sl_offset_pips`/`tp_offset_pips`. No destructive migration; rows preserved.
* `backend/app/mt5/mt5_real.py` + `simulator.py` + `bridge.py` — **read-only** `deal_history()`
  (narrow, additive; order/deal/position identities preserved; simulator returns [] = unknown).
  The proven execution path (order construction, filling modes, single-send) is untouched.
* `backend/app/live_testing/engine.py` —
  * `_record_result` writes the canonical ledger row from the ACTUAL broker response
    (requested vs confirmed SL/TP, retcode, broker comment, timestamps, evidence source).
  * `_reconcile` (§6.3/§6.5): exit-deal capture (SL/TP/SO/manual/UNKNOWN classification by
    broker `deal.reason` codes — unclassifiable stays `UNKNOWN_EXIT`), partial vs full close,
    realized gross/net P/L, idempotent `EXIT_DEAL` events, recovery of broker positions with
    no local record (`broker_recovered`, explanation explicitly "unavailable"), reservation
    reconciliation, and a visible `sync_state` (never falsely "SYNCED").
  * `_explain_signal` (§3.2) — read-only explanation hook at the signal boundary: the engine
    rule verbatim from the genome, the condition tree with computed values at the decision bar,
    indicator values/thresholds, `explanation_kind="engine_rule_and_feature_snapshot"`. Facts
    only; nothing reconstructed from direction or later price action; never affects the signal.

### Lifecycle control (§4)
* `backend/app/live_testing/admission.py` (NEW) — `NODE_STOPPED` admission refusal: after a
  STOP is acknowledged the node can never pass the submission gate (closes the in-flight race).
* `frontend/src/components/LiveNodeIndex.jsx` — every row shows the BACKEND worker state with
  explicit `STARTING…`/`STOPPING…` transitional labels, STOP for running nodes (per node;
  never touches positions; other nodes unaffected), disabled-with-explanation titles; state is
  re-read from the API after every action and on refresh (never flipped client-side).

### Trade limits (§5)
* `backend/app/config.py` — `max_active_trades_per_node_default = 1` (new; existing
  `max_active_trades` default preserved as 1 — migration never resets operator values).
* `backend/app/live_testing/risk.py` — `resolve_per_node_limit` (override vs inherited Default,
  always names its source).
* `backend/app/live_testing/admission.py` — enforcement at the execution boundary: global AND
  per-node caps under a process-wide lock with **reservations** (concurrent workers cannot
  jointly exceed a cap; verified reject releases; UNCERTAIN keeps its slot until broker
  reconciliation proves non-execution; restart counts derive from broker state; positions
  counted once by stable position ticket; per-node attribution by the origin magic stamp;
  unrelated magic excluded; reduced caps block new entries and never close positions).
* `backend/app/api/routes.py` — `/live-testing/settings` accepts `max_active_trades` +
  `max_active_trades_per_node_default` (integer 1..100, invalid refused); per-node config
  accepts `max_active_trades` (Default/null or explicit override) with validation;
  nodes-table rows expose `max_active_trades` / `_source` / `_effective` / `_default`.

### Statistics & results (§7)
* `backend/app/live_testing/results.py` — `per_node_live_stats` publishes the full
  position-level metric set (attempts/confirmed/rejected, wins/losses/breakeven/unclassified,
  partial closes, gross/net P/L + commission/swap/fees, win rate, avg win/loss, profit factor
  with explicit unavailable state, avg duration, drawdown, volume, last trade ts) and a
  published `counting_conventions` block. Closed-status family extended to
  `CLOSED/CLOSED_MISSING/CLOSED_VERIFIED` (a reconciliation-captured closure is closed).
  Research/backtest metrics are never touched.
* `backend/app/api/routes.py` — `/live-testing/trades` filters (node, symbol, side, status,
  date range, exit reason) + `counting_conventions`; `/live-testing/trades/{row_id}` detail
  with event history; `/live-testing/active-trades` (§8) merges broker positions with ledger
  enrichment (entry reason, default/effective SL/TP + offsets, floating P/L in money AND pips,
  SL/TP distance + ESTIMATES labelled as estimates, freshness); `/live-testing/sync` (§6.5).

### Live Testing UI (§3)
* `frontend/src/components/LiveActiveTrades.jsx` (NEW) — Active Trades section (first),
  compact expandable rows, text+icon status labels, recorded entry reasons, estimate labelling;
  `LiveTradeLimitsPanel` — global constraints with visible unsaved state.
* `frontend/src/pages/LiveTesting.jsx` — §3.4 order: Active Trades → controls/constraints →
  nodes table → collapsible Manual Demo Trade (stays mounted; form values preserved; header
  keeps the demo-only safety label) → recent events + sync warnings.
* `frontend/src/api.js` — new endpoints.

### Experimental offsets (§9)
* `backend/app/live_testing/offsets.py` (NEW) — pure, documented semantics: 0/0 = the
  strategy's EXACT levels (not even re-normalized); BUY: +SL farther below, +TP inward;
  SELL: +SL farther above, +TP inward; negatives = documented reverse; tick normalization;
  placement-side and broker stops-level validation; invalid ⇒ safe refusal with a clear code
  (never an invalid submission). Defaults/effective/offsets are recorded separately in the
  ledger (§9.3.7). Per-node isolation; nothing auto-adjusts after wins/losses; clearly marked
  experimental in the UI. No AI tuning (§10: the ledger/signal/offset evidence is stored for
  future analysis; V6.5 lets NO system change parameters autonomously).

## C. Evidence

* **Commands:** `EVOLUTIONARY_LAB_DATA_ROOT=<fresh scratch copy of the published DATA>
  .venv/bin/python -m pytest tests/ -q` (never bare pytest; authoritative DATA untouched).
* **Regression (V6.4 contract preserved):** full suite **1002 passed, 1 skipped, 1 failed**
  in the pre-record run — the single failure is
  `test_v5_2_3_one_table_and_zip_identity.py::test_12_the_published_index_describes_this_tree`,
  which by design demands the published fingerprint describe the current tree; it passes once
  the V6.5 entry is recorded (final gate below). No test was removed, weakened or skipped;
  the one skip (`test_v5_1a_coverage_and_balances.py:120`) is the pre-existing
  "no simulator dataset" skip, disclosed as before.
* **V6.5 suites:** `test_v6_5_trade_ledger` (16), `test_v6_5_trade_limits` (16),
  `test_v6_5_offsets` (13), `test_v6_5_statistics` (10), `test_v6_5_limits_api` (8) — all pass.
* **Ledger/idempotency/recovery:** duplicate history polls → one event; upsert never
  double-counts a position; multi-deal positions stay one row; missing deal history ⇒
  explicit `position_absent`, never invented exits; restart persistence verified; persistence
  failure reported, not raised into the engine.
* **Calendar determinism (disclosed):** the product default schedule is Mon–Fri, so
  schedule/flow tests are flaky on a weekend run through no code defect (they were green on
  the Friday V6.4 gate). `tests/conftest.py` pins ONLY the schedule/demo-schedule/market-panel
  clocks to the nearest weekday (same time of day); all assertions are unchanged. Two tests
  were made clock-coherent (test_19 "today" derives from the same pin; test_25 reads the same
  database the endpoint uses). Root-cause fixes in product code from this gate run:
  `per_node_live_stats` latent `NameError` on open rows (previously untriggered), a duplicate
  `/live-testing/reconcile` route registration, `resolve_per_node_limit` import in the engine,
  and `record_event` exception containment.
* **Build & fingerprint:** `npm ci && npm run build` (deterministic), guard `FRESH`;
  clean-clone (`git clone`, fresh `npm ci`) build reproduces `index_sha256` and
  `entry_assets` exactly (bit-for-bit, verified); `--match` = V6.5.
* **DATA preservation:** authoritative DATA sha256 unchanged all session
  (`c159b0718c43b43f97609881bd3be196dad76274029949a9fb4c4e710f106a12` for the restored
  published copy); `git diff` for `DATA/` empty; tests ran only against scratch copies.
* **Final gate on the published tree:** see publication record (run after fingerprint record).

## D. Honest limitations

* **Windows/MT5 demo acceptance (§13) is NOT completed** — it cannot be performed from this
  Linux host. Every item in §13 is **PENDING MANUAL VERIFICATION** on the operator's
  Windows/MT5 demo account. No real broker verification was performed (expected: no).
* **Node 718's three observed trades:** local DATA holds no live-test trade rows for them;
  the only local traces are three `mt5_manual_orders` rows (XAUUSD BUY 0.03/0.05/0.05) stored
  as `UNKNOWN` by the pre-V6.4 result parser (their order/deal tickets exist only inside the
  stored raw message text). Their exits are NOT retrievable from this host. V6.5's
  reconciliation will complete/attribute them only against live MT5 history on the operator's
  machine (procedure in §E) — nothing is fabricated in their place.
* **MT5 fields unavailable at record time** stay NULL with `evidence_source`
  (e.g. `broker_recovered` rows have no signal explanation; a vanished position without
  retrievable deals keeps `close_kind=UNKNOWN` and `evidence_source=position_absent`).
* **Unresolved trade-history discrepancies** are recorded (reconcile flags / events), never
  silently overwritten.
* **Magic attribution caveat:** per-node ownership uses the existing magic stamp
  (`778000 + sid % 900`, unchanged); two node ids congruent mod 900 share a stamp — the ledger
  comment/`client_order_id` correlate exactly, but cross-node position-count separation for
  such pairs relies on that correlation.
* **Floating P/L snapshots** are sampled at the UI poll (15 s) / meaningful events, not
  written to disk per render (§6.4).
* Unavoidable skips: only the pre-existing "no simulator dataset" skip (1), disclosed.

## E. Manual operator checklist (Windows / MT5 demo — REQUIRED before trusting live claims)

1. `git pull` (or fresh ZIP) → `pre-requisite.bat` → `start.bat`; confirm the build guard
   reports FRESH and the footer build chip shows V6.5 with assets `index-BeNlcShL.js`.
2. Open LIVE TESTING: Active Trades is the first section; Manual Demo Trade collapses/expands
   without losing form values; the global constraints panel shows MAX ACTIVE TRADES and
   DEFAULT MAX ACTIVE TRADES PER NODE (start with 10 / 1).
3. Start node 718 → its row shows STARTING… → STOP (backend-confirmed); stop it individually;
   verify other nodes keep running and its open positions are untouched.
4. Let node 718 place a demo trade: verify Active Trades shows real order/deal/position
   tickets, entry price/time, SL/TP, floating P/L in money + pips, and the recorded entry
   reason (rule + indicator values). Verify the ledger row appears in
   Live Testing Results / node statistics after refresh and after an app restart.
5. Close a demo position (or wait): verify the exit deal, exit price/time, reason
   (`SL_EXIT`/`TP_EXIT`/`MANUAL_OR_EXPERT_CLOSE`/`UNKNOWN_EXIT`) and realized gross/net P/L
   persist (`/api/live-testing/trades` + detail view).
6. Limits: with two nodes running and global max 10 / per-node default 1, verify each node
   stops at ONE active position; set node 718's override to 2 and verify only 718 gets a
   second; verify the global cap blocks further entries with the documented block reason.
7. Offsets (demo, small size): set node 718 SL offset +10 pips; verify the request/default/
   effective/broker-confirmed SL values are all recorded separately and 0/0 nodes keep the
   exact strategy levels.
8. **Node 718 history recovery:** with MT5 connected, POST `/api/live-testing/reconcile` (or
   wait for the periodic pass) — any broker position with lab magic and no local record is
   backfilled with `evidence_source=broker_recovered` and the discrepancy is visible. The
   three historical manual-path trades (magic 777000) remain in `mt5_manual_orders` as
   UNKNOWN with their raw evidence; complete them only from the terminal's own history.
9. Confirm `/api/live-testing/sync` shows a truthful `certainty` state after a
   disconnect/reconnect cycle (never a green SYNCED over failed history retrieval).
10. Confirm no unrelated MT5 positions are counted, modified or closed.

---

## Publication record

(see GIT HASH block below — filled at publication)
