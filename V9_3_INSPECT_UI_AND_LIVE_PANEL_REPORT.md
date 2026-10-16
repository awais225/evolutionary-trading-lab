# V9.3 — Inspect results and Manual Trading disclosure

## Inspect-only results redesign

The existing node table layout and controls remain; the results area below INSPECT is the redesign target. Existing themed KPI cards retain return, P&L, PF, drawdown, win rate, trade count, expectancy, Sharpe/Sortino availability and scenario state. Their source, requested/actual UTC range, dataset fingerprint, engine versions, initial capital, coverage and immutable scenario ID remain visible. Added responsive calendar summary cards expose units and observation states, with structured tables for day totals, day×session drill-down, aggregate sessions and every ISO calendar week across the selected range. Native details/summary and overflow containers keep it keyboard-accessible and usable at narrow widths. No new tab was introduced.

Date bounds (UTC entry from / exclusive before), day exclusions, major sessions, per-day session exclusions, an optional full daily matrix, scenario selection, active indicators and reset form the grouped filtering controls. Save-current-filters includes the whole normalized configuration. Reset restores the unfiltered scenario; filters are post-trade analysis, never a fresh backtest claim. UTC/DST-aware old Inspect session membership remains distinct and explicitly disclosed from the new fixed-UTC exclusive matrix.

All statistics come from backend aggregation of the complete persisted trade population. Wins/eligible unique closed trades, gross-profit/gross-loss and chronological realised equity are recomputed on each subset; session percentages are never averaged. Overlap row counts are explicitly non-additive; overall cards use the unique union. NO_TRADES differs from missing history. Undefined ratios/metrics stay unavailable rather than fabricated zeros or infinity. Missing timestamp counts remain visible.

Recalculation clears the old filtered result, provides a loading state, and shows an explicit error without substituting the unfiltered summary under active filters. Latest-request guards cover filtering, detail/trade fetches and range counts. An important additional fix drains all backend-capped trade pages: although the old UI requested10,000, the route caps each response at500. The inspector now continues by actual received offset until the total is loaded; an unexpectedly empty page fails explicitly rather than presenting a partial list as complete. The functional harness proves offsets0/500/1000 for1,001 trades, while normal DOM trade rendering remains paginated.

## Manual Trading panel

`ManualPanelDisclosure.jsx` wraps the original, unchanged `ManualOrderPanel` on LIVE TESTING. It starts collapsed on a fresh mount, has a full-width native button header, right-side chevron, aria-expanded and aria-controls. Button keyboard semantics provide Enter/Space behavior. Hidden content remains mounted, preserving drafts and validation state across collapse/expand. Only header clicks toggle; interacting with child fields/buttons does not collapse it.

The wrapper has no API imports/calls, no order logic and no cancellation path. Original sizing, MT5 validation, signed offsets, account/demo constraints and explicit order confirmation stay inside the original panel. Tests exercise collapsed default, retained draft, child isolation and zero disclosure network calls. They do not send any real/demo order.

## Operational proof and limits

Functional jsdom exercises shared source controls/cells, persisted scenario hydration, selection, queue progress, read-only Live review, manual disclosure, initial counts and superseded response rejection; legacy functional harnesses still cover order controls using fakes. Production Vite build/fingerprint checks are required and recorded in the main report. This is not a claim of native Edge visual QA, Windows launcher execution or successful MT5 terminal execution; those environments were unavailable. No screenshots or manual native-validation claims are fabricated.
