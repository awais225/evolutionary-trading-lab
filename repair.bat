@echo off
setlocal enabledelayedexpansion

REM ===========================================================================
REM       EVOLUTIONARY TRADING RESEARCH LAB V3.2 - DEEP REPAIR SCRIPT
REM  Re-verifies virtual environment, repairs broken dependencies,
REM  rebuilds frontend production assets, and verifies database integrity.
REM  NEVER deletes or wipes research data.
REM ===========================================================================

title Evolutionary Trading Research Lab - Deep Repair V3.2

set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
cd /d "%ROOT_DIR%"

set "LOG_DIR=%ROOT_DIR%\LOGS"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "REPAIR_LOG=%LOG_DIR%\repair.log"

echo ===========================================================================
echo  EVOLUTIONARY TRADING RESEARCH LAB - DEEP REPAIR (V3.2)
echo ===========================================================================
echo.
echo [LAUNCHER] Execution shell: CMD
echo [LAUNCHER] Script: REPAIR.BAT
echo [LAUNCHER] Working directory: %ROOT_DIR%
echo [LAUNCHER] Launcher execution confirmed
echo.
echo Project Root: %ROOT_DIR%
echo Repair Log:   %REPAIR_LOG%
echo.

echo [1/4] Checking Python Runtime and Virtual Environment...
set "VENV_PYTHON="
if exist "%ROOT_DIR%\.venv\Scripts\python.exe" set "VENV_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"
if exist "%ROOT_DIR%\backend\.venv\Scripts\python.exe" set "VENV_PYTHON=%ROOT_DIR%\backend\.venv\Scripts\python.exe"

if "%VENV_PYTHON%"=="" (
    echo [ACTION] Virtual environment missing. Calling Prerequisite.bat...
    call "%ROOT_DIR%\Prerequisite.bat"
    if errorlevel 1 (
        echo [ERROR] Prerequisite installer failed. Check LOGS\prerequisite.log.
        pause
        exit /b 1
    )
    if exist "%ROOT_DIR%\.venv\Scripts\python.exe" set "VENV_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"
)

echo [2/4] Repairing Backend Dependencies and Running Verification...
call "%VENV_PYTHON%" -m pip install --upgrade pip >> "%REPAIR_LOG%" 2>&1
call "%VENV_PYTHON%" -m pip install -r "%ROOT_DIR%\backend\requirements.txt" >> "%REPAIR_LOG%" 2>&1
call "%VENV_PYTHON%" "%ROOT_DIR%\backend\app\verify_deps.py"
if errorlevel 1 (
    echo [ERROR] Backend dependency repair failed. See %REPAIR_LOG%.
    pause
    exit /b 1
)

echo [3/4] Rebuilding Frontend Production Bundle...
where npm >nul 2>nul
if not errorlevel 1 (
    pushd "%ROOT_DIR%\frontend"
    call npm install --no-audit --no-fund >> "%REPAIR_LOG%" 2>&1
    call npm run build >> "%REPAIR_LOG%" 2>&1
    popd
    if not exist "%ROOT_DIR%\frontend\dist\index.html" (
        echo [ERROR] Frontend build failed. See %REPAIR_LOG%.
        pause
        exit /b 1
    )
    echo [OK] Frontend production bundle rebuilt at frontend\dist\index.html.
) else (
    echo [WARN] npm not found. Skipping frontend rebuild.
)

echo [4/4] Verifying Database Connectivity and Historical Data...
call "%VENV_PYTHON%" "%ROOT_DIR%\backend\tools\verify_database.py"
if errorlevel 1 (
    echo [ERROR] Database verification failed. See %REPAIR_LOG%.
    pause
    exit /b 1
)

echo.
echo ===========================================================================
echo  REPAIR COMPLETED SUCCESSFULLY!
echo ===========================================================================
echo You can now launch the application with START.bat.
echo.
pause
exit /b 0
