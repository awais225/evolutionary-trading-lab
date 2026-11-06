# DATA Layout

The DATA root is chosen by `HERO1_DATA_ROOT` (environment variable). The
default is `~/.hero1/data` — deliberately **outside** the repository — and the
application refuses any DATA root located inside a repository checkout
(`ensure_outside_repository`, `packages/storage/src/hero1_storage/engine.py`).

Production (Windows): `D:\Hero1\DATA`. Development: `~/.hero1/data` or any
absolute path outside the repo. The repository `.gitignore` additionally blocks
`DATA/`, `*.db*`, `*.sqlite*`, backups, logs and model files.

## Required tree (managed by code — never by hand in production)

```
DATA/
├── DATABASE/
│   └── hero1_registry.db            # global registry (alembic: registry/*)
├── MT5/
│   ├── accounts/                    # Phase 5: account metadata (NOT passwords)
│   ├── symbols/                     # Phase 5: symbol specs & sessions
│   └── connection_logs/             # Phase 5: redacted connection attempts
├── XAUUSD/
│   ├── MARKET_DATA/                 # Phase 3+: tick/candle stores (Parquet)
│   ├── NODES/
│   │   ├── CUSTOM/ WWW/ INTERACTIVE/ FINE_TUNED/
│   ├── DEEPTEST/                    # Phase 4+: scenario artifacts & exports
│   ├── LIVE_TESTING/                # Phase 5+: redacted live journals
│   ├── RESEARCH/                    # Phase 7+: checkpoints & evidence bundles
│   └── DATABASE/hero1.db            # per-instrument database (alembic: instrument/*)
├── EURUSD/  …same shape…
└── BTCUSD/  …same shape…
```

`scripts/init_db.py --create` creates missing directories, applies the correct
alembic head to each database, and seeds the instrument registry
(XAUUSD, EURUSD, BTCUSD — display names, asset classes, M15 defaults). It is
idempotent; rerunning never destroys data.

## Two database namespaces

| | Registry (`hero1_registry.db`) | Per-instrument (`<SYMBOL>/DATABASE/hero1.db`) |
|---|---|---|
| Tables | instruments, application_settings, global_events, alembic_version | nodes, node_lineage, backtest_runs, deeptest_scenarios, deeptest_trades, research_tasks, research_findings, research_sources, live_sessions, live_configs, live_trades, approvals, activity_events, alembic_version |
| Clone behavior | one global file | independent files; ids may repeat across instruments by design |
| Reference | only place `instruments/*` lives | never references registry tables |

Composite indexes cover the hot paths: `(instrument, origin)`,
`(instrument, status)`, `(instrument, approval_state)` on nodes;
`(instrument, created_at)` on activity events; `(category, severity, id)` and
`(instrument, id)` on global events.

## Reserved behaviors

- **No silent fallbacks.** Unknown instrument → hard error before opening any file.
- **No DATA in git.** Tests use `tmp_path`; `test_repo_hygiene.py` asserts no
  `DATA/` tree or `*.db*` exists inside the repository and `.env` is not tracked.
- **Backups** (user responsibility, Phase 6): copy `DATA/` while services are
  stopped; never partial-copy WAL files mid-write.
- **Reset (dev only):** delete `~/.hero1/data` and rerun
  `python scripts/init_db.py --create`.
