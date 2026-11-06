# Hero1 — Trading Research Workstation

> **HERO V1.1.0** — canonical repository: <https://github.com/awais225/evolutionary-trading-lab> (`main`).
> The original Evolutionary Trading Lab project is preserved on the `legacy-archive` branch.

Hero1 is a local-first research and execution platform for algorithmic trading.
It is built around **instrument isolation** (XAUUSD, EURUSD, BTCUSD are separate
data universes end-to-end) and a strict **research → test → qualify → approve →
live** pipeline. No strategy reaches live trading without an explicit, separate
approval record.

> **Status: Phase 2 — Strategy specification, validation & deterministic compiler.**
> Storage, API, event system, web shell, docs and tests are in place; strategies
> are now typed, versioned JSON specs that pass server-side validation and a
> deterministic SHA-256 fingerprinting compiler. Backtesting, research agents,
> fine-tuning and live execution are **not implemented yet** — see
> [docs/PHASE_ROADMAP.md](docs/PHASE_ROADMAP.md). The WWW Research / Interactive
> WWW / DeepTesting / Fine Tuning / Live Testing / LOG tabs are clearly marked
> placeholders; they contain no fake functionality.

## Why this project exists

A disciplined, auditable path from *idea* → *evidence* → *approval*:

1. **Find or invent** a strategy (manual, web research, conversational AI).
2. **Prove it** on out-of-sample data, walk-forward, robustness and stress tests —
   one shared execution engine, never a per-mode copy.
3. **Approve it** explicitly before any capital is involved. Live approval is a
   separate record from testing qualification, and AI can never grant it.

## Architecture at a glance

```
apps/web (React + TS + Vite)          services/api (FastAPI)
  8 tabs · LIVE FEED · instrument      REST /api/v1/* · WS /ws/v1/events
  context · new visual identity            │
packages/                                   ▼
  storage (SQLAlchemy 2 + Alembic) ── registry DB (global) + 1 SQLite per instrument
  eventbus (durable events + replay)      XAUUSD · EURUSD · BTCUSD
  strategy_spec (Phase 2: DSL · validator · compiler) · exec_semantics
  · indicators · research (Phase 3+ skeletons)
  ai_orchestrator (provider interface + Ollama) · mt5_adapter (safe probe only)
```

Full details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
data tree: [docs/DATA_LAYOUT.md](docs/DATA_LAYOUT.md)

## Quick start

### Windows (two files, no manual commands)

Double-click **`PREREQUISITES.bat`** once, then **`START.bat`** whenever you
want Hero1 — it verifies the environment, database and ports, starts the
backend (:8788) and frontend (:5173) in their own persistent windows, polls
real HTTP readiness, verifies the answering server is THIS Hero1 instance
(never a bare HTTP 200), opens the browser only after both are verified, and
keeps a visible monitor loop. Logs: `<PROJECT_ROOT>\DATA\LOGS\startup.log`.
Troubleshoot with `cmd /k START.bat`. See
[docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md).

### 1. Backend

```bash
# Python 3.11+ (3.13 verified). From repo root:
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ./packages/storage -e ./packages/eventbus -e ./packages/ai_orchestrator \
            -e ./packages/strategy_spec -e ./packages/exec_semantics -e ./packages/indicators \
            -e ./packages/research -e ./packages/mt5_adapter -e ./services/api
pip install -r requirements-dev.txt

# Canonical DATA root is <repo>/DATA (git-ignored). Override if needed:
export HERO1_DATA_ROOT=/absolute/path/to/DATA             # Windows: $env:HERO1_DATA_ROOT="D:\Hero1\app\DATA"

python scripts/init_db.py --create     # create + migrate + seed registries
python scripts/doctor.py               # read-only environment check
python -m uvicorn services.api.app.main:app --host 127.0.0.1 --port 8788
```

API docs: <http://127.0.0.1:8788/docs> · health: <http://127.0.0.1:8788/health>

### 2. Frontend

```bash
cd apps/web
npm install
npm run dev        # http://localhost:5173 (proxies /api, /health, /ws → :8788)
```

Production build: `npm run build` → `apps/web/dist/`.

### 3. Tests

```bash
# Backend (synthetic temp data only — never touches a real DATA root)
python -m pytest

# Frontend
cd apps/web && npm test && npm run build
```

## What is implemented (through Phase 2) vs pending

| Area | Status |
|---|---|
| Repo, structure, CI workflow, `.gitignore` hard rules | ✅ implemented |
| Registry DB + per-instrument SQLite + Alembic migrations | ✅ implemented |
| Nodes (origin taxonomy CUSTOM/WWW/INTERACTIVE/FINE_TUNED), CRUD | ✅ API + DB |
| Strategy spec DSL + validator + deterministic compiler (IR, SHA-256 fingerprint) | ✅ packages/strategy_spec (Phase 2) |
| Immutable node versioning (parent links, server-computed fingerprints, lineage) | ✅ API + DB (Phase 2) |
| Settings (persisted, validated) | ✅ implemented |
| Global event log + WebSocket feed + since_id replay | ✅ implemented |
| `doctor` environment checker | ✅ implemented |
| Ollama provider (health + small structured-output probe) | ✅ implemented, **small models only** — no RTX 4090 performance claims |
| MT5 adapter | ⚠️ safe availability probe only — **no orders, no execution** (Phase 5, Windows) |
| Manual Nodes UI, Settings UI, navigation shell, LIVE FEED | ✅ implemented |
| WWW Research / Interactive WWW / DeepTesting / Fine Tuning / Live Testing / LOG | ⛔ placeholders, phases 4–8 |
| Backtester, testing engine, approvals, live trading | ⛔ not started — see roadmap |

## Safety guarantees (by construction)

- **DATA lives only in the canonical, git-ignored `<PROJECT_ROOT>\DATA`.**
  Any other in-repository path is refused; nothing under `DATA/` is ever committed.
- Instruments are **never** silently defaulted. Every instrument-specific endpoint
  requires an explicit, validated identifier (`XAUUSD`/`EURUSD`/`BTCUSD`).
- Live trading requires a separate `Approval` record. Nothing in this release can
  activate, arm, or place orders. The MT5 adapter contains no order code at all.
- AI output is never executed as Python. Provider interface is vendor-neutral.
- Secrets: `.env` is git-ignored; `.env.example` contains dummies only;
  logs redact secrets server-side (LOG tab, Phase 6).

## Documentation

- [CHATGPT.txt](CHATGPT.txt) — primary project handoff (start here)
- [CHANGELOG.md](CHANGELOG.md) — version history · [docs/ISSUES_AND_FIXES.md](docs/ISSUES_AND_FIXES.md) — issue register · [PROMPTS/](PROMPTS/) — prompt archive
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — modules, layers, event flow, API surface
- [docs/DATA_LAYOUT.md](docs/DATA_LAYOUT.md) — DATA tree, registry vs instrument DBs, path validation
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — dev setup, tests, migrations, troubleshooting
- [docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md) — Windows 11 + MT5 + RTX 4090 laptop runbook
- [docs/PHASE_ROADMAP.md](docs/PHASE_ROADMAP.md) — phase plan 1→9 with honest status
- [docs/SECURITY.md](docs/SECURITY.md) — secrets, credentials, approvals, redaction

## License / ownership

Owner: awais225. Release identity HERO V1.1.0 (2026-11-06). Repository is public by explicit owner decision.
