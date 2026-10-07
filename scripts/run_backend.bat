@echo off
setlocal

REM ===========================================================================
REM       EVOLAB BACKEND SERVER RUNNER (Port 8787)
REM  Pure Windows Command Prompt syntax. Zero fragile parenthesized blocks.
REM ===========================================================================

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT=%%~fI"
cd /d "%ROOT%\backend"

REM V5.1a §9 - resolve the interpreter in exactly the same order start.bat
REM does. The process that verifies the environment and the process that serves
REM the app must never be two different Pythons: that mismatch is what makes
REM `MetaTrader5` appear installed while execution still reports MT5_UNAVAILABLE.
set "PYTHON_EXE="
if exist "%ROOT%\.venv\Scripts\python.exe" set "PYTHON_EXE=%ROOT%\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%ROOT%\backend\.venv\Scripts\python.exe" set "PYTHON_EXE=%ROOT%\backend\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%ROOT%\venv\Scripts\python.exe" set "PYTHON_EXE=%ROOT%\venv\Scripts\python.exe"
if not defined PYTHON_EXE for /f "delims=" %%p in ('where python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%p"
if not defined PYTHON_EXE set "PYTHON_EXE=python.exe"
:PY_FOUND

title EvoLab Backend Server - Port 8787

echo ===========================================================
echo  EVOLAB BACKEND SERVER
echo ===========================================================
echo Root Directory:    %ROOT%
echo Working Directory: %CD%
echo Python:            %PYTHON_EXE%
if exist "%ROOT%\backend\tools\mt5_runtime_diagnostic.py" (
    "%PYTHON_EXE%" "%ROOT%\backend\tools\mt5_runtime_diagnostic.py" --root "%ROOT%" --preflight
)
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
