# Windows Setup (RTX 4090 Laptop Target)

This is the runbook for the intended production machine: Windows 11, Intel i9,
RTX 4090 (Laptop, 16 GB VRAM), 32 GB RAM, MT5 installed via MetaTrader 5.

## 1. Prerequisites

- Python 3.11+ from python.org (check "Add to PATH")
- Node.js 20 LTS
- Git for Windows
- MetaTrader 5 (terminal already logged into the **demo** account)
- Ollama for Windows (<https://ollama.com/download/windows>)

## 2. DATA root

```powershell
$env:HERO1_DATA_ROOT = "D:\\Hero1\\DATA"   # persistent: set via System → Environment Variables
```

The app creates the required subtree idempotently on `init_db --create`.
Nothing is ever written into the git checkout.

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

## 4. MT5 availability (Phase 5 groundwork only)

- On Windows only, `pip install MetaTrader5` makes the official package importable;
  `scripts/doctor.py` then reports MT5 as **available** (package import check only).
- A **deep** probe (`python scripts/doctor.py --mt5`) additionally initializes the
  terminal and reads account/terminal info. It never places, modifies or cancels orders.
- Without the package or a running terminal, MT5 status is reported as
  `unavailable`/`unconfigured` — never as healthy. Linux/macOS dev boxes report
  `unavailable` by construction.

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
