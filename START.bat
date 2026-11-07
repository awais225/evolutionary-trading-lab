@echo off
setlocal EnableDelayedExpansion
title HERO1 Trading Research Workstation

REM ===========================================================================
REM  HERO1 - MAIN LAUNCHER (Windows cmd.exe)
REM  Adapted from the PROVEN legacy Evolutionary Trading Lab v10.4-throughput
REM  start.bat (root detect -^> venv -^> import check -^> DB verify -^> port
REM  ownership -^> persistent service windows -^> real HTTP readiness -^>
REM  instance verification -^> browser -^> foreground monitor loop).
REM  HERO1 differences (verified against this repository - do not "fix" blindly):
REM    * backend package dir  : services\api      (entrypoint app.main:app)
REM    * frontend             : Vite dev server on 5173 (strictPort)
REM    * vite proxies /api, /health and /ws to the backend on 8788
REM    * DATA root            : <repo>\DATA (override: set HERO1_DATA_ROOT)
REM  Exit-code contract for every external call: the rc is captured into RC and
REM  logged; success requires rc == "0" EXACTLY (string compare, never errorlevel,
REM  which mis-evaluates negative native-crash codes). On any non-zero rc the
REM  launcher prints the failed command, its exit code and the tail of
REM  DATA\LOGS\startup.log, then waits for a keypress. It can never exit
REM  silently. Windows test status: UNTESTED on a real Windows host - see docs.
REM ===========================================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"
set "RC=%errorlevel%"
if not "%RC%"=="0" (
  set "FAILED_RC=%RC%"
  goto :ROOT_FAIL
)

if not "%HERO1_DATA_ROOT%"=="" (
  set "DATA_ROOT=%HERO1_DATA_ROOT%"
) else (
  set "DATA_ROOT=%ROOT%\DATA"
)
if not defined HERO1_DATA_ROOT set "HERO1_DATA_ROOT=%DATA_ROOT%"
set "LOG_DIR=%DATA_ROOT%\LOGS"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
if not exist "%LOG_DIR%" goto :LOG_FAIL
set "STARTUP_LOG=%LOG_DIR%\startup.log"

echo. >> "%STARTUP_LOG%"
echo =========================================================== >> "%STARTUP_LOG%"
echo HERO1 LAUNCHER  %DATE% %TIME% >> "%STARTUP_LOG%"
echo  root=%ROOT%  data=%DATA_ROOT% >> "%STARTUP_LOG%"
echo =========================================================== >> "%STARTUP_LOG%"

echo ===========================================================
echo  HERO1 TRADING RESEARCH WORKSTATION
echo  Project root: %ROOT%
echo ===========================================================
echo.

REM ---- STAGE 1: required files ---------------------------------------------
if not exist "%ROOT%\services\api\app\main.py"        goto :MISSING_MAIN
if not exist "%ROOT%\apps\web\package.json"           goto :MISSING_WEB
if not exist "%ROOT%\apps\web\vite.config.ts" if not exist "%ROOT%\apps\web\vite.config.mjs" goto :MISSING_VITE
if not exist "%ROOT%\apps\web\node_modules"           goto :MISSING_NODE_MODULES
if not exist "%ROOT%\scripts\run_backend.cmd"         goto :MISSING_BACKEND_RUNNER
if not exist "%ROOT%\scripts\run_frontend.cmd"        goto :MISSING_FRONTEND_RUNNER
if not exist "%ROOT%\scripts\init_db.py"            goto :MISSING_UTILS
if not exist "%ROOT%\scripts\check_port.py"           goto :MISSING_UTILS
if not exist "%ROOT%\scripts\wait_ready.py"           goto :MISSING_UTILS
if not exist "%ROOT%\scripts\verify_instance.py"      goto :MISSING_UTILS
if not exist "%ROOT%\packages\storage\src\hero1_storage\__init__.py" goto :MISSING_STORAGE
echo [OK] Required files present.

REM ---- STAGE 2: virtual environment -----------------------------------------
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  echo [ACTION] Virtual environment missing -^> running PREREQUISITES.bat once...
  echo [%DATE% %TIME%] venv missing; calling PREREQUISITES.bat >> "%STARTUP_LOG%"
  call "%ROOT%\PREREQUISITES.bat"
)
if not exist "%VENV_PY%" goto :NO_VENV
echo [OK] Python: %VENV_PY%

REM ---- STAGE 3: backend import verification ----------------------------------
echo [..] Verifying backend imports...
"%VENV_PY%" -c "import sys; sys.path.insert(0, r'%ROOT%\services\api'); import app.main; print('[OK] backend import')" >> "%STARTUP_LOG%" 2>&1
set "RC=%errorlevel%"
echo [..] backend import exit code: %RC%  (details appended to %STARTUP_LOG%)
if not "%RC%"=="0" (
  set "FAILED_RC=%RC%"
  goto :BACKEND_IMPORT_FAIL
)
echo [OK] Backend application import verified.

REM ---- STAGE 4: database verification ----------------------------------------
echo [..] Verifying database integrity (read-only check)...
set "DB_CHECK_OUT=%TEMP%\hero1_dbcheck.out"
"%VENV_PY%" "%ROOT%\scripts\init_db.py" --check > "%DB_CHECK_OUT%" 2>&1
set "RC=%errorlevel%"
type "%DB_CHECK_OUT%"
type "%DB_CHECK_OUT%" >> "%STARTUP_LOG%" 2>&1
del "%DB_CHECK_OUT%" >nul 2>&1
echo [..] database check exit code: %RC%
if not "%RC%"=="0" (
  set "FAILED_RC=%RC%"
  goto :DB_CHECK_FAIL
)
echo [OK] Database verified.

REM ---- STAGE 5: frontend dependency check (dev server serves src) ------------
REM  Vite serves apps\web\src directly, so this launcher can never serve a stale
REM  production bundle. A stale/missing apps\web\dist is reported as an advisory
REM  only; build it with "npm run build" in apps\web when you need one.
if exist "%ROOT%\apps\web\dist\index.html" (
  echo [INFO] Production dist present (advisory; dev server serves live source).
) else (
  echo [INFO] No production dist - dev server will serve live source. To create one: npm run build ^(in apps\web^)
)

REM ---- STAGE 6: port ownership + existing-instance detection -----------------
set "BACKEND_REUSE=0"
set "FRONTEND_REUSE=0"
set "FAILED_RC="

"%VENV_PY%" "%ROOT%\scripts\check_port.py" 8788
set "RC=%errorlevel%"
echo [..] port 8788 check exit code: %RC%  (0=free, 10=verified Hero1, 20=foreign)
if "%RC%"=="20" goto :PORT_BACKEND_FOREIGN
if "%RC%"=="10" set "BACKEND_REUSE=1"
if not "%RC%"=="0" if not "%RC%"=="10" if not "%RC%"=="20" (
  set "FAILED_RC=%RC%"
  goto :PORT_CHECK_FAILED
)

"%VENV_PY%" "%ROOT%\scripts\check_port.py" --frontend 5173
set "RC=%errorlevel%"
echo [..] port 5173 check exit code: %RC%  (0=free, 10=verified Hero1, 20=foreign)
if "%RC%"=="20" goto :PORT_FRONTEND_FOREIGN
if "%RC%"=="10" set "FRONTEND_REUSE=1"
if not "%RC%"=="0" if not "%RC%"=="10" if not "%RC%"=="20" (
  set "FAILED_RC=%RC%"
  goto :PORT_CHECK_FAILED
)

if "%BACKEND_REUSE%"=="1" (
  echo [..] Port 8788 answers Hero1 -^> verifying it serves THIS repository...
  "%VENV_PY%" "%ROOT%\scripts\verify_instance.py" --mode backend --url http://127.0.0.1:8788/health --root "%ROOT%" >> "%STARTUP_LOG%" 2>&1
  set "RC=%errorlevel%"
  echo [..] backend reuse identity check exit code: %RC%
  if not "%RC%"=="0" (
    set "FAILED_RC=%RC%"
    goto :BACKEND_FOREIGN_REUSE
  )
)

REM ---- STAGE 7: launch token --------------------------------------------------
set "HERO1_START_TOKEN=HERO1-%RANDOM%%RANDOM%-%RANDOM%"
set "HERO1_REPO_ROOT=%ROOT%"
echo [INFO] Launch token: %HERO1_START_TOKEN% >> "%STARTUP_LOG%"

REM ---- STAGE 8: backend (persistent visible window) ---------------------------
if "%BACKEND_REUSE%"=="1" goto :BACKEND_READY
echo [..] Starting HERO1 backend in its own terminal window...
start "HERO1 Backend :8788" cmd /k call "%ROOT%\scripts\run_backend.cmd"
echo [..] Waiting for the backend (health + Hero1 identity)...
call "%VENV_PY%" "%ROOT%\scripts\wait_ready.py" http://127.0.0.1:8788/health 120 --require backend >> "%STARTUP_LOG%"
set "RC=%errorlevel%"
echo [..] backend readiness exit code: %RC%
if not "%RC%"=="0" (
  set "FAILED_RC=%RC%"
  goto :BACKEND_TIMEOUT
)

REM post-start identity check: the answering process must be THIS launch
"%VENV_PY%" "%ROOT%\scripts\verify_instance.py" --mode backend --url http://127.0.0.1:8788/health --root "%ROOT%" --token "%HERO1_START_TOKEN%" >> "%STARTUP_LOG%" 2>&1
set "RC=%errorlevel%"
echo [..] backend identity verification exit code: %RC%
if not "%RC%"=="0" (
  set "FAILED_RC=%RC%"
  goto :BACKEND_NOT_OURS
)

:BACKEND_READY
echo [OK] Backend serving at http://127.0.0.1:8788

REM ---- STAGE 9: frontend (persistent visible window) --------------------------
if "%FRONTEND_REUSE%"=="1" goto :FRONTEND_READY
echo [..] Starting HERO1 frontend (vite dev) in its own terminal window...
start "HERO1 Frontend :5173" cmd /k call "%ROOT%\scripts\run_frontend.cmd"
echo [..] Waiting for the frontend...
call "%VENV_PY%" "%ROOT%\scripts\wait_ready.py" http://127.0.0.1:5173/ 120 --require frontend >> "%STARTUP_LOG%"
set "RC=%errorlevel%"
echo [..] frontend readiness exit code: %RC%
if not "%RC%"=="0" (
  set "FAILED_RC=%RC%"
  goto :FRONTEND_TIMEOUT
)

:FRONTEND_READY
echo [..] Verifying frontend -^> backend connectivity through the vite proxy...
"%VENV_PY%" "%ROOT%\scripts\verify_instance.py" --mode frontend-proxy --url http://127.0.0.1:5173/health
set "RC=%errorlevel%"
echo [..] frontend-proxy exit code: %RC%
if not "%RC%"=="0" (
  set "FAILED_RC=%RC%"
  goto :PROXY_FAIL
)
echo [OK] Frontend and backend are connected.
echo.

REM ---- STAGE 10: browser ------------------------------------------------------
start "" "http://127.0.0.1:5173/?v=%HERO1_START_TOKEN%"
echo [OK] Browser opened at http://127.0.0.1:5173/
echo.

echo ===========================================================
echo  HERO1 IS RUNNING
echo ===========================================================
echo  Dashboard : http://127.0.0.1:5173/
echo  Backend   : http://127.0.0.1:8788/health
echo  Logs      : %STARTUP_LOG%
echo.
echo  Keep this window open - it monitors the backend.
echo  Service windows stay open on purpose; close them to stop.
echo ===========================================================
echo.

REM ---- STAGE 11: foreground monitor loop --------------------------------------
set "MONITOR_TICKS=0"
:MONITOR_LOOP
timeout /t 5 /nobreak >nul
set /a MONITOR_TICKS=MONITOR_TICKS+1
"%VENV_PY%" "%ROOT%\scripts\check_port.py" 8788 >nul 2>&1
set "BE_RC=!ERRORLEVEL!"
if not "!BE_RC!"=="10" (
  echo.
  echo [WARN] Backend is no longer answering on port 8788 ^(check rc=!BE_RC!^).
  echo [%DATE% %TIME%] [WARN] backend lost on 8788 >> "%STARTUP_LOG%"
  echo        Inspect the 'HERO1 Backend :8788' window and %STARTUP_LOG%.
  echo.
  echo Press any key to exit the launcher...
  pause >nul
  exit /b 1
)
if "!MONITOR_TICKS!"=="6" (
  set "MONITOR_TICKS=0"
  "%VENV_PY%" "%ROOT%\scripts\check_port.py" --frontend 5173 >nul 2>&1
  set "FE_RC=%errorlevel%"
  if "%FE_RC%"=="20" echo [%DATE% %TIME%] [WARN] port 5173 no longer serves the Hero1 frontend >> "%STARTUP_LOG%"
)
goto :MONITOR_LOOP

REM ============================ FAILURE HANDLERS =============================
:ROOT_FAIL
set "FAILED_REASON=Cannot cd to %ROOT%."
set "FAILED_CMD=cd /d %ROOT%"
goto :STARTUP_FAILED
:LOG_FAIL
set "FAILED_REASON=%DATA_ROOT%\LOGS does not exist and could not be created. Check the DATA root (default <repo>\DATA, override HERO1_DATA_ROOT) and its permissions."
set "FAILED_CMD=mkdir %DATA_ROOT%\LOGS"
goto :STARTUP_FAILED
:MISSING_MAIN
set "FAILED_REASON=services\api\app\main.py is missing - is this a complete Hero1 checkout?"
set "FAILED_CMD=git status"
goto :STARTUP_FAILED
:MISSING_WEB
set "FAILED_REASON=apps\web\package.json is missing - is this a complete Hero1 checkout?"
set "FAILED_CMD=git status"
goto :STARTUP_FAILED
:MISSING_VITE
set "FAILED_REASON=apps\web\vite.config.ts is missing."
set "FAILED_CMD=git status"
goto :STARTUP_FAILED
:MISSING_NODE_MODULES
set "FAILED_REASON=apps\web\node_modules is missing. The normal launcher does not install dependencies - run PREREQUISITES.bat first (or: cd apps\web ^&^& npm install)."
set "FAILED_CMD=PREREQUISITES.bat"
goto :STARTUP_FAILED
:MISSING_BACKEND_RUNNER
set "FAILED_REASON=scripts\run_backend.cmd is missing."
set "FAILED_CMD=git status"
goto :STARTUP_FAILED
:MISSING_FRONTEND_RUNNER
set "FAILED_REASON=scripts\run_frontend.cmd is missing."
set "FAILED_CMD=git status"
goto :STARTUP_FAILED
:MISSING_UTILS
set "FAILED_REASON=A launcher utility (check_port.py / wait_ready.py / verify_instance.py) is missing."
set "FAILED_CMD=git status"
goto :STARTUP_FAILED
:MISSING_STORAGE
set "FAILED_REASON=packages\storage is missing - is this a complete Hero1 checkout?"
set "FAILED_CMD=git status"
goto :STARTUP_FAILED
:NO_VENV
set "FAILED_REASON=.venv\Scripts\python.exe was not found even after PREREQUISITES.bat. See %DATA_ROOT%\LOGS\prerequisite.log"
set "FAILED_CMD=PREREQUISITES.bat"
goto :STARTUP_FAILED
:PORT_CHECK_FAILED
set "FAILED_REASON=scripts\check_port.py exited with unexpected code %FAILED_RC% for a port check (expected 0, 10 or 20). The port check itself is broken - interpreter, script or permissions. The launcher will NOT guess whether the port is free. Inspect %STARTUP_LOG% and run the FAILED_CMD to see the raw error."
set "FAILED_CMD=%VENV_PY% scripts\check_port.py 8788"
goto :STARTUP_FAILED
:BACKEND_IMPORT_FAIL
set "FAILED_REASON=The backend failed to import (import app.main, exit code %FAILED_RC%). Run PREREQUISITES.bat, then check %STARTUP_LOG%."
set "FAILED_CMD=cd services\api and run: python -m uvicorn app.main:app --host 127.0.0.1 --port 8788"
goto :STARTUP_FAILED
:DB_CHECK_FAIL
set "FAILED_REASON=Database integrity check FAILED (exit code %FAILED_RC%). The full read-only report was printed above and appended to %STARTUP_LOG%. Nothing was created, migrated, repaired or deleted. Fix the underlying issue (missing/unhealthy DB at the path shown in the report, or an unexpected crash - rc=2) and run this launcher again. If every database is merely missing, run PREREQUISITES.bat first (it creates the canonical <repo>\DATA registry and instrument databases)."
set "FAILED_CMD=%VENV_PY% scripts\init_db.py --check"
goto :STARTUP_FAILED
:PORT_BACKEND_FOREIGN
set "FAILED_REASON=Port 8788 is held by an application that is NOT Hero1 (or no longer identifiable as Hero1). Close that application - this launcher never kills unrelated software - or set HERO1_DATA_ROOT and check with scripts\check_port.py 8788."
set "FAILED_CMD=%VENV_PY% scripts\check_port.py 8788"
goto :STARTUP_FAILED
:PORT_FRONTEND_FOREIGN
set "FAILED_REASON=Port 5173 is held by an application that is NOT the Hero1 dashboard (vite uses strictPort, so it cannot move to a free port). Close the other application and launch again. This launcher never kills unrelated software."
set "FAILED_CMD=%VENV_PY% scripts\check_port.py --frontend 5173"
goto :STARTUP_FAILED
:BACKEND_FOREIGN_REUSE
set "FAILED_REASON=Port 8788 answers Hero1 but NOT this repository (project_root mismatch or older unidentified build). Refusing to reuse or kill it. Close the other Hero1 instance and launch again."
set "FAILED_CMD=%VENV_PY% scripts\verify_instance.py --mode backend --url http://127.0.0.1:8788/health --root %ROOT%"
goto :STARTUP_FAILED
:BACKEND_TIMEOUT
set "FAILED_REASON=The backend did not become ready at http://127.0.0.1:8788/health within 120s. Inspect the 'HERO1 Backend :8788' window for the traceback and %STARTUP_LOG%."
set "FAILED_CMD=%VENV_PY% scripts\wait_ready.py http://127.0.0.1:8788/health 120 --require backend"
goto :STARTUP_FAILED
:BACKEND_NOT_OURS
set "FAILED_REASON=Port 8788 answered with the Hero1 health schema but the identity check failed (project_root / start_token mismatch). The answering process was NOT started by this run - refusing to claim success. Another Hero1 instance may have been already running; close it and launch again."
set "FAILED_CMD=%VENV_PY% scripts\verify_instance.py --mode backend --url http://127.0.0.1:8788/health --root %ROOT% --token %HERO1_START_TOKEN%"
goto :STARTUP_FAILED
:FRONTEND_TIMEOUT
set "FAILED_REASON=The frontend did not become ready at http://127.0.0.1:5173/ within 120s. Inspect the 'HERO1 Frontend :5173' window (vite errors stay visible there) and confirm apps\web\node_modules exists."
set "FAILED_CMD=%VENV_PY% scripts\wait_ready.py http://127.0.0.1:5173/ 120 --require frontend"
goto :STARTUP_FAILED
:PROXY_FAIL
set "FAILED_REASON=The frontend page loaded but http://127.0.0.1:5173/health did NOT reach the Hero1 backend through the vite proxy. The API is not connected - do not use the dashboard. Check the backend window, the /health proxy entry in apps\web\vite.config.mjs, and %STARTUP_LOG%."
set "FAILED_CMD=%VENV_PY% scripts\verify_instance.py --mode frontend-proxy --url http://127.0.0.1:5173/health"
goto :STARTUP_FAILED

:STARTUP_FAILED
echo.
echo ===========================================================
echo  STARTUP FAILED
echo ===========================================================
echo.
echo  Diagnostic:
echo  %FAILED_REASON%
echo.
if defined FAILED_RC echo  Exit code: %FAILED_RC%
if defined FAILED_RC echo.
if defined FAILED_CMD (
  echo  Command to reproduce / inspect:
  echo  %FAILED_CMD%
  echo.
)
if exist "%VENV_PY%" (
  echo  --- last 30 lines of %STARTUP_LOG% ---
  "%VENV_PY%" -c "import sys; d=open(sys.argv[1], errors='replace').read(); print('\n'.join(d.splitlines()[-30:]))" "%STARTUP_LOG%" 2>nul
  echo  ----------------------------------------
  echo.
)
echo  Launcher log: %STARTUP_LOG%
echo.
echo [%DATE% %TIME%] [STARTUP_FAILED] %FAILED_REASON% >> "%STARTUP_LOG%"
if defined FAILED_CMD echo [%DATE% %TIME%] command: %FAILED_CMD% >> "%STARTUP_LOG%"
echo  The application was NOT started (or NOT verified).
echo  This window stays open. Press any key to close it.
pause >nul
exit /b 1
