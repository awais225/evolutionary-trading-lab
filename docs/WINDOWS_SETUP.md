# Windows Setup (RTX 4090 Laptop Target)

## 0. Two-file startup (recommended, zero manual commands)

Two batch files live in the repository root — this is the supported way to run
Hero1 on Windows:

1. **`PREREQUISITES.bat`** — double-click it ONCE per machine. It detects
   Windows/Python 3.11+/Node 20 LTS, creates (or reuses) `.venv`, upgrades pip,
   installs the documented editable Python packages, runs `npm ci`, verifies
   backend imports with the exact `.venv` interpreter, runs the production
   frontend build, checks database integrity and prints a clear success/failure
   report. MT5 is detected but optional; Ollama is optional.
   Canonical DATA root: `<PROJECT_ROOT>\DATA` (the folder containing the BAT
   files). Override by setting `HERO1_DATA_ROOT` before double-clicking,
   e.g. `set HERO1_DATA_ROOT=E:\Hero1Data`. The launcher windows are
   persistent; both stay open (or exit with a visible error + pause) until you
   close them.
2. **`START.bat`** — double-click it whenever you want Hero1. Adapted from the
   proven legacy Evolutionary Trading Lab launcher, it runs in stages:
   project-root detection → venv resolution → backend import check → database
   integrity check → frontend dependency check → **port ownership checks**
   (`scripts\check_port.py`; exit 10 = verified Hero1, 20 = foreign app — never
   killed, never reused silently) → starts the backend in a persistent visible
   window (`HERO1 Backend :8788`) → **real HTTP health polling** (`scripts\wait_ready.py`)
   → **instance identity verification** (`scripts\verify_instance.py`: the process
   answering :8788 must serve THIS repo — `HERO1_REPO_ROOT` — and, for a fresh
   launch, carry this run's `HERO1_START_TOKEN`) → starts the frontend in a
   persistent window (`HERO1 Frontend :5173`, vite `strictPort`) → verifies the
   page AND that the **vite proxy forwards `/health` to our backend** → only then
   opens <http://127.0.0.1:5173/> in the browser → keeps a foreground monitor
   loop in the launcher window. Re-running reuses verified instances instead of
   launching duplicates. Every failure path prints diagnostics and pauses — the
   window never closes silently. For troubleshooting run it as
   `cmd /k START.bat` so all output stays visible, and see
   `<PROJECT_ROOT>\DATA\LOGS\startup.log`.

Logs: `<PROJECT_ROOT>\DATA\LOGS\{prerequisite,startup}.log`
(append-only; what it did, what failed, what to do next). The backend and
frontend print directly to their own service windows by design (the proven
legacy pattern — no pipes), so tracebacks always remain visible there.

**Verification status:** the files were written for Windows 10/11 and reviewed;
the underlying commands (editable installs, `npm ci`, `npm run build`,
`init_db.py --create/--check`, `doctor.py`, health polling) are all
Linux-verified from scratch in a clean environment, but **the .bat files
themselves have NOT been executed on a Windows machine yet** — treat first run
on Windows as a smoke test and report any failure with the log file above.
The canonical in-checkout DATA root is `<PROJECT_ROOT>\DATA` (the folder
containing the BAT files); it is git-ignored and never deleted or reset by these
scripts. `HERO1_DATA_ROOT` overrides it (e.g. `E:\Hero1Data`) for machines with
a dedicated data drive — do not commit it or point it inside `.git`.

This is the runbook for the intended production machine: Windows 11, Intel i9,
RTX 4090 (Laptop, 16 GB VRAM), 32 GB RAM, MT5 installed via MetaTrader 5.

## 0.1 If START.bat fails at any stage (read this FIRST)

START.bat is now a PERSISTENT OUTER shell. The real startup procedure lives in
scripts\START_INTERNAL.bat and runs inside a persistent `cmd /k` session — no
internal failure (missing label, syntax error, Python exception, native crash,
database error, missing dependency) can close the window. If the inner
procedure fails, the window stays open showing the FAILED block: failed stage,
exact command, exit code (signed-crash safe), working directory, the tail of
DATA\LOGS\startup.log, then waits for a keypress. If the procedure ends and
the window drops to a bare `>` prompt, type `EXIT` and press Enter.

START.bat always prints, on startup: its own absolute path, file size, modified
date, SHA-256 (certutil) and the checkout git HEAD. If the window ever closes by
itself, the file you double-clicked is NOT this one — check `git status`,
`git pull`, and re-run from the repository root.

Whatever stage fails, open `TROUBLESHOOTING.txt` in the repository root FIRST
and Ctrl+F the exact error text or the STAGE number. Every entry there is a
verified cause + verified fix with its source; the failure window itself now
prints this pointer. Stage 10 also opens the dashboard automatically; if the
browser does not open, stage 10 prints a [WARN] with the address to open
manually (http://127.0.0.1:5173/) — Hero1 itself is running in that case.

Fastest diagnosis (read-only; no databases created, migrated, repaired or
deleted): double-click `START_DIAGNOSTIC.bat`. It prints the same information
`START.bat` would need, tees everything to `<DATA_ROOT>\LOGS\diagnostic.log`,
and ALWAYS keeps the window open. Attach `diagnostic.log` to any bug report.

Diagnostics for the database stage (run from an already-open CMD window):

```bat
.venv\Scripts\python.exe scripts\init_db.py --check
echo %errorlevel%            REM 0=healthy, 1=unhealthy (JSON report per DB), 2=crashed (traceback)
```

Integrity of the databases themselves (read-only; changes nothing):

```bat
.venv\Scripts\python.exe -c "import sqlite3,glob,os; root=os.environ.get('HERO1_DATA_ROOT') or os.path.join(os.getcwd(),'DATA'); paths=[os.path.join(root,'DATABASE','hero1_registry.db')]+[os.path.join(root,s,'DATABASE','hero1.db') for s in sorted(os.listdir(root)) if s.isupper() and os.path.isdir(os.path.join(root,s))]; [print(p, '->', sqlite3.connect('file:'+p+'?mode=ro', uri=True).execute('PRAGMA integrity_check').fetchall()[0][0]) for p in paths]"
```

Both commands are safe to run repeatedly; neither writes to any database.
Attach `DATA\LOGS\startup.log` and the console output to any bug report.
Static safety net (also runs in CI): `.venv\Scripts\python.exe scripts\audit_launcher.py`.

## 1. Prerequisites

- Python 3.11+ from python.org (check "Add to PATH")
- Node.js 20 LTS
- Git for Windows
- MetaTrader 5 (terminal already logged into the **demo** account)
- Ollama for Windows (<https://ollama.com/download/windows>)

## 2. DATA root

Default (in-checkout, git-ignored): `<PROJECT_ROOT>\DATA` — no configuration
needed. To relocate to a data drive:

```powershell
setx HERO1_DATA_ROOT "E:\Hero1Data"   # persistent: set via System → Environment Variables
```

The app creates the required subtree idempotently on `init_db --create`.
Set the same variable before launching `START.bat` / `PREREQUISITES.bat` if you
relocate DATA.

## 3. Backend

```powershell
cd C:\\path\\to\\hero1-trading-platform
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1

pip install -e .\\packages\\storage -e .\\packages\\eventbus -e .\\packages\\ai_orchestrator `
            -e .\\packages\\strategy_spec -e .\\packages\\exec_semantics -e .\\packages\\indicators `
            -e .\\packages\\research -e .\\packages\\mt5_adapter
pip install -e .\\services\\api
pip install -r requirements-dev.txt

python scripts\\doctor.py          # expect: data root OK, DBs OK, ollama OK, MT5 probe
python -m uvicorn services.api.app.main:app --host 127.0.0.1 --port 8788
```

## 4. MT5 availability, broker metadata, and market data (HERO V1.4.2)

- On Windows only, `pip install MetaTrader5` makes the official package importable;
  `scripts/doctor.py` then reports MT5 as **available** (package import check only).
- A **deep** probe (`python scripts/doctor.py --mt5`) additionally initializes the
  terminal and reads account/terminal info. It never places, modifies or cancels orders.
- Without the package or a running terminal, MT5 status is reported as
  `unavailable`/`unconfigured` — never as healthy. Linux/macOS dev boxes report
  `unavailable` by construction. Nothing is ever simulated in place of a terminal.

Broker metadata (required before any strategy can validate as VALID):

1. Open the MT5 terminal and log in to the broker/server you trade with.
2. In the dashboard, pick the terminal/account in the TopBar MT5 selector
   (this selection applies to Live Testing only).
3. Use Metadata → Refresh for the instrument (e.g. XAUUSD). Hero1 stores a
   snapshot per broker/server/symbol under `DATA\MARKET_METADATA\<server>\`;
   one broker's snapshot is never applied to another broker or server.
4. Validate the strategy again. Until a snapshot exists, validate honestly
   answers `NEEDS_METADATA`; pip/tick/volume values are never typed in by
   hand and never guessed. Snapshots can also be imported (Metadata →
   Import) from a file collected on a machine with a terminal.

Historical candles (MT History Center CSV exports):

```bat
.venv\Scripts\python.exe scripts\import_market_data.py --data-root <DATA_ROOT> XAUUSD_H1.csv XAUUSD_Daily.csv
```

- Name exports `SYMBOL_TIMEFRAME.csv` (e.g. `XAUUSD_M30.csv`, `XAUUSD_Daily.csv`);
  the importer reads instrument/timeframe from the filename (or `--instrument` /
  `--timeframe`, which must agree with the filename) and writes
  `<DATA_ROOT>\MARKET_DATA\market_data.db` + `import_manifest.json`.
- It is all-or-nothing: one bad row rejects the whole file (reason printed per
  line; exit code 1) and writes zero candles. Re-importing the same file is a
  safe no-op; a newer export rolls the data forward. Gaps are reported, never
  filled. MT exports cap at ~100,000 rows per file — export older ranges
  separately for deeper history.
- If a strategy still says `NEEDS_METADATA`, or an import says REJECTED,
  search the exact words in `TROUBLESHOOTING.txt` (repository root).

## 5. Ollama & GPU reality check

- The app talks to Ollama at `HERO1_OLLAMA_HOST` (default `http://127.0.0.1:11434`).
- Default model in `.env.example` is a **14B coder model** as a placeholder —
  pull and benchmark YOUR model before trusting it: `ollama pull qwen2.5-coder:14b-instruct-q5_K_M`.
- **No benchmark has been run on the RTX 4090 laptop from this project yet.**
  Numbers from other machines (or smaller models like qwen2.5:0.5b used in
  smoke tests) are not evidence of 4090 performance. Run a real benchmark and
  record it in docs when available.
- Keep AI strictly off the live path. AI output is never executed as Python.

## 6. Frontend

```powershell
cd apps\\web
npm install
npm run dev     # http://localhost:5173
```

## 7. Database initialization & verification

```powershell
python scripts\\init_db.py --create
python scripts\\init_db.py --check   # exit 0 = healthy
python -m pytest
```

## 8. What still needs Windows + MT5 hardware verification

- MT5 package import, terminal attach, demo order round-trip (Phase 5)
- RTX 4090 inference throughput for the chosen model
- Path/case sensitivity of `D:\\Hero1\\DATA` handling
- Firewall prompts for 127.0.0.1 services

Until those are measured on the target machine, reports keep MT5 at
`unavailable/unconfigured` and GPU claims stay `not_detectable`/unmeasured.
