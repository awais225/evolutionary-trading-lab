# DATA Layout

The canonical DATA root (owner decision 2026-11-06) is `<PROJECT_ROOT>\DATA`
— directly inside the project root that contains `PREREQUISITES.bat`,
`START.bat`, `pyproject.toml`, `apps\`, `packages\`, `services\`. This is the
single authoritative location for ALL application-generated data and logs.

`HERO1_DATA_ROOT` (environment variable) may override it. Any OTHER path inside
a Git repository checkout is refused by `ensure_outside_repository`
(`packages/storage/src/hero1_storage/engine.py`) — only `<repo>\DATA` (any
nesting under it) is accepted from inside the checkout. When the variable is
unset, the default is `<repo>\DATA` (found by walking up for `.git`), falling
back to `~/.hero1/data` only when no checkout can be located.

Required canonical subdirectories (created by `init_db.py --create` and
`PREREQUISITES.bat`):

```
<PROJECT_ROOT>/DATA/
├── DATABASE/      # registry + per-instrument DB storage root
├── LOGS/          # prerequisite.log, startup.log, backend.log, frontend.log
├── BACKUPS/       # manual or Phase 6 automated backups
├── CACHE/         # transient computation cache
├── MARKET_DATA/   # Phase 3+: candles/ticks (per instrument below repo root)
├── RESEARCH/      # checkpoints and evidence bundles
└── ARTIFACTS/     # exports, IR dumps, reports
```

## Permanent historical baseline (owner directive 2026-11-07)

```
DATA/MARKET_DATA/
├── IMPORTS/<SYMBOL>/*.csv   # the supplied original MT exports — TRACKED IN GIT
├── market_data.db           # canonical candle store (git-ignored, built locally)
└── import_manifest.json     # import registry (git-ignored, built locally)
```

The seven supplied XAUUSD CSVs live permanently at
`DATA/MARKET_DATA/IMPORTS/XAUUSD/` in every checkout. They are the ONLY tracked
files under `DATA/`: `.gitignore` keeps a narrow exception for
`DATA/MARKET_DATA/IMPORTS/<SYMBOL>/*.csv` while `*.db`, manifests and every
other DATA content remain hard-ignored. Each installation imports these CSVs
into its OWN canonical `DATA/MARKET_DATA/market_data.db` (importer discovery
mode / API startup `ensure_baseline()`), so the LMS and Windows dashboards
serve identical history without sharing a database file. See
TROUBLESHOOTING ISSUE-049.

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
- **Reset (dev only):** stop services, delete ONLY the dev database files
  under `<DATA_ROOT>\DATABASE` if a destructive reset is explicitly intended
  (never the production DATA folder; prefer restoring from BACKUPS), then rerun
  `python scripts/init_db.py --create`.
