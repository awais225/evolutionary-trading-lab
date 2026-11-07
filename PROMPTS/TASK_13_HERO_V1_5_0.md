# TASK 13 — HERO-V1.5.0 prompt archive

> Archive note: this file preserves the TASK 13 brief from the session task
> record after an automatic context compaction. It is a faithful task-record
> reconstruction, not a byte-exact transcript of the original message.

## Objective

Complete the Windows MT5 integration, canonical market-data deployment path,
incremental FETCH DATA, and node-metadata activation for HERO-V1.5.0 without
rebuilding the verified TASK 12 MT5 adapter or CSV importer.

## Required work

1. Start from `a84279646421f883707ceaada6450898b4eccb88`; never overwrite or
   move tags; produce annotated tag `HERO-V1.5.0` only as awaiting Windows
   acceptance unless genuine Windows acceptance is proven.
2. Audit the Windows failures: missing `MetaTrader5` module despite terminal
   discovery, browser not auto-opening, and unavailable node metadata. Do not
   dismiss Windows failures because Linux tests pass.
3. Install and verify `MetaTrader5` in the exact backend virtual environment;
   provide an interpreter-exact diagnostic.
4. Discover, detect, launch, initialize, and connect the terminal with explicit
   states including `CONNECTED` / login-required and `ACCOUNT_LOADED`. Never
   store credentials; login occurs inside MT5.
5. Show account identity, broker/server, currency, balance, equity, margin,
   free margin, margin level, leverage, profit, and permissions. Keep terminal
   selection isolated to Live Testing.
6. Deploy the verified 419,013 XAUUSD candles (M1–D1) through the existing
   importer into the resolved canonical `DATA/MARKET_DATA` store. Back up an
   existing store, preserve the single database/manifest source of truth, and
   give exact Windows commands if Windows DATA cannot be reached from Linux.
   Do not delete scratch data before canonical deployment is verified.
7. Retrieve real broker/server XAUUSD metadata and use legacy-verified sizing
   and the IC Markets GMT+2/GMT+3 US-DST server-time rule. Never assume a
   universal pip or retro-shift stored server timestamps.
8. Add FETCH DATA with ALL/single-timeframe selection, timezone-aware UTC
   `copy_rates_range`, incremental append, validation, duplicate/conflict,
   incomplete-candle, transaction, manifest, and per-timeframe reporting rules.
9. Trace node metadata availability end-to-end. Do not weaken
   `NEEDS_METADATA`; historical OHLC is not broker metadata.
10. Fix START.bat stage-10 browser opening without duplicate tabs and without
    failing healthy services.
11. Append verified ISSUE-041..044 entries to root `TROUBLESHOOTING.txt`;
    label Windows-pending items honestly.
12. Document custom indicators only (FVG example); no `eval`/`exec` or
    arbitrary code loading.
13. Preserve the dark/amber UI and custom dropdowns; no native selects and no
    unrelated redesign.
14. Run backend, frontend, build, launcher, and targeted mocked tests. Mocked
    MT5 tests must never be represented as real Windows integration.
15. Never commit CSVs, SQLite contents, credentials, or personal account data.
16. Update all project records and provide a 30-point final report with each
    item labeled Linux verified / Windows verified / Mocked test / Static
    audit only / Not yet verified.

## OUTCOME (2026-11-07, HERO-V1.5.0, implementation 78f63ae96)

Implementation, Linux tests, mocked MT5 tests, static launcher audit, and a
fresh Linux CSV-import trial are complete. Backend 188 passed / 1 skipped;
frontend 44/44; build clean. The annotated HERO-V1.5.0 tag is explicitly
awaiting genuine Windows acceptance; no real terminal/account/metadata/fetch
or Windows browser result is claimed.
