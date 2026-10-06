@echo off
REM ===========================================================================
REM  EVOLUTIONARY TRADING RESEARCH LAB - WINDOWS EXE PACKAGER (V3.2)
REM  Compiles standalone EvolutionaryTradingLab.exe via PyInstaller
REM ===========================================================================
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"

echo ===========================================================================
echo   EVOLUTIONARY TRADING RESEARCH LAB - EXE BUILD PROCESS [V3.2]
echo ===========================================================================
echo  Root Directory: %ROOT_DIR%
echo.

REM 1. Verify Virtual Environment
set "VENV_PYTHON="
if exist "%ROOT_DIR%\.venv\Scripts\python.exe" set "VENV_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"
if exist "%ROOT_DIR%\backend\.venv\Scripts\python.exe" set "VENV_PYTHON=%ROOT_DIR%\backend\.venv\Scripts\python.exe"

if "%VENV_PYTHON%"=="" (
    echo [ACTION] Virtual environment missing. Running pre-requisite.bat...
    call "%ROOT_DIR%\pre-requisite.bat"
    if errorlevel 1 (
        echo [ERROR] Prerequisites failed.
        pause
        exit /b 1
    )
    if exist "%ROOT_DIR%\.venv\Scripts\python.exe" set "VENV_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"
)

REM 2. Verify Frontend Dist
if not exist "%ROOT_DIR%\frontend\dist\index.html" (
    echo [ACTION] Compiling frontend production bundle...
    pushd "%ROOT_DIR%\frontend"
    call npm run build
    popd
    if not exist "%ROOT_DIR%\frontend\dist\index.html" (
        echo [ERROR] Failed to compile frontend\dist\index.html.
        pause
        exit /b 1
    )
)

REM 3. Ensure PyInstaller is installed
echo [..] Checking PyInstaller...
"%VENV_PYTHON%" -m pip install pyinstaller >> "%ROOT_DIR%\LOGS\build_exe.log" 2>&1

REM 4. Build Standalone Executable
echo [..] Building EvolutionaryTradingLab.exe with PyInstaller...
cd /d "%ROOT_DIR%"
"%VENV_PYTHON%" -m PyInstaller --clean EvolutionaryTradingLab.spec

if not exist "%ROOT_DIR%\dist\EvolutionaryTradingLab.exe" (
    echo.
    echo [ERROR] PyInstaller build failed. Check LOGS\build_exe.log for details.
    pause
    exit /b 1
)

echo.
echo ===========================================================================
echo  [OK] EXE BUILD COMPLETED SUCCESSFULLY!
echo  Executable: %ROOT_DIR%\dist\EvolutionaryTradingLab.exe
echo ===========================================================================
echo.
pause
