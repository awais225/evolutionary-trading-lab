@echo off
REM HERO1 - INTERNAL STARTUP PROCEDURE (called by START.bat inside cmd /k)
REM ============================================================================
REM  Normally you do NOT run this file directly - double-click START.bat.
REM  Direct use is safe: it behaves identically, and errors cannot close the
REM  parent console launched via START.bat. Every stage prints its number,
REM  the exact command, the working directory, resolved paths and the exit
REM  code, then PASS or FAIL. The same text goes to DATA\LOGS\startup.log.
REM  Exit codes: 0 = launched and verified (does not return; monitor runs)
REM              1 = a stage failed (window stays open with the diagnostic)
REM ============================================================================
setlocal EnableDelayedExpansion EnableExtensions
title HERO1 Startup Procedure

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
rem strip scripts\ -> repo root
for %%I in ("%ROOT%\..") do set "REPO_ROOT=%%~fI"
set "ROOT=%REPO_ROOT%"
cd /d "%ROOT%"

REM ---- STAGE 1/10: project root and DATA root resolution --------------------
set "FAILED_STAGE=1"
if not "%HERO1_DATA_ROOT%"=="" (
  set "DATA_ROOT=%HERO1_DATA_ROOT%"
) else (
  set "DATA_ROOT=%ROOT%\DATA"
)
if not defined HERO1_DATA_ROOT set "HERO1_DATA_ROOT=%DATA_ROOT%"
set "LOG_DIR=%DATA_ROOT%\LOGS"
set "STARTUP_LOG=%LOG_DIR%\startup.log"
call :LOG "======================================================="
call :LOG "[STAGE 1/10] Project root and DATA root resolution"
call :LOG "  working dir : %CD%"
call :LOG "  project root: %ROOT%"
call :LOG "  data root   : %DATA_ROOT%"
call :LOG "  log file    : %STARTUP_LOG%"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
if not exist "%LOG_DIR%" (
  set "FAILED_REASON=%DATA_ROOT%\LOGS does not exist and could not be created."
  set "FAILED_CMD=mkdir %LOG_DIR%"
  goto :STAGE_FAILED
)
echo HERO1 INTERNAL STARTUP %DATE% %TIME% >> "%STARTUP_LOG%"
set "HERO1_START_TOKEN=HERO1-%RANDOM%%RANDOM%-%RANDOM%"
set "HERO1_REPO_ROOT=%ROOT%"
call :LOG "  launch token: generated (exported to service windows; never printed)"
where git >nul 2>&1 && for /f "delims=" %%G in ('git -C "%ROOT%" rev-parse --short HEAD 2^>nul') do call :LOG "  git HEAD    : %%G"
call :LOG "[PASS] stage 1"
echo.

REM ---- STAGE 2/10: Python virtual environment -------------------------------
call :LOG "[STAGE 2/10] Python virtual environment"
set "FAILED_STAGE=2"
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  call :LOG "  venv missing - running PREREQUISITES.bat once ..."
  call "%ROOT%\PREREQUISITES.bat"
)
if not exist "%VENV_PY%" (
  set "FAILED_REASON=.venv\Scripts\python.exe not found even after PREREQUISITES.bat."
  set "FAILED_CMD=PREREQUISITES.bat"
  goto :STAGE_FAILED
)
"%VENV_PY%" --version 1>> "%STARTUP_LOG%" 2>&1
"%VENV_PY%" --version 2>&1
call :LOG "  venv python : %VENV_PY%"
call :LOG "  MetaTrader5 package check (same interpreter; report-only at startup):"
"%VENV_PY%" "%ROOT%\scripts\check_mt5_env.py"
set "RC=%errorlevel%"
call :LOG "  MetaTrader5 env rc : %RC%  (0=importable, 1=missing, 2=broken install)"
if not "%RC%"=="0" (
  echo  [WARN] MetaTrader5 is not importable in the backend venv.
  echo         MT5 connection and FETCH DATA will stay unavailable until fixed.
  echo         Run PREREQUISITES.bat, then: "%VENV_PY%" "%ROOT%\scripts\check_mt5_env.py"
  echo [%DATE% %TIME%] [WARN] MetaTrader5 env rc=%RC% in %VENV_PY% >> "%STARTUP_LOG%"
)
call :LOG "[PASS] stage 2"
echo.

REM ---- STAGE 3/10: required files -------------------------------------------
call :LOG "[STAGE 3/10] Required files"
set "FAILED_STAGE=3"
set "MISSING="
for %%F in (services\api\app\main.py apps\web\package.json scripts\init_db.py scripts\check_port.py scripts\wait_ready.py scripts\verify_instance.py scripts\run_backend.cmd scripts\run_frontend.cmd packages\storage\src\hero1_storage\__init__.py) do (
  if exist "%ROOT%\%%F" (call :LOG "  [OK]      %%F") else (call :LOG "  [MISSING] %%F" & set "MISSING=!MISSING! %%F")
)
if not exist "%ROOT%\apps\web\vite.config.ts" if not exist "%ROOT%\apps\web\vite.config.mjs" set "MISSING=!MISSING! apps\web\vite.config.ts"
if not exist "%ROOT%\apps\web\node_modules" set "MISSING=!MISSING! apps\web\node_modules"
if defined MISSING (
  set "FAILED_REASON=Required files missing:%MISSING%. Is this a complete Hero1 checkout?"
  set "FAILED_CMD=git -C %ROOT% status ^&^& git -C %ROOT% pull"
  goto :STAGE_FAILED
)
call :LOG "[PASS] stage 3"
echo.

REM ---- STAGE 4/10: backend import verification --------------------------------
call :LOG "[STAGE 4/10] Backend import verification"
set "FAILED_STAGE=4"
set "CMD_DESC=%VENV_PY% -c import app.main (cwd=%ROOT%)"
call :LOG "  command     : %CMD_DESC%"
"%VENV_PY%" -c "import sys; sys.path.insert(0, r'%ROOT%\services\api'); import app.main; print('[OK] backend import')" >> "%STARTUP_LOG%" 2>&1
set "RC=%errorlevel%"
call :LOG "  exit code   : %RC%"
if not "%RC%"=="0" (
  set "FAILED_RC=%RC%"
  set "FAILED_REASON=Backend failed to import (import app.main, exit code %RC%). See %STARTUP_LOG%."
  set "FAILED_CMD=%VENV_PY% -c import app.main   (cwd=services\api)"
  goto :STAGE_FAILED
)
call :LOG "[PASS] stage 4"
echo.

REM ---- STAGE 5/10: database integrity (read-only) ----------------------------
call :LOG "[STAGE 5/10] Database integrity check (read-only)"
set "FAILED_STAGE=5"
set "DB_CHECK_OUT=%TEMP%\hero1_dbcheck.out"
call :LOG "  command     : %VENV_PY% %ROOT%\scripts\init_db.py --check"
"%VENV_PY%" "%ROOT%\scripts\init_db.py" --check > "%DB_CHECK_OUT%" 2>&1
set "RC=%errorlevel%"
type "%DB_CHECK_OUT%"
type "%DB_CHECK_OUT%" >> "%STARTUP_LOG%" 2>&1
del "%DB_CHECK_OUT%" >nul 2>&1
call :LOG "  exit code   : %RC%   (0=healthy, 1=unhealthy, 2=crashed)"
if not "%RC%"=="0" (
  set "FAILED_RC=%RC%"
  if "%RC%"=="2" (
    set "FAILED_REASON=Database check CRASHED (rc=2) - path or unexpected error; traceback above."
  ) else (
    set "FAILED_REASON=Database integrity check FAILED (rc=%RC%) - see the report above and %STARTUP_LOG%."
  )
  set "FAILED_CMD=%VENV_PY% %ROOT%\scripts\init_db.py --check"
  goto :STAGE_FAILED
)
call :LOG "[PASS] stage 5"
echo.

REM ---- STAGE 6/10: backend startup (persistent window) ------------------------
call :LOG "[STAGE 6/10] Backend startup (own persistent window)"
set "FAILED_STAGE=6"
set "BACKEND_REUSE=0"
set "FAILED_RC="
"%VENV_PY%" "%ROOT%\scripts\check_port.py" 8788
set "RC=%errorlevel%"
call :LOG "  port 8788   : rc=%RC%  (0=free, 10=verified Hero1, 20=foreign)"
if "%RC%"=="20" (
  set "FAILED_RC=%RC%"
  set "FAILED_REASON=Port 8788 is held by an application that is NOT Hero1. This launcher never kills unrelated software."
  set "FAILED_CMD=%VENV_PY% %ROOT%\scripts\check_port.py 8788"
  goto :STAGE_FAILED
)
if "%RC%"=="10" set "BACKEND_REUSE=1"
if not "%RC%"=="0" if not "%RC%"=="10" (
  set "FAILED_RC=%RC%"
  set "FAILED_REASON=check_port.py for 8788 returned unexpected rc=%RC%; refusing to guess."
  set "FAILED_CMD=%VENV_PY% %ROOT%\scripts\check_port.py 8788"
  goto :STAGE_FAILED
)
REM Permanent baseline: import DATA\MARKET_DATA\IMPORTS CSVs into THIS
REM installation's canonical store (idempotent - identical sources skip by
REM SHA-256 in seconds; the backend lifespan performs the same ensure).
call :LOG "  baseline    : %VENV_PY% %ROOT%\scripts\import_market_data.py --data-root %DATA_ROOT%"
"%VENV_PY%" "%ROOT%\scripts\import_market_data.py" --data-root "%DATA_ROOT%" >> "%STARTUP_LOG%" 2>&1
set "RC=%errorlevel%"
call :LOG "  baseline import exit code: %RC%   (0=all imported/skipped, 1=a file was REJECTED, 2=no CSVs found)"
if not "%RC%"=="0" (
  set "FAILED_RC=%RC%"
  set "FAILED_REASON=Historical baseline import FAILED (rc=%RC%) - the permanent CSVs under DATA\MARKET_DATA\IMPORTS must import cleanly; see %STARTUP_LOG%."
  set "FAILED_CMD=%VENV_PY% %ROOT%\scripts\import_market_data.py --data-root %DATA_ROOT%"
  goto :STAGE_FAILED
)
if "%BACKEND_REUSE%"=="1" (
  call :LOG "  reusing identity-verified backend already on 8788"
  "%VENV_PY%" "%ROOT%\scripts\verify_instance.py" --mode backend --url http://127.0.0.1:8788/health --root "%ROOT%" >> "%STARTUP_LOG%" 2>&1
  set "RC=%errorlevel%"
  call :LOG "  reuse identity check: rc=%RC%"
  if not "%RC%"=="0" (
    set "FAILED_RC=%RC%"
    set "FAILED_REASON=8788 answers Hero1 but NOT this repository. Refusing to reuse or kill it."
    set "FAILED_CMD=%VENV_PY% %ROOT%\scripts\verify_instance.py --mode backend --url http://127.0.0.1:8788/health --root %ROOT%"
    goto :STAGE_FAILED
  )
  goto :BACKEND_READY
)
call :LOG "  starting backend window: HERO1 Backend :8788"
start "HERO1 Backend :8788" cmd /k call "%ROOT%\scripts\run_backend.cmd"

:BACKEND_READY
call :LOG "[PASS] stage 6"
echo.

REM ---- STAGE 7/10: backend health verification ---------------------------------
call :LOG "[STAGE 7/10] Backend health verification"
set "FAILED_STAGE=7"
if "%BACKEND_REUSE%"=="1" goto :BACKEND_HEALTH_OK
call "%VENV_PY%" "%ROOT%\scripts\wait_ready.py" http://127.0.0.1:8788/health 120 --require backend >> "%STARTUP_LOG%"
set "RC=%errorlevel%"
call :LOG "  readiness poll rc  : %RC%"
if not "%RC%"=="0" (
  set "FAILED_RC=%RC%"
  set "FAILED_REASON=Backend did not become ready at :8788/health within 120s. See the Backend window + %STARTUP_LOG%."
  set "FAILED_CMD=%VENV_PY% %ROOT%\scripts\wait_ready.py http://127.0.0.1:8788/health 120 --require backend"
  goto :STAGE_FAILED
)
"%VENV_PY%" "%ROOT%\scripts\verify_instance.py" --mode backend --url http://127.0.0.1:8788/health --root "%ROOT%" --token "%HERO1_START_TOKEN%" >> "%STARTUP_LOG%" 2>&1
set "RC=%errorlevel%"
call :LOG "  identity verify rc : %RC%"
if not "%RC%"=="0" (
  set "FAILED_RC=%RC%"
  set "FAILED_REASON=8788 answered but the identity check failed (root/token mismatch) - NOT this launch."
  set "FAILED_CMD=%VENV_PY% %ROOT%\scripts\verify_instance.py --mode backend --url http://127.0.0.1:8788/health --root %ROOT% --token <launch token>"
  goto :STAGE_FAILED
)
:BACKEND_HEALTH_OK
call :LOG "[PASS] stage 7"
echo.

REM ---- STAGE 8/10: frontend startup (persistent window) ------------------------
call :LOG "[STAGE 8/10] Frontend startup (own persistent window)"
set "FAILED_STAGE=8"
set "FRONTEND_REUSE=0"
"%VENV_PY%" "%ROOT%\scripts\check_port.py" --frontend 5173
set "RC=%errorlevel%"
call :LOG "  port 5173   : rc=%RC%  (0=free, 10=verified Hero1, 20=foreign)"
if "%RC%"=="20" (
  set "FAILED_RC=%RC%"
  set "FAILED_REASON=Port 5173 is held by an application that is NOT the Hero1 dashboard (vite strictPort)."
  set "FAILED_CMD=%VENV_PY% %ROOT%\scripts\check_port.py --frontend 5173"
  goto :STAGE_FAILED
)
if "%RC%"=="10" set "FRONTEND_REUSE=1"
if not "%RC%"=="0" if not "%RC%"=="10" (
  set "FAILED_RC=%RC%"
  set "FAILED_REASON=check_port.py for 5173 returned unexpected rc=%RC%; refusing to guess."
  set "FAILED_CMD=%VENV_PY% %ROOT%\scripts\check_port.py --frontend 5173"
  goto :STAGE_FAILED
)
if "%FRONTEND_REUSE%"=="1" (
  call :LOG "  reusing Hero1 frontend already on 5173"
  goto :FRONTEND_READY
)
call :LOG "  starting frontend window: HERO1 Frontend :5173"
start "HERO1 Frontend :5173" cmd /k call "%ROOT%\scripts\run_frontend.cmd"

:FRONTEND_READY
call :LOG "[PASS] stage 8"
echo.

REM ---- STAGE 9/10: frontend health + backend connectivity ----------------------
call :LOG "[STAGE 9/10] Frontend health and frontend-to-backend connectivity"
set "FAILED_STAGE=9"
if "%FRONTEND_REUSE%"=="1" goto :FRONTEND_HEALTH_OK
call "%VENV_PY%" "%ROOT%\scripts\wait_ready.py" http://127.0.0.1:5173/ 120 --require frontend >> "%STARTUP_LOG%"
set "RC=%errorlevel%"
call :LOG "  readiness poll rc  : %RC%"
if not "%RC%"=="0" (
  set "FAILED_RC=%RC%"
  set "FAILED_REASON=Frontend did not become ready at :5173 within 120s. See the Frontend window + %STARTUP_LOG%."
  set "FAILED_CMD=%VENV_PY% %ROOT%\scripts\wait_ready.py http://127.0.0.1:5173/ 120 --require frontend"
  goto :STAGE_FAILED
)
:FRONTEND_HEALTH_OK
"%VENV_PY%" "%ROOT%\scripts\verify_instance.py" --mode frontend-proxy --url http://127.0.0.1:5173/health
set "RC=%errorlevel%"
call :LOG "  proxy verify rc    : %RC%  (frontend -> backend)"
if not "%RC%"=="0" (
  set "FAILED_RC=%RC%"
  set "FAILED_REASON=The page loads but :5173/health did NOT reach the backend through the vite proxy. API NOT CONNECTED - do not use the dashboard."
  set "FAILED_CMD=%VENV_PY% %ROOT%\scripts\verify_instance.py --mode frontend-proxy --url http://127.0.0.1:5173/health"
  goto :STAGE_FAILED
)
call :LOG "[PASS] stage 9"
echo.

REM ---- STAGE 10/10: browser launch + monitor ---------------------------------
call :LOG "[STAGE 10/10] Browser launch"
set "FAILED_STAGE=10"
set "DASHBOARD_URL=http://127.0.0.1:5173/?v=%HERO1_START_TOKEN%"
if "%FRONTEND_REUSE%"=="1" (
  call :LOG "  frontend was already running; browser auto-open skipped to avoid a duplicate tab"
  echo  Dashboard already running: http://127.0.0.1:5173/
  echo  Browser auto-open skipped to avoid opening a duplicate tab.
  goto :BROWSER_OPEN_DONE
)
start "" "%DASHBOARD_URL%"
set "RC=%errorlevel%"
call :LOG "  browser launch rc : %RC%  (0 = auto-open requested)"
call :LOG "  browser launch URL: %DASHBOARD_URL%"
if not "%RC%"=="0" (
  echo.
  echo  [WARN] The browser did not open automatically - start rc=%RC%
  echo         Trying the Windows URL handler fallback...
  rundll32 url.dll,FileProtocolHandler "%DASHBOARD_URL%"
  set "RC=%errorlevel%"
  call :LOG "  browser fallback rc: %RC%"
  call :LOG "  browser fallback URL: %DASHBOARD_URL%"
)
if not "%RC%"=="0" (
  echo.
  echo  [WARN] The browser did not open automatically - final rc=%RC%
  echo         Hero1 itself IS running normally. Open this address in
  echo         your browser manually:  http://127.0.0.1:5173/
  echo         Full launch URL: %DASHBOARD_URL%
  echo         Or search "browser" in TROUBLESHOOTING.txt - repository root.
  echo [%DATE% %TIME%] [WARN] browser auto-open failed rc=%RC% - manual open http://127.0.0.1:5173/ full-url=%DASHBOARD_URL% >> "%STARTUP_LOG%"
  echo.
)
:BROWSER_OPEN_DONE
call :LOG "[PASS] stage 10 - HERO1 IS RUNNING"
echo.
echo ===========================================================
echo  HERO1 IS RUNNING - all ten stages passed
echo ===========================================================
echo  Dashboard : http://127.0.0.1:5173/
echo  Backend   : http://127.0.0.1:8788/health
echo  Logs      : %STARTUP_LOG%
echo.
echo  This window stays open and monitors the backend.
echo  Service windows stay open on purpose; close them to stop.
echo ===========================================================
echo.

set "MONITOR_TICKS=0"
:MONITOR_LOOP
timeout /t 5 /nobreak >nul
set /a MONITOR_TICKS=MONITOR_TICKS+1
"%VENV_PY%" "%ROOT%\scripts\check_port.py" 8788 >nul 2>&1
set "BE_RC=!ERRORLEVEL!"
if not "!BE_RC!"=="10" (
  echo.
  echo [WARN] Backend is no longer answering on port 8788 (rc=!BE_RC!).
  echo [%DATE% %TIME%] [WARN] backend lost on 8788 >> "%STARTUP_LOG%"
  echo        Inspect the HERO1 Backend :8788 window and %STARTUP_LOG%.
  goto :STAGE_FAILED
)
if "!MONITOR_TICKS!"=="6" (
  set "MONITOR_TICKS=0"
  "%VENV_PY%" "%ROOT%\scripts\check_port.py" --frontend 5173 >nul 2>&1
  set "FE_RC=!ERRORLEVEL!"
  if "!FE_RC!"=="20" echo [%DATE% %TIME%] [WARN] port 5173 no longer serves the Hero1 frontend >> "%STARTUP_LOG%"
)
goto :MONITOR_LOOP

REM ============================ FAILURE HANDLER ==============================
:STAGE_FAILED
echo.
echo ===========================================================
echo  HERO1 STARTUP FAILED - STAGE %FAILED_STAGE%
echo ===========================================================
echo  What failed : %FAILED_REASON%
if defined FAILED_RC echo  Exit code   : %FAILED_RC%
if defined FAILED_CMD echo  Reproduce   : %FAILED_CMD%
echo  Working dir  : %CD%
echo  Data root    : %DATA_ROOT%
echo  Log file     : %STARTUP_LOG%
echo  Next step    : open TROUBLESHOOTING.txt in the repository root and
echo                 search for "STAGE %FAILED_STAGE%" or the exact error text above.
echo.
echo [%DATE% %TIME%] [STARTUP_FAILED] stage=%FAILED_STAGE% rc=%FAILED_RC% reason=%FAILED_REASON% >> "%STARTUP_LOG%"
if exist "%VENV_PY%" (
  echo  --- last 30 lines of %STARTUP_LOG% ---
  "%VENV_PY%" -c "import sys; d=open(sys.argv[1], errors='replace').read(); print('\n'.join(d.splitlines()[-30:]))" "%STARTUP_LOG%" 2>nul
  echo  ----------------------------------------
)
echo.
echo  NOTHING was deleted, created or migrated.
echo  Read the output above; send it plus %STARTUP_LOG% with any bug report.
echo.
echo Press any key to close this window...
pause >nul
exit /b 1

REM ============================ SUBROUTINES ==================================
:LOG
echo %~1
if defined STARTUP_LOG echo [%DATE% %TIME%] %~1 >> "%STARTUP_LOG%"
exit /b 0
