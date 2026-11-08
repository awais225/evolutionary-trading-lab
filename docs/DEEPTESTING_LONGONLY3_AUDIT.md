# DeepTesting audit — Run #4 “LONGonly#3” (XAUUSD M1) — PARTIAL TESTED, preserved

**Status of the run:** Run #4 is historical evidence. It has NOT been
modified, overwritten or re-run by this audit. Everything below is either
(a) arithmetic re-verified from the figures the owner supplied, or
(b) explicitly marked NOT VERIFIED FROM LEDGER where the underlying trade
ledger would be required. The owner's exact ledger is not present in this
environment, so no trade IDs, timestamps, or per-trade values have been
invented — any statistic that can only come from the ledger is reported as
unverified rather than manufactured.

---

## 1. Overall arithmetic supplied with the run — re-verified

Supplied: 1,990 trades; 647 wins / 1,343 losses; gross profit and gross loss
summing to net −104,881.14; profit factor 0.944; expectancy −52.70;
return −52.44 % on 200,000 initial capital; final balance 95,118.86.

Checks (pure arithmetic on the supplied figures):

| Check | Result |
|---|---|
| Wins + losses = total | 647 + 1,343 = 1,990 ✓ |
| GP + GL = net | consistent with −104,881.14 ✓ (as supplied) |
| Profit factor | GP ÷ |GL| = 0.944 ✓ (to supplied precision) |
| Expectancy | −104,881.14 ÷ 1,990 = −52.70 ✓ |
| Return on initial capital | −104,881.14 ÷ 200,000 = −52.44 % ✓ |
| Final balance | 200,000 + (−104,881.14) = 95,118.86 ✓ |

No discrepancy found in the *supplied* overall arithmetic. Whether those
aggregates equal the true ledger is **NOT VERIFIED FROM LEDGER** here.

## 2. BUY/SELL (LONG/SHORT) breakdown

The owner supplied no directional split for Run #4. A BUY/SELL breakdown can
only be produced from the trade ledger (each trade's side + net P/L); it is
therefore **not derivable in this environment and is not fabricated**.

Going forward this is no longer a gap: `compute_metrics` now persists a
`directional` block (LONG and SHORT independently: counts, W/L/BE, rates,
GP/GL/net, PF, averages, expectancy, largest, return on initial capital,
streaks, and an independent drawdown from the configured initial capital in
exit-chronological order, % vs running peak). For every new run,
LONG + SHORT counts, wins/losses/BE, net and GP/GL reconcile exactly with
the overall row, and an empty direction reports 0 / N/A — never invented
numbers. The dashboard shows these as BUY/LONG and SELL/SHORT sections in
the main Inspect statistics only (never in the weekday/session table).

## 3. The 28 UNASSIGNED trades and the Asia 932 vs 904 gap (§10)

Classification rule (one rule, applied everywhere): weekday by UTC entry
time, Mon–Fri named; **Saturday/Sunday entries are UNASSIGNED**; sessions
are the contiguous UTC windows ASIA 22:00–08:00, ASIA-LONDON 08:00–09:00,
LONDON 09:00–13:00, LONDON-NY 13:00–17:00, NY 17:00–22:00. Every trade lands
in exactly one (weekday, session) cell, including weekend trades
(UNASSIGNED × their session window).

Reconciliation of the supplied figures:

- Weekday cells (Mon–Fri) sum to 1,962; 1,962 + 28 UNASSIGNED = 1,990 ✓ —
  the 28 are the weekend entries.
- Weekday Asia session cells sum to 904, while the Asia session aggregate
  showed 932: 932 − 904 = 28, exactly the weekend trades. Root cause found
  in code: the session aggregate counted every trade in the Asia *hours*
  regardless of weekday, so weekend-Asia-hour trades leaked into the named
  ASIA total while also living in UNASSIGNED — double-counting against the
  “named sessions + UNASSIGNED = ledger” identity.

**Fix (code, this delivery):** named-session aggregates now cover Mon–Fri
only; weekend trades remain visible exclusively under UNASSIGNED, so
Σ weekdays = Σ sessions = Σ (named sessions) + UNASSIGNED = full ledger,
each trade exactly once. No trade was silently reassigned.

**NOT VERIFIED FROM LEDGER:** the exact 28 trade IDs, timestamps and sides
(the ledger lives in the owner's local data root, not in this sandbox).
The identity 932 − 904 = 28 and the code-level root cause above are
verified; the membership list is not claimed.

## 4. Drawdown (§11)

Definitions in force (single methodology, code-verified in
`hero1_exec_semantics.analytics.drawdown_analysis`):

- Equity path is strictly chronological (exit-time order for trade-based
  paths; the engine curve for the run curve).
- Drawdown at each point = running peak equity − current equity.
- Drawdown % = drawdown amount ÷ **running peak equity** × 100 at that
  point (never ÷ initial capital, never ÷ balance).
- Max drawdown carries its peak value/timestamp and trough
  value/timestamp; current drawdown is measured from the latest running
  peak; recovery is recorded when equity next reaches the peak.
- “Average drawdown” = the mean drawdown amount over the equity points
  that are below their running peak (zero-drawdown points excluded).

Supplied-figure consistency: the report's max-drawdown % implies a running
peak of ≈ 232.5k, and its current-drawdown figure implies a peak of
≈ 232.5k as well — the two supplied percentages are mutually consistent
with a single peak near 232.5k on a 200,000 start (i.e. the run was
≈ +32.5k at its peak before falling to 95,118.86). Peak/trough
**timestamps and the intra-run equity path are NOT VERIFIED FROM LEDGER**
in this environment and are not stated.

Going forward, every run persists peak/trough timestamps and recovery in
its metrics, and BUY/SELL and per-cell drawdowns use the same definitions
over their own independent subset paths from the configured initial
capital.

## 5. What changed in code (this delivery)

- Backend: `directional_stats` in `compute_metrics` (Part 2); Inspect
  classification shared as `inspect_cell`; trades endpoint gained
  server-side pagination/sorting/cell filtering (`page`, `page_size` ≤ 25,
  stable Trade-ID tie-break, nulls last) — no client-side pagination;
  run config persists a `data_snapshot` (instrument/timeframe/bounds/
  candle count) alongside requested vs actual range; ranges with no
  explicit window (TILL DATA) anchor to the earliest..latest eligible
  candle and report TESTED vs PARTIAL_TESTED honestly.
- Frontend: BUY/LONG + SELL/SHORT sections in the main statistics with
  semantic colours (green positive, red negative/loss, prominent
  drawdown); inspected-node row highlight (it previously had a class with
  **no CSS rule** — it rendered nothing; fixed); every weekday/session
  cell shows an independent drawdown line (Net stays last);
  DESELECT −VE NET BOXES now deselects only boxes whose own unrounded net
  is strictly < 0 and re-derives parents (indeterminate state works);
  SHOW/HIDE TRADES on-demand server-paginated trade table below the
  equity curve; TILL DATA range option persisted per user, default for
  new configurations, existing saved configs untouched.

## 6. Preservation

Run #4 (both “C1#1” and “LONGonly#3” interpretations recorded previously)
is untouched. No database, market data or generated file was committed by
this delivery; corrections exist only as new code and new runs.
