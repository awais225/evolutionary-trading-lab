# V6.7 — Release Report (2026-10-11)

Focused corrective + additive release on top of V6.6 (`5e42c270`): one
authoritative inventory pipeline with real RECHECK reconciliation, reliable
MT5 history synchronisation, a complete DeepTesting node-selection / scenario /
scheduling / execution workflow with persistent DEEPTESTED NODES + INSPECT
analysis (including interactive day/session exclusion filters), and the
cache-freshness / ranking-scope corrections found while verifying the data
pipeline against both stored database states.

## A. Release commit chain (branch `main`, in order)

| # | sha | purpose |
|---|-----|---------|
| 1 | `8da908a587a1b37ab3a15f5490c4987954941d19` | V6.7 content: inventory pipeline + RECHECK, MT5 sync hardening, DeepTesting workflow (scenarios / filters / DEEPTESTED / INSPECT / Overview DeepTested tab), cache-freshness + ranking-scope fixes, V6.7 acceptance suite |
| 2 | (this commit) | docs: V6.7 publication record (`V6_7_REPORT.md` + `BUILD_FINGERPRINTS.json`, recorded `--release V6.7 --commit 8da908a`) |

Production build fingerprint (recorded at the content commit):

- `SRC_HASH=e14e7c6a43ce41a031226f6439603e5825a6ea0b2c833af77f2a8f271403244a`
- `CODE_HASH=7a4e4e3d9389704ef5f20dfde3d1de74ad1dfb9502458269fd910331d84b6a13`
- `INDEX_SHA256=1510c2c604b68250c290fff11fb746a8757153b006092814d237b8a59848996c`
- entry assets `assets/index-DekxfF9f.js` / `assets/index-DG45pVtK.css`
- `build_fingerprints.py --match` identifies this tree as **V6.7 (8da908a)**.

Push status: `origin/main` verified equal to the publication-record commit at
push time (see chat evidence / `git ls-remote` check in the release message).
Zero `DATA/` paths in any commit.

## B. Regression gate (fresh canonical DATA, database sha `c159b0718c43b43f…`)

Established resource-safe procedure (same as the V6.6 gate: “fresh scratch DATA
copies” — `V6_6_REPORT.md` §B): 63 test files split 32 + 31; each half run in
its own pytest process against a byte-identical restore of the canonical
database snapshot (sha verified before every run).

| run | files | result |
|-----|-------|--------|
| half 1 (repo order, first 32) | 32 | **639 passed, 1 skipped, 0 failed** (158 s) |
| half 2 (remaining 31, incl. V6.7 suite) | 31 | **476 passed, 0 skipped, 0 failed** (146 s) |
| focused V6.7 acceptance suite | 1 | **23 passed, 0 failed** |
| previously failing node-config cluster (`test_v6_5_1_lifecycle`, `test_v6_5_1_nodes_config_api`, `test_v6_5_1_risk_modes`) | 3 | **31 passed, 0 failed** (also green inside both halves) |

**Totals: 1115 passed, 1 skipped, 0 failed** (V6.6 published 1092 passed /
1 skipped; the +23 delta is exactly the new V6.7 acceptance file). The single
skip is the same environment-gated skip as the V6.6 gate:
`test_v5_1a_coverage_and_balances.py:120 — “this installation carries no
simulator dataset”`. No test was skipped or weakened to hide a failure.

### B.1 Root cause of the 8 earlier failures (live-node config 422s) — shared
test-state pollution, not a code regression; evidence chain (all reproduced
this session):

1. **Canonical DB** (sha `c159b071…`: 10 000 rows, ids 788–10787, zero legacy
   rows) → `test_v6_5_1_*` = **31/31 pass**, including the all-None reset POST
   to `/api/live-testing/nodes/{sid}/config` (HTTP 200).
2. **`tests/test_v4_7_reliability.py`** (startup-readiness tests boot the real
   app) triggers the app’s bounded bootstrap step `data_restoration`
   (`ResearchRestorationEngine.restore_all`), which re-imports preserved
   historical research runs from `DATA/RESEARCH` + `DATA/BACKUPS`
   (RUN-HISTORICAL-PRESERVED 350, RUN-20260927-CLEAN-MT5 360,
   RUN-20261005-055251, RUN-TEST-A-NEW2, RUN-TEST-ENTROPY, RUN-V41-* fixture
   runs — 809 rows carrying their original ids 1–787). Reproduced file-in-
   isolation: DB goes 10 000 → 10 809 rows, min id 788 → 1.
3. In any **later process**, `Database.__init__`’s V3.6 legacy classifier
   re-labels those rows `LEGACY_TEST` (id ≤ 787 / run_id LIKE '%TEST%' /
   RUN-HISTORICAL-PRESERVED). `/api/nodes?filter=all` (node-id ascending) then
   returns node id=1 first; `test_v6_5_1_*::_node_ids` selects it; and
   `/api/live-testing/nodes/1/config` correctly refuses with
   **422 `LEGACY_NODE_NOT_TRADEABLE`** — for every payload, including the
   all-None reset.
4. **The reset/partial-update contract itself is correct** (verified by code
   read + 31/31 green on canonical): an explicit `null` clears the field, an
   absent key keeps the stored value, invalid values are refused with typed
   codes. The422 was the legacy-node guard, never a reset bug.
5. My patch is not implicated: `git diff` shows no change to the live-config
   endpoint or its validation; the A/B above flips purely with DB state.

Procedure consequence (matches the V6.6 gate’s “fresh scratch DATA copies”):
because `test_v4_7_reliability` legitimately exercises restoration against the
shared app DB, every regression half must start from the canonical snapshot —
which is what the gate above did. The canonical database was restored
byte-identically (sha verified) after every run and at release time.

## C. Root causes and product fixes (V6.7 scope)

1. **Competing “qualified” formulas / stale counts** — one authoritative
   inventory (`inventory_snapshot`: bucket-classifier counts, raw
   `counts_by_status` histogram, display scope `counts_excl_legacy`, identity
   map, full-dataset tops) with a content-derived `inventory_revision` and a
   cheap `state_token`; `GET /api/inventory/snapshot` refuses to serve a cached
   snapshot whose `state_token` no longer matches the stored dataset (found by
   the new suite: a restored-database state change exposed the stale-cache
   path). `POST /api/lab/recheck` computes + commits one atomic snapshot and
   returns inventory + revision + timestamp + duration + deterministic flag +
   errors. `/api/status` carries additive `inventory_revision` /
   `qualified_authoritative`; legacy keys untouched. Overview’s reconciliation
   strip shows the authoritative count with every competing source labeled.
2. **Top-return ranking / node table inconsistency** — `/api/population` now
   supports `sort_desc` and full-dataset metric sorts (return/PF/DD/WR/trades/
   net profit, missing-last, canonical-id tie-break); inventory `tops` rank the
   same universe `/api/population` ranks (display scope), so one ranking can
   never disagree with the table the operator sees.
3. **Node identity (the #718 question)** — canonical id vs research number are
   both shown with explicit labels (`Node #<id>` / `research #<num>`), search
   matches both namespaces, and displayed generation metrics equal the stored
   record exactly (see §D.3). Never hardcoded.
4. **MT5 history sync** — `sync_master` rewritten: bounded chunked
   `copy_rates_range` walk (15 000 bars/request), bounded retries, wall-clock
   deadline, real per-chunk progress, per-(symbol,tf) concurrency dedupe,
   cancellable (`cancel_sync`, wired into the GET-MT5-DATA fetch job), always
   terminal states **COMPLETE / PARTIAL / FAILED (no-data) / CANCELLED**, with
   an explicit requested-vs-actual coverage report (ranges, bars, chunks,
   reason). The old stall (“requesting recent 259205 bars” single unbounded
   `copy_rates`) and the fake fixed 10/20/35/… progress are gone.
5. **DeepTesting workflow** — effective scenario signature (node + genome +
   config + symbol/TF + requested range + settings + node settings + dataset
   fingerprint + engine version): identical reruns reuse the existing valid
   scenario (“Already DeepTested — see Scenario N” + INSPECT), a material
   change creates a new scenario; the SCENARIO button opens the shared
   scheduling presentation saved to **DeepTesting-only** storage
   (`deeptest_node_configs`, additive table + `signature` column) — live
   scheduling/risk/execution settings are never mutated (asserted).
6. **INSPECT** — summary stats (incl. fitness/complexity/species/mutation/
   fingerprints/cost assumptions + scenario id + requested/actual ranges),
   monthly PnL and exit reasons reconciled from saved trades, scrollable
   sticky-header trade list with sorting/filtering.
7. **Performance exclusion filters (Mon–Sun + Asia/London/NY/off-session)** —
   OR-style independent dimensions, immediate recalculation from the persisted
   trades (same accounting), original-vs-filtered comparison, reset + active
   indication + disclosure (“post-trade exclusion analysis — NOT a
   re-simulation”); never creates a scenario or alters trades/live (asserted).
8. **DEEPTESTED NODES table + Overview DeepTested section** — one row per node
   (generation metrics and DeepTest metrics side by side, never mixed),
   sortable/searchable/scrollable with empty state; the node-detail DeepTested
   tab shows NOT DEEPTESTED or the preferred scenario’s status/period/actual
   range/coverage/metrics with an INSPECT action, from the same
   statistics-priority projection the DeepTesting tab uses.
9. **Corrective fixes found during verification** — `resume_add_nodes` target
   arithmetic now derives from the preserved population count it reports (the
   earlier run-scoped/global mix failed whenever a second user-research run
   existed); config DATA-path resolution preserves path identity through
   layout symlinks (provenance records exactly the path the dataset is stored
   under — `test_v4_6_mt5_historical::test_provenance…` in any symlinked
   layout).

## D. Acceptance audit (15/15 probe checks + suite evidence)

1. **First dashboard load and RECHECK share one authoritative inventory** —
   `/api/status.inventory_revision` == `/api/inventory/snapshot.inventory_revision`;
   two RECHECKs return the same revision + result + timestamp + `errors: []`.
2. **Qualified count and top-return ranking are derived, not hardcoded** —
   `qualified_authoritative` == `counts_excl_legacy.qualified`; top-return ids
   from `/api/population` equal the inventory `tops` ranking over the full
   dataset (restored-state probe also proves tops and the table share one
   universe). The “99 qualified” observation is explained, not asserted: on the
   historically-complete (restored) database state the raw stored-status
   histogram `counts_by_status["QUALIFIED"]` is **99** (the number the V6.6
   Overview summed), while the single bucket-classifier rule counts across all
   runs — every source is labeled in the RECHECK strip and none is hardcoded
   (on the canonical snapshot classifier == raw == 83).
3. **Node #718** — resolved against the stored record: canonical **Node #718**
   (RUN-HISTORICAL-PRESERVED, research #318, symbol XAUUSD, **timeframe M1**,
   stored status SURVIVED, `data_source=LEGACY_TEST` → live-excluded) carries
   exactly the operator’s generation values in its detail record:
   return **1.481 (148.100%)**, **PF 2.545**, **DD 0.0405 (4.050%)**,
   **WR 0.7724 (77.240%)**, **905 trades** (Sharpe 35.181, expectancy 0.001636,
   avg hold 12.9 bars). The UI displays them because the record holds them —
   the acceptance test asserts **displayed == stored** (exact fractions;
   display rule = ×100 at 3 decimals) and that “718” finds the node despite its
   exclusion status, with both identity labels explicit. (Research #718 is a
   different node — canonical #1505 — and its own record is displayed for it:
   9.170% / 57 trades / PF 1.84.) **Zero M1 bars** is the expected consequence:
   gen provenance ≠ MT5 cache — `DATA/MT5/XAUUSD/M1.parquet` does not exist,
   and nothing fabricates candles; the DeepTesting flow offers GET MT5 DATA /
   CSV import for real data.
4. **Generation vs DeepTest metrics stay separate** — `generation_metrics`
   (generation record only) and `deeptest` (preferred DeepTest) are distinct
   row fields with distinct sort keys (`generation_*` / `deeptest_*`); the
   statistics-priority projection is labeled; generation values are never
   overwritten.
5. **MT5 sync terminates and distinguishes COMPLETE / PARTIAL / FAILED /
   CANCELLED** — asserted with instrumented bridges (bounded chunk sizes, no
   unbounded fallback; empty bridge ⇒ terminal `no_data_from_bridge`; short
   range ⇒ PARTIAL with reason, never COMPLETE; cancel ⇒ CANCELLED; concurrent
   callers dedupe; incremental resume requests only the missing tail).
   Verification basis: simulator/fake-bridge tests in this Linux environment —
   a real MT5 terminal was **not** available here (see §E).
6. **Identical DeepTests reuse the same valid scenario** — signature
   determinism + reuse (no new row, engine not rerun, failed/cancelled never
   qualify) asserted; material change (range/settings/data/version) ⇒ new
   identity.
7. **Scenario button** — opens the scheduling/configuration popup and persists
   to `deeptest_node_configs`; live `live_test_configs` verified unchanged.
8. **Filtered metrics / monthly PnL / exit reasons / trades reconcile** —
   3-trade fixture: exclude Sat+off keeps 2 (PnL 80.0), exit reasons TP 2×80 /
   SL 1×−20, monthly reconciles with trades; all-enabled == original; the
   scenario and its trades are byte-identical after filtering.
9. **Overview DeepTested section** — reads the same preferred-scenario
   projection as the DeepTesting tab (status/period/actual range/coverage/
   metrics + open INSPECT; scenarios never mixed). Verified by component code
   + API contract tests; interactive browser click-through not performed (see
   §E).
10. **Frontend serves the current production build** — production build green
    (`vite` 5.7 s), `test_frontend_serving` in half 1, TestClient audit shows
    the dashboard 200 referencing `assets/index-DekxfF9f.js`, and the
    fingerprint entry names those assets.

## E. Verification matrix — performed vs not performed

| check | status |
|-------|--------|
| full regression gate (63 files, 32+31 split, canonical DB each) | **performed** — 1115 passed / 1 skipped / 0 failed |
| V6.7 acceptance suite (23 incl. Node-#718 displayed==stored) | **performed** |
| inventory/RECHECK/count/ranking audit probes | **performed** (15/15) |
| production build + `BUILD_FINGERPRINTS.json` + `--match` V6.7 (8da908a) | **performed** |
| MT5 sync state machine | **performed** against simulator/instrumented bridges (explicitly reported SIMULATOR mode) |
| real MetaTrader5 terminal / broker connectivity / order path | **NOT performed in this environment** (no MT5 on Linux) — operator-side: V6.5 §13 Windows MT5 demo acceptance + V6.4 §11 checklist remain on the operator machine |
| interactive browser acceptance (click-through of DeepTesting UI) | **NOT performed** (no browser runtime in this sandbox) — covered by production build, frontend render-smoke suites (`test_v4_7_frontend`, `test_v4_8_frontend`, `test_v5_1a_next_ui_contract`, theme suite) and API audit probes; any UI wording/layout doubt should be confirmed in the operator’s browser |

## F. Data preservation

- The canonical LFS database (sha `c159b0718c43b43f…`) is byte-identical at
  release time (verified after every gate run; the restore-ritual script
  snapshots and restores it — test runs are the only mutators).
- No destructive resets; no `DATA/` path in any commit; additive schema only
  (`deeptest_node_configs`, `deeptest_scenarios.signature`, additive
  `/api/status` keys); LIVE TESTING, live execution, orders, node scheduling,
  risk controls and MT5 connectivity code paths untouched.

## G. Known suite characteristic (for future releases)

`tests/test_v4_7_reliability.py` legitimately exercises the app’s
`data_restoration` bootstrap step against the shared app database; in any
**subsequent** process the V3.6 legacy classifier re-labels the imported
historical rows and `test_v6_5_1_*` node selection then hits the legacy-node
guard. The V6.6 gate avoided this with fresh scratch DATA copies; V6.7 does the
same (documented above). A future hardening could scope that file’s restoration
exercise to a scratch DATA root; deliberately out of scope here (no historical
test changes in a corrective release).
