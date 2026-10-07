# Launcher Fix — “the normal launcher must serve the current frontend”

**Commits:** `ab2c18f2df06d7083006d24ec5e590511c97b98a` — `fix: serve current frontend from normal launcher`
and `f3cf751` — `fix: build current frontend in the launcher and packager` (packaging scripts)
**Previous HEAD:** `97cbf90` · **Branch:** `main` · **Pushed to:** `origin` (github.com/awais225/evolutionary-trading-lab)
**Reported symptom:** `npm run dev` on `:5173` showed the V5 interface, the normal launcher showed the old interface, while the backend itself was healthy (`/health` V3.6, bridge `mt5_real`, MT5 CONNECTED, data_feed LIVE).

---

## 1. Exact root cause

`frontend/dist` is a **generated artifact** and is **not tracked** (`.gitignore:5` → `dist/`), so:

1. it is **never updated by `git pull`**, and it does not exist at all after a fresh clone;
2. the backend serves the dashboard from that folder (`StaticFiles(FRONTEND_DIST)` mounted on `/`);
3. **old `START.bat` STAGE 4 only checked that `frontend\dist\index.html` existed** — never whether it had been built from the `frontend\src` currently on disk;
4. `backend/tools/check_port.py` returns **10** for *anything* that answers `/health` with HTTP 200, and the old STAGE 5 reacted to `10` by simply doing `start http://127.0.0.1:8787/`.

So in the reported case the source tree was V5, but the folder being served contained an **older bundle**, and the launcher neither rebuilt it nor noticed. The already-running process kept serving the old interface. This is a **launcher / static-serving defect, not a Git problem**: commit `6d025ae`, GitHub `main` and the working tree all contained the V5 source (verified file by file against `raw.githubusercontent.com`).

**Not the cause:** branch/remote state, Vite config, `package.json`, the build pipeline itself, the backend API, DATA, MT5/bridge logic, “another copy” of the app.

## 2. Files changed

| File | Change |
|---|---|
| `backend/app/frontend_build.py` | **new** (271 lines) — source fingerprint, build stamp, bundle status, `/system/build` payload |
| `backend/tools/frontend_build_guard.py` | **new** (387 lines) — launcher CLI (`--check/--stamp/--summary/--verify-served/--identify/--stop`) |
| `tests/test_frontend_serving.py` | **new** (321 lines) — 15 tests for the whole mechanism |
| `start.bat` | +122/−43 — STAGE 4 build gate, STAGE 5 verify-then-reuse/replace, post-start verification |
| `backend/app/main.py` | +51/−1 — `DashboardStaticFiles` (no-store HTML, immutable assets), `/system/build`, startup bundle identity log |
| `backend/app/doctor.py` | +22/−5 — reports bundle identity instead of only file size |
| `Prerequisite.bat`, `pre-requisite.bat` | +1 each — stamp the bundle after building it |
| `repair.bat` | +9/−1 — stamp after rebuild; fail loudly when npm is missing and the bundle is not verifiable |
| `scripts/build_exe.bat` | +83/−12 — verify/rebuild/stamp the bundle **before** PyInstaller embeds it in `EvolutionaryTradingLab.exe` |
| `scripts/build_and_serve.bat` | +22/−… — same gate before serving; skip the rebuild when the bundle already matches |

Launcher fix: 9 files, **1,185 insertions(+), 50 deletions(-)**; packaging follow-up: 2 files, **93 insertions(+), 12 deletions(-)**. No trading, research, evolution, backtest, DATA, DB, MT5 or UI source was touched; `frontend/src` stays the source of truth and the production build still lands in `frontend/dist`.

## 3. The fix

1. **Build identity is now provable.** `frontend_build.py` hashes `frontend/src` + `index.html` + `package.json` + `package-lock.json` + `vite.config.js` + tsconfig files, and writes the result into `frontend/dist/build-info.json` (`--stamp`). `check_dist()` classifies the bundle: `FRESH / STALE / UNSTAMPED / CORRUPT / BROKEN / MISSING`. **`UNSTAMPED` counts as “rebuild required”** — a bundle of unknown origin is never presented as current.
2. **The launcher acts on it.** `START.bat` STAGE 4 runs `--check`; when the bundle is stale or missing it does `npm install` (only if `node_modules` is absent) → `npm run build` → `--stamp` → `--check` again. If npm is not available the launcher **stops with an explicit message** instead of serving a stale dashboard.
3. **A running instance must prove itself.** `GET /system/build` (new) publishes the repository root, the source hash and the bundle status the instance actually serves. STAGE 5 now runs `--verify-served` before reusing an occupied port: same root **and** current source hash → reuse; another repository (exit 11), older build (12) or a pre-`/system/build` instance (13) → replace through the lab’s own graceful shutdown; unrelated software on the port is never touched (STAGE 5 aborts, `--stop` exits 3).
4. **No browser cache can mask a rebuild.** `index.html` is served `Cache-Control: no-store, must-revalidate`; content-hashed `/assets/*` files stay `immutable`.
5. **Everything else follows suit:** `Prerequisite.bat` / `pre-requisite.bat` / `repair.bat` stamp the bundle they build, and `doctor.py` reports the build identity.
6. **Packaging cannot bake a stale bundle any more.** `EvolutionaryTradingLab.spec` / `EvolutionaryTradingResearchLab.spec` embed `frontend/dist` verbatim, so `scripts/build_exe.bat` now runs the same gate before PyInstaller: `--check` → (only if needed) `npm install`/`npm run build` → `--stamp` → `--check` → prints the bundle identity it is about to embed, and aborts with exit 1 when npm is missing, the stamp fails, or the bundle still does not match. `scripts/build_and_serve.bat` behaves the same way before serving. No executable is rebuilt or committed here — packaging is an operator step, and it is now impossible to produce a stale one silently.

Nothing machine-specific is hardcoded — every path resolves from `%ROOT%` / the script’s own location.

## 4. Build result

| Check | Result |
|---|---|
| `npx vite build` in `frontend/` | ✓ built in 5.21 s (1012 modules) |
| guard on a bundle-less tree | `GUARD_STATUS=MISSING`, exit **10** (rebuild required) |
| guard right after a manual build | `GUARD_STATUS=UNSTAMPED`, exit **10** (unverifiable) |
| `--stamp` then `--check` | `GUARD_STATUS=FRESH`, exit **0** |
| guard after editing `frontend/src/pages/DeepBacktest.jsx` | `GUARD_STATUS=STALE`, exit **10** (`SRC_HASH ebb1c6cb47bc48ca → bb254a5873bc4e94`) |
| rebuild + `--stamp` | `FRESH` again |
| fresh clone (no `frontend/dist`) | `MISSING` → `npm install` → `npm run build` (4.86 s) → `--stamp` → `FRESH` |
| packaging stage (`scripts/build_exe.bat` sequence, stale dist) | `--check` → `STALE` → rebuild (5.55 s) → `--stamp` → `--check` → `FRESH`, `src_hash 2b391dfad2454ac2`, 55 files — the identity PyInstaller would embed |

## 5. Launcher test result

`START.bat` cannot run under `cmd.exe` in this environment, so its stages were executed with the **same tools in the same order** (`launcher_flow_test.sh`), and `start.bat` itself was reviewed for label/`goto` integrity (16 labels, all targets defined, `popd` balanced on every failure path).

| Scenario | Result |
|---|---|
| Fresh clone, nothing running | STAGE 4 build → STAGE 5 free port → STAGE 7 `/health` OK → STAGE 8 `SERVED_STATUS=FRESH`, `SAME_ROOT=True` — **exit 0** |
| Instance of this repo already running, bundle fresh | verify → `SERVED_MATCHES_LOCAL_SRC=True` → **reused** (exit 0, browser opened) |
| **Reported bug**: source changed while the old instance was running | `--check` → `STALE`; `--verify-served` → exit **12** (“serves an older/different frontend build”); flow rebuilt + re-stamped → the *already running* instance then served the new chunk over HTTP (a temporary marker in `DeepBacktest.jsx` appeared in the served `assets/DeepBacktest-*.js`; the marker was reverted and the bundle rebuilt before committing) |
| Instance on the port from another checkout (`/tmp/other_checkout`) | `SAME_ROOT=False` → exit 11 → replaced via `POST /api/power/shutdown` (`STOP_METHOD=power_api`, `STOPPED=True`) → own backend started |
| Instance without `/system/build` (stub) | exit **13** → replace |
| Nothing listening | exit **14** |
| **Unrelated** program on the port | `--identify` exit 1, `--stop` exit **3**, the process was verified still alive afterwards |

Served-content proof from the running instance: `index.html` byte-identical to `frontend/dist/index.html` and served `no-store`; 24 lazy chunks (643 kB) fetched over HTTP; markers `Deep Backtest`, `Show Failed`, `Are you sure you want to close the dashboard?`, `SHUTDOWN DASHBOARD`, `Prop-firm monitor`, `DEMO ACCOUNT ONLY`, `IDLE ON ENTRY` all present. `/system/build` returned `serving=static`, `dist_status=FRESH`, `dist_matches_src=true`, `src_file_count=55`, `entry_assets=[assets/index-Dn4Yw0Ji.js, assets/index-DY0FUd-S.css]`, `reasons=[]`.

**Regression tests:** `node frontend/tests/v48_ui_smoke.mjs` **47/47**; full suite **391 passed / 1 warning in 59.36 s** (`EVOLUTIONARY_LAB_DATA_ROOT=/opt/lmsarena-storage/v5_testdata`; DATA provisioned verbatim: 10,787 strategies, 10,000 USER_RESEARCH + 787 LEGACY_TEST); render smoke `frontend/tests/v48_ui_smoke.mjs` **47/47**; the 15 new tests are in `tests/test_frontend_serving.py`.

## 6. Git

```
ab2c18f  fix: serve current frontend from normal launcher    <- the fix
then     two lmsarena.txt evidence/result entries, then this report
```

All of them are pushed to `origin/main`; `git rev-parse HEAD == git rev-parse origin/main`,
and the commit `ab2c18f` was cross-checked through the GitHub API (it lists exactly the
nine files of the launcher fix; `raw.githubusercontent.com` returns HTTP 200 for the three
new files). The packaging follow-up `f3cf751` touches only
`scripts/build_exe.bat` and `scripts/build_and_serve.bat`; the exact push range and final
HEAD are recorded in `lmsarena.txt` ("PUSH RESULT" entries).

Committed and pushed are **only** the files in §2 plus the log files (`lmsarena.txt`,
`chatgpt.txt`) and this report. Never staged: `DATA/**`, database/WAL/SHM, `LOGS/*`,
runtime `CONFIG` files, `frontend/dist` and `frontend/dist/build-info.json` (generated;
`git check-ignore` → `.gitignore:5:dist/`), `node_modules`, virtualenvs, secrets or
credentials.

## 7. What the user sees on the next normal launch

Double-click `START.bat`. It now rebuilds `frontend/dist` from `frontend/src` when it does not match, replaces a running instance that serves an older build or another repository (never unrelated software), and verifies the dashboard it serves afterwards. No manual `npm` step is required, and the same flow works from a fresh clone.
