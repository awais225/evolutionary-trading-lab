# DeepTesting integration handoff for the future Monte Carlo task

This is integration documentation only. No Monte Carlo API, engine, database table, tab, or navigation item is added here. Stage A remains NOT PASSED pending complete evidence infrastructure and Windows MT5-linked verification.

## Reuse points to inspect

- **Run orchestration:** `services/api/app/deeptesting_service.py::execute_run` is the existing DeepTesting service entry point. Its API routes are in `services/api/app/api/routers/deeptesting.py` (run creation/rerun and run retrieval/export routes). Reuse this execution path rather than creating a second simulator.
- **Strategy identity:** DeepTesting resolves the selected node (`node_id`, node version, stored strategy JSON) and persists `fingerprint`, compiler version and engine version on `DeepTestRun` in `packages/storage/src/hero1_storage/instrument_models.py`.
- **Compilation and execution semantics:** `hero1_strategy_spec` compilation and `hero1_exec_semantics.run_backtest` are the shared strategy and execution path. Keep fills, SL/TP, management, exposure, costs, swaps and accounting here; do not duplicate them in a simulation-specific engine.
- **Historical data:** `deeptesting_service.load_candles`, `available_bounds`, and the market-data store/services supply the candle source. Run-bound dataset provenance is currently represented in the run configuration's `data_snapshot` and import-manifest provenance. This needs further evidence-infrastructure completion before treating it as verified.
- **Instrument specifications:** `metadata_service.best_snapshot` / `snapshot_broker_meta` and the MT5 adapter supply specification inputs. The exact run-linked provenance and complete independent verification still need completion; do not treat this handoff as historical broker proof.
- **Risk and sizing:** `RunConfig`, `InstrumentSpec`, and `hero1_exec_semantics.size_position` are the current sizing semantics. Independent sizing verifier coverage is in `services/api/app/execution_evidence_verifiers.py`, but the evidence requirements remain incomplete.
- **Trade execution/accounting:** use `run_backtest` and its result's trade ledger, equity, costs, swap and exit records. Existing corrected gross/net accounting and signed swap handling must be preserved.
- **Decision-event ledger:** `DeepTestDecisionEvent`, `app.decision_ledger.persist_event_batch` and `load_all_events` are the indexed append-only persistence path. The event taxonomy/reconciliation still requires completion before a Monte Carlo audit can rely on it.
- **Audit export/verifier:** `services/api/app/audit_export.py` builds the ZIP; `app.execution_evidence_verifiers` contains initial independent checks. Do not treat its current partial coverage as a passed execution audit.
- **Scenario and filters:** DeepTesting scenario configuration is persisted by the existing scenario service/models; trade filters and result metrics are part of the existing DeepTesting API/result structures. Monte Carlo should consume immutable run configuration and node/fingerprint references, not mutate them.
- **Frontend integration:** existing tab routing and navigation are in `apps/web/src/App.tsx` and related navigation components; DeepTesting's UI is `apps/web/src/pages/DeepTesting.tsx`. Add a Monte Carlo tab only in the separately authorized Monte Carlo implementation task.

## Required gate before reuse for Monte Carlo

Complete the independent decision, causal, reference-level, sizing and provenance verifiers; pass a controlled end-to-end exported-package audit; then obtain and independently verify new Windows MT5-linked DeepTesting audit packages. Passing infrastructure tests alone does not authorize Monte Carlo or establish Stage A compliance.
