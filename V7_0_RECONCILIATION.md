# V7.0 — Reconciliation & Root-Cause Report (pre-change)

## 1. Verified baseline

| item | value |
|---|---|
| reported V6.9 baseline | `b97e5df3b0bb2bc5e80c33ea41b563d6b11653c2` |
| remote `origin/main` (fetched) | `b97e5df3…` — matches the report |
| local HEAD at session start | `5e42c270` (V6.6) + uncommitted V6.7-era diffs (workspace snapshot restore pattern) |
| reconciliation | the worktree matched published V6.9 except 9 files lost from the snapshot (5 release reports, `backend/app/research/node_data.py`, the three V6.7/V6.8/V6.9 test files) + 2 tool mode bits. Restored from `origin/main`; `main` moved to `b97e5df3`; worktree byte-identical to the reported V6.9 (clean). Nothing discarded. |
| canonical database | LFS object `c159b0718c43b43f…` (10 000 rows) — backup saved (`/tmp/lab_state.canonical.db`, hash recorded) and the established LFS restore procedure followed. |

## 2. §2 root cause — every duration fetches the same ~1-month window

Traced `duration selection → … → displayed bar count` in the real code:

1. **Frontend never transmits the duration.** `DeepBacktest.jsx` `onFetch(symbol,
   timeframe)` posts `api.deeptestFetch({ symbol, timeframe })` — the selected
   `periodKey` (1D…3Y) is not in the payload (and `DeepCoveragePanel`'s button
   uses the same wrapper).
2. **The route drops it too.** `POST /api/deeptest/fetch` reads only
   `symbol`/`timeframe` and calls `start_fetch(symbol, timeframe)` — the
   `period` parameter `start_fetch` already accepts is never passed
   (`_FetchJob.period` is carried and then ignored).
3. **`fetch_job._run` ignores `job.period`** — it calls
   `engine.sync_master(symbol, timeframe)` with no range at all (the comment
   “the requested period extends the goal” was never implemented).
4. **`sync_master` derives its range from CONFIG, not the request**:
   `months = cfg.data.history_months.get(timeframe, 6)` and
   `fetch_start = now - months*30*86400`. The operator's CONFIG carries
   **`history_months: {"M15": 1}`** — one calendar month of M15 ≈ 2,100–2,900
   bars — **the “approximately 2,345 bars” the user sees for EVERY duration
   button**. Incremental mode then fetches only the tail delta, and the
   up-to-date shortcut (`now - last_stored < step`) returns “REUSING EXISTING
   DATA” without ever extending the range — repeated clicks show the same bars.
5. The bridge itself is correct: `mt5_real.copy_rates_range(symbol, tf, start,
   end)` passes the window to `mt5.copy_rates_range` unchanged; the V6.7
   chunked walker (15 000 bars/chunk, bounded retries) is sound — it is simply
   aimed at the wrong window. `deeptest/periods.resolve_period(duration_key)`
   already exists and computes documented UTC bounds for the nine presets.

So the defect is **request plumbing + range policy**, not the terminal bridge:
the selected duration dies at the frontend and the backend substitutes a fixed
recent window (§2.2 items: “frontend failing to transmit the selected
duration”, “hardcoded recent-history window”, “cache lookup that returns a
shorter range for a longer request” — the `reuse_existing` shortcut).

### Secondary findings (same path)

- The up-to-date/reuse shortcuts check only the TAIL freshness, never that the
  store's `first_ts` covers the requested start — a short cache can masquerade
  as satisfying a long request (§2.5 defect, confirmed in code).
- The chunked loop fetches ONE interval (`fetch_start → fetch_end`); earlier
  history (requested start before `first_ts`) is never requested — a 2-year
  request against a 1-month-old cache only extends forward.
- `sync_status` COMPLETE/PARTIAL (V6.7) is computed against the wrong
  “request” (the config window), so coverage honesty is currently mis-aimed.

## 3. §3 — Inspect panel state (as-built)

`DeepScenarioPanel.jsx` mounts correctly (V6.9 fixed the `useRef` crash; the
smoke mounts INSPECT). The statistics block is a plain two-column definition
table over `SUMMARY_ROWS`; monthly/exit blocks and the performance filters
already recalculate from the kept trades (V6.8/V6.9 semantics, tested); the
trade table is a `tbl mono` table with 5 sort keys (client-side, full loaded
set). Gaps vs the §3 brief: no statistic-card grid, unthemed metric block,
limited sort columns, filter/theme polish. The data contracts (summary keys,
`analytics.breakdowns`, trade fields: side/entry/exit/sl/tp/exit_reason/
hold_seconds/net_pnl/session/dow) are unchanged and will be reused as-is.

## 4. §4 root cause — the Manual Trading Panel was demoted and hidden, the
bridge is intact

V6.4 reference (commit `1e47735d` “V6.4: fix execution, identity, metrics and
live testing”; publication `bf25ae00`):

- `LiveTesting.jsx` mounted `<ManualOrderPanel defaultSymbol={marketSymbol} />`
  in the third section (“Action controls”, hint: “the manual order panel uses
  the same MT5 execution path as the engine”), **always visible**, before the
  nodes table.
- `LiveTestingPanels.jsx:ManualOrderPanel` — manual BUY/SELL with backend
  risk↔lot conversion (`mt5ExecutionPreview`), confirmation dialog, and the
  proven place path (`mt5ExecutionPlace` → `/api/mt5-execution/place` →
  `mt5.execution.place_demo_order` → bridge `place_order`, with the V4.2
  IOC→FOK→RETURN filling fallback and SL/TP keys omitted when absent).

What changed since (verified by diff `1e47735d..HEAD`):

- `LiveTesting.jsx` (V6.5-era reading-order refactor): the panel moved BELOW
  the nodes table and was wrapped in a **collapsed-by-default** toggle
  (`manualOpen` default `false`; body `display: none`), renamed
  “Manual Demo Trade … demo only · collapsed”. On the operator's screen the
  panel body is invisible — “the panel has disappeared”.
- `LiveTestingPanels.jsx` changes are only the RiskStrip V6.5.1 work; the
  ManualOrderPanel itself is unchanged.
- `backend/app/mt5/execution.py` is **byte-identical to V6.4** (0-line diff);
  `bridge.py`/`mt5_real.py` diffs are additive. The routes
  (`/mt5-execution/state|preview|validate|place`) all exist and answer (probed:
  state 200; preview 200 with the honest `INVALID_ENTRY_PRICE` block when no
  quote exists). Duplicate protection (client_order_id idempotency + operation
  gate) is present in `place_demo_order`.

**Conclusion**: the order-placement bridge was NOT removed; the panel's
visibility/position/label was. V7.0 restores the panel to its V6.4 prominence
(expanded by default, “Action controls” position, clear title) while keeping
the newer collapse toggle as an optional convenience, and locks the whole
request/bridge path with mocked regression tests.

## 5. Pre-change snapshots

- canonical DB sha: `c159b0718c43b43f…` (backup `/tmp/lab_state.canonical.db`).
- production fingerprints: `BUILD_FINGERPRINTS.json` records V6.9 @ `c78e865`.
- V6.9 gate (prior release, re-verified in this checkout): 1 155 passed /
  1 skipped / 0 failed.

## 6. Freeze audit (§5)

V7.0 changes are confined to: the fetch request path (range policy), the
Inspect presentation layer, and the Manual Trading Panel visibility. No
changes to import/qualification/survival/generation formulas, the node-data
authority, scenario isolation, schedule evaluation, live order safety
(`place_demo_order` semantics), or risk limits.
