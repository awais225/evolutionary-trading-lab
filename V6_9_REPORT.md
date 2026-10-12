# V6.9 — Release Report (2026-10-12)

Corrective release on V6.8 (`1897b0bd`): one canonical node identity and one
qualified count across Overview / LIVE TESTING / DEEPTESTING, the 88→99 and
5,240 root-cause fixes, the DeepTesting INSPECT crash fix, and the schedule
editor repair. Pre-change baseline and full measurement in
`V6_9_RECONCILIATION.md`.

## A. Commit chain (branch `main`, in order)

| # | sha | purpose |
|---|-----|---------|
| 1 | `c78e86598cd978ead8c54dd2c79c8aba0f8efa92` | V6.9 content (9 files, +695/−60): node-data authority caching + first-response counts, cross-tab qualified unification, 5,240 naming, INSPECT crash fix + smoke mount, schedule-editor contract, layer labels, V6.9 acceptance suite (15 tests) |
| 2 | (this commit) | docs: V6.9 publication record (`V6_9_RECONCILIATION.md` + this report + `BUILD_FINGERPRINTS.json`, recorded `--release V6.9 --commit c78e865`) |

Production build fingerprint (recorded at `c78e865`):

- `SRC_HASH=06cc5ee8fc7edb53c57f9515c32a667aa16e0bfd5ab195e8dcea0a69e4f14203`
- `CODE_HASH=6145dc81ab66eab2e22fe279c51fdd0db8fae398c244a06c0ef0aa9d70a761eb`
- `build_fingerprints.py --match` identifies the tree as **V6.9 (c78e865)**.
- Production `vite` build verified; the launcher-identity suite (which checks
  the guard against the served bundle) passes against the fresh `dist/`.

## B. Root causes and fixes (measurements in the reconciliation report)

1. **88 → 99 delayed count (§6)** — two stacked causes: (a) the Overview
   headline fell back to `lab.qualified` (`reconstruct_state(exclude_legacy=
   True)` — the exclude-legacy scope, 88 in the classification state where 782
   preserved-history rows are tagged `LEGACY_TEST`) until `/api/node-data`
   resolved; (b) that endpoint was uncached (~2.5 s per call) and its first
   call races the background V3.2 restoration — a failed/slow first fetch was
   silently caught, leaving the 88 fallback up for the user's 5–10 minutes.
   Fixed at the source: the authority is computed once per data state (cache
   keyed by database + state token), rides the FIRST `/api/status` response,
   and every tab reads it; no fallback is ever presented as the final count
   (explicit loading instead). Regression test reproduces both classification
   states and asserts initial == refresh == authority.
2. **DEEPTESTING's 5,240 “qualified” (§2.3)** — a valid count of a DIFFERENT
   concept: the frozen `node_bucket` gate-passed/VALID tier (stored status
   SURVIVED + QUALIFIED), i.e. the filter's row count over the same canonical
   records. Preserved under its own name (“Qualified tier” filter + “filter row
   counts … gate-passed/VALID-tier ≠ qualified research nodes” legend) while
   the shared **Qualified research nodes** statistic (status QUALIFIED+PAPER =
   the `get_qualified_strategies` formula) is displayed identically on all
   three tabs. No query was falsified; both numbers are computed from actual
   records and tested against the seeded database.
3. **`useRef is not defined` on INSPECT (§4)** — `DeepScenarioPanel.jsx` used
   `useRef` (per-scenario filter state, request sequence guards) without
   importing it; the production bundle crashed on mount. Fixed at source,
   audited every component for unimported hooks (regression test), and the
   jsdom smoke now MOUNTS INSPECT through the real button (57/57 renders).
   That new case immediately caught and led to fixing two unguarded
   `trade_counts` reads in the same panel.
4. **Schedule editor blanks / N/A / BLOCKED (§5)** — the DeepTesting popup's
   loader returned only the saved config: the dialog's option lists
   (`options.*`), node metadata (`node`) and engine evaluation (`allowed`,
   `reason`, `local_time`, …) were all missing, so controls rendered empty and
   the verdict badge fell back to “BLOCKED” (unknown ≠ a real block). The
   endpoints now serve the same contract as the live schedule API — real
   `supported_options` lists, genome-derived conditions, node identity
   metadata, and a real evaluation from the SAME `app.live_testing.schedule`
   evaluator (`eng.schedule_state`); a disabled schedule still reports BLOCKED
   with its specific reason (never forced to READY). Persistence, reopen
   restoration and defaults-reset verified; live settings untouched.
5. **Canonical identity (§2.1)** — one identity layer (persisted canonical id
   ↔ `research_node_num` ↔ `run_id`) shared by all tabs, with per-database
   cache keys and `mixed_snapshot` detection; alias search never substitutes
   one node for another (tested).

## C. Metric layers (§3) — as enforced

- **Layer A (generation)**: best generation record, immutable — a completed
  DeepTest never rewrites it (tested byte-equal before/after).
- **Layer B (DeepTest)**: per-scenario immutable records; `NOT DEEPTESTED`
  when absent; multi-scenario inspect/compare kept independent; stale/partial/
  failed runs never presented as completed.
- **Layer C (live)**: live records only, labelled `NO LIVE DATA` when absent
  (INSPECT strip) — never copied from research metrics (tested). Distinguished
  from simulator/paper/research by the existing source labels.
- Display projections keep `metrics_source` provenance; missing values stay
  missing; ranking source explicit.

## D. Regression gate (established resource-safe procedure; canonical DATA sha
`c159b0718c43b43f…` verified before each half; 65 files = 33 + 32)

| run | result |
|---|---|
| half 1 (final) | **670 passed, 1 skipped, 0 failed** (3:46) |
| half 2 (final) | **485 passed, 0 skipped, 0 failed** (3:53) |
| V6.9 acceptance suite (in half 2; 15 tests) | **passed** |
| V6.8 (25) + V6.7 (23) suites (in half 2) | **passed** |
| jsdom render smoke incl. INSPECT mount | **57/57** |
| launcher identity suite (served-bundle guard) | **13/13** |

**Totals: 1 155 passed, 1 skipped, 0 failed.** The single skip is the same
environment-gated skip as V6.6–V6.8 (`test_v5_1a_coverage_and_balances.py:120
— “this installation carries no simulator dataset”`). One earlier
development-run failure (`test_5_1a_launcher_identity::test_01`) was the
missing production `dist/` (not persisted across sandbox sessions) — green
after the production build; the two fingerprint tests were green after the
release record. No test was weakened or skipped.

## E. Acceptance criteria (§11)

1. **One canonical identity + one generation/qualification dataset** — the
   `app.research.node_data` authority serves all tabs; identity mapping shared
   and labelled (`Node #<id>` / `research #<num>`); duplicate representations
   cannot inflate counts (one row per canonical id).
2. **Qualified count identical across tabs** — measured live on the restored
   research history: first `/api/status` = refresh = `/api/node-data` =
   **99**, computed from actual records (status QUALIFIED+PAPER); never
   hardcoded.
3. **5,240 resolved** — named and labelled as the gate-passed/VALID-tier
   filter inventory (verified against the seeded records: equals the count of
   the frozen VALID-tier statuses), distinct from qualified research nodes.
4. **88→99 corrected** — explained in the reconciliation report (§2.1) and
   eliminated (deterministic regression test over both classification states).
5. **Layers separate and traceable** — tested (immutability, per-scenario
   independence, live-only live metrics, no cross-fallback).
6. **INSPECT renders** — smoke mounts the real button path (57/57); hook
   import audit enforced by test.
7. **Schedule editor** — real options/metadata/verdicts; persistence/reset;
   shared evaluator; blocks truthful (tested).
8. **DeepTesting features preserved** — V6.7+V6.8 suites pass unchanged (48
   tests), plus the duration/coverage/CSV/dedup paths covered there.
9. **LIVE TESTING protected** — zero diffs in live execution/order/risk code;
   live suites pass unchanged; DeepTest edits write only `deeptest_node_configs`.
10. **Gate + build + push** — this report.

## F. Verification matrix — performed vs not performed

| check | status |
|---|---|
| full regression gate (65 files, canonical DATA per half) | **performed** — 1 155 / 1 / 0 |
| V6.9 acceptance suite (counts, identity, layers, editor, crash, mixed snapshots) | **performed** |
| live probes: 99-across-tabs, 5,240 naming, #718 displayed==stored, editor contract | **performed** |
| production build + fingerprints + launcher identity (13/13) | **performed** |
| jsdom render smoke incl. INSPECT mount (57/57) | **performed** |
| real browser click-through on Windows | **NOT performed** (no browser in this sandbox) — the jsdom smoke covers the reported crash path; visual confirmation remains with the operator |
| real MT5 terminal / broker orders | **NOT performed** (no MT5 on Linux; simulator/mocked bridges only) — operator-side: V6.5 §13 Windows MT5 demo acceptance + V6.4 §11 checklist remain open |
| the exact Windows launcher .bat | **NOT run here** (Windows-only) — the launcher identity/guard suite passes against the served build |

## G. Data preservation

- Canonical LFS database (`c159b0718c43b43f…`) restored byte-identically after
  every gate run and at release time (verified); the V3.2 restoration import is
  untouched and the reported numbers were reproduced on that real state.
- Zero `DATA/` paths committed; additive schema only (no migrations); LIVE
  TESTING settings and execution paths untouched.
