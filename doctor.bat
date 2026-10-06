@echo off
setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

echo ===========================================================
echo  EVOLUTIONARY TRADING RESEARCH LAB - SYSTEM DOCTOR (V3.2)
echo ===========================================================
echo [LAUNCHER] Execution shell: CMD
echo [LAUNCHER] Script: DOCTOR.BAT
echo [LAUNCHER] Working directory: %ROOT%
echo [LAUNCHER] Launcher execution confirmed
echo Directory: %ROOT%
echo.

set "PY_EXE="
if exist "%ROOT%\.venv\Scripts\python.exe" (
    set "PY_EXE=%ROOT%\.venv\Scripts\python.exe"
) else (
    where python.exe >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "delims=" %%i in ('where python.exe') do (
            if not defined PY_EXE set "PY_EXE=%%i"
        )
    )
)

if not defined PY_EXE (
    echo [ERROR] Python was not found in .venv or system PATH.
    echo Please install Python 3.10-3.13 and run pre-requisite.bat.
    echo.
    pause
    exit /b 1
)

echo Using Python: %PY_EXE%
echo.

"%PY_EXE%" "%ROOT%\backend\app\doctor.py"
set "DOC_CODE=%errorlevel%"

if %DOC_CODE% neq 0 (
    echo.
    echo [ERROR] Diagnostic check reported failures (exit code: %DOC_CODE%).
    echo Run pre-requisite.bat or repair.bat to fix missing items.
    echo.
    pause
    exit /b %DOC_CODE%
)

echo.
echo [OK] All diagnostic checks passed.
exit /b 0
