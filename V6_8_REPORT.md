# V6.8 — Release Report (2026-10-12)

Freeze node-data authority · restore cross-tab consistency · DeepTesting theme
· scenario comparison · reactive performance filters · data coverage refresh ·
sorting. Baseline: published V6.7 `7bc17283` (worktree verified byte-identical
before any change; the two V6.7 files lost from the local snapshot were
restored from `origin/main` first — see `V6_8_RECONCILIATION.md` §1).

## A. Commit chain (branch `main`, in order)

| # | sha | purpose |
|---|-----|---------|
| 1 | `c391c4af6fbd7782b4256ba20347a50ccf812153` | V6.8 content: node-data authority + consistency, DeepTesting theme/scenarios/compare, reactive filters, coverage refresh, sorting (9 files, +1410/−175) |
| 2 | `d2e1c18fe1008753b3f66c0ccedb0df7d6331fc5` | V6.8: name the release (`PRODUCT_RELEASE` V6.8) |
| 3 | (this commit) | docs: V6.8 publication record (`V6_8_RECONCILIATION.md` + this report + `BUILD_FINGERPRINTS.json`, recorded `--release V6.8 --commit d2e1c18`) |

Production build fingerprint (recorded at `d2e1c18`):

- `SRC_HASH=49c7486ae44e54120ed5d813c84e45cadb775c743e56b3bc34c968f17cadcc22`
- `CODE_HASH=f58935d2aa7bb544c8fc07ff6da8de008272f70c0fa03b33a86e1000340a8384`
- `build_fingerprints.py --match` identifies the tree as **V6.8 (d2e1c18)**.
- Entry assets served on first load: `assets/index-B-kEpM3_.js` (production
  `vite` build, stale-frontend protection unchanged).

## B. Root causes (full measurement in `V6_8_RECONCILIATION.md`)

1. **V6.7 replaced the established qualified-count authority** (`8da908a5`):
   the Overview headline switched from the frozen `status QUALIFIED+PAPER`
   formula (the `get_qualified_strategies` / run-history / manifest ledger
   semantics — the user's anchored 99 on the live data) to V6.7's new
   `inventory_snapshot` bucket count (5 198 on the same data). Fixed by
   freezing a single node-data authority (`app.research.node_data`) built ONLY
   from existing formulas; the V6.7 inventory is demoted to a consistency
   verifier and never a headline.
2. **Boot-time legacy classification made the record set session-unstable**:
   the V3.6 `id ≤ 787 …` heuristic re-tags the V3.2-restored historical
   research records (real nodes, e.g. Node #718) as `LEGACY_TEST` after the
   first database open — counts jumped 99→88 between sessions and Node #718
   vanished from every legacy-hiding list and from DeepTesting's default
   search. The classification rule itself is FROZEN (untouched); V6.8 fixes
   the consequence: identity search now spans the complete stored research
   history from any filter (each result shows its own status label), and every
   ranking/counter names its scope explicitly.
3. **Node #718 "top-return" regression**: V6.7's ranking sources changed scope
   mid-release (inventory tops flipped from all-records to non-legacy). Under
   the stored records Node #718 IS the highest-return node of the complete
   research history (1.481 = 148.100%); V6.8's full-history ranking shows its
   true place consistently (verified at API + test level).

## C. What changed (files)

- `backend/app/research/node_data.py` (new) — the authority: stored rows,
  frozen QUALIFIED+PAPER tier, `reconstruct_state` semantics, `node_bucket`
  labels, best-generation-record metrics, full-history tops, one `revision`
  per response. `counts_current_experiment` kept as a labelled secondary.
- `backend/app/api/routes.py` — `GET /api/node-data`; RECHECK returns the same
  authority (same algorithm as first load); `/api/status` carries a cheap
  revision stamp; `/api/population` gains `include_legacy` (established V4.5
  pattern) + scope/revision stamps; `nodes_index` identity search spans the
  full research history + revision stamp. All existing keys/behaviours intact.
- `backend/app/deeptest/scenarios.py` — per-scenario compact list on
  `node_deeptest_summary` (§5.1); `compare` extended: per-side metrics with
  deltas/unavailable reasons, monthly PnL + exit-reason blocks from each
  scenario's OWN trades, explicit non-equivalence warnings (§5.2).
- `backend/app/deeptest/coverage.py` — `pair_coverage(range_start, range_end)`
  computes REAL `bars_in_range` from persisted timestamps (§7.1; unavailable =
  `None`, never an estimate).
- `frontend/src/pages/Overview.jsx` — headline counts from the authority
  (99-class established formula), labelled scopes, RECHECK refreshes the same
  source, top-strategies table ranks the complete research history (Node #718
  top-return), authority strip (revision/timestamp/errors/RECHECK).
- `frontend/src/pages/DeepBacktest.jsx` — full theme pass on the design system
  (panels/tbl/btn/pill/pos/neg/muted/input); visible sortable headers on all
  required columns (numeric, missing-last, id tie-break, full dataset);
  persisted-bar + real range-bar coverage columns refreshed after every sync
  (all rows sharing a symbol/TF, no page reload, sort preserved); identity
  search hint; DEEPTESTED rows expand to per-scenario actions (Inspect S1..N,
  two-scenario Compare).
- `frontend/src/components/DeepScenarioPanel.jsx` — performance filters drive
  EVERY displayed statistic (filtered recomputation shown in metrics, monthly,
  exits, trade list + counts; OR-exclusions; per-scenario filter state restored
  on switch; latest-request-wins; reset restores the originals; zero-trade safe
  state); comparison view (own metrics each, deltas, warnings, per-side
  INSPECT); theme pass.
- `frontend/src/api.js` — `nodeData`, coverage range, compare plumbing.
- `tests/test_v6_8_authority_and_workflow.py` (new) — 25 acceptance tests
  (authority consistency + frozen semantics + Node-#718 contract + scenario
  compare/immutability + numeric filter recalculation incl. Thursday-exclusion
  and drawdown-from-filtered-equity + real range coverage + sorting + theme).

## D. Regression gate (established resource-safe procedure; canonical DATA sha
`c159b0718c43b43f…` verified before each half; 64 files = 32 + 32)

| run | result |
|---|---|
| half 1 (final, after release naming + fingerprint record) | **639 passed, 1 skipped, 0 failed** (4:15) |
| half 2 (final) | **501 passed, 0 skipped, 0 failed** (4:13) |
| V6.8 acceptance suite (in half 2; also run standalone) | **25 passed** |
| V6.7 suite (in half 2; also run standalone) | **23 passed** |
| V4.7/V4.8 frontend render smokes | **33 passed** (jsdom render smoke 56/56 pages) |

**Totals: 1140 passed, 1 skipped, 0 failed.** The one skip is the same
environment-gated skip as V6.6/V6.7 (`test_v5_1a_coverage_and_balances.py:120
— "this installation carries no simulator dataset"`).

Known flake disclosed: `test_v5_1a_live_test_order_path` showed order-dependent
failures in 2 of 4 development runs of half 1 (different tests each time —
`BLOCKED` instead of the expected terminal states; the same family the V6.6
report documented for `test_02`). The file passes standalone (11/11), the live
cluster passes together (107/107), and the final half-1 gate run is fully
green. No live-execution code was changed.

## E. Acceptance criteria (§12)

1. **Import/qualification/generation logic unchanged** — verified by
   `test_frozen_qualification_semantics_unchanged` (SURVIVED→VALID bucket but
   outside the QUALIFIED tier; QUALIFIED inside both — exactly as frozen) and
   by zero diffs in `status.py`, `database.py`, `engine.py` (evolution),
   `live_testing/*`.
2. **One authority across tabs** — `/api/node-data` is the source; qualified
   count identical across the authority, `get_qualified_strategies()` and the
   per-run counters; `nodes_index` totals equal the authority's record count;
   one revision per response.
3. **Node #718** — measured on the live data: displayed == stored exactly
   (148.100% / PF 2.545 / 4.050% / 77.240% / 905 trades, XAUUSD M1);
   discoverable by identity search under the DEFAULT filter and all filters
   with both labels ("Node #718" and its own "research #318"); ranked #1 by
   generation return over the complete research history wherever that ranking
   is shown. Not hardcoded — values read from its stored record.
4. **Theme** — DeepTesting node table, DEEPTESTED table, statistics, monthly
   PnL, exit reasons, filter controls, trade list and dialogs use the existing
   design system (render smoke 56/56; `gen_theme_layer --check` unmapped: 0).
5. **Scenarios** — per-scenario Inspect S1/S2/… + Compare S1/S2 (own saved
   metrics each, deltas, monthly/exits per side, non-equivalence warnings,
   per-side trade inspection); identical reruns reuse (V6.7 behaviour kept).
6. **Filters** — Thursday exclusion removes exactly the Thursday trades and
   recalculates PnL/return/drawdown/counts/monthly/exits/trade list from the
   kept trades (numeric assertions in the suite); combined OR-restore/reset;
   the stored scenario and trades are byte-identical after filtering.
7. **Coverage** — persisted bars + REAL range-bar counts appear in the node
   table and refresh after every sync (shared per symbol/TF, no reload); a
   failed sync never reports COMPLETE (V6.7 suite) ; range bars unavailable =
   N/A, never faked.
8. **Sorting** — visible themed headers with ↑/↓ on node id, gen return/net/
   PF/DD/WR/trades, fitness, timeframe, stored bars, range bars, DeepTest
   return/PF/DD/trades, scenario count (numeric, missing-last, id tie-break,
   full dataset).
9. **LIVE TESTING etc. untouched** — no diffs in live execution/order/risk/
   scheduling code; the live-testing suites (V4.2/V4.3/V5.1a/V6.5.1) pass
   unchanged in the gate.
10. **Gate/build/push** — this report.

## F. Verification matrix — performed vs not performed

| check | status |
|---|---|
| full regression gate (64 files, canonical DATA per half) | **performed** — 1140 passed / 1 skipped / 0 failed |
| V6.8 + V6.7 acceptance suites | **performed** |
| Node #718 displayed==stored + ranking + findability (live data, API level) | **performed** |
| production build + fingerprints + `--match` V6.8 (d2e1c18) | **performed** |
| clean first load serves the current build (TestClient 200 + asset names) | **performed** |
| jsdom render smoke (all pages incl. DeepTesting contracts) | **performed** (56/56) |
| real browser click-through | **NOT performed** (no interactive browser in this sandbox) — covered by the render smoke + API probes; confirm visuals in the operator's browser |
| real MetaTrader5 terminal / broker / order path | **NOT performed here** (no MT5 on Linux; simulator/monkeypatched-bridge tests only) — operator-side: V6.5 §13 Windows MT5 demo acceptance + V6.4 §11 checklist remain open on the operator machine |
| Windows launcher (.bat) | **NOT run here** (Windows-only) — in-sandbox launcher path covered by `test_v5_1a_launcher_identity` + first-load serving |

## G. Data preservation

- Canonical LFS database (`c159b0718c43b43f…`) restored byte-identically after
  the gate (verified); no destructive resets; the app's own V3.2 restoration
  import is untouched (it runs on boot exactly as before).
- Zero `DATA/` paths in any commit; additive schema only (none needed this
  release — no migrations); LIVE TESTING settings/data untouched; DeepTesting
  scenario settings remain in `deeptest_node_configs` (never live config).
