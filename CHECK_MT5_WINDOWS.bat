@echo off
rem ===========================================================================
rem  CHECK_MT5_WINDOWS.bat  -  READ-ONLY MetaTrader 5 diagnostic (V5.1a-next)
rem
rem  What it does
rem    * finds the Python interpreter the lab launcher would use,
rem    * runs backend\tools\mt5_windows_diagnostic.py with it,
rem    * prints a full report and classifies the exact failing layer
rem      (MT5 READY / MT5 PACKAGE MISSING / MT5 TERMINAL NOT FOUND / ...).
rem
rem  What it never does
rem    * NO order is placed    - the diagnostic only calls mt5.order_check
rem    * NO process is killed  - terminals are only listed, never touched
rem    * NO credentials are read or printed
rem
rem  The report is also written to LOGS\MT5_WINDOWS_DIAGNOSTIC_<stamp>.txt so it
rem  can be attached to the chat instead of copied by hand.
rem ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title CHECK MT5 (WINDOWS) - READ ONLY

chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "LOGDIR=%~dp0LOGS"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1

rem ---- log file stamp: wmic when present, otherwise a unique fallback ----
set "STAMP="
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set "STAMP=%%I"
if defined STAMP (
  set "STAMP=!STAMP:~0,8!_!STAMP:~8,6!"
) else (
  set "STAMP=run_%RANDOM%"
)
set "LOG=%LOGDIR%\MT5_WINDOWS_DIAGNOSTIC_!STAMP!.txt"

echo ===========================================================================
echo  MT5 WINDOWS DIAGNOSTIC - read only
echo  no order is placed, no process is started or killed
echo ===========================================================================
echo.
echo  working directory : %CD%
echo  log file          : !LOG!
echo.

rem ---- resolve python exactly like start.bat / run_backend.bat ----
set "PYEXE="
set "PYHOW="
if exist "%~dp0.venv\Scripts\python.exe" (
  set "PYEXE=%~dp0.venv\Scripts\python.exe"
  set "PYHOW=root\.venv"
)
if not defined PYEXE if exist "%~dp0backend\.venv\Scripts\python.exe" (
  set "PYEXE=%~dp0backend\.venv\Scripts\python.exe"
  set "PYHOW=backend\.venv"
)
if not defined PYEXE if exist "%~dp0venv\Scripts\python.exe" (
  set "PYEXE=%~dp0venv\Scripts\python.exe"
  set "PYHOW=root\venv"
)
if not defined PYEXE (
  for /f "delims=" %%P in ('where py 2^>nul') do (
    if not defined PYEXE (
      set "PYEXE=%%P"
      set "PYHOW=py launcher"
    )
  )
)
if not defined PYEXE (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYEXE (
      set "PYEXE=%%P"
      set "PYHOW=python on PATH"
    )
  )
)

if not defined PYEXE goto :nopython

echo  interpreter       : !PYEXE!   (!PYHOW!)
echo  running the deep MT5 diagnostic ... (this can take up to a minute)
echo.
"!PYEXE!" "%~dp0backend\tools\mt5_windows_diagnostic.py" --root "%~dp0" --symbol XAUUSD --save "!LOG!"
set "RC=!ERRORLEVEL!"
echo.
echo ---------------------------------------------------------------------------
echo  diagnostic exit code : !RC!   (0 = MT5 READY, 3 = not ready, 4 = not Windows)
echo  full report saved to : !LOG!
echo  copy the text above (or attach that file) back into the chat
echo ---------------------------------------------------------------------------
goto :done

:nopython
rem ---- no Python at all: collect what cmd can, write it to the log, print it ----
(
  echo ===========================================================================
  echo  MT5 WINDOWS DIAGNOSTIC - no Python interpreter was found
  echo ===========================================================================
  echo.
  echo  The lab needs a 64-bit Windows Python ^(any CPython 3.6-3.14^) with the
  echo  MetaTrader5 package. Python 3.13 is NOT required.
  echo.
  echo  ---- machine ----
  ver
  echo  working directory : %CD%
  echo.
  echo  ---- python ----
  echo  where python:
  where python 2^>nul
  echo  where py:
  where py 2^>nul
  echo  where pip:
  where pip 2^>nul
  echo.
  echo  ---- MetaTrader5 terminal (read only, nothing is started or killed) ----
  echo  running terminal64.exe / terminal.exe processes:
  tasklist /FI "IMAGENAME eq terminal64.exe" 2^>nul
  tasklist /FI "IMAGENAME eq terminal.exe" 2^>nul
  echo  usual install locations:
  if exist "%ProgramFiles%\MetaTrader 5" echo   found: %ProgramFiles%\MetaTrader 5
  if exist "%ProgramFiles(x86)%\MetaTrader 5" echo   found: %ProgramFiles(x86)%\MetaTrader 5
  if exist "%APPDATA%\MetaQuotes\Terminal" echo   found: %APPDATA%\MetaQuotes\Terminal
  echo.
  echo  ---- classification ----
  echo  CLASSIFICATION: MT5 PACKAGE MISSING
  echo  REASON        : no Python interpreter was found - the MetaTrader5 package
  echo                  cannot be imported by anything. Install a 64-bit Windows
  echo                  Python, then run:  python -m pip install MetaTrader5
  echo                  (and install it into the lab environment the launcher uses:
  echo                  .venv\Scripts\python.exe -m pip install MetaTrader5).
) > "!LOG!" 2>&1
type "!LOG!"
echo.
echo ---------------------------------------------------------------------------
echo  full report saved to : !LOG!
echo  copy the text above (or attach that file) back into the chat
echo ---------------------------------------------------------------------------

:done
echo.
pause
endlocal
