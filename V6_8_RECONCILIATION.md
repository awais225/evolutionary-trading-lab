# V6.8 — Before/After Reconciliation Report (pre-change)

This report was produced BEFORE modifying any code, per the V6.8 master prompt
§2. All numbers below were measured this session against the real DATA
database and the committed sources.

## 1. Git baseline

| item | value |
|---|---|
| remote `origin/main` (fetched) | `7bc1728377d2f926850142e79eb1906e70695ed4` (docs: V6.7 publication record) |
| V6.7 content commit | `8da908a587a1b37ab3a15f5490c4987954941d19` |
| previous release | V6.6 `5e42c270` |
| local HEAD at session start | `5e42c270` with the V6.7 work sitting **uncommitted** in the worktree (two files of the published V6.7 — `V6_7_REPORT.md`, `tests/test_v6_7_deeptest_workflow.py` — had been lost from the snapshot) |
| resolution | restored the two lost files from `origin/main`, fixed two tool mode bits, moved `main` to `7bc17283`; worktree now **byte-identical to published V6.7** (clean). V6.8 builds on the published V6.7. |

Nothing was discarded: the uncommitted V6.7 diffs were verified identical to
the published V6.7 before the ref was moved.

## 2. The two dataset states (both real, neither invented)

| | S0 — canonical LFS snapshot | S1 — app-boot state (the user's live state) |
|---|---|---|
| how produced | LFS object `c159b0718c43b43f…` (the committed DATA database) | S0 + the app's own V3.2 `ResearchRestorationEngine.restore_all()` bootstrap (measured: `restored_nodes: 10809`) |
| rows / ids | 10 000 rows, ids 788–10787 | 10 809 rows, ids 1–10814 (preserved runs RUN-HISTORICAL-PRESERVED 350, RUN-20260927-CLEAN-MT5 360, RUN-20261005-055251 27, RUN-TEST-* 69, … re-imported) |
| Node #718 | **absent** | **present**: run RUN-HISTORICAL-PRESERVED, status SURVIVED, symbol XAUUSD, **timeframe M1** |
| QUALIFIED+PAPER | 83 | **99** (all rows) / **88** (after the boot classifier) |

## 3. Node #718 — record verification (§3.3)

Stored generation record of **canonical Node #718** (`backtests.strategy_id=718`,
stage `detail`) — measured exactly:

| field | stored | matches user's listed value |
|---|---|---|
| return | `1.481` → 148.100% | ✓ |
| profit factor | `2.545` | ✓ |
| max drawdown | `0.0405` → 4.050% | ✓ |
| win rate | `0.7724` → 77.240% | ✓ |
| trades | `905` | ✓ |
| symbol / timeframe | XAUUSD / M1 | ✓ |

Identity mapping (both namespaces, measured):

- **canonical `Node #718`** — id 718, `research_node_num` **318**, run
  RUN-HISTORICAL-PRESERVED, XAUUSD **M1**, stored status **SURVIVED**,
  best generation record = the values above. Rank **#1 of the complete stored
  research history** by generation return (1.481; #2 = id 736 at 0.8198).
- **`research #718`** — a **different node**: canonical id **1505**, run
  RUN-20261009-051720, its own record (9.17% / 57 trades / PF 1.84).
  Neither node replaces the other; both must stay discoverable under both
  labels.

Confirmed discrepancy to report (§3.3, never fabricate): Node #718's stored
record IS the highest-return record in the research history, but after the
V3.6 boot classification it carries `data_source='LEGACY_TEST'`
(`node_bucket` → **"Excluded"**) and is therefore absent from every default
`LEGACY_TEST`-hiding list (`/api/population`, `get_qualified_strategies(
exclude_legacy=True)`, the DeepTesting default "Qualified" filter). Search
"718" under DeepTesting's default filter returns `research #718` (id 1505)
and **silently drops canonical #718** — a node disappearing from a tab through
an identity/status interpretation difference, exactly what §3.3 forbids.

## 4. Why the numbers changed — root causes

### RC-1 (primary): V6.7 replaced the established qualified-count authority

Frozen established formula (present since V3/V4, unchanged in every release
before V6.7): **qualified = stored status `QUALIFIED` + `PAPER`**
(`db.get_qualified_strategies()`, `reconstruct_state`, `database.py:822`
per-run counters, the V3.2 manifest ledger `qualified_nodes`, and
`pipeline_state.cumulative`). On S1 that is **99** (all research records).

V6.7 (`8da908a5`, Overview.jsx) replaced the headline with
`inventory.counts_excl_legacy.qualified` — the **`node_bucket` classifier's
"qualified" bucket**, which maps V3-era `SURVIVED` to VALID and counts
**5 198** on S1. That is a different measure (VALID-tier vs QUALIFIED-tier),
computed by a **new authority** (`inventory_snapshot`) that V6.7 introduced.
Result: the Overview QUALIFIED card changed from the established value
(99/88 → the value the user screenshotted and anchored on is 99) to 5 198,
while run panels / LIVE TESTING / run-history views kept 88/99 —
"qualified-node counts have changed" and cross-tab inconsistency, exactly as
reported.

### RC-2: boot-time legacy classification makes the record set session-unstable

`Database.__init__`'s V3.6 "dummy/legacy development records" heuristic
(`id <= 787 OR run_id IS NULL OR run_id LIKE '%TEST%' OR run_id =
'RUN-HISTORICAL-PRESERVED'` → `data_source='LEGACY_TEST'`) re-tags the
**V3.2-restored historical research records** (real nodes with real
generation records, ids 1–787) on the first database open **after** the
restoration import. Consequence across sessions:

- session right after import: 99 qualified, `/api/population` top-return =
  **Node #718 (148.1%)** (its `data_source` was still `USER_RESEARCH`);
- every later session (incl. today's): 88 qualified in legacy-hiding scopes,
  Node #718 hidden from those lists and from DeepTesting's default search.

This is why the user saw "99 qualified + top-return Node #718" before and
"counts changed + #718 gone" now. The classification rule itself is frozen
(V6.8 will NOT change it — acceptance §12.1); what V6.8 fixes is that every
tab must show ONE stable, labelled view of the same records instead of each
endpoint picking its own scope and silently dropping rows.

### RC-3: V6.7's inventory `tops` scope flip

V6.7 first ranked `inventory.tops` over all rows (Node #718 #1), then
mid-release re-scoped them to non-legacy (Node #718 dropped) to match
`/api/population` — the ranking source changed under the user between builds.
V6.8 removes `inventory_snapshot` as a display authority entirely (it stays
only as a revision/fingerprint consistency verifier) and ranks from the same
records every tab lists.

### RC-4 (display, separate workstream): DeepTesting theme / scenario
comparison / filter reactivity / coverage refresh / sorting — see §6.

## 5. Number matrix (measured on S1 = user's live state)

| displayed quantity | source formula | V6.6 | V6.7 | V6.8 (target) |
|---|---|---|---|---|
| QUALIFIED headline (Overview) | status QUALIFIED+PAPER | 88¹ | **5 198** ✗ | **99** (full research history, labelled) |
| QUALIFIED (run history / cumulative / manifest ledger) | Σ per-run QUALIFIED+PAPER | 99 | 99 | **99** ✓ same |
| DeepTesting "Qualified" filter chip | `node_bucket` qualified (SURVIVED+QUALIFIED) | 5 198 | 5 198 | 5 198 (unchanged frozen filter semantics, explicitly labelled "filter · VALID-tier") |
| top-return #1 (full research history) | best generation record return | Node #718 2 | Node #718 dropped | **Node #718** (same source every tab) |
| Node #718 search visibility | identity search | visible only under All/Excluded filters | same + tops dropped | visible from **any** tab/search by either identity, metrics from its stored record |

¹ 88 only after the boot classification; 99 in the session right after the
import and in all non-legacy-hiding counters (manifest, run Σ, cumulative).
The instability is RC-2; V6.8 pins the displayed scope explicitly.

## 6. V6.8 fix plan (mapped to acceptance criteria)

1. **Node data authority (frozen formulas, one projection, one revision).**
   New shared projection built ONLY from existing queries
   (`get_qualified_strategies` semantics, `reconstruct_state` semantics,
   `node_bucket` labels, best-generation-record metrics). Every node-related
   response carries the same `data_revision`/fingerprint (mixed snapshots
   detectable). V6.7's `inventory_snapshot` is demoted to a consistency
   verifier; its bucket count is never the headline again. No import,
   qualification, status or generation-metric logic is changed.
2. **Qualified count = 99 everywhere** (QUALIFIED+PAPER over the complete
   stored research history — the established `get_qualified_strategies()`
   default, manifest ledger and run-Σ formula), with the legacy-hiding
   sub-scope (88) shown only as a labelled secondary. Overview RECHECK
   refreshes this same projection (same algorithm as first load).
3. **Node #718**: preserved (record untouched); identity search spans the full
   research history from every node tab so it can never be dropped by a
   status interpretation; ranking shows it at its true place (#1 by generation
   return over the full history) wherever that ranking is displayed. Not
   hardcoded — every displayed value comes from its stored record.
4. **Theme** — DeepTesting tables/statistics/inspector rebuilt on the existing
   Overview/LIVE TESTING design tokens and shared components (no new theme).
5. **Scenarios** — per-scenario Inspect S1/S2/… actions, two-scenario
   side-by-side comparison (own metrics each, diffs, non-equivalence warnings,
   trade-list access), keeping V6.7 identical-scenario reuse.
6. **Performance filters** — checkbox state → filtered trade set → every
   displayed statistic recomputed from those trades (numbers asserted in
   tests), OR-exclusions, restore/reset, no stale async overwrites, no
   scenario mutation.
7. **Coverage refresh** — node-table bars/coverage recomputed from persisted
   candles after GET MT5 DATA completes (all rows sharing symbol/TF updated,
   no page reload, no false success).
8. **Sorting** — visible working header sort on all required DeepTesting node
   columns (numeric, missing-last, id tie-break, full dataset).
9. **LIVE TESTING / import / qualification / generation calculations** —
   untouched; regression gate + before/after live smoke comparison.
