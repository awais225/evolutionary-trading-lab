# V9.2 MT5 history and GitHub data audit

## Intentional data layout
- Exact seven user filenames/bytes: `DATA/HISTORICAL/baseline/XAUUSD_{M1,M5,M15,M30,H1,H4,Daily}.csv`, Git LFS with `-text` (no newline normalization).
- Provenance/counts/timezone/coverage receipts: `DATA/HISTORICAL/manifest.json`.
- Authoritative operational bars: `DATA/XAUUSD/raw/<TF>/USER_HISTORY_ICMARKETS_CANONICAL/YYYYMM.parquet` and the7 corresponding metadata JSON files. Existing monthly-partition time indexes prune range reads; SQLite remains the dataset/provenance registry, not a new competing candle database.
- Runtime user imports retain their original bytes in `DATA/HISTORICAL/imports/<sha256>/<filename>`, with a provenance receipt. Runtime fetch status/recovery material is not versioned.

Old SIMULATOR/MT5 profile stores are preserved. They are not silently reinterpreted under the newly confirmed timezone. These configured7 pairs resolve to the canonical baseline for Overview inventory and Deep/scenario reads independent of active bridge. A canonical dataset snapshot is resolved by its exact ID, never by a generic recent MT5/TF cache. CSV snapshots do not overwrite the old MT5 convenience parquet files.

## FETCH LATEST MT5 DATA
Overview and Deep mount the same component, endpoints and single keyed job. All7 or one TF can be selected. Only a real bridge with an IC Markets broker/server identity may update this broker baseline; simulator and unrelated/unknown broker identities are refused. This check does not switch account, initialize Live workers, enable trading, start generation or call an order interface.

The range starts at the latest stored timestamp minus two bars, never at the beginning of the baseline. Only closed native-UTC bars are accepted. Requests are bounded to5000 bars, retry at most3 times, adaptively walk unreturned head/tail portions of capped successful responses, report rejected/in-range/added/corrected/identical counts, per-chunk bounds and persisted coverage. Tests include more than2683 and6000 new bars, first/last2683 caps, dedupe and pair isolation. Independent empty/error chunks remain explicitly unresolved; the existing history is not deleted on a bridge failure. Cancellation stops between native calls. A native MT5 call cannot be forcibly interrupted; the sandbox cannot verify its terminal latency.

CSV conflicts preserve incumbent values and record discrepancies. Verified MT5 correction overlap replaces differing closed bars with old/new numeric provenance and an explicit `trusted_mt5_correction` resolution. Original CSV files never change. Store writes and range reads serialize across threads/processes by canonical path, back up only touched months and use an interruption-recovery journal. A failed metadata/partition write rolls back; an interrupted transaction is recovered before store use. No artificial candles or OHLC interpolation are introduced.

## Reconstruction / ongoing synchronization
Install Git LFS and run `git lfs pull` after cloning. The committed normalized partitions and metadata can be queried immediately; startup does not parse the CSV files. If deliberately reconstructing the normalized baseline from the original objects, run:

```
python backend/tools/import_csv_baseline.py --directory DATA/HISTORICAL/baseline --source-timezone ICMARKETS_NY_CLOSE --confirm-source-timezone
```

The explicit confirmation is required. It validates all seven inputs before routing/writing, refuses a different existing baseline timezone, and records per-file results. It does not connect a terminal or trade.

Fetches persist locally, not to GitHub automatically. Review counts, coverage, corrections and intentional changed partitions before manually staging the relevant metadata and LFS data, committing and pushing. Never stage account settings, credentials, runtime databases, logs, derived feature caches or unrelated data. The release includes only the attached CSV baseline and normalized baseline state, **not claimed real-terminal fetch results**. Independent GitHub LFS re-download verified377 objects (46,370,888 bytes):7 exact CSVs and370 canonical monthly parquets. The7 additional normalized metadata JSONs and the historical manifest are ordinary Git files. Final branch publication and targeted fresh-clone query verification are recorded in the main report.

## Environment limits
Linux has no MetaTrader5 Python package/terminal. Real IC Markets retrieval, terminal chart-limit effects, Windows launchers/Edge rendering and live order execution were not exercised. Mock bridge tests prove the code paths and safety isolation, not real-terminal availability. No real order was submitted.


### Concurrency and immutable snapshot details
POSIX `flock` and standard-library Windows byte locks protect each store. Nested calls are thread-reentrant; forked workers reset inherited Python locks and close inherited lock descriptors before using their own lock. The Windows branch exists but native Windows locking has not been exercised here. An active writer cannot be mistaken for an abandoned transaction by another worker's constructor. Existing per-version canonical snapshot IDs remain immutable as the store version advances; materialization obtains the same store lock, refreshes metadata first and rebuilds an absent cache snapshot from the corresponding current persistent version. It never substitutes the unrelated generic MT5 cache. Mock/native-cap tests do not represent an actual IC Markets connection.

## Completed native fresh-clone check
At 2026-10-16T14:08:00Z, a new GitHub clone of main `4c75059198ab0a1fa51cadf21d29c35eb8ebb125` pulled the targeted baseline through native git-lfs. All377 hashes, all7 row counts and the4,131-bar3-day M1 query passed. This verifies the newly published baseline/source subset, not a complete re-download/startup of every legacy project dataset. No local pre-existing CSV/parquet was substituted for the remote objects. The final documentation-only evidence push is separately verified against remote main.
