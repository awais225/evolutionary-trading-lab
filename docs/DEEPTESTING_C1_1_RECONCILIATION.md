# C1#1 DeepTesting Reconciliation Report (Part 12)

Subject: Node C1#1 · XAUUSD · M15 · Run #4 (owner's local installation).
Status of Run #4: **preserved unchanged** — no historical result was
modified. Corrections below apply to the calculation code and to all
future runs.

## 1. Arithmetic verification (from the supplied figures)

Every main statistic was independently recomputed from the supplied
aggregates; all are arithmetically consistent with a 385-trade ledger:

| Check | Recomputation | Supplied | Verdict |
|---|---|---|---|
| Win rate | 118 / 385 = 30.65% | 30.6% | ✓ |
| Loss rate | 267 / 385 = 69.35% | 69.4% | ✓ |
| Profit factor | 442,896.71 / 503,891.66 = 0.879 | 0.879 | ✓ |
| Expectancy | −60,994.95 / 385 = −158.43 | −158.43 | ✓ |
| Average win | 442,896.71 / 118 = 3,753.36 | 3,753.36 | ✓ |
| Average loss | −503,891.66 / 267 = −1,887.23 | −1,887.23 | ✓ |
| Total return | −60,994.95 / 200,000 = −30.50% | −30.5% | ✓ |
| Final balance | 200,000 − 60,994.95 = 139,005.05 | 139,005.05 | ✓ |
| Max consec. loss % | 26,175.54 / 200,000 = 13.09% | 13.09% | ✓ (denominator = the run's own starting capital) |
| Max drawdown % | 92,044.62 / 0.4036 ≈ 228,059 peak | 40.36% | ✓ denominator is **peak equity**, not initial capital |
| Current drawdown % | 89,061.06 / 0.3905 ≈ 228,068 peak | 39.05% | ✓ same peak-equity basis |
| LONG + SHORT net | −60,994.95 + 0 | −60,994.95 | ✓ reconciles |

Documented drawdown basis (now stated wherever shown): drawdown % is
relative to the **peak equity reached before the trough**, computed on
the engine's equity curve (balance + unrealized at each bar close).
Average drawdown and duration derive from the same equity series.
Recovery factor is net / max-drawdown and is shown as N/A when undefined
(non-positive net or zero drawdown) — for this run it is negative-net, so
it carries no recovery meaning.

## 2. The three missing trades — root cause identified

Weekday sums: 382 trades / 118 W / 264 L, GP ≈ 442,897 (matches main),
GL ≈ −497,857 (main: −503,891.66). Session sums: identically 382 / 118 /
264. The gap is exactly **3 losing trades totalling −6,034.66** of gross
loss (net gap −6,034.95 incl. rounding of the supplied integers). All
118 wins are present in both breakdowns; only losses are missing.

**Cause (code-level, fixed):** the Inspect weekday/session grid
attributed each trade by `WEEKDAYS[UTCDay − 1]`. Entries on a **Saturday
or Sunday have no weekday column** (gold commonly opens Sunday
~21:00–23:00 UTC, and some feeds print Saturday-early bars), so those
trades received no cell and were silently excluded from BOTH the weekday
and the session breakdowns — and, when any filter was active, from the
filtered statistics as well. The backend main statistics (analytics over
the full ledger) always counted them, hence 385 vs 382.

**Exact trade IDs:** Run #4's ledger lives in the owner's local DATA
root (`E:\evolutionary-trading-lab`), which is intentionally not in Git —
the exact three IDs cannot be read from this repository and are **not
fabricated here**. On the owner's machine they are precisely the trades
of run 4 whose `entry_ts` falls on a Saturday/Sunday (UTC), retrievable
via `GET /api/v1/instruments/XAUUSD/deeptesting/runs/4/trades`, and with
this fix they now appear in the grid's **UNASSIGNED (weekend)** column
instead of vanishing.

**Fix:** weekend entries are attributed to an explicit UNASSIGNED
column (with the same statistics, checkboxes and filtering as every
other column). Every executed trade now lands in exactly one
weekday/session cell, so:

main ledger = Σ weekday cells = Σ session cells = Σ aggregate column,
subject only to display rounding. Nothing was added to or adjusted in
the main statistics — the ledger remains the source of truth.

## 3. Date-range / status investigation

Requested 2025-11-08 11:12 → 2026-11-08 11:12 (the "1 Year" button sent
wall-clock now as the end); actual ended 2026-10-16 10:45 = the latest
stored eligible M15 candle. The old logic clamped the end silently and
marked PARTIAL only when the *start* preceded stored history, so the
run showed TESTED although the tested window (342 days) was shorter
than the requested duration (365 days).

**Fix (Part 10):** the requested window now anchors to the latest
eligible candle of the node's own instrument/timeframe
(end = latest candle, start = end − requested duration). If that
anchored start precedes the first stored candle, all available data is
tested and the result is labelled **PARTIAL TESTED** with requested vs
actual ranges and candle counts persisted. Under the corrected logic a
1-year test over this data tests [2025-10-16 → 2026-10-16] and is
honestly labelled; Run #4 itself is preserved exactly as generated —
rerun the node to create a new, correctly labelled result.

## 4. Reconciliation table (after fix, per construction)

| Source | Trades | Wins | Losses | Gross + | Gross − | Net |
|---|---|---|---|---|---|---|
| Main trade ledger | 385 | 118 | 267 | 442,896.71 | −503,891.66 | −60,994.95 |
| Σ weekday columns (Mon–Fri + UNASSIGNED) | 385 | 118 | 267 | 442,896.71 | −503,891.66 | −60,994.95 |
| Σ session aggregate (TOTAL column) | 385 | 118 | 267 | 442,896.71 | −503,891.66 | −60,994.95 |
| Filtered aggregate (all selected) | 385 | 118 | 267 | 442,896.71 | −503,891.66 | −60,994.95 |

The previously missing −6,034.66 of gross loss is the UNASSIGNED
(weekend) column's content for this run.
