# HERO1 Monte Carlo manual testing guide

## Scope and safety qualification

Monte Carlo resamples persisted completed DeepTesting trade outcomes. It is a research aid, not a replay of historical candles. **START_POINT is TRADE-SEQUENCE START-POINT SAMPLING: it rotates the completed trade sequence and is not historical candle replay.** EXECUTION_STRESS requires recorded `spread_cost` and `slippage` on every source trade. Missing costs are not assumed to be zero. Monte Carlo does not validate broker fills, execution costs, MT5 behavior, or live readiness. **STAGE A EXECUTION EVIDENCE GATE: NOT PASSED.**

## Install and launch after cloning

Use Python 3.11+ and Node 20+ (see `docs/DEVELOPMENT.md`). From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\\Scripts\\Activate.ps1
bash scripts/dev_install.sh
cd apps/web && npm ci && cd ../..
python scripts/init_db.py --create
```

The project defaults persistent HERO1 data to `~/.hero1/data` outside the repository. To explicitly set a data root, set `HERO1_DATA_ROOT` to a directory outside the repository before starting the API. Keep user DATA and database files out of Git.

Start the backend in one terminal:

```bash
source .venv/bin/activate
uvicorn services.api.app.main:app --host 127.0.0.1 --port 8788 --reload
```

Start the web application in another terminal:

```bash
cd apps/web && npm run dev
```

Open the Vite URL (normally `http://localhost:5173`) and select **MONTE CARLO**. The page is scoped to the global HERO1 instrument selector; the current instrument is shown above its filters. It does not combine nodes from multiple instruments in one table.

## Node filters, sorting, paging, and selection

Combine search by node name/ID with timeframe, direction, category/origin, DeepTesting state, folder metadata, and starred scope. Folder choices are shown only when folder metadata exists; tags are not silently converted into folders. A starred filter is a research scope, not an eligibility override. The star button changes the existing node favorite metadata.

Sort by clicking column headings. Sorting applies to all filtered rows before pagination. Numeric columns sort numerically, drawdown sorts by absolute magnitude, IDs sort naturally, and missing values remain last. Click the active heading again to reverse direction. Choose 10, 15, 25, or ALL rows; the default is 25. Filters, page size and sort persist in browser local storage. Individual selections remain selected when changing page/filter; only eligible nodes can be selected. **Select all filtered eligible** selects eligible nodes across the full filtered set, not just the visible page.

Eligibility for submission requires a completed DeepTesting run and a non-empty persisted trade ledger. Ineligible rows have a disabled checkbox and backend reason. Missing metrics display N/A, not zero.

## Configure and start simulations

Review the simulation settings before submitting. The displayed seed is persisted; keep the seed, method, source DeepTesting run and every configuration field unchanged for an exact reproducibility attempt. Same seed alone is not enough. If blank, the client generates a seed at submission and shows it in the saved result.

- **RESHUFFLE:** random permutation of the completed trade outcome sequence.
- **BLOCK_BOOTSTRAP:** resampling of circular contiguous blocks of completed outcomes; record the configured block size.
- **START_POINT:** rotation of completed trade outcomes. It does not load candles or replay the strategy.
- **EXECUTION_STRESS:** only available when every source trade has recorded spread and slippage. It applies the configured stress multiplier to those recorded costs. It does not synthesize commission, swap, spread, slippage, or broker costs.

Select one or more eligible nodes and click **RUN SELECTED NODES**. The existing async service creates independent node/method tasks. Progress advances on persisted completed batches. Inspect task status, cancel one task, and confirm sibling tasks remain independent. A process restart marks in-flight work interrupted/failed rather than automatically resuming it; available partial results remain persisted.

## Inspect completed and partial runs

Choose a saved run in **INSPECT SAVED RUN**, or use **Inspect** in saved-run history. This reopens the persisted run; it does not rerun the simulation. The baseline section references the original DeepTesting run and displays only metrics actually persisted there. Missing metrics remain N/A. Expand the evidence/readiness details to review the recorded source warnings.

Simulation aggregates are calculated from persisted completed per-simulation metrics. A PARTIAL banner reports completed/requested sample counts and excludes pending simulations. It is not a final full-run distribution. Review Monte Carlo method, master/per-simulation seeds, requested/completed count, percentiles, drawdown and survival/ruin metrics, score components/coverage, and eligibility separately. The fixed mandatory limits are zero ruin, survival at least 99%, P95 drawdown at most 25%, P99 drawdown at most 35%, and positive median return. Scoring weights are survival/ruin 25%, drawdown behavior 25%, return distribution 20%, consistency/dispersion 15%, sequence robustness 10%, and execution stress 5%. Each component is rated on a 0–10 scale, multiplied by its weight, then summed without renormalizing missing evidence; unavailable components contribute zero and reduce coverage. Robustness is not approval for live trading. Eligibility evaluates the mandatory quantitative thresholds, known logic/compliance failures, and persisted DeepTesting data readiness. If source evidence is absent, it reports **INSUFFICIENT EVIDENCE**; known failed thresholds or BLOCKED readiness report **NOT ELIGIBLE**. Even an eligibility result for further review is not live-trading approval.

## Curves and chart controls

Toggle the original DeepTesting path, Monte Carlo median, P5–P95 envelope, and up to ten sample simulations. Zoom with +/− or the mouse wheel; pan with arrow controls; Reset restores the default view. Hover plotted sample points for curve and equity details. The starting-capital line is the persisted Monte Carlo configuration's initial capital.

The horizontal axis is normalized progress. Original DeepTesting and resampled paths may have different lengths/time bases; matching x positions are not matching market dates. No candle path is reconstructed.

## Compare saved runs

Tick saved-run comparison rows to compare without rerunning. Review node, DeepTesting run, strategy fingerprint/version, instrument/timeframe, method, seed, count, settings, metrics, warnings, score and eligibility. The review labels runs DIRECTLY COMPARABLE only when the recorded identity/configuration matches; PARTIALLY COMPARABLE when settings or completion state differ; INCOMPATIBLE SETUPS when DeepTesting run, fingerprint/version, instrument/timeframe, tested date range or method differs. It lists the exact differing field values and reports unrecorded required comparison data instead of treating it as equal. Metrics may still be inspected individually; robustness scores are never combined. Reopening and comparison are read-only and do not submit simulations.

## Export

- **EXPORT TABLE (TXT)** exports the current filtered, sorted node table (or the selected-node comparison/history summary when nodes are selected), not a whole-run simulation archive.
- **EXPORT COMPLETE MONTE CARLO DATA (ZIP)** exports one saved run, independently of table pagination. It contains a manifest, run configuration, original DeepTesting reference, strategy identity, per-simulation JSON/CSV, aggregate and percentile metrics, persisted curves, robustness, eligibility, warnings and evidence readiness. It does not duplicate the original trade ledger; the reference identifies its persisted source run. For partial runs the manifest states partial status and completed sample count.

After exporting, inspect `manifest.json`, `configuration.json`, `original_deeptesting_reference.json`, `simulation_results.csv`, `simulation_results.json`, `aggregate_metrics.json`, `equity_curves.json`, `robustness_score.json`, `eligibility.json`, and warnings/evidence files. Confirm run ID, source run, fingerprint, seed and sample count against the Inspect panel.

## End-to-end checklist

- [ ] Start HERO1 and select the instrument containing a completed DeepTesting run and trades.
- [ ] Combine search and at least two table filters; verify no duplicate rows.
- [ ] Sort a numeric metric, toggle its direction, and check missing values remain last.
- [ ] Page at 10, 15, 25, then ALL; retain a selected eligible node while paging/filtering.
- [ ] Select two eligible nodes and start a seeded run; confirm distinct independent task IDs.
- [ ] Cancel one long-running task and confirm the other continues; inspect the retained partial run.
- [ ] Reopen the completed run and partial run from history without rerunning.
- [ ] Inspect baseline/source identity and N/A behavior for missing historical metrics.
- [ ] Toggle each chart series; hover points; zoom, pan, and reset; verify normalized-progress disclosure.
- [ ] Compare two saved runs with different methods/configurations and review exact non-equivalence warnings.
- [ ] Export the table and a complete saved-run ZIP; inspect members and compare persisted/exported data.
- [ ] Repeat a completed configuration with same source/method/settings/seed and compare deterministic per-simulation results.
- [ ] Refresh the page and verify saved run/task history remains available.

## Known limitations

There is no historical candle-replay Monte Carlo method. The underlying Monte Carlo simulator cannot independently establish source logic/compliance correctness or market-data readiness; a quantitative gate pass alone is therefore not an eligibility approval. A persisted master seed deterministically generates a distinct recorded 64-bit seed for each simulation; per-simulation seeds and metrics are included in both JSON and CSV exports. Original baseline metrics depend on what DeepTesting persisted. Axis interactions do not make original historical dates comparable to simulated trade-progress points. Export includes the retained simulation curves and source run reference, not a duplicate of the original trade ledger. Monte Carlo research completion does not establish broker execution accuracy, MT5 validation, or live-trading readiness.
