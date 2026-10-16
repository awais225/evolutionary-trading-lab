# V9.2 implementation and verification report

**In verification — not a claim of completed publication.**

The7 attached CSVs have been imported into permanent monthly operational partitions under the explicitly user-confirmed IC Markets New-York-close seasonal timezone. Full persistent readback counts are M1 100057, M5 100012, M15 100856, M30 71441, H1 35738, H4 9349, D1 1560. Independent parsing compared every UTC timestamp, OHLC value, tick volume, real volume and spread against the persisted bars.

An actual isolated Deep run of the real persisted Node718 genome consumed4131 baseline M1 bars through the existing engine and completed without mutating its strategy/generation backtest records. Qualified population before/after that run was99 from real restored history, not a constant.

Implemented: full-frame historical CSV merges, profile-independent canonical reads and exact snapshots, failed/interrupted write recovery, a shared bounded incremental MT5 job/controls, startup population readiness and durable table preferences, signed offsets with legacy migration, truthful log evidence/color, versioned shared Inspect/Live filters, saved scenarios and SCHEDULE assignment, and optional separate generation-window metrics.

Supporting reports:
- V9_2_DATA_IMPORT_AND_TIMEZONE_AUDIT.md
- V9_2_MT5_HISTORY_AND_GITHUB_DATA_AUDIT.md
- V9_2_QUALIFIED_NODE_AND_IDENTITY_AUDIT.md
- V9_2_INSPECT_SCENARIO_AND_GENERATION_REPORT.md

Full final gates, performance comparison, exact source/build identity, preservation review and remote publication verification are pending. Linux cannot verify real MT5 retrieval, Windows/Edge behavior or live execution; no real order has been sent.
