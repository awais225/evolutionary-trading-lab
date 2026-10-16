# V9.2 Inspect, saved filters and generation-window report

## Inspect
The existing metrics remain. The added calendar panel reports weekday→session hierarchy, per-weekday and across-weekday sessions, a unique total over the selected range, and actual ISO-year/week plus calendar-date ranges without a four-week cap. It distinguishes observed/no trades from missing entry timestamps and does not imply that a no-trade week proves complete candle history.

Version1 rules use actual UTC entry timestamps and the existing Tokyo/London/New York local exchange hours with historical DST. Session membership can overlap; a London/New York overlap trade belongs in those analytical panels but enters a selected union only once. Global day/session exclusions and weekday-specific session exclusions share one evaluator with Live and historical scenario entry masks. Zero, missing and undefined metrics remain separate. Post-trade exclusion analysis is disclosed as such, not represented as a fresh strategy rerun.

Unversioned legacy API requests retain their original recorded-label day/session semantics for compatibility. The V9.2 UI explicitly sends schema_version1 and uses server-kept trade numbers; it does not independently apply an incompatible client filter. Named scenarios store the explicit version1 UTC contract.

## Saved scenarios
`SAVE CURRENT FILTERS AS SCENARIO` opens a name/scope/current-checklist/structured-summary Save/Cancel popup. The rules and origin run provenance are durable in dedicated SQLite tables, separate from immutable Deep run snapshots. Global and node-local scope are validated against canonical nodes. A saved scenario is discoverable in the existing SCHEDULE modal for Live or Deep, with explicit assignment/removal buttons and active-rule summary. Assignment adds only the shared entry gate; it does not mutate the underlying original schedule/risk/enabled state. Removing it restores the original unchanged schedule. It never starts a worker, submits an order, enables Live or changes capital/risk.

Deep captures the assigned rules into a run snapshot/signature, rather than reading mutable assignment state midway through the run. Changed rules therefore cannot silently reuse an incompatible historical result. Live evaluates the same predicates at the prospective entry timestamp.

## TIME RANGE BACKTEST DURING GENERATION
Overview exposes an optional enabled setting and an integer1..3650 calendar days (default3). Saving does not start generation. The default is OFF to retain zero extra backtest cost until deliberately requested.

During screen evaluation, a shared per-symbol/TF/data-fingerprint/date/day context identifies the persistent baseline window. The existing Backtester and the node's unchanged genome/cost/risk defaults run the additional window in the existing worker, not a fabricated heuristic. Fewer than120 in-range bars or incomplete requested coverage is explicitly INSUFFICIENT_HISTORY; dates are not silently shortened. Context caches are bounded. The result identity includes data fingerprint, range, days, genome, configuration digest and engine versions. Results live in a separate generation_window_results table, never in the original backtests/fitness/qualification, Deep or live metric namespaces. Preparation/run/persistence failures do not alter the original screening verdict or eligibility.

Results are loaded only when the operator expands the result panel, avoiding a large eager Overview payload. The normal generation algorithm and fitness/survival thresholds are unchanged. Default-off and enabled-window resource timings are recorded in the final report; real browser paint remains unverified.


## Verification boundaries
The real persisted Node718 genome is exercised through the same `bt_worker` path and dedicated generation-window persistence writer against4,131 actual M1 bars over3 calendar days. The original screen metrics are compared excluding runtime timing; original strategy and backtest-row counts must remain unchanged. Repeat window strategy metrics are deterministic. No new production generation campaign, Live worker or real order is started for this proof. Performance values and the separate persisted record check appear in the final evidence.

Assignments are cached only for the specific live database connection. Replacing/reopening a connection invalidates both schema readiness and assignment contents, preventing an old node's entry gate leaking into a replacement database. Explicit Save/assignment mutations invalidate the shared cache. Out-of-process ad hoc SQL edits are not a supported live assignment-edit interface.
