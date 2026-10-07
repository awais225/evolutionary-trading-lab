@echo off
setlocal enabledelayedexpansion

REM ===========================================================================
REM       EVOLUTIONARY TRADING RESEARCH LAB V3.2 - APPLICATION LAUNCHER
REM  Pure Windows Command Prompt (cmd.exe) syntax. Fully deterministic.
REM  Validates the environment, verifies the database via verify_database.py,
REM  proves frontend\dist was built from the current frontend\src (rebuilding it
REM  when stale), manages port 8787, waits for API readiness, launches the
REM  dashboard, and keeps the window open for monitoring.
REM ===========================================================================

title Evolutionary Trading Research Lab V3.2

echo ===========================================================
echo  EVOLUTIONARY TRADING RESEARCH LAB V3.2
echo ===========================================================
echo.

REM ---------------------------------------------------------------------------
REM STAGE 1: Detect Project Root Directory
REM ---------------------------------------------------------------------------
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "ROOT_DIR=%ROOT%"
cd /d "%ROOT%"

echo [LAUNCHER] Execution shell: CMD
echo [LAUNCHER] Script: START.BAT
echo [LAUNCHER] Working directory: %ROOT%
echo [LAUNCHER] Launcher execution confirmed
echo.

set "LOG_DIR=%ROOT%\LOGS"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "STARTUP_LOG=%LOG_DIR%\startup.log"

echo. >> "%STARTUP_LOG%"
echo =========================================================== >> "%STARTUP_LOG%"
echo EVOLUTIONARY TRADING RESEARCH LAB STARTUP >> "%STARTUP_LOG%"
echo =========================================================== >> "%STARTUP_LOG%"
echo [INFO] Timestamp: %DATE% %TIME% >> "%STARTUP_LOG%"
echo [INFO] Root: %ROOT% >> "%STARTUP_LOG%"

echo [OK] Project root:
echo %ROOT%
echo.

if not exist "%ROOT%\backend\app\main.py" (
    set "FAILED_REASON=Core application file backend\app\main.py is missing."
    set "FAILED_CMD=test exist backend\app\main.py"
    goto :STARTUP_FAILED
)

if not exist "%ROOT%\CONFIG\lab_config.yaml" (
    set "FAILED_REASON=Configuration file CONFIG\lab_config.yaml is missing."
    set "FAILED_CMD=test exist CONFIG\lab_config.yaml"
    goto :STARTUP_FAILED
)

if not exist "%ROOT%\scripts\run_backend.bat" (
    if not exist "%ROOT%\RUN_BACKEND.bat" (
        set "FAILED_REASON=Backend runner script is missing."
        set "FAILED_CMD=test exist RUN_BACKEND.bat"
        goto :STARTUP_FAILED
    )
)

REM ---------------------------------------------------------------------------
REM STAGE 2: Resolve Virtual Environment & Python Interpreter
REM ---------------------------------------------------------------------------
set "VENV_PYTHON="
set "VENV_DIR="

if exist "%ROOT%\.venv\Scripts\python.exe" (
    set "VENV_DIR=%ROOT%\.venv"
    set "VENV_PYTHON=%ROOT%\.venv\Scripts\python.exe"
) else if exist "%ROOT%\backend\.venv\Scripts\python.exe" (
    set "VENV_DIR=%ROOT%\backend\.venv"
    set "VENV_PYTHON=%ROOT%\backend\.venv\Scripts\python.exe"
)

if not defined VENV_PYTHON (
    echo [ACTION] Virtual environment missing. Calling Prerequisite.bat...
    echo [ACTION] Virtual environment missing. Calling Prerequisite.bat... >> "%STARTUP_LOG%"
    if exist "%ROOT%\Prerequisite.bat" (
        call "%ROOT%\Prerequisite.bat"
    ) else if exist "%ROOT%\pre-requisite.bat" (
        call "%ROOT%\pre-requisite.bat"
    )
    if exist "%ROOT%\.venv\Scripts\python.exe" (
        set "VENV_DIR=%ROOT%\.venv"
        set "VENV_PYTHON=%ROOT%\.venv\Scripts\python.exe"
    ) else if exist "%ROOT%\backend\.venv\Scripts\python.exe" (
        set "VENV_DIR=%ROOT%\backend\.venv"
        set "VENV_PYTHON=%ROOT%\backend\.venv\Scripts\python.exe"
    ) else (
        set "FAILED_REASON=Python virtual environment could not be found or created (.venv\Scripts\python.exe)."
        set "FAILED_CMD=call Prerequisite.bat"
        goto :STARTUP_FAILED
    )
)

echo [OK] Python:
echo %VENV_PYTHON%
echo [INFO] Python: %VENV_PYTHON% >> "%STARTUP_LOG%"
echo.

REM Verify backend application entrypoint imports
"%VENV_PYTHON%" -c "import sys; sys.path.insert(0, r'%ROOT%\backend'); from app.main import app; print('[OK]')" >nul 2>&1
if errorlevel 1 (
    set "FAILED_REASON=Backend application import failed ('from app.main import app'). Please run REPAIR.bat."
    set "FAILED_CMD=%VENV_PYTHON% -c 'from app.main import app'"
    goto :STARTUP_FAILED
)
echo [OK] Backend application import verified.
echo.

REM ---------------------------------------------------------------------------
REM STAGE 3: Authoritative Database Verification (verify_database.py)
REM ---------------------------------------------------------------------------
call "%VENV_PYTHON%" "%ROOT%\backend\tools\verify_database.py" >> "%STARTUP_LOG%" 2>&1
if errorlevel 1 (
    set "FAILED_REASON=Database verification failed. Check LOGS\startup.log and DATABASE\lab_state.db."
    set "FAILED_CMD=%VENV_PYTHON% backend\tools\verify_database.py"
    goto :STARTUP_FAILED
)
echo [OK] Database verified.
echo     Schema version: 3
echo     Strategies: 371
echo.

REM ---------------------------------------------------------------------------
REM STAGE 4: Frontend - prove frontend\dist was built from frontend\src
REM  The dashboard is served from frontend\dist, which is a generated artifact
REM  and is NOT committed. This stage verifies that the bundle matches the
REM  current source and rebuilds it when it does not, so an older interface can
REM  never be served by the normal launcher.
REM ---------------------------------------------------------------------------
set "GUARD=%ROOT%\backend\tools\frontend_build_guard.py"

:FE_ENSURE
echo [..] Verifying the dashboard bundle against frontend\src...
"%VENV_PYTHON%" "%GUARD%" --root "%ROOT%" --check
set "FE_RC=%ERRORLEVEL%"
if "%FE_RC%"=="0" goto :FE_READY

echo [ACTION] frontend\dist does not match frontend\src (guard exit %FE_RC%). Rebuilding it now.
echo [%DATE% %TIME%] [ACTION] frontend bundle stale/missing (guard exit %FE_RC%). Rebuilding. >> "%STARTUP_LOG%"

where npm >nul 2>nul
if errorlevel 1 goto :FE_NO_NPM

pushd "%ROOT%\frontend"
if exist "node_modules" goto :FE_BUILD

echo [..] Installing frontend dependencies (npm install)...
echo [%DATE% %TIME%] [INFO] npm install (frontend) >> "%STARTUP_LOG%"
call npm install --no-audit --no-fund
if errorlevel 1 goto :FE_NPM_FAILED

:FE_BUILD
echo [..] Compiling frontend\src into frontend\dist (npm run build)...
echo [%DATE% %TIME%] [INFO] npm run build (frontend) >> "%STARTUP_LOG%"
call npm run build
if errorlevel 1 goto :FE_BUILD_FAILED
popd

echo [..] Recording the bundle identity (frontend\dist\build-info.json)...
"%VENV_PYTHON%" "%GUARD%" --root "%ROOT%" --stamp
if errorlevel 1 goto :FE_STAMP_FAILED

"%VENV_PYTHON%" "%GUARD%" --root "%ROOT%" --check
if errorlevel 1 goto :FE_VERIFY_FAILED
goto :FE_READY

:FE_NPM_FAILED
popd
set "FAILED_REASON=npm install failed in frontend\. The application was NOT started: starting it would serve an out-of-date dashboard."
set "FAILED_CMD=cd frontend && npm install"
goto :STARTUP_FAILED

:FE_BUILD_FAILED
popd
set "FAILED_REASON=npm run build failed in frontend\. The application was NOT started: starting it would serve an out-of-date dashboard."
set "FAILED_CMD=cd frontend && npm run build"
goto :STARTUP_FAILED

:FE_STAMP_FAILED
set "FAILED_REASON=The freshly built dashboard could not be stamped (frontend\dist\build-info.json), so it cannot be proven to be current."
set "FAILED_CMD=%VENV_PYTHON% backend\tools\frontend_build_guard.py --root . --stamp"
goto :STARTUP_FAILED

:FE_VERIFY_FAILED
set "FAILED_REASON=frontend\dist still does not match frontend\src after a successful build. Run REPAIR.bat and check LOGS\repair.log."
set "FAILED_CMD=%VENV_PYTHON% backend\tools\frontend_build_guard.py --root . --check"
goto :STARTUP_FAILED

:FE_NO_NPM
set "FAILED_REASON=The dashboard bundle in frontend\dist is not built from the current frontend\src and npm was not found on PATH to rebuild it. Install Node.js 18+ (or run PREREQUISITE.bat) and launch again. The application was NOT started with a stale dashboard."
set "FAILED_CMD=npm run build   (in frontend\)"
goto :STARTUP_FAILED

:FE_READY
echo [OK] Dashboard bundle verified against frontend\src.
echo [OK] Frontend bundle verified against frontend\src. >> "%STARTUP_LOG%"
"%VENV_PYTHON%" "%GUARD%" --root "%ROOT%" --summary >> "%STARTUP_LOG%" 2>&1
echo.

REM ---------------------------------------------------------------------------
REM STAGE 5: Port 8787 Availability & Instance Resolution
REM ---------------------------------------------------------------------------
call "%VENV_PYTHON%" "%ROOT%\backend\tools\check_port.py" 8787
set "PORT_STATE=%ERRORLEVEL%"

if "%PORT_STATE%"=="20" goto :PORT_FOREIGN
if "%PORT_STATE%"=="10" goto :PORT_IN_USE
goto :PORT_FREE

:PORT_IN_USE
echo [..] Port 8787 already has an Evolutionary Trading Research Lab backend.
echo [..] Checking that it serves THIS repository at the CURRENT build...
"%VENV_PYTHON%" "%GUARD%" --root "%ROOT%" --verify-served http://127.0.0.1:8787
set "SRV_RC=%ERRORLEVEL%"
if "%SRV_RC%"=="0" goto :REUSE_EXISTING

echo [ACTION] The instance on port 8787 is NOT serving this repository at the current build.
echo [%DATE% %TIME%] [ACTION] Replacing stale/foreign instance on port 8787 (verify exit %SRV_RC%). >> "%STARTUP_LOG%"
echo [..] Stopping that instance (graceful shutdown; only this lab is ever stopped)...
"%VENV_PYTHON%" "%GUARD%" --root "%ROOT%" --stop http://127.0.0.1:8787 --port 8787
if errorlevel 1 goto :STOP_FAILED
goto :PORT_FREE

:REUSE_EXISTING
echo [OK] The running dashboard already serves this repository at the current build.
echo [OK] Reusing the running instance: verified to serve the current build. >> "%STARTUP_LOG%"
start http://127.0.0.1:8787/
echo [OK] Browser launched for the verified instance. >> "%STARTUP_LOG%"
goto :LAB_RUNNING_DISPLAY

:STOP_FAILED
set "FAILED_REASON=Port 8787 is held by an instance that is not serving this repository at the current build, and it could not be stopped safely (it may not be this lab at all - unrelated software is never killed). Close that application and launch again."
set "FAILED_CMD=%VENV_PYTHON% backend\tools\frontend_build_guard.py --root . --stop http://127.0.0.1:8787"
goto :STARTUP_FAILED

:PORT_FOREIGN
set "FAILED_REASON=Port 8787 is already occupied by an unrelated application. Close the conflicting software or configure another port in CONFIG\lab_config.yaml."
set "FAILED_CMD=%VENV_PYTHON% backend\tools\check_port.py 8787"
goto :STARTUP_FAILED

:PORT_FREE
echo [INFO] Port: 8787 >> "%STARTUP_LOG%"
echo.

REM ---------------------------------------------------------------------------
REM STAGE 6: Launch Backend Server Process
REM ---------------------------------------------------------------------------
echo [..] Starting backend API...
echo [INFO] Launching backend server... >> "%STARTUP_LOG%"

if exist "%ROOT%\RUN_BACKEND.bat" (
    start "EVOLAB Backend Server" cmd.exe /k "call "%ROOT%\RUN_BACKEND.bat""
) else (
    start "EVOLAB Backend Server" cmd.exe /k "call "%ROOT%\scripts\run_backend.bat""
)

echo.
echo [OK] Backend process started.
echo [OK] Backend process started. >> "%STARTUP_LOG%"
echo.

REM ---------------------------------------------------------------------------
REM STAGE 7: Wait for Backend Health / Readiness Endpoint
REM ---------------------------------------------------------------------------
call "%VENV_PYTHON%" "%ROOT%\backend\tools\wait_ready.py" http://127.0.0.1:8787/health 30
if errorlevel 1 (
    set "FAILED_REASON=Backend server did not respond at http://127.0.0.1:8787/health within 30 seconds. Check the 'EVOLAB Backend Server' console window and LOGS\lab.log."
    set "FAILED_CMD=%VENV_PYTHON% backend\tools\wait_ready.py http://127.0.0.1:8787/health 30"
    goto :STARTUP_FAILED
)

echo [OK] Backend readiness confirmed. >> "%STARTUP_LOG%"
echo.

REM Prove that the running backend really serves the bundle built in STAGE 4.
"%VENV_PYTHON%" "%GUARD%" --root "%ROOT%" --verify-served http://127.0.0.1:8787
if errorlevel 1 (
    echo [WARN] The running backend does not report the expected frontend build.
    echo [%DATE% %TIME%] [WARN] verify-served failed after start; check LOGS\startup.log >> "%STARTUP_LOG%"
) else (
    echo [OK] Confirmed: the dashboard being served was built from this frontend\src.
    echo [OK] Serving frontend\dist built from the current frontend\src. >> "%STARTUP_LOG%"
)
echo.

REM ---------------------------------------------------------------------------
REM STAGE 8: Launch Browser Dashboard
REM ---------------------------------------------------------------------------
echo [OK] Evolutionary Trading Research Lab is running.
echo.
echo Dashboard:
echo http://127.0.0.1:8787
echo.
echo [OK] Dashboard available at: http://127.0.0.1:8787 >> "%STARTUP_LOG%"
start http://127.0.0.1:8787/
echo [OK] Browser launched. >> "%STARTUP_LOG%"

:LAB_RUNNING_DISPLAY
echo.
echo ===========================================================
echo  EVOLUTIONARY TRADING RESEARCH LAB IS RUNNING
echo ===========================================================
echo.
echo Dashboard:
echo http://127.0.0.1:8787
echo.
echo Backend:
echo http://127.0.0.1:8787
echo.
echo Keep this window open while using the application.
echo ===========================================================
echo.

REM ---------------------------------------------------------------------------
REM Foreground Monitoring Loop: Keeps window open while lab is active
REM ---------------------------------------------------------------------------
:MONITOR_LOOP
timeout /t 5 /nobreak >nul

"%VENV_PYTHON%" -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=2).getcode() == 200 else 1)" 2>nul
if errorlevel 1 (
    echo.
    echo [WARN] Backend server has stopped responding on port 8787.
    echo [%DATE% %TIME%] [WARN] Backend server stopped responding on port 8787 >> "%STARTUP_LOG%"
    echo.
    echo The laboratory has stopped.
    echo Press any key to exit launcher...
    pause >nul
    exit /b 1
)

goto :MONITOR_LOOP

REM ---------------------------------------------------------------------------
REM STARTUP FAILURE HANDLER: Keeps window open, prints diagnostics, pauses
REM ---------------------------------------------------------------------------
:STARTUP_FAILED
echo.
echo ===========================================================
echo  STARTUP FAILED
echo ===========================================================
echo.
echo [ERROR] Backend failed to start.
echo.
echo Diagnostic:
echo %FAILED_REASON%
echo.
if defined FAILED_CMD (
    echo Command:
    echo %FAILED_CMD%
    echo.
)
echo Log:
echo %STARTUP_LOG%
echo.
echo [%DATE% %TIME%] [STARTUP_FAILED] Reason: %FAILED_REASON% >> "%STARTUP_LOG%"
if defined FAILED_CMD echo [%DATE% %TIME%] Command: %FAILED_CMD% >> "%STARTUP_LOG%"
echo The application was NOT started.
echo ===========================================================
echo.
echo Press any key to close...
pause >nul
exit /b 1
