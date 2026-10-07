@echo off
setlocal

REM ===========================================================================
REM       EVOLAB BACKEND SERVER RUNNER (Port 8787)
REM  Pure Windows Command Prompt syntax. Zero fragile parenthesized blocks.
REM ===========================================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%\backend"

set "PYTHON_EXE=python.exe"
if exist "%ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%ROOT%\.venv\Scripts\python.exe"
    goto :PY_FOUND
)
if exist "%ROOT%\venv\Scripts\python.exe" (
    set "PYTHON_EXE=%ROOT%\venv\Scripts\python.exe"
    goto :PY_FOUND
)
:PY_FOUND

title EvoLab Backend Server - Port 8787

echo ===========================================================
echo  EVOLAB BACKEND SERVER
echo ===========================================================
echo [LAUNCHER] Execution shell: CMD
echo [LAUNCHER] Script: RUN_BACKEND.BAT
echo [LAUNCHER] Working directory: %ROOT%
echo [LAUNCHER] Launcher execution confirmed
echo Root Directory:    %ROOT%
echo Working Directory: %CD%
echo Python:            %PYTHON_EXE%
echo Launch token:      %EVOLUTIONARY_LAB_START_TOKEN%
echo.
REM Name the exact build this process is about to serve: the dashboard's Build
REM chip and /system/build report the same values, so what is running is never
REM a matter of opinion.
if exist "%ROOT%\backend\tools\frontend_build_guard.py" (
    "%PYTHON_EXE%" "%ROOT%\backend\tools\frontend_build_guard.py" --root "%ROOT%" --summary
)

"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8787

echo.
echo ===========================================================
echo  BACKEND PROCESS STOPPED
echo ===========================================================
echo.
pause
