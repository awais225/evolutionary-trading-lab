@echo off
REM ===========================================================================
REM  EVOLUTIONARY TRADING RESEARCH LAB - BUILD AND SERVE SCRIPT (V3.2)
REM  Builds the production dashboard and serves on http://localhost:8787
REM ===========================================================================
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"

echo [1/3] Checking virtual environment...
set "VENV_PYTHON="
if exist "%ROOT_DIR%\.venv\Scripts\python.exe" set "VENV_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"
if exist "%ROOT_DIR%\backend\.venv\Scripts\python.exe" set "VENV_PYTHON=%ROOT_DIR%\backend\.venv\Scripts\python.exe"

if "%VENV_PYTHON%"=="" (
    echo [ERROR] Virtual environment missing. Running Prerequisite.bat...
    call "%ROOT_DIR%\Prerequisite.bat"
    if errorlevel 1 exit /b 1
)

echo [2/3] Ensuring the production bundle is built from the current frontend\src...
if "%VENV_PYTHON%"=="" set "VENV_PYTHON=python"
set "GUARD=%ROOT_DIR%\backend\tools\frontend_build_guard.py"

"%VENV_PYTHON%" "%GUARD%" --root "%ROOT_DIR%" --check
if not errorlevel 1 (
    echo [OK] frontend\dist already matches frontend\src - rebuilding is not required.
    goto :FE_READY
)

pushd "%ROOT_DIR%\frontend"
if not exist "node_modules" call npm install --no-audit --no-fund
call npm run build
popd

if not exist "%ROOT_DIR%\frontend\dist\index.html" (
    echo [ERROR] Frontend build failed to produce frontend\dist\index.html.
    pause & exit /b 1
)

"%VENV_PYTHON%" "%GUARD%" --root "%ROOT_DIR%" --stamp
"%VENV_PYTHON%" "%GUARD%" --root "%ROOT_DIR%" --check
if errorlevel 1 (
    echo [ERROR] frontend\dist does not match frontend\src after the build.
    echo         Refusing to serve a dashboard that is not the current interface.
    pause & exit /b 1
)

:FE_READY
echo [OK] Serving frontend\dist built from the current frontend\src.

echo [3/3] Starting backend server and opening dashboard at http://localhost:8787...
start http://localhost:8787/
cd /d "%ROOT_DIR%\backend"
"%VENV_PYTHON%" -m uvicorn app.main:app --host 0.0.0.0 --port 8787
