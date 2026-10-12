# V6.9 — Reconciliation & Root-Cause Report (pre-change)

## 1. Verified baseline

| item | value |
|---|---|
| reported V6.8 commit | `1897b0bd8728f0c6b0a680b1b128a8735faeab9c` |
| remote `origin/main` (fetched) | `1897b0bd…` — matches the report |
| local HEAD at session start | `5e42c270` (V6.6) + uncommitted V6.7-era diffs (workspace snapshot restore pattern) |
| reconciliation | the worktree matched published V6.8 **except** 6 files lost from the snapshot (`V6_7_REPORT.md`, `V6_8_RECONCILIATION.md`, `V6_8_REPORT.md`, `backend/app/research/node_data.py`, the two V6.7/V6.8 test files) + 2 tool mode bits. Those were restored from `origin/main`; `main` moved to `1897b0bd`; worktree now byte-identical to the reported V6.8 (clean). Nothing was discarded. |
| canonical database | LFS object `c159b0718c43b43f…` (10 000 rows, ids 788–10787) — byte-identical backup saved (`/tmp/lab_state.canonical.db`) and the established LFS restore procedure followed. No synthetic database was written over user data. |
| fingerprints | `BUILD_FINGERPRINTS.json` records V6.8 @ `d2e1c18` (published chain `7bc17283…1897b0bd`); the running Windows build could not be inspected from this sandbox — the launcher/build step is re-verified in the release gate. |

## 2. The counted numbers — traced to their sources (measured this session)

On the **app-restored research history** (10 809 rows = canonical 10 000 + the
V3.2 `restore_all` import of preserved runs; `restored_nodes=10809`,
`qualified_nodes=99`) there are exactly TWO stable database states, differing
only in the V3.6 boot classifier's `data_source` tagging of the 782 imported
rows with ids ≤ 787:

| measurement | State X (imported rows still `USER_RESEARCH`/NULL — the state in the importing session) | State Y (after any restart: `Database.__init__` re-classifies ids ≤ 787 / `'%TEST%'` / `RUN-HISTORICAL-PRESERVED` as `LEGACY_TEST`) |
|---|---|---|
| QUALIFIED+PAPER (all rows) | **99** | 99 |
| `lab.qualified` / `recheck.qualified` / `lab.qualified_nodes` (`reconstruct_state(exclude_legacy=True)`) | 99 | **88** (the 11 preserved QUALIFIED rows are tagged legacy) |
| `/api/node-data` `counts.qualified` (V6.8 authority: QUALIFIED+PAPER over the complete stored research history) | 99 | 99 |
| DeepTesting `filter_totals.qualified` (frozen `node_bucket` "qualified" = SURVIVED+QUALIFIED) | **5 240** (5 141 + 99) | 5 198 |
| `filter_totals.excluded` (rows `node_bucket` labels Excluded = `LEGACY_TEST`) | **0** | 782 |
| `alive` / `eligible` (bucket qualified+alive = SURVIVED+QUALIFIED+BORN) | 6 006 | 5 225 |

The user's current DeepTesting report (`qualified=5240 alive=6006 eligible=6006
all=10809 failed=4762 excluded=0 blocked=41 unknown=0`) is exactly **State X**,
and the “88 immediately after opening” is exactly **State Y** — measured, not
guessed.

### 2.1 Root cause A — the 88 → 99 “5–10 minute” discrepancy (§6)

Two mechanisms stack:

1. **Different scopes under one name.** `lab.qualified` (= `reconstruct_state(
   exclude_legacy=True)`) counts only non-legacy rows → **88** in State Y,
   while the V6.8 authority counts the complete stored research history →
   **99**. Before the authority arrives (and in every component still reading
   `lab.*` — e.g. LIVE TESTING's `PopulationSummary` uses `lab.qualified_nodes`),
   the UI shows the 88-scope number.
2. **Delayed authority hydration.** The Overview headline falls back
   `nodeData?.qualified ?? recheckData?.qualified ?? lab?.qualified ?? …`; until
   `/api/node-data` resolves, the displayed value is the 88-scope fallback.
   `/api/node-data` is **not cached** (a full 10 809-row recomputation per call
   — 2.5 s measured here) and its first call races the background V3.2
   restoration (write-lock bursts while importing 6 200 backtests); a failed or
   slow first fetch is silently caught, so the 88 fallback can stay on screen
   for minutes until a later refresh succeeds — the user's 5–10 minutes.

Fixed at the source (V6.9): one authority for every tab, server-side cached by
the existing state token, carried in the FIRST status response, with explicit
loading states — never a different-scope fallback presented as the final count.

### 2.2 Root cause B — DEEPTESTING's 5 240 “qualified” (§2.3)

`5 240` is a **valid count of a different inventory concept**: the frozen
V5.1.1 `node_bucket` classifier's *"qualified/VALID" tier* (stored status
SURVIVED + QUALIFIED — “tested against real data and still alive”), computed
over the same canonical rows (`nodes_index` → `filter_totals`). It is the row
count of DeepTesting's **filter**, not the qualified-research-node statistic
(`status IN ('QUALIFIED','PAPER')` = **99** — the formula of
`get_qualified_strategies`, `reconstruct_state`, the per-run counters and the
V3.2 manifest ledger). Both concepts are legitimate; they were both labelled
“qualified”. V6.9 keeps both counts, computed from the same records, with
distinct names: **Qualified research nodes (99)** vs **Gate-passed/VALID-tier
inventory (5 240)** (plus the other inventory totals, clearly labelled as
inventory statistics).

### 2.3 Root cause C — `useRef is not defined` crash (§4)

`frontend/src/components/DeepScenarioPanel.jsx` line 5 imports
`{ useCallback, useEffect, useState }` but the V6.8 code uses `useRef`
(`filtByRun`, `filtSeq`, `initialRef`). `useRef` is therefore undefined in the
production bundle and INSPECT crashes on mount. (esbuild syntax checks and the
jsdom render smoke did not exercise the INSPECT mount path.)

### 2.4 Root cause D — schedule editor blank options / N/A metadata / BLOCKED (§5)

The SCENARIO popup (`ScheduleDialog`) renders from the `loadFn` response:
option lists from `res.options.{sessions,regimes,timeframes,conditions,days,
timezones}`, node metadata from `res.node`, engine clock/verdict from
`res.{local_time,timezone,allowed,reason,rules}` — the exact shape of
`GET /api/live-testing/schedule/{sid}` (evaluated by `app.live_testing.schedule`
/ `eng.schedule_state`). The DeepTesting loader (`GET /api/deeptest/node-config/
{id}`) returns only `{config, configured}` — **no options, no node metadata, no
evaluation** — so every option list renders empty and the metadata shows N/A,
and `state.allowed` is undefined → the verdict badge falls back to “BLOCKED”
(a fallback rendering of *unknown*, not a real engine block).

### 2.5 Identity (§2.1)

Canonical id (`strategies.id`), research-local display number
(`research_node_num`, assigned per run) and run provenance (`run_id`) are
persisted columns; the V6.8 `node_data.identity` map already resolves both
labels from them. The instability is not identity math but the `data_source`
re-tagging above (State X vs Y) plus per-tab query scopes. V6.9 keeps one
identity layer (same mapping, same revision stamps on every tab response) and
adds explicit mixed-snapshot reporting.

## 3. Metric layers (§3) — as-built and gaps

- Layer A (generation) exists per node: best generation record (detail>screen),
  exposed as `generation_metrics` (frozen query; displayed == stored).
- Layer B (DeepTest) exists: per-scenario immutable records with
  requested/actual range, coverage, bars, status; `NOT DEEPTESTED` shown when
  absent; V6.8 compare keeps scenarios independent.
- Layer C (live) exists in `nodes_index` rows (`live`, `position`, `worker`,
  `lifecycle`) — needs the explicit `NO LIVE DATA` labelling and provenance
  polish per §3, and no cross-layer fallback anywhere (V6.8's
  `metrics_source` already labels the display projection).

## 4. Freeze audit

`node_bucket`/`v5_status`, `get_qualified_strategies`, `reconstruct_state`,
evolution/fitness, import/ingestion and live-order safety code are untouched by
this plan; V6.9 changes presentation sources, caching, the popup's data
contract and tests only.
