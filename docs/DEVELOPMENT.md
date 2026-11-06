# Development

## Prereqs

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ (3.13 verified) | |
| Node.js | 20 LTS+ | only for `apps/web` |
| Git | any recent | repo: `main`, no remotes yet |

## Editable install (Linux/macOS)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ./packages/storage -e ./packages/eventbus -e ./packages/ai_orchestrator \
            -e ./packages/strategy_spec -e ./packages/exec_semantics \
            -e ./packages/indicators -e ./packages/research -e ./packages/mt5_adapter
pip install -e ./services/api
pip install -r requirements-dev.txt
```

`scripts/dev_install.sh` wraps exactly this. If `pip` cannot write its global
scripts directory, add `--user` (do not fight system Python).

## Editable install (Windows PowerShell)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
# same pip commands as above with backslashes
```

## Running on Windows (two-file launcher)

Double-click `PREREQUISITES.bat` (once) then `START.bat` (every session).
Details and verification status: [WINDOWS_SETUP.md](WINDOWS_SETUP.md) §0.

## Running

```bash
export HERO1_DATA_ROOT=/abs/path/outside/repo/DATA
python scripts/init_db.py --create    # once
python scripts/doctor.py              # environment report (safe, read-only)
uvicorn services.api.app.main:app --host 127.0.0.1 --port 8788 --reload
cd apps/web && npm run dev            # separate terminal; http://localhost:5173
```

`HERO1_DATA_ROOT` unset → safe default `~/.hero1/data` (outside the repo).
A path inside the repository is rejected loudly.

## Tests

```bash
python -m pytest                 # backend (synthetic tmp data)
python -m pytest -k ollama       # only the Ollama integration test
cd apps/web && npm test          # vitest
cd apps/web && npm run build     # tsc --noEmit + production bundle
```

Notes:
- Backend tests create a fresh DATA root under pytest `tmp_path` — your real
  `~/.hero1/data` is never touched.
- `tests/test_ollama.py` **skips** (never fails) when no local Ollama endpoint
  answers; with Ollama it runs a small structured-output probe against the
  configured model. It does not download models and makes no performance claims.
- `tests/test_repo_hygiene.py` fails if a DATA tree, database file, `.env`, or
  credential-shaped string is tracked by git or present in the repo root.

## Migrations

Two independent alembic setups (registry / instrument) live inside
`packages/storage/src/hero1_storage/alembic/`. The env files are imported
programmatically (no single `alembic.ini`). To add a migration:

1. Update the model module first (`instrument_models.py` or `registry_models.py`).
2. Hand-write a new revision file next to `0001_initial.py`
   (e.g. `0002_add_live_limits.py`) with symmetrical `upgrade()`/`downgrade()`.
3. Verify: `python -m pytest tests/test_alembic_parity.py` — it asserts that a
   database built by `create_all` matches one built by `alembic upgrade head`.
4. `python scripts/init_db.py --create` applies heads to all databases.

## Codebase map

```
packages/storage    engine/session, models, repositories, init, alembic/{registry,instrument}
packages/eventbus   durable publish + in-memory WS fanout (attach_loop + call_soon_threadsafe)
packages/ai_orchestrator  AIProvider ABC, OllamaProvider, JSON validation helpers
services/api        app factory, routers, schemas, deps (DI), config, logging
services/worker     placeholder — Phase 4+ background DeepTest worker
scripts/            init_db.py (create/check) · doctor.py (read-only check) · dev_install.sh
tests/              pytest suite (28 tests) · apps/web src/__tests__ (6 tests)
```

## Troubleshooting

- **Init hangs** → you are likely on old code before the `RLock` fix in
  `engine.py`. Pull latest; no action needed.
- **WS clients never receive events** → ensure uvicorn was started with this
  tree (eventbus uses `attach_loop` + `call_soon_threadsafe`).
- **pip Permission denied (websockets/uvicorn script)** → use `pip install --user`.
- **vite preview/dev proxy errors** → backend must be on `127.0.0.1:8788`.

## Documentation & commit protocol (permanent)

Every commit follows the Phase 1.1 rule (see `CHATGPT.txt` §17–19):
1. Update `CHATGPT.txt` (append/revise — never wholesale-replace) and `CHANGELOG.md`.
2. Update `docs/ISSUES_AND_FIXES.md` when an issue is found/fixed/deferred.
3. Archive the exact task prompt in `PROMPTS/` before executing it.
4. Update `docs/ARCHITECTURE.md` when architecture changes.
5. Run relevant tests, inspect the staged diff, verify no DATA/secrets staged.
The docs travel **in the same commit** as the change they describe.
