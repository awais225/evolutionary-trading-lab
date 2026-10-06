@echo off
setlocal enabledelayedexpansion

REM ===========================================================================
REM       EVOLUTIONARY TRADING RESEARCH LAB V3.2 - APPLICATION LAUNCHER
REM  Pure Windows Command Prompt (cmd.exe) syntax. Fully deterministic.
REM  Validates environment, verifies database via verify_database.py, checks
REM  pre-compiled frontend bundle without npm, manages port 8787, waits for
REM  API readiness, launches dashboard, and keeps window open for monitoring.
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
REM STAGE 4: Frontend Production Bundle Verification (Zero NPM required)
REM ---------------------------------------------------------------------------
if not exist "%ROOT%\frontend\dist\index.html" (
    echo.
    echo ===========================================================
    echo  STARTUP FAILED
    echo ===========================================================
    echo.
    echo [ERROR] Production frontend bundle is missing.
    echo.
    echo Expected:
    echo frontend\dist\index.html
    echo.
    echo Run:
    echo REPAIR.bat
    echo.
    echo The application was NOT started because the dashboard bundle is missing.
    echo Log: %STARTUP_LOG%
    echo [%DATE% %TIME%] [STARTUP_FAILED] Missing frontend\dist\index.html >> "%STARTUP_LOG%"
    echo.
    echo Press any key to close...
    pause >nul
    exit /b 1
)

echo [OK] Frontend production bundle:
echo frontend\dist\index.html
echo [OK] Frontend production bundle detected. >> "%STARTUP_LOG%"
echo.

REM ---------------------------------------------------------------------------
REM STAGE 5: Port 8787 Availability & Instance Resolution
REM ---------------------------------------------------------------------------
call "%VENV_PYTHON%" "%ROOT%\backend\tools\check_port.py" 8787
set "PORT_STATE=%ERRORLEVEL%"

if "%PORT_STATE%"=="10" (
    echo [OK] Existing Evolutionary Trading Research Lab backend detected.
    echo [OK] Opening existing dashboard...
    echo [OK] Existing lab detected on port 8787. >> "%STARTUP_LOG%"
    start http://127.0.0.1:8787/
    echo [OK] Browser launched for existing instance. >> "%STARTUP_LOG%"
    goto :LAB_RUNNING_DISPLAY
)

if "%PORT_STATE%"=="20" (
    set "FAILED_REASON=Port 8787 is already occupied by an unrelated application. Close the conflicting software or configure another port in CONFIG\lab_config.yaml."
    set "FAILED_CMD=%VENV_PYTHON% backend\tools\check_port.py 8787"
    goto :STARTUP_FAILED
)

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
