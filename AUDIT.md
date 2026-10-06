# Technical Audit — Evolutionary Trading Research Lab (V1, pre-V2)

Date: 2026-09-25 · Method: direct reading of current source (no README claims).
Every answer names the responsible file/function. Verdict legend:
✅ implemented · ⚠️ partial / caveats · ❌ not implemented.

## Section 1 — Data persistence (Q2–Q14)

**Q2. Is ALL MT5 historical data saved permanently to disk?** ⚠️
Bar history: YES. `data/engine.py:DataEngine.ingest()` downloads a window once per
(symbol, timeframe, date-window, source) and writes it via
`data/storage.py:write_bars()` to Parquet, with a registry row in SQLite `datasets`.
Tick history: NO (see Q18). Live/forming bars: NO — they exist only in memory
(`paper/engine.py:_live_frame()` fetches a transient 400-bar frame each cycle).

**Q3. Where is it stored?**
- Raw bars: `<project>/data_cache/{dataset_id}.parquet`
- Feature arrays: `<project>/data_cache/features/{dataset_id}.parquet`
- All research state: `<project>/lab_state.db` (+ `-wal`, `-shm`)
- Logs: `<project>/lab.log` (`logging_setup.py:setup_logging`, hardcoded `ROOT_DIR`)
- Config: `<project>/config/lab_config.yaml`
`dataset_id = {symbol}_{tf}_{start:%Y%m%d}_{end:%Y%m%d}_{source}`
(`data/engine.py:make_dataset_id`).

**Q4. Guaranteed inside the project directory?** ✅ by default.
`ROOT_DIR = Path(__file__).resolve().parents[2]` (`config.py:20`); defaults for
`cache_dir` and `database_path` are `ROOT_DIR`-based, and relative yaml values are
resolved against `ROOT_DIR` in `config.py:load_config`. The user *can* point them
outside via yaml — that is a choice, not a default.

**Q5. Move the whole folder to another drive/PC — does data move with it?** ✅ with one caveat.
Everything (db, parquet, config, log) lives under the project root and relative
paths resolve on load. Caveat: `config.py:save_config` writes the *resolved absolute*
paths back into the yaml, so after the first Settings-page save on machine A the yaml
carries machine-A absolute paths. Moving then still works only if those paths exist.
(V2 fix: re-relativize paths under ROOT_DIR before saving.)

**Q6. Anything important stored outside the project?** ✅ No (by default).
Only Python/npm caches (irrelevant, gitignored). Worker processes are explicitly
blocked from writing the shared feature cache (`orchestrator/workers.py:init_worker`,
env `LAB_NO_FEATURE_PERSIST`, checked in `features/engine.py:_persist`).

**Q7. Single configurable DATA root?** ⚠️ Partial.
`data.cache_dir` is the de-facto data root (bars + features live under it), but the
SQLite db is a separate `database_path`, the log is hardcoded to ROOT_DIR, and there
is no single "DATA/" concept.

**Q8. Changeable from Settings?** ❌ Not currently.
The Settings UI data section exposes only `symbol`, `timeframes`, `enabled_symbols`
(`frontend/src/pages/Settings.jsx` SECTIONS). `cache_dir`/`database_path` are
yaml-editable only. (`PUT /api/settings/data` would accept `cache_dir` — the backend
`update_config` is generic — but the UI does not offer it, and moving existing data
is not handled.)

**Q9. Raw data stored separately from features?** ✅
Different files, different directories, different write paths: `write_bars` (raw,
written once per dataset id) vs `write_feature_cache`/`FeatureEngine._persist`
(features-only file). No code path writes indicator values into a raw file.

**Q10. Is raw preserved so features can be recomputed later?** ✅
Raw snapshots are immutable (a dataset id is written once; `ingest` returns early on
cache hit). Features are derived on demand from `get_frame(dataset_id)` at any time
(`features/engine.py:get` → `_compute_spec`). Deleting `data_cache/features/` is a
safe full-rebuild path.

**Q11. Compressed?** ✅ Parquet with pyarrow's default codec (snappy); feature files
additionally downcast to float32 (`storage.py:write_feature_cache`).

**Q12. Raw format?** Parquet, one file per dataset, columns fixed by
`storage.py:COLUMNS` = ts, open, high, low, close, bid, ask, spread, tick_volume,
session, dow, dom, hour, minute (written via `pandas.to_parquet(index=False)`).

**Q13. Feature format?** Parquet wide-table, one file per dataset
(`features/{dataset_id}.parquet`), one float32 column per feature output name
(e.g. `rsi:14`, `bb_upper:20:2.0`), row-aligned with the raw file.

**Q14. Why this format?** Columnar + compressed (typically 3–6× smaller than CSV),
near-instant vectorized reads through pandas/pyarrow, immutable single-file snapshots
make every dataset content-addressable by id (reproducibility), and DuckDB can query
the files in place without loading them (`storage.py:duckdb_query` — implemented as a
utility, currently *unused* by the pipeline). SQLite handles the small, relational,
transactional side (state, indexes).

## Section 2 — Directory structure

**Equivalent to the proposed DATA/RESEARCH/DATABASE/LOGS tree?** ❌ Not structurally.
Current layout is flat:

```
evolutionary-trading-lab/
  data_cache/                      # raw bars  {dataset_id}.parquet
    features/                      # features  {dataset_id}.parquet
  lab_state.db (+ -wal/-shm)       # all research state (SQLite)
  lab.log
  config/lab_config.yaml
```

What exists conceptually vs your tree: per-symbol grouping ❌ (symbol is in the file
name, not the path); `metadata/` → lives in SQLite `datasets`/`backtests`/etc. rather
than files; `RESEARCH/strategies|generations|backtests|validations|paper_trading|
hypotheses|experiments` → all present as SQLite *tables* (strategies,
generation_stats, backtests, validations, paper_trades+executions, hypotheses) but
with **no human-readable file export**; `DATABASE/` and `LOGS/` → single files at root.

**What would need to change:** (1) a `paths.py` layout module + config keys for
`data_root`, `research_root`, `db_dir`, `log_dir`; (2) `storage.dataset_path` /
`feature_path` to build `DATA/{symbol}/raw|features/…`; (3) `logging_setup` to use
the configured log dir with rotation; (4) a new **export layer** that mirrors SQLite
research rows into `RESEARCH/…` JSON/Parquet files (see Sections 22 & 37). None of
this touches the engines — it is plumbing plus one new module.

## Section 3 — Incremental MT5 ingestion

**Does it exist?** ❌ NO — and this is the single most important gap for V2.

Exact current behavior (`data/engine.py:ingest`):
1. `end_ts = time.time()`, `start_ts = end_ts − months·30d` (window from config
   `data.history_months` per timeframe).
2. `did = make_dataset_id(...)` — **the calendar date is baked into the id**
   (`%Y%m%d`).
3. `SELECT * FROM datasets WHERE id=?` + parquet-exists check → **cache hit only for
   the same day**. Restarting later the same day: no download ("already cached" log).
4. Any next day: new id → miss → `bridge.copy_rates_range(symbol, tf, start, end)`
   re-downloads the **entire configured window** → writes a **new full snapshot
   parquet**. Old snapshots are never updated, never merged, never deleted.

There is no "last stored timestamp" lookup, no delta request, no append, no merge.
Your required flow (check stored → find max ts → request only missing → append →
dedupe → update features → continue) is **not implemented in any form**.

**What must be built (V2):** a persistent *master store* per
(symbol, tf, source, server) — e.g. `DATA/XAUUSD/M15/master.parquet` or partitioned
by month — with `SELECT MAX(end_ts)` from the registry; ingest becomes
`copy_rates_range(last_ts + tf_step, now)` → validate → dedupe on ts → append →
rewrite only the touched partition. Dataset *ids used by experiments* should become
(start, end) **views/slices** of the master so reproducibility is preserved while
storage stays incremental. Cost note: on the simulator the current full re-download
is ~1.5 s/dataset (harmless); against real MT5 with multi-year M1 it would be
minutes–hours **every day** — unacceptable, which is exactly why you flagged it.

## Section 4 — Duplicate data protection

- Duplicate (symbol, timeframe, timestamp) detection: ❌ none — because there is no
  append path at all. Each file is a whole-window snapshot written once.
- Same historical range requested twice, **same day**: ✅ safe — `ingest` cache hit,
  no second download, no second file.
- Same range requested on **different days**: two distinct dataset files with
  heavily overlapping content (no dedupe across files; disk grows by one full
  snapshot per operating day).
- MT5 returns overlapping data: ❌ never merged, so overlap can't corrupt a file —
  but that's accidental safety, not a dedupe mechanism.
- Within one file: duplicates are impossible by construction (single full-range
  DataFrame write; simulator series and `copy_rates_range` slices are strictly
  monotonic). For real MT5, no monotonicity/duplicate check is applied to whatever
  the terminal returns (see Section 31).
- If incremental append is added (Section 3), dedupe on ts + bar-equality checks
  becomes mandatory — it does not exist yet.

## Section 5 — Data versioning per experiment

Stored per backtest row (`db/database.py` schema, written by
`orchestrator/lab.py:_screen_batch/_detail_batch`):

| Item | Stored? | Where |
|---|---|---|
| Symbol, timeframe | ✅ | via `dataset_id` + strategies.symbol/timeframe |
| Dataset start/end | ✅ | `datasets.start_ts/end_ts` + `backtests.window` JSON |
| Data source | ✅ | `dataset_id` suffix + `datasets.source` (SIMULATOR/MT5) |
| Broker/server | ❌ | not recorded anywhere in dataset identity |
| Data version (content hash) | ❌ | no checksum of the parquet |
| Feature version | ❌ | none exists (Section 7) |
| Genome version | ⚠️ | full genome JSON + `genome_hash` in strategies and in `metrics` — content-addressed, but no schema-version field |
| Backtester version | ❌ | app version "0.1.0" exists only in `main.py` FastAPI metadata; never stamped into results |
| Cost/slippage model version | ❌ | assumptions are *echoed* in metrics (`avg_slippage_points`, `total_spread_cost`, stress multipliers in `backtests.params`) but the model has no version id |
| Configuration version/snapshot | ❌ | config at run time is not captured |

So: an experiment is reproducible **only while the code and config stay unchanged**
(the backtester itself is deterministic — Section 26). "Old results remain
reproducible after the backtester changes" is **not** guaranteed or even detectable
today, because nothing records which engine produced a row.

## Section 6 — Feature cache (exact behavior)

Engine: `features/engine.py:FeatureEngine`. Layers: in-memory LRU
(`MAX_CACHE_ENTRIES=512`, keyed `(dataset_id, output_name)`) + on-disk parquet
(`features/{dataset_id}.parquet`, float32). Strategies never compute indicators —
the backtester pulls arrays via `get()/get_many()` (`backtest/engine.py` uses the
feature engine exclusively).

Your scenarios, honestly:
- **Restart with EMA20/EMA50/RSI14/ATR14/ADX14/VWAP already cached:** mixed.
  Non-core specs are loaded from disk and reused exactly (`_load_disk_cache`).
  BUT at startup `orchestrator/lab.py:_precompute_features` **unconditionally
  recomputes the 21 CORE_FEATURE_SPECS into memory for every dataset** even when the
  disk cache exists (0.3–3 s per dataset — visible as "precomputed core features"
  log lines). `_persist` then skips columns already on disk, so the file isn't
  rewritten — but the compute *is* repeated. Redundant, bounded, and worth fixing
  (check disk cache first).
- **Add a new indicator:** ✅ only that spec is computed (`get` miss →
  `_compute_spec`), merged as new columns into the existing feature file
  (`_persist` keeps existing columns untouched).
- **RSI14 → RSI16:** ✅ separate spec keys → RSI16 computed alone; RSI14 retained
  on disk and in cache.
- **New timeframe:** ✅ new dataset id → its own raw + feature files; other
  timeframes untouched.
- **New market data tomorrow:** ❌ the whole feature set is recomputed for the *new
  full-window dataset* (because ingestion creates a new snapshot — Section 3). There
  is no "compute indicators only for appended bars" path. Indicator warm-up (e.g.
  EMA over prior bars) makes prefix-reuse feasible but it is not implemented.

## Section 7 — Feature cache invalidation

❌ **Not implemented.** There is no `FEATURE_VERSION`, no hash of
`features/library.py` algorithms, no provenance metadata in the feature parquet.
Cache validity is decided purely by *column name* — if you change the RSI algorithm
in `library.py`, cached `rsi:14` columns are **silently reused** (stale). The only
safe procedure today is manual: delete `data_cache/features/` (they rebuild from
immutable raw). V2 needs: a sidecar/index storing `{column: (engine_version,
code_hash, computed_at)}` and invalidation of only the affected columns on mismatch.

## Section 8 — Strategy duplicate testing

✅ Implemented, and stronger than "reference the existing result":
`genome/schema.py:canonical()` serializes with `sort_keys=True, separators=(",",":")`
→ **JSON key ordering is normalized** (unit-tested:
`test_genome.py::test_hash_deterministic_and_order_free`).
`genome_hash = sha256(canonical)[:32]`; `strategies.hash` is `UNIQUE`;
`evolution/engine.py:try_insert()` calls `db.find_by_hash(h)` first — a duplicate is
**never inserted, never backtested**; the counter `duplicates_blocked` increments
and the parent keeps the existing individual. So your #734 would never exist; #100
already holds the result.

Limit: only *structural* JSON identity is detected. Logically equivalent genomes
that differ structurally (e.g. `and`-clause lists in a different order, `x>25` vs
`x>=25.0001`) hash differently and *would* be tested twice. Clause-order
canonicalization (sorting commutative clauses) is a cheap V2 improvement.

## Section 9 — Backtest result persistence

✅ Permanent, per-stage, in SQLite (`backtests`, `validations`, `matrices`),
committed immediately per row (`db.x` commits every statement; WAL).
Recoverable after a Windows restart: strategy id, genome (full JSON), parent,
generation, status, fitness, complexity, creation_reason, mutation_type, species,
dataset id + exact window, trades count, net/gross profit/loss, profit factor
(+ raw), max drawdown %, total return %, Sharpe, Sortino, win rate, expectancy,
consistency, avg hold bars, exit-reason histogram, monthly PnL, final equity,
runtime, stress results, and the last **300 trades** (`metrics.trades_sample`).
Validation battery (OOS, walk-forward, perturbation, spread/slippage stress, Monte
Carlo, regime holdout, robustness score, pass/fail, death reasons) is stored whole
in `validations` (one row per strategy).

Not persisted: **equity curves** (workers return the last 400 points,
`orchestrator/workers.py:bt_worker`, but the orchestrator discards them) and trade
lists beyond the 300-trade sample. Dashboard recovers everything above via
`GET /api/strategies/{sid}` after restart ✅ (verified live).

## Section 10 — Backtest reuse

⚠️ Reuse exists but the key is **existence-based, not content-hash-based**:
- `_detail_batch`: selects `SURVIVED` strategies where
  `NOT EXISTS (backtests WHERE strategy_id=? AND stage='detail')`.
- `_validation_batch`: `NOT EXISTS (validations WHERE strategy_id=?)`.
- `_specialization_pass`: `NOT EXISTS (matrices WHERE strategy_id=?)`.

So after a restart, completed stages are skipped and only pending work runs ✅.
Effective identity = (strategy_id ↔ unique genome hash, stage), with the dataset
being "latest at run time" (recorded in the row as `dataset_id` + `window`, and
`genome_hash` is echoed inside `metrics`). It is **not** your proposed
`HASH(genome + symbol + tf + dataset_version + backtester_version + cost_model +
slippage_model + validation_config)`. Consequences: a cost-model change does not
invalidate old results (Section 29D), and results can't be safely compared across
config changes because nothing fingerprints the config. Building the full
experiment-fingerprint key is a core V2 item.

## Section 11 — Partial / interrupted backtests

✅ Automatic resume, per-strategy granularity:
- Each finished strategy's result row is committed the moment it completes
  (loop in `_screen_batch`/`_detail_batch` → `db.x` per row).
- Strategies are marked `BACKTESTING` before dispatch; on crash they'd be stuck —
  `_screen_batch` opens with an explicit recovery statement:
  `UPDATE strategies SET status='BORN' WHERE status='BACKTESTING' AND updated_at < now−120s`
  → they are re-screened on the next tick.
- Detail/validation resume via the NOT-EXISTS skips (Section 10).
So closing the app at 430/1000: 1–430 are kept; the in-flight batch (≤ batch size,
default 30 screen / 8 detail / 2 validation) re-runs; 601+ continue as BORN.
There is no explicit "PARTIAL" marker — partialness is expressed as
(status column) + (absence of stage rows), which the pipeline interprets correctly.

## Section 12 — Crash safety

- **SQLite**: WAL journal + `synchronous=NORMAL` (`db/database.py:Database.__init__`).
  Python/process crash → nothing lost (WAL replays). OS crash / power loss → DB
  integrity survives; the last few committed transactions *may* be lost (NORMAL
  fsyncs at checkpoints). `synchronous=FULL` would eliminate even that.
- **Per-result atomicity**: every `db.x()` commits its own transaction — a completed
  experiment is durable immediately; there is no multi-row transaction that could
  half-apply.
- **Parquet**: single-shot writes. An interrupted raw-dataset write can leave a
  corrupt file: feature reads are guarded (`storage.py:read_feature_cache` catches
  and falls back to recompute ✅) but `read_dataset` is **not** guarded ❌ — a torn
  raw file would raise on load with no self-heal (V2: write-temp-then-rename +
  optional checksum).
- **MT5 disconnects**: real-bridge calls fail soft (return `[]`/`None` + warning
  log); the paper loop skips a cycle when a frame has <120 bars
  (`paper/engine.py:_live_frame`) — no false ticks, no crash ✅; but no explicit
  detection/reconnect/gap record ❌ (Section 16).
- **Frontend crash**: irrelevant to state — the UI is stateless over REST/WS.
- **Corrupted/partial result detection**: none beyond the feature-cache try/except;
  no `PRAGMA integrity_check`, no row validation on load.

## Section 13 — Generation persistence

✅ `strategies` persists generation, parent_id, status, fitness, genome, mutation
metadata for every individual; `EvolutionEngine.generation()` =
`SELECT MAX(generation) FROM strategies` — so the lab resumes **at generation 37**,
not 0 (verified live: generation continued across restarts). `generation_stats`
snapshots born/tested/failed/survived/validated/qualified/killed/retired/
duplicates/best+avg fitness per completed generation. Pending experiments = status
column + missing stage rows (Sections 10–11).
In-memory only (reset on restart, cosmetic): `Lab.counters` cumulative totals,
`_tested_this_gen`, `duplicates_blocked`, evolution RNG state.

## Section 14 — Evolution history / ancestry

✅ Permanently in SQLite: `parent_id` chains (indexed), `generation`,
`mutation_type`, human-readable `creation_reason` (e.g. "specialization of #109:
drop sunday — negative expectancy", "mutation of #42: threshold jitter"),
`origin` (seed/mutation/crossover/exploration/hypothesis/specialization),
`species_key`, and `hypothesis_id` → `hypotheses` table storing
observation/hypothesis/proposal JSON/status/child link. `GET /api/tree` rebuilds the
full graph from the DB after any restart; "why was #284 created" is fully answerable
(parent #109, mutation type, reason text, linked hypothesis). 
Caveat: the user-triggered `clear_failed` (`evolution/engine.py:clear_failed`)
**deletes** FAILED/KILLED rows and their orphaned backtests — that can punch holes
in ancestry (a KILLED parent of a living child disappears). Retirement
(population cap) preserves rows ✅. V2 should make deletion ancestry-safe
(tombstones) or archive-first.

## Section 15 — Paper trading persistence

⚠️ Split:
- **Persisted (survives restart):** every paper trade with full execution trail
  (`paper_trades`: signal ts, requested/exec price, bid/ask, spread points, exec
  delay ms, slippage points, lots, regime, genome hash, exit ts/price/reason, PnL,
  source), all execution/rejection records (`executions`), calibration snapshots
  (`calibration` table, written every 60 cycles via `calib.snapshot_to_db()` and on
  demand), per-strategy equity (derived on the fly from DB PnL —
  `paper/engine.py:_strategy_pnl`), enabled-strategy set (recomputed from DB status
  + fitness each cycle).
- **NOT persisted:** open positions. `PaperEngine.positions` is an in-memory dict;
  `start()` does not reload OPEN rows. After a restart: yesterday's OPEN trades stay
  `status='OPEN'` in the DB **forever** (orphaned — never managed or closed), the
  risk manager's concurrency registry (`risk/controls.py:_open_positions`, in
  memory) resets, and engine stat counters reset. This is a real V2 fix: on start,
  reload OPEN trades into `positions` (all data needed — entry, sl, tp, trailing
  state, atr — must be persisted on the row; sl/tp/trailing are currently *not*
  columns, another gap), or explicitly close them at market on recovery.

## Section 16 — MT5 reconnection

❌ Not implemented. `mt5/factory.py:build_bridge` connects **once** at startup (or on
`reset_bridge()` — triggered manually by saving MT5 settings). There is no heartbeat,
no disconnect detection after the initial connect (`_connected` stays True even if
the terminal dies; individual calls just return empty + warning logs), no automatic
re-init, no "feed lost" event to the dashboard, no missing-data-period record.
Safety behavior that *does* exist: the paper loop naturally idles when frames come
back empty/short (`_live_frame` → None → skip) and never fabricates ticks ✅;
startup fallback in `auto` mode goes to the clearly-labelled SIMULATOR ✅; `real`
mode refuses to fake anything and reports the honest error ✅. V2 needs a connection
monitor thread: detect (`terminal_info().connected` / consecutive empty results),
pause paper with an event, retry `mt5.initialize` with backoff, log the outage
window, and re-sync data after reconnect.

## Section 17 — Live data appending

❌ No. Live bars are pulled per-cycle into a transient in-memory frame
(`paper/engine.py:_live_frame`, 400 bars) and discarded. Nothing appends to the
parquet snapshots; datasets are static full-window files written at ingest time.
History is only "captured" indirectly: tomorrow's ingest re-downloads the window,
which by then includes today's live bars. For the simulator this is exact
(deterministic day-anchored series); for real MT5 it means the persistent record of
a live session exists only after the next ingest — and tick-level live data is never
persisted at all.

## Section 18 — Tick data

❌ Not stored. The system persists **OHLC bars only** (with per-bar spread; bid/ask
columns are bar-level reconstructions: simulator models them, the real bridge
derives them from the bar's spread field — `mt5_real.py:_rows_to_bars`).
`GET /api/data/ticks` returns N *live* `latest_tick` samples — ephemeral, nothing
written to disk.
Difficulty to add: **moderate**. Real MT5 exposes `copy_ticks_range`; you'd add a
tick store (partitioned parquet per symbol/day), a registry table, dedupe on
(ts, bid, ask, last), and a config toggle. The simulator already generates a live
tick stream that could be recorded the same way.
Storage estimate for XAUUSD (0.5–2 ticks/s in active sessions): ~50k–200k ticks/day
→ ~15–50 M ticks/year → at ~30 B/tick raw and Parquet delta+snappy compression
(~12–25 B/tick): **~0.2–1.2 GB per year, roughly 1–6 GB for 5 years** — an order of
magnitude above bar storage (Section 20) but entirely feasible on disk; RAM-wise it
must stay query-partitioned (never whole-load).

## Section 19 — Raw vs derived separation

✅ The principle holds (see Q9/Q10): raw snapshots are write-once
(`storage.write_bars`, called only from `ingest`), derived features live in a
separate `features/` file per dataset and are append-merged column-wise
(`FeatureEngine._persist`). Deleting all derived data never harms raw. The layout
differs from your proposed per-indicator files (`XAUUSD_M15_RSI14.parquet`): the
current design uses **one wide float32 parquet per dataset** — fewer files, single
read to load everything, column projection still possible via pyarrow/DuckDB.
Equally efficient for the current access pattern (strategies pull many features from
one dataset); per-indicator files would only win for selective single-indicator
rebuilds, which the column-merge already approximates.

## Section 20 — Storage efficiency (estimates)

XAUUSD, 24/5 market (~1,380 trading hours-bound M1 bars/day, 260 days/yr):

| TF | bars/yr | 5-yr bars | raw parquet (snappy, ~50–90 B/bar) |
|---|---|---|---|
| M1 | ~360k | ~1.8M | ~90–160 MB |
| M5 | ~72k | ~360k | ~20–30 MB |
| M15 | ~24k | ~120k | ~7–11 MB |
| M30 | ~12k | ~60k | ~4–6 MB |
| H1 | ~6k | ~30k | ~2–3 MB |

Bars total (5 yrs, all five TFs): **~125–210 MB**. Feature files add roughly 2–3×
the raw size per dataset (86 float32 columns compress well): **~250–500 MB** more.
Parquet compression is a large factor vs CSV (typically 3–6×). Daily-snapshot
ingestion (Section 3) multiplies raw storage by the number of operating days —
another reason incremental ingestion matters.
DuckDB: `storage.duckdb_query` **can** query the parquet files directly (pushdown,
no full RAM load) — implemented but currently unused; the live pipeline is pandas.
Large-dataset processing without exhausting RAM: partially — see Section 21.

## Section 21 — Memory management

- `DataEngine.get_frame` loads the **entire dataset** into a float64 pandas frame;
  an in-memory cache holds up to 12 datasets then clears wholesale
  (`data/engine.py:get_frame`). M1 5-yr ≈ 40–80 MB RAM/dataset — acceptable at this
  scale, problematic at 20-yr multi-symbol scale.
- `FeatureEngine` holds float64 arrays in an LRU of 512 arrays
  (~80 MB/dataset for 86 features × 120k bars) — bounded, then evicts.
- Selective loading by symbol/timeframe: ✅ — a genome only ever touches its own
  (symbol, tf) dataset (`orchestrator/lab.py:_dataset_for`).
- Chunked processing / memory-mapped arrays / partial-column loads: ❌ not
  implemented (pyarrow could do column projection & row-group filtering; DuckDB
  path exists but unused).
- Multiplier to watch: each **worker process** loads its own copies of frames it
  touches (`ProcessPoolExecutor` with fork/spawn) — with `workers=2` hot datasets
  occupy ~2–3× RAM. Fine on the demo config; a real 1000-population run on big data
  needs the DuckDB/row-group path or shared-memory frames.

## Section 22 — Research directory (human-readable files)

❌ No file-based RESEARCH tree exists. Equivalent *content* is all in SQLite:
strategies (genome+ancestry) ≈ `RESEARCH/strategies/`, generation_stats ≈
`generations/`, backtests ≈ `backtests/` (but trades are a 300-row JSON sample, not
`trades.parquet`; equity curves are not stored at all), validations ≈ `validations/`
(one row with all batteries as JSON blobs), paper_trades+executions ≈
`paper_trading/`, hypotheses ≈ `hypotheses/`. Nothing is readable outside the
dashboard/API today except by querying the db file. V2: an exporter that writes
`strategy_%06d.json`, per-generation JSON, trades/equity parquet per backtest, and
validation JSONs — plus storing full trade lists and equity curves (currently
dropped) so those files can be complete.

## Section 23 — SQLite vs Parquet split

✅ Direction is correct and documented in code (`db/database.py` header: "this DB
holds metadata & research state only"): bars+features → Parquet; strategies,
ancestry, status, experiment rows, hypotheses, events, calibration, small metric
summaries → SQLite. Deviations from your ideal: (a) trade samples live as JSON
*inside* SQLite (fine now; should become parquet when trade history grows),
(b) equity curves are neither (discarded), (c) the `events` table grows unbounded
(no pruning policy).

## Section 24 — Database backup

⚠️ Copying the project folder captures everything **by default**
(`data_cache/`, `lab_state.db*`, `config/`, `lab.log`) — with two caveats:
1. **WAL**: while the app is running, recent transactions live in
   `lab_state.db-wal`. Copy the `-wal`/`-shm` files together with the db, or
   checkpoint first, or (best) use SQLite's online backup
   (`VACUUM INTO 'backup.db'` / backup API) for a consistent snapshot.
2. A parquet write interrupted mid-copy could be torn (no checksums — Section 12).
No "Backup Research Lab" button exists ❌. V2: one endpoint that runs
`VACUUM INTO`, zips `data_cache` + `config` + versions manifest into
`BACKUPS/lab_backup_{ts}.zip`.

## Section 25 — Portability

✅ with the caveat from Q5. All runtime paths derive from `ROOT_DIR`
(`config.py`, `logging_setup.py`, `main.py`); the shipped yaml uses **relative**
`cache_dir: data_cache` / `database_path: lab_state.db`, resolved against ROOT_DIR at
load — so C:\TradingLab → D:\TradingLab works, including after OS reinstall, provided
venv/node_modules are recreated (`scripts/start_lab.bat` does both). No absolute
paths are hardcoded in source. The one leak: `save_config` persists resolved
absolute paths after any Settings save (fix = relativize-on-save when under ROOT_DIR).

## Section 26 — Research reproducibility (6 months later)

Stored ✅: genome (exact JSON + hash), full mutation ancestry and reasons,
hypothesis linkage, dataset id + exact window used, all stage metrics (incl. cost
echoes: spread cost, slippage, exit histogram), full validation battery, species,
fitness. The backtester is **deterministic**: slippage/Monte-Carlo/perturbation
streams are seeded from
`sha256(genome_hash | stage | seed_salt | window | spread_mult | slippage_mult | perturb)`
(`backtest/engine.py:280`, `_perturb_genome`, MC rng at 267) — unit-tested
(`test_backtest_runs_and_reproducible`). Same data file + same code ⇒ bit-identical
rerun.
NOT stored ❌: software/backtester/feature-engine versions, the config snapshot in
force at run time (fitness weights, death rules, commission, slippage params — only
*some* are echoed in metrics/params), the evolution RNG stream (you cannot replay
*why this population sequence* was born), and a content checksum of the dataset.
So "reproduce exactly why #847 succeeded": yes for the evaluation math (rerun the
backtests from the stored genome on the stored dataset file), no for guaranteeing
the *environment* was the same — versions/config aren't stamped (Sections 5, 34).

## Section 27 — Randomness & seeds

- Backtests/validation: deterministic derived seeds (above) — reproducible by
  construction; salts for WF/perturbation/MC runs are systematic (`wf{i}`,
  `pert{i}`, `mc{i}` in `validation/engine.py`) and stress params are stored in
  `backtests.params`; the seed *string* itself is derivable, not stored per row.
- Evolution: `EvolutionEngine._rng = random.Random(20250925)` — a fixed seed at
  construction (`evolution/engine.py:38`), but the *stream position* depends on
  runtime call order and is not persisted; `genome/ops.py` directed ops can run with
  an unseeded `random.Random()`. There is **no per-experiment/per-child seed
  column** ❌ — you cannot replay "strategy #500 was generated from seed 12345".
  V2: store `rng_state` or a per-birth seed on each strategy row.

## Section 28 — Incremental research (new data arrives)

Honest answer: **no incremental testing exists — in either direction.**
- The good half of your requirement is satisfied *by accident of the skip logic*:
  the lab never blindly repeats the 1,000 historical tests when new data arrives —
  completed stages are skipped by NOT-EXISTS (Section 10), and results stay bound to
  their original `dataset_id`.
- The bad half: existing strategies are therefore **never re-evaluated on new
  data** either; only *newly born* children test against the latest dataset. There
  is no trigger like "dataset changed → re-run stage X for strategies Y".
- What can safely be cached: feature arrays for the unchanged prefix (needs the
  master-store/append design of Section 3 + warm-up handling), and any result keyed
  by a full fingerprint (genome hash + dataset content hash + config/engine
  versions) — identical fingerprint ⇒ provably identical result (determinism,
  Section 26) ⇒ skip.
- What must be recomputed when data extends: all path-dependent statistics (equity,
  drawdown, consecutive losses, holds spanning the boundary), and every
  window-relative stage (train/test split, walk-forward folds, OOS tail shift).
  "Test only the new bars and stitch" is **not mathematically safe** for these; the
  correct incremental scheme is fingerprint-based skipping + full re-run of affected
  strategies on the extended dataset, prioritized by fitness/age.

## Section 29 — New strategy vs new data (cases A–F)

- **A. Existing strategy + existing data → reuse.** ✅ Implemented
  (birth-time hash dedupe + stage NOT-EXISTS skips).
- **B. Existing strategy + new data → test only new data if safe, else full.**
  ❌ Neither happens: the strategy is simply not re-tested (stale result remains
  canonical). Required V2 behavior = fingerprint change detection → full re-run of
  affected stages on the extended dataset (tail-only stitching is unsafe for
  path-dependent metrics — Section 28).
- **C. New strategy + existing data → test against cached data/features.**
  ✅ Exactly what happens (`_dataset_for` → latest dataset; features from cache;
  zero indicator recomputation per strategy).
- **D. Existing strategy + changed execution costs → reuse or recalc?**
  ❌ Undetected: no config fingerprint is stored with results, so old-cost results
  silently coexist and compete with new-cost results. Correct V2: stamp runs with a
  cost-model fingerprint; on change, mark affected results STALE and requeue.
- **E. Changed indicator implementation → invalidate caches.** ❌ Not detected
  (Section 7) — stale feature arrays are silently reused. Needs engine-version /
  code-hash invalidation.
- **F. Changed risk rules → what recalculates?** Risk gates apply at *trade time*
  going forward ✅ (`risk/controls.py` consulted live by the paper engine; config
  changes take effect immediately). Historical backtest/fitness results are not
  re-judged — which is defensible for execution risk, but *fitness death rules*
  (`fitness/evaluator.py:death_check`) are config-driven and equally un-fingerprinted
  → same STALE problem as D.

## Section 30 — Continuous mode overnight

✅ Durable: every completed experiment commits its own SQLite transaction the
instant it finishes (`db.x` per row; WAL) — after 11 h + power loss, all committed
work survives (at most the final seconds of transactions under
`synchronous=NORMAL`). Pending work is fully expressible from disk state: strategies
by status (BORN/SURVIVED/…), missing stage rows, stuck-BACKTESTING recovery,
generation via MAX(generation). On restart with `LAB_AUTOSTART=1` the loop resumes
automatically exactly where the persisted state says (verified live multiple times
today). Not durable: cumulative UI counters, paper open positions (Section 15),
RNG stream position (Section 27).

## Section 31 — Data integrity checks

❌ **None implemented.** `data/engine.py:_normalize` only casts types and derives
hour/minute/dow/dom/session (+spread fallback when missing). No checks for:
timestamp ordering or duplicates, missing candles/gaps, OHLC consistency
(high ≥ max(o,c) ≥ min(o,c) ≥ low), positive prices, bid ≤ ask, plausible spread
bounds, timezone consistency, DST/session sanity. Bad MT5 data would be silently
written and used. The only filtering is simulator-specific weekend-bar dropping.
V2 needs a `validate_frame()` gate in `ingest` that rejects or flags-and-quarantines
bad rows and records an integrity report per dataset.

## Section 32 — Broker-specific data identity

❌ Insufficient. Dataset identity = symbol + tf + date window + `source`, where
`source` is only `"MT5"` or `"SIMULATOR"` (`mt5_real.py:MT5RealBridge.source` is a
constant). Broker A's XAUUSD and Broker B's XAUUSD on the same day produce the
**same dataset id** → the second ingest's `INSERT OR REPLACE` + same file path would
overwrite/conflate the first. Server/account are available (`account_info()` exposes
login/server) but never enter dataset identity. V2: include
`{server}_{account}` (or a broker profile hash) in `make_dataset_id` and the
registry; treat them as distinct datasets everywhere (which also protects
calibration: spread/slippage observations are already grouped by `source` only —
same issue).

## Section 33 — Timezone

- Internal storage: **UTC** ✅ — simulator series are built on UTC epochs;
  normalization uses `pd.to_datetime(unit="s", utc=True)`; all ts columns are Unix
  seconds.
- Dashboard display: browser-local ✅ (`frontend/src/api.js:fmt.ts/dt` →
  `toLocaleString`) — Pakistan time automatically for you.
- Sessions: **fixed UTC bands, no DST** ⚠️ —
  `data/engine.py:SESSIONS_UTC = {asia 0–7, london 7–13, newyork 13–21, off 21–24}`
  (duplicated in `risk/controls.py:session_of_hour`). Real London open is 07:00 UTC
  in winter but 07:00–08:00-shifted in summer; New York shifts between 12/13 UTC.
  Session/day research therefore carries up to ~1 h mislabeling half the year.
  V2: compute session boundaries from proper tz rules (e.g. Europe/London,
  America/New_York via zoneinfo) per bar date.
- Real-MT5 timezone: **unverified risk** ⚠️ — `copy_rates_range` is passed tz-aware
  UTC datetimes while broker servers typically run UTC+2/+3 with DST;
  `_rows_to_bars` trusts `r.time` conversion. This must be validated against a live
  terminal (compare `mt5.symbol_info_tick().time` vs local clock) before trusting
  real data — a classic 2–3 h bar-offset bug source. The bar `time` semantics also
  affect session labels derived from them.

## Section 34 — Versioning

❌ Effectively none. The only version string in the codebase is
`FastAPI(version="0.1.0")` (`main.py:34`) — never stamped into any record. No
versions for: backtester, feature engine/library, fitness evaluator, validation
engine, cost/slippage models, genome schema, config. Results produced under
different code are indistinguishable in the DB (the exact scenario you want to
prevent). V2: a `versions.py` module (semantic constants + git hash if available),
stamped into every backtests/validations/datasets row and into the backup manifest.

## Section 35 — Cleanup & archive

⚠️ Partial:
- Dead strategies stay queryable but out of the active loop: population cap retires
  the worst into `RETIRED` (rows preserved, ancestry intact —
  `enforce_population_cap`, unit-tested); all pipeline queries filter by status with
  LIMIT batches and use indexes on status/generation/fitness/parent, so millions of
  dead rows slow storage, not the active loop (much).
- `clear_failed` (user button) hard-DELETEs FAILED/KILLED + orphaned backtests —
  destructive to ancestry (Section 14); no archive-first option.
- No compression/archival of old dataset snapshots (daily snapshots accumulate
  forever — no GC), no `events` pruning, no feature-file GC for deleted datasets,
  no "archive generation range to cold storage" facility. All are V2 additions;
  SQLite + parquet make archiving trivial (move files / `VACUUM INTO`).

## Section 36 — Dashboard data source

✅ DB-backed, not session-backed. Every page fetches through REST endpoints that
read SQLite/parquet on demand (`api/routes.py`), plus WS events for live updates
(with a 30-event replay buffer for fresh connections — `main.py:ws_endpoint`).
Restart backend/frontend → UI reconstructs fully from persistent state (verified
live today). Exceptions (in-memory, reset on restart): `Lab.counters` cumulative
totals, paper engine `stats` counters, open positions — the *histories* behind them
are all in the DB.

## Section 37 — Export

⚠️ Minimal. Machine-readable JSON is available through the API
(`GET /api/strategies/{sid}` returns genome, lineage, children, all backtest stage
metrics, validation, matrices, hypotheses, paper summary; `/api/population`,
`/api/tree`, `/api/paper/trades`, `/api/calibration` etc.) and can be saved from a
browser — but there are **no dedicated export endpoints, no CSV/Parquet downloads,
no human-readable report generation, no equity-curve export** (not stored), and no
one-click ancestry export. V2: `/api/export/strategy/{id}` (JSON bundle + report
markdown), trades/equity as parquet/CSV, full-tree JSON.

## Section 38 — Test coverage (41 tests)

`tests/test_backtest.py` (8):
1. `test_backtest_runs_and_reproducible` — a genome backtests end-to-end and two
   runs are numerically identical (determinism).
2. `test_costs_reduce_profit` — spread/commission/slippage lower net profit vs the
   gross path.
3. `test_zero_cost_model_is_not_free` — even zeroed cost config still charges
   spread-crossing realism (guards the "free backtest" trap).
4. `test_window_slice_reduces_trades` — window slicing limits the trade set.
5. `test_session_filter_respected` — genome session filters exclude out-of-session
   entries.
6. `test_day_filter_respected` — day-of-week filters enforced.
7. `test_max_hold_enforced` — positions close at max_hold_bars.
8. `test_regime_filter_shapes_trades` — regime filters change the trade set.

`tests/test_evolution.py` (6):
9. `test_duplicate_genome_blocked` — identical genome insert is rejected/counted.
10. `test_invalid_genome_rejected` — schema violations never enter the population.
11. `test_ancestry_chain` — parent→child links and generations are recorded.
12. `test_reproduce_respects_mix_and_dedupes` — elite/mutation/crossover/exploration
    split honored; duplicates skipped.
13. `test_elite_diversity_cap` — species cap limits elite monoculture.
14. `test_population_cap_retires_worst` — over-cap populations retire lowest
    fitness (rows preserved as RETIRED).

`tests/test_features.py` (8):
15. `test_rsi_range` — RSI stays in [0,100].
16. `test_adx_range` — ADX stays in [0,100].
17. `test_ema_sma_converge_on_constant` — MAs equal a constant series.
18. `test_no_lookahead_prev_day` — prev-day features never leak same-day data.
19. `test_no_lookahead_breakout` — structure features are lag-safe.
20. `test_sessions_and_time` — session/time features match bar timestamps.
21. `test_regime_binary` — regime masks are 0/1.
22. `test_feature_engine_caches` — second `get()` is served from cache (no recompute).

`tests/test_fitness.py` (9):
23. `test_good_strategy_survives_and_scores` — healthy metrics → positive fitness.
24. `test_insufficient_trades_kills` — min-trades death rule.
25. `test_drawdown_kills` — max-drawdown death rule.
26. `test_low_pf_kills` — min profit-factor death rule.
27. `test_oos_collapse_kills` — OOS degradation limit.
28. `test_stress_degradation_kills` — spread/slippage stress death rule.
29. `test_parameter_instability_kills` — perturbation instability death rule.
30. `test_complexity_penalty_prefers_simple` — equal performance ⇒ simpler genome
    scores higher.
31. `test_negative_expectancy_damped` — negative expectancy is penalized, not
    rewarded.

`tests/test_genome.py` (10):
32. `test_valid_genome_passes` — reference genome validates.
33. `test_unknown_feature_rejected` — features must exist in the library.
34. `test_too_many_indicators_rejected` — early complexity cap enforced.
35. `test_risk_band_enforced` — risk fields within allowed bands.
36. `test_hash_deterministic_and_order_free` — canonical hashing ignores JSON key
    order.
37. `test_random_genomes_valid` — random generator produces schema-valid genomes.
38. `test_mutations_produce_valid_different_children` — all mutation types yield
    valid, changed genomes.
39. `test_crossover_valid` — crossover children valid.
40. `test_directed_ops` — directed (specialization/hypothesis) mutations work.
41. `test_complexity_and_species` — complexity counting + species key stability.

**Missing test coverage (exactly the areas you listed):**
- persistence/restart: no test that state survives a fresh `Database` instance over
  an existing file (resume-from-disk untested).
- stuck-BACKTESTING recovery: untested.
- dataset cache-hit path (`ingest` twice same day): untested.
- duplicate/overlapping data append: untestable today (feature absent — Section 3/4).
- incremental ingestion: absent.
- DB recovery / WAL / torn-parquet handling: absent.
- feature-cache invalidation: absent (feature itself absent — Section 7).
- reproducibility across config/version change: absent (only same-code determinism
  is tested).
- MT5 disconnect/reconnect & simulator fallback labeling: untested.
- Also untested entirely: paper engine (entries/exits/fills/calibration), risk
  manager gates, validation engine battery, specialization matrices, API routes,
  simulator bar/tick realism, orchestrator tick sequencing.

## Section 39 — DAY 1 → DAY 2: exact startup sequence (as coded)

DAY 1 (fresh install): startup ingests the configured windows per timeframe
(`history_months`: M1=1, M5=6, M15/M30/H1=12 by demo config — a "2019→2026" request
means setting `history_months` accordingly), writes snapshots, precomputes 21 core
feature specs per dataset, seeds population, screens/details/validates, qualifies,
auto-starts paper when QUALIFIED>0. Close app: everything committed per-row to
`lab_state.db`; snapshots + feature files on disk.

DAY 2 reopen — numbered, exactly what happens:
1. **Reads from disk:** `config/lab_config.yaml` (paths resolved vs ROOT_DIR);
   opens `lab_state.db` (WAL replay — any committed state from yesterday is intact);
   `setup_logging` reopens `lab.log`.
2. **Checks bridge:** `factory.build_bridge` — tries real MT5 (`mt5.initialize`);
   on this machine falls back to labelled SIMULATOR (`bridge_status`).
3. **Autostart:** `LAB_AUTOSTART=1` → `get_lab().start("continuous")` → orchestrator
   thread; `_datasets_ready=False` → `_ensure_datasets()` runs once.
4. **New MT5 data requested:** for each (symbol, tf): `ingest()` computes
   start/end = *now-based* window → dataset id contains **today's** date →
   registry lookup misses → **full-window re-download**
   (`copy_rates_range(start,end)`). *This is the whole window again — not a delta.*
5. **What gets appended:** nothing is appended — a **new full snapshot parquet** is
   written per (symbol, tf); registry row inserted; yesterday's snapshot files
   remain untouched on disk (never merged, never deleted).
6. **Features recalculated:** the 21 core specs are recomputed for **every**
   dataset (new *and* old) into memory (`_precompute_features`); on disk, the new
   datasets get fresh feature parquets; old datasets' feature files gain nothing
   (merge skips existing columns). Non-core features (e.g. `rsi:16`) are *not*
   recomputed — they load from disk when requested.
7. **Experiments skipped:** every existing strategy that already has a
   screen/detail/validation/matrix row is skipped (NOT-EXISTS queries) — the 50,000
   historical candidate runs are **never repeated**. Yesterday's results stay bound
   to yesterday's dataset ids.
8. **Experiments resumed:** strategies in BORN (plus any stuck in BACKTESTING
   >120 s, auto-reset to BORN) are screened **against the new dataset**
   (`_dataset_for` → `latest_dataset`); SURVIVED-without-detail go to detail;
   detail-without-validation go to the validation battery; new QUALIFIED get
   specialization matrices.
9. **Strategies still alive:** all of them — statuses (SURVIVED/QUALIFIED/PAPER/
   RETIRED/KILLED/FAILED) were persisted; population top-up only births the deficit
   vs `population_size` (elites/mutation/crossover/exploration from persisted
   fitness).
10. **Paper positions/trades:** all closed trades, executions, calibration rows,
    per-strategy PnL/equity survive (SQLite). **Open positions do NOT** — the
    in-memory position map is empty after restart; yesterday's OPEN rows are
    orphaned (stay OPEN forever, unmanaged). Paper engine auto-restarts in
    continuous mode when QUALIFIED+PAPER > 0, re-selects the top-8 by fitness, and
    begins fresh signal evaluation on the live feed.
11. **Generation resumed:** `MAX(generation)` from the strategies table — continues
    at yesterday's generation (e.g. 37), not 0. `generation_stats` keeps yesterday's
    snapshots; in-memory cycle counters restart at 0 (cosmetic).
12. **New children generated:** only to fill the population deficit (≤60 births per
    tick, throttled): elite-selection → mutation/crossover/exploration per configured
    percentages, each dedupe-checked by genome hash; plus specialization children of
    newly-matrixed QUALIFIED strategies and up to 2 applied AI hypotheses per
    research pass (every 8th cycle).

## The "auto ingested on start" verdict (your specific worry)

The README phrase maps to `Lab._ensure_datasets` → `DataEngine.ingest_default`.
Its **actual** semantics today:

- ✅ It checks what is already stored (registry + file existence) and does
  **not** re-download on same-day restarts.
- ❌ It does **not** determine the latest stored timestamp, request only missing
  data, merge, or dedupe. On a new day it re-downloads the **entire configured
  window** and writes a brand-new snapshot dataset (old snapshots accumulate).
- ✅ Feature precompute afterwards is fast and merge-safe on disk (only missing
  columns are written), though core specs are redundantly recomputed into memory at
  every startup.
- ✅ Experiments are never blindly repeated (existence-based stage skips).

So the behavior is "**daily full-window snapshot + full experiment reuse**", not the
"**delta append**" model you specified. On the simulator this costs ~10 s/day; on a
real MT5 feed with multi-year M1 data it would cost minutes-to-hours every day and
would multiply disk usage — the incremental master-store design (Section 3) is the
top V2 build item, exactly as you suspected.

## Section 40 — Final audit

### A. ALREADY IMPLEMENTED CORRECTLY
1. Raw/derived separation with immutable write-once raw snapshots
   (`storage.write_bars` / `FeatureEngine._persist`).
2. Parquet (snappy, float32 features) + SQLite (WAL, per-statement commits) split —
   right technology choices, right responsibilities.
3. Canonical, order-free genome hashing + UNIQUE constraint → true duplicate-genome
   prevention at birth (never re-tested).
4. Deterministic, seeded backtester (slippage/MC/perturbation derived from
   genome_hash+stage+salt+window) — reproducible by construction, unit-tested.
5. Stage-level experiment reuse via NOT-EXISTS skips → automatic resume after
   restart without repeating completed work.
6. Crash-recovery of stuck BACKTESTING strategies (120 s staleness reset).
7. Full permanent ancestry: parent chains, generations, mutation types,
   human-readable creation reasons, hypothesis linkage, species, generation stats.
8. Paper-trade execution trail persistence (signal/requested/exec prices, bid/ask,
   spread, delay, slippage, regime, source) + executions/rejections + calibration
   snapshots + DB-derived equity.
9. All state inside the project directory with ROOT_DIR-relative resolution →
   folder-move portable (default config).
10. UTC-internal timestamps, browser-local display.
11. DB-backed dashboard (no session dependence) + WS event replay.
12. Risk layer independence: gates applied at trade time, real execution
    double-locked (typed phrase + real bridge required + persisted flag),
    simulator always labelled.

### B. IMPLEMENTED BUT NEEDS IMPROVEMENT
1. **Ingestion** — same-day cache works, but daily full-window re-download with
   snapshot accumulation; needs incremental master store + delta requests +
   dataset views (top priority).
2. **Feature precompute at startup** — recomputes 21 core specs into memory even
   when disk cache is valid; should check disk first.
3. **Reuse key** — existence-based (strategy_id, stage) instead of a content
   fingerprint (genome+dataset+config+engine versions); results can't be
   invalidated when costs/rules/code change.
4. **Paper open positions** — not recovered on restart; OPEN rows orphaned; sl/tp/
   trailing state not persisted on the trade row.
5. **Kill switch persistence** — `set_kill_switch` mutates in-memory config only;
   a restart silently releases an engaged kill switch (should persist, and arguably
   fail *closed*).
6. **Real-MT5 resilience** — soft-failing calls but no disconnect detection,
   auto-reconnect, outage recording, or safe-stop signaling.
7. **Broker identity** — dataset id lacks server/account; two brokers would
   collide on the same id (INSERT OR REPLACE overwrite).
8. **Timezone/session** — fixed UTC session bands (no DST); real-MT5 bar-time
   timezone semantics unverified against a live terminal.
9. **Integrity checks** — none on ingested frames (ordering, OHLC sanity, bid≤ask,
   gaps); torn raw-parquet reads unguarded.
10. **Portability leak** — `save_config` writes absolute paths into yaml.
11. **Storage growth** — no GC/archival for old snapshots, events table, or feature
    files of dead datasets.
12. **clear_failed** — hard-deletes rows, punching ancestry holes.
13. **Trades/equity persistence** — only 300-trade samples; equity curves discarded.
14. **In-memory counters** — lab/paper cumulative stats reset on restart (cosmetic
    but dashboard-visible).
15. **Test coverage** — engines well-tested; persistence/paper/risk/API/simulator/
    recovery paths untested.

### C. NOT IMPLEMENTED — SHOULD BE ADDED BEFORE V2
1. **Incremental data ingestion** (last-timestamp delta + append + dedupe + merge)
   — Sections 3/4.
2. **Versioning stamps** — engine/feature/fitness/validation/cost-model/config/app
   versions on every dataset, backtest, validation row (+ backup manifest) —
   Sections 5/34.
3. **Feature-cache invalidation** — engine version + algorithm code-hash per cached
   column; invalidate only affected features — Section 7.
4. **Experiment fingerprints** — HASH(genome, dataset content, config, versions)
   driving reuse/invalidation, replacing bare existence checks — Sections 10/29.
5. **Data integrity gate** in ingest (validate/quarantine + report) — Section 31.
6. **Broker-aware dataset identity** (server/account in id + registry + calibration
   grouping) — Section 32.
7. **Paper position recovery** on restart (persist sl/tp/trailing/atr on the row;
   reload or market-close orphaned OPEN trades) — Section 15.
8. **MT5 connection monitor** (detect → pause paper with event → reconnect w/
   backoff → record outage window → resync) — Section 16.
9. **RESEARCH/ export layer** — human-readable strategy/generation/validation JSON,
   trades+equity parquet, report generation, export endpoints — Sections 22/37.
10. **Consistent backup facility** — "Backup Research Lab" (VACUUM INTO + zip +
    manifest) — Section 24.
11. **DST-correct session boundaries** (zoneinfo-based London/NY) — Section 33.
12. **Per-birth RNG/seed persistence** for evolution reproducibility — Section 27.

### D. NOT NECESSARY YET / CAN WAIT FOR V2+
1. Tick-data persistence (valuable, but ~1–6 GB/5 yrs and only needed once real
   MT5 microstructure calibration starts) — Section 18.
2. DuckDB-based chunked/out-of-core backtesting (the utility exists; RAM is
   adequate at current 5-yr × 5-TF scale; revisit when datasets grow 10×) —
   Sections 20/21.
3. Column-per-indicator feature files (wide-table + column merge is equivalent for
   current access patterns) — Section 19.
4. Multi-symbol parallel research (BTCUSD/NAS100/EURUSD) — architecture supports
   it (enabled_symbols), but data identity fixes (C6) should land first.
5. Cold-storage archiving/compression of ancient generations — needed only at
   millions-of-strategies scale — Section 35.
6. Cross-run scientific workflow tracking (MLflow-style experiment servers) —
   the fingerprint + versions work (C2/C4) covers the practical need.
7. Clause-order semantic genome canonicalization (nice dedupe improvement, low
   impact today) — Section 8.
