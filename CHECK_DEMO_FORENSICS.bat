@echo off
rem ===========================================================================
rem  CHECK_DEMO_FORENSICS.bat  -  V5.3 MT5 DEMO EXECUTION FORENSICS
rem
rem  WHY
rem    Ten releases could not prove a DEMO order because every failure was
rem    summarised after the fact. This tool walks the REAL chain with the REAL
rem    interpreter, MetaTrader5 binding, terminal and account, and prints every
rem    value the binding produced - including mt5.last_error() BEFORE and AFTER
rem    order_send and the ACTUAL terminal state (positions_get/orders_get).
rem
rem  DEFAULT (no arguments): READ-ONLY
rem    order_check only. NOTHING is sent to the broker.
rem
rem  TO SEND ONE DEMO ORDER
rem    set SEND_DEMO_ORDER=1 before running this file (or run the python tool
rem    with --send --confirm PLACE_DEMO_ORDER). Exactly ONE order_send attempt,
rem    never a retry.
rem
rem  BEFORE ANY SEND (V5.4)
rem    the tool first reads the terminal (positions_get/orders_get) and reports
rem    any OPEN position/order for this symbol with magic 777000 (dashboard) or
rem    777900 (this tool). If one exists it REFUSES to send (exit 6) and asks you
rem    to inspect the terminal first - an earlier attempt may already have filled.
rem    Nothing is transmitted in that case. Only add FORCE_DEMO_ORDER=1 (or
rem    --force) when you have looked at the terminal and still want a new order.
rem
rem  The full report is saved to LOGS\MT5_DEMO_FORENSICS_<stamp>.txt so it can be
rem  attached instead of copied by hand.
rem ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title CHECK MT5 DEMO FORENSICS (V5.3)

chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "LOGDIR=%~dp0LOGS"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
set "STAMP="
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set "STAMP=%%I"
if defined STAMP (
  set "STAMP=!STAMP:~0,8!_!STAMP:~8,6!"
) else (
  set "STAMP=run_%RANDOM%"
)
set "LOG=%LOGDIR%\MT5_DEMO_FORENSICS_!STAMP!.txt"

echo ===========================================================================
echo  MT5 DEMO EXECUTION FORENSICS (V5.3)
if defined SEND_DEMO_ORDER (
  echo  MODE: SEND - exactly ONE demo order will be attempted
) else (
  echo  MODE: READ-ONLY - order_check only, nothing is sent to the broker
)
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
set "EXTRA="
if defined SEND_DEMO_ORDER set "EXTRA=--send --confirm PLACE_DEMO_ORDER"
if defined FORCE_DEMO_ORDER set "EXTRA=!EXTRA! --force"
set "SYMSIDE=%~1"
if not defined SYMSIDE set "SYMSIDE=buy"
echo  symbol / side     : XAUUSD / !SYMSIDE!
echo  running the forensic chain ... (initialize -^> symbol -^> filling probe -^> order_check%s)
echo.
"%PYEXE%" "%~dp0backend\tools\mt5_demo_forensics.py" --root "%~dp0" --symbol XAUUSD --symbol-side !SYMSIDE! --volume 0.03 --log "!LOG!" !EXTRA!
set "RC=!ERRORLEVEL!"
echo.
echo ---------------------------------------------------------------------------
echo  exit code : !RC!   (0 = ran / PASS, 2 = sent but not verified, 3 = no real MT5 bridge,
echo                      4 = no live quote, 5 = confirmation missing,
echo                      6 = REFUSED: an open position/order already exists - inspect it)
echo  full report saved to : !LOG!
echo  paste the report (or attach that file) into the chat - it contains every
echo  value MT5 returned, so the exact failing layer can be named.
echo ---------------------------------------------------------------------------
goto :done

:nopython
echo  No Python interpreter was found. Run Prerequisite.bat once, then retry.
goto :done

:done
echo.
echo  Press any key to close this window . . .
pause >nul
endlocal
