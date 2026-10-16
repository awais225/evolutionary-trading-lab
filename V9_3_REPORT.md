# V9.3 implementation and verification report

## Scope delivered

- One schema2 canonical historical-statistics projection/shared cells and source selector for Deep/Live; same registry authority as Overview, stable primary identities, provenance and honest generation/partial/unavailable labels. Default descending numeric WR across the complete filtered population, deterministic canonical-ID ties; live operational statistics remain separate.
- Shared actual28-cell schedule, four global session masters, seven day masters and indeterminate state; persisted names/content versions/immutable applied copies, hydration, unsaved/outdated states and compare-and-set. Historical and live evaluators use the same optional matrix without bypassing other safety gates.
- Material-signature scenario dedupe across repeated/thread/process requests; explicit incomplete retry, preserved history, guarded queued dataset/config snapshots, no request-ID/release-name substitution.
- Full-filter SELECT ALL / DESELECT ALL on both tables; durable bounded asynchronous historical batches, progress/reasons, cancel remaining, restart interruption/explicit resume and saved-job discovery. Live selected review never starts trading; existing explicit per-node START remains required.
- Initial operational/range bar counts from actual stored records, keyed-object API fix, request/range stale protection, import/fetch refresh and visible partial/unavailable states.
- Inspect result cards/structured trade-weighted calendar tables/date/day/session/per-day/matrix filters with loading/error/empty states. All capped trade pages are drained before presenting a complete trade list.
- Default-collapsed accessible Manual Trading disclosure preserving the original panel/drafts/validation/order workflow.
- Original IC files and authoritative normalized store preserved; all-seven full-file HTTP pipeline/restart query and actual strategy backtest verified on an isolated logical SQLite/data copy. Existing explicit shared MT5 latest workflow retained, not claimed natively executed.

No removed V8 tabs were restored. No order/risk/sizing/account/generation/qualification/survival/signal logic was changed. The live schedule diff is exactly four additive lines replacing only legacy days/sessions when the requested matrix is explicitly active. The frozen guard permits exactly those assignments and otherwise retains permanent V8 comparisons.

## Starting state and data integrity

Actual remote main at start: `836df1ade134ba911d8388a82dc488ffee98e8b4`; confirmed unchanged immediately before publication preparation. Original `/home/user/evolutionary-trading-lab` was642cd3b1 plus dirty earlier work; its status/patch were preserved outside it and implementation occurred only in the fresh `/home/user/v93-app` clone. Native Git LFS objects were restored, not regenerated. No reset/clean/force push.

Important evidence distinction: versioned main DB SHA `c159b0718c43b43f97609881bd3be196dad76274029949a9fb4c4e710f106a12`; immutable main-only SQLite has70 QUALIFIED/22 SURVIVED, while logical original plus its versioned WAL has83 QUALIFIED. The early70 private trace copied only the main file and is not an initialization-induced83→70 result. WAL-aware SQLite backup plus actual research reconciliation yields99 in the operational acceptance audit; counts are measured, never hardcoded. Canonical718/726/728/735/737 are traced by exact primary IDs after research restoration, with missing research aliases kept missing.

Incidental test-side DB checkpoint/log artifacts were archived outside the checkout; exact original tracked DB/WAL/SHM/log bytes were restored. No DATA content is included as a V9.3 change; existing seven CSV LFS pointers/normalized files remain on main. No credentials or unrelated runtime data are staged.

## Final completed acceptance gate

Final receipt is `verification/V9_3_FINAL_GATE_RESULTS.json`:74 test files in11 bounded completed batches, each with its exact file list, command exit status and test summary. Final completed count: **1,274 passed, 1 pre-existing environment-dependent skip, 0 failures**. All11 batch receipts have actual completed exits; superseded failed/interrupted development attempts are excluded from successful totals. No new skips or weakened assertions were added to ship. The existing skip is `tests/test_v5_1a_coverage_and_balances.py::test_05_a_non_mt5_dataset_is_labelled_as_simulator`: this isolated installation carries no simulator dataset.

The final-source driver initially captured the preceding fingerprint index in its isolated snapshot. This correctly failed the release-identity self-consistency check in batch5 (1 failed/131 passed); all other batches completed successfully. After synchronizing the current index, the **entire seven-file batch5** was rerun, not just the failing assertion. Its completed replacement result and preceding failure are both recorded in the final receipt. Command: `python -u /home/user/v93_retry_identity_batch.py` (copy in `verification/`). No functional source or assertion was changed for this retry.

Exact sandbox driver: `python -u /home/user/v93_gate.py` (copy in `verification/v93_bounded_gate.py`). It uses an isolated matching source/DATA tree, the real versioned main DB fixture per batch, actual historical restoration prerequisites for batches3/4, and seven sorted test files per process to bound memory. Legacy simulator tests intentionally use the pre-canonical data profile; they cannot safely write simulator data into the user's IC store. Operational canonical-data acceptance is separate and WAL-aware, below. Each test command is:

```text
<isolated-root>/.venv/bin/python -m pytest -q <the exact files listed for that batch>
EVOLUTIONARY_LAB_DATA_ROOT=<isolated-root>/DATA
TMPDIR=SQLITE_TMPDIR=/home/user/.cache/v93-test-tmp
```

New V9.3 tests add40 deterministic backend cases and one pytest wrapper around11 functional UI behaviors. Backend cases cover all28 matrix cells, global/day state derivation, boundaries, strict validation, full-week UTC/NewYork DST/Karachi/live-mask parity, enabled/exclusion protection, unique eligible closed trade accounting, numeric/source/partial/missing/tie ranking, immutable/cross-connection/outdated assignments,24-thread and genuine2-process dedupe, changed data/settings, explicit retry/force refusal, invalid-result reuse refusal, batch durable ownership/errors, read-only Live validation, unavailable generation dates irrelevant Live/default metadata exclusion and Deep preview isolation from opposite Live assignments/trade history. Existing V9.1/V9.2 suites supply actual source identity/lazy population,9-period/range/chart-cap, import/journal/cache/correction and signed offset/safety regressions.

Standalone functional harnesses all exit0:35/35 +54/54 +16/16 +6/6 +6/6 +11/11 = **128 internal render/behavior checks**. These are not128 extra pytest cases; wrappers are already included in the gate. Receipt: `verification/V9_3_UI_RESULTS.json`. Commands from `frontend/`:

```text
node tests/v47_render_smoke.mjs
node tests/v48_ui_smoke.mjs
node tests/v6_5_1_nodes_ui.mjs
node tests/v9_1_nodes_ui.mjs
node tests/v9_2_ui.mjs
node tests/v9_3_ui.mjs
```

Development failures were investigated rather than counted as successful gates: legacy tests initially compared different physical DBs/data profiles; missing fixture restoration caused schedule/legacy-node prerequisites; the added preview isolation case initially required its normal Deep-schema prerequisite; old UI assumptions exposed label/format compatibility issues; new tests caught request-key UNIQUE-column leakage and the actual keyed coverage shape. An interrupted parent command was superseded with completed batches; no killed/incomplete run is counted. Final completed receipts are authoritative.

## Actual data/strategy acceptance (not mocked)

`verification/v93_actual_data_audit.py` records the sandbox-specific exact procedure. Command:

```text
.venv/bin/python /home/user/v93_actual_data_audit.py
```

The audit isolates a copy of the original persisted DATA and a read-only logical SQLite backup including WAL. All7 exact uploaded originals match versioned bytes, validate their full419,013 rows, explicitly commit through HTTP, preserve/reopen archived source bytes, retain identical existing rows with0 new/updates/conflicts/rejects, and query all7 normalized datasets through the actual historical DataEngine. A fresh interpreter reopens all419,013 unique rows without reloading CSVs. Full per-file hashes/counts/ranges/gaps/results are in the data report and sanitized JSON receipts.

The actual canonical718 historical strategy used4,131 M1 bars, exactly matching the initial range endpoint, UTC2026-10-13T12:32→exclusive2026-10-16T12:32, actual last12:31. Explicit acceptance setting max40 trades/initial capital200,000:40 closed/28 wins, WR70%, PF1.792, net4,725.85, return2.3629%, realised DD1.3719%, COMPLETED. Repeat reused the same ID with `engine_rerun:false`. This experimental historical population is not generation905 trades/77.24%. Qualified authority stayed99 before/after import/Deep. Exact run ID and provenance are in `verification/V9_3_ACTUAL_DATA_AUDIT.json`; no private test result is committed into the original database.

## Build, launcher and release identity

Commands: `npm run build --prefix frontend`; `PYTHONPATH=backend .venv/bin/python -m compileall -q backend/app`; shared `frontend_build.write_stamp` and `check_dist` reported FRESH; `.venv/bin/python backend/tools/build_fingerprints.py --record --release V9.3` and `--match` identify V9.3. Existing Windows launchers and backend stale/unstamped/corrupt bundle guards remain; dist is generated, not committed, so a fresh checkout must build through those existing guards.

- Frontend source SHA-256: `7eb0b616e54d2930101ff02aec0b8b7deac4824d40108ea95fb8f769694bdd39`.
- Backend code SHA-256: `ddc5acbcf0bc5a967d3f2573dfa5077e975ef067bb756dee213d9d051d090d23`.
- Exact bundle index hash/assets and source counts are recorded in the V9.3 `BUILD_FINGERPRINTS.json` entry / build receipt.
- Vite emitted the existing >500kB shared-chunk warning; build completed successfully. No performance improvement or native browser paint benchmark is invented.

Release-index metadata records the functional source commit after that reviewed commit exists; a final metadata/report commit may follow without changing source hashes. The actual final pushed main SHA is verified against `ls-remote` and supplied in the delivery message/external publication receipt rather than inserting a self-referential hash into its own commit.

## Safety and remaining environmental limits

- No real or demo orders were submitted. Tests use isolated data/fake execution bridges where trading workflows are exercised; the one actual run is historical only.
- Native Windows byte-lock semantics, batch/PowerShell launcher execution, Edge visual/manual behavior, account-terminal connection and live IC Markets retrieval/chart-history availability were **not verified** on this Linux host. MetaTrader5 Python package/terminal was unavailable.
-2683-per-call traversal is established by deterministic bridge tests and source trace, not successful native broker retrieval. Existing observed spans/gaps do not prove the broker's full historical market calendar.
- Batch owner liveness is conservative PID-based protection, not proof of worker health; explicit inspection/resume/retry is required after interruption. Cancelling remaining work intentionally preserves already-started/shared historical runs. Queue capacity is bounded per request/process, not a new distributed global scheduler.
- Legacy schedules without the new matrix keep their existing semantics. New matrix weekdays are DST-aware per timestamp; unrelated legacy window algorithms are not silently rewritten.
- Old historical duplicate rows are preserved. No unproven equivalence classes, native screenshots, manual clicks or unavailable metric values are fabricated.

## Reports

1. `V9_3_CANONICAL_STATISTICS_AND_RANKING_AUDIT.md`
2. `V9_3_SELECTION_SCENARIO_AND_SCHEDULE_REPORT.md`
3. `V9_3_DATA_IMPORT_AND_BAR_COUNT_AUDIT.md`
4. `V9_3_INSPECT_UI_AND_LIVE_PANEL_REPORT.md`
5. This `V9_3_REPORT.md`
