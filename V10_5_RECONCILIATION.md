# V10.5 — reconciliation evidence and calculation contract

## Exact example: records unavailable, not fabricated

Requested run: `dt-9edaa1dcbd0d42e7`. The current verified source clone contains Git LFS pointers rather than the native research database; available uploaded text/evidence does not contain this run. No authoritative DB was reset, imported, repaired, copied wholesale or backtested to manufacture evidence. **The canonical node ID is unresolved**: neither #4325 nor #4315 can be certified from the run metadata here. Research #3538 is supplied context, not independently verified.

The following values are **user-provided observations**, not newly computed results. Corrected values cannot be supplied without the original closed trades and run configuration.

| Metric | Table observation | Inspect observation | Monday-Asia-filtered observation | Corrected exact-run result |
|---|---:|---:|---:|---|
| Net P&L | not supplied | 1,327,176.86 | 1,648,844.95 | unavailable: need stored trades |
| Initial capital | not supplied | N/A | 10,000 | unavailable: inspect stored run settings |
| Return | 663.588% | not supplied | 16,488.449% | unavailable |
| Profit factor | 1.094 | 1.094 | 1.123 | unavailable |
| Max drawdown amount | not supplied | not supplied | not supplied | unavailable |
| Max drawdown percent | 399.415% | 84.860% | 6,900.554% | unavailable; old definitions/sources differ |
| Win rate | 28.67% | 28.67% | 28.97% | unavailable |
| Closed trades | 15,277 | 15,277 | 14,586 | unavailable for independent verification |
| Average trade | not supplied | 86.87 | 113.04 | unavailable |
| Expectancy | not supplied | N/A | 113.0430 | unavailable |
| Sharpe | not supplied | 0.611 | 0.569 | unavailable |
| Sortino | not supplied | 1.744 | N/A | unavailable |

Supplied arithmetic is internally plausible: 15,277 − 691 = 14,586; 1,327,176.86 − (−321,668.08) = 1,648,844.94, one cent from the filtered observation. This **does not prove** source membership, capital, chronology or drawdown. We do not infer capital from the reported return, assign an illustrative $8,486 drawdown, assume USD, or claim removal of losing trades must reduce drawdown.

## Proven code defects, versus unproven exact-run causality

1. The old filtered endpoint selected `sc.metrics` as its original summary, whereas table/headline paths selected `sc.analytics.summary`. Those are independently calculated snapshots; Inspect could switch its source after the filtered request loaded.
2. Its capital lookup checked `metrics.initial_capital`, then `settings.initial_capital`, then **invented 10,000**. Actual DeepTest orchestration stores and uses **`settings.total_capital`**. The service now prioritizes the stored run capital and never uses a 10,000 fallback. Zero/missing capital remains zero/missing, not a fallback opportunity.
3. `analytics.summarize` accumulated P&L in incoming order, usually persisted trade number/entry order, not chronological **exit order**. Concurrent positions make those orders materially different. Its drawdown denominator was initial capital; an engine metric is not interchangeable merely because both fields contain “drawdown”.
4. The previous P&L reconciliation tolerance grew by two cents **per trade**. It could accept a large discrepancy in a large run. It now uses compensated summation and one half-cent rounding tolerance for the **whole summary**.

These defects explain how inconsistent views are possible. Without this run's records we cannot attribute its exact 399.415/84.860/6,900.554 values to particular curves, dates, missing rows, or engine versions.

## Authoritative backend contract

`app.deeptest.metric_service` reads run headers and **all** saved closed trades in the same SQLite snapshot. It releases the DB/read lock **before** financial CPU work. It powers scenario lists, node summaries/headlines, Inspect, preference responses, comparisons, the shared historical projection and metrics JSON export. Low-level immutable engine/storage snapshots remain preserved as evidence; no destructive migration or retrospective backtest is performed.

- **Eligible trades:** finite closed-trade net P&L; unique run/trade identity. Conflicting duplicate evidence is refused, not silently overwritten. Anonymous legacy rows are not collapsed. Fees/swap/commission are already in net P&L and are not subtracted twice.
- **Net P&L / gross profit / gross loss:** compensated sums of net observations, positive observations and absolute nonpositive losses respectively. Zero-loss profit factor is N/A, not infinity.
- **Capital:** stored run configuration (`total_capital`, then compatible explicit capital fields), with source recorded. No current-account balance or inferred capital is substituted for historical run capital.
- **Return:** net P&L divided by positive stored initial capital. Legacy `*_pct` API fields remain **fractions** for existing UI contracts; explicit `return_percent`/`drawdown_percent` are percent units. UI multiplies legacy fractions by 100 only for formatting.
- **Win rate:** positive-net closed trades / eligible closed trades. Average trade and expectancy use this same population.
- **Realised equity:** initial capital followed by closed P&L ordered by `exit_ts`, then `trade_no`, entry time and canonical row ID. Initial equity is a possible running peak. Missing/nonfinite exit time makes chronology-based metrics unavailable.
- **Drawdown amount:** maximum running-peak-to-subsequent-equity decline. **Percentage denominator: initial capital**, never peak equity. Both values come from the same curve. No 100% clamp; sufficiently negative equity can legitimately produce more than 100% of initial capital. This is not intrabar/unrealised account drawdown.
- **Sharpe:** existing documented trade-return convention, sample standard deviation, zero risk-free return, assumed 252 trades/year. **Sortino:** same series, RMS downside over all observations, same assumption. This is not inferred daily/calendar annualisation. Missing chronology, insufficient samples, unavailable positive capital or zero dispersion/downside gives N/A with reasons. No falsely precise native risk-ratio validation is claimed.
- **Filtering:** start with the full authoritative eligible set; independently calculate original, included and excluded populations. Rebuild equity separately; never delete points from an already calculated curve or reuse original drawdown. Historical position sizes and costs remain: this is post-trade analysis, **not a re-simulation**.
- **Membership:** current versioned rules use actual UTC entry weekdays and DST-aware Tokyo/London/New York membership, including overlaps. Legacy unversioned requests retain explicitly identified recorded-label exclusion behavior. Canonical calendar/entry-session groups and legacy recorded-session report groups are separately labelled; overlapping session totals must not be added.
- **PARTIAL:** execution/coverage status is retained. Available/eligible/expected trade counts, bars where known, requested range, method, capital source, currency, timestamp and trade snapshot hash are exposed. Missing expected trades are flagged. A summary-only record with no underlying expected trades returns **N/A**, not invented zero performance, and cannot support filtering.
- **Currency:** stored run currency when available; otherwise “currency unavailable”, never an assumed dollar sign. Inspect shows the monetary drawdown prominently with its matching percentage and denominator. Metrics JSON export preserves both; full trade CSV remains unbounded.

## Independent regression evidence — not the user run

A deliberately isolated three-trade fixture uses stored capital 200,000 EUR and conflicting stale engine capital 10,000. Exit-chronological P&L is +200, −150, −120, while insertion order differs. Correct results: net −70, maximum realised decline 270, drawdown fraction 0.00135 (0.135%), return −0.035%. Excluding the fixture's Monday Asia trade leaves +200, −150: net +50 and drawdown 150 / 200,000 = 0.075%; excluded P&L −120 reconciles exactly.

A separate synthetic 15,277-row cache/performance fixture is **not** the exact example. No fixture uses the user's run ID or masquerades as market/production evidence. The regression suite also covers 6,201 persisted rows, entry filtering, empty populations, winners-only/losers-only/zero P&L, missing/zero capital, negative equity, tied exits, invalid timestamps, cross-connection cache invalidation, missing raw trades, authoritative local/AI stars, transactional deletion, cancellation and database-lock release.

## Native read-only verification still required

After deploying this exact release through the normal safe update procedure (preserve unrelated edits, DATA, DB/WAL/SHM/backups and failed-preflight evidence), use the **existing** backend. Do not rerun the backtest. In PowerShell:

```powershell
$Api = (Read-Host 'Existing backend base URL, e.g. your normal dashboard backend').TrimEnd('/')
$Run = 'dt-9edaa1dcbd0d42e7'
$Out = Join-Path ([IO.Path]::GetTempPath()) ('ETL-v105-reconcile-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $Out | Out-Null
$Detail = Invoke-RestMethod "$Api/api/deeptest/run/$Run"
$Detail.scenario | Select-Object run_id,node_id,status,coverage_status,bars_used,metric_provenance
Invoke-WebRequest "$Api/api/deeptest/run/$Run/export.csv" -OutFile (Join-Path $Out 'all-trades.csv')
Invoke-WebRequest "$Api/api/deeptest/run/$Run/metrics.json" -OutFile (Join-Path $Out 'original-metrics.json')
$Rules = @{schema_version=1;timezone='UTC';exclude_by_day=@{mon=@('asia')}} | ConvertTo-Json -Depth 8
$Filtered = Invoke-RestMethod -Method Post "$Api/api/deeptest/run/$Run/filtered" -ContentType 'application/json' -Body $Rules
$Filtered | ConvertTo-Json -Depth 100 | Set-Content (Join-Path $Out 'filtered-metrics.json')
$Detail.scenario.settings | Select-Object total_capital,initial_capital,account_currency,currency
$Filtered.trade_counts
$Filtered.original.summary
$Filtered.filtered.summary
$Filtered.excluded.summary
```

A 404 means the run is absent in that backend: **stop and report it**, do not substitute another node/run. Resolve the canonical node only from this response. Check metric version `V10.5-realised-initial-v1`, source run ID, capital source and currency, snapshot hashes/counts, PARTIAL coverage and N/A reasons. Compare table and Inspect against `original.summary` for this exact run and snapshot. Sum all CSV net P&L independently; sort closes by the documented keys and recompute the running peak/decline from the stored capital. Verify original P&L = included + excluded and membership = the chosen versioned rule, rather than assuming 691 excluded rows. Preserve all output locally outside DATA; do not publish private raw trades, credentials or account information.

Native exact-run reconciliation, canonical node resolution and visual/native shutdown checks remain **unverified** until those real records are available. This report does not declare them complete.
