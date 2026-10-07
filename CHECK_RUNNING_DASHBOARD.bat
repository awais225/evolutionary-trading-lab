@echo off
rem ===========================================================================
rem  EvolutionaryTradingV5 - CHECK_RUNNING_DASHBOARD.bat
rem
rem  READ-ONLY runtime dashboard identity diagnostic.
rem
rem  Double-click this file, wait for it to finish, then copy the WHOLE console
rem  window and send it back. It answers one question with evidence:
rem
rem      which GitHub commit / source / bundle / process / dashboard is
rem      actually running on the dashboard port right now?
rem
rem  It does NOT stop or start any process, does NOT rebuild the frontend, does
rem  NOT modify the repository, Git, DATA, configuration, MT5 or the browser,
rem  and does NOT place any trade. It only inspects and reports.
rem ===========================================================================
setlocal EnableExtensions
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
title EvolutionaryTradingV5 - Runtime Dashboard Diagnostic (read-only)
color 0F

set "BATDIR=%~dp0"
if "%BATDIR:~-1%"=="\" set "BATDIR=%BATDIR:~0,-1%"
set "ROOT=%BATDIR%"
set "DASHURL=http://127.0.0.1:8787"
set "REPORT=%TEMP%\evolutionary_lab_dashboard_diagnostic.json"

echo ==============================================================================
echo   EVOLUTIONARYTRADINGV5  -  RUNTIME DASHBOARD DIAGNOSTIC  (READ-ONLY)
echo ==============================================================================
echo   This window only INSPECTS. Nothing is stopped, rebuilt or modified.
echo   When it finishes: copy the entire window and send it back.
echo ==============================================================================
echo.

if not exist "%ROOT%\frontend" (
  echo [WARNING] "%ROOT%" does not look like the repository root ^(no frontend folder^).
  echo           Put this file in the repository root (for example C:\EvolutionaryTradingV5)
  echo           and run it again.
  echo.
)

rem ---------------------------------------------------------------------------
rem  Windows-side probe: machine, tool paths, port owner, launchers present
rem ---------------------------------------------------------------------------
set "PROBE=%ROOT%\scripts\dashboard_probe.ps1"
if exist "%PROBE%" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%PROBE%" -Port 8787 -RepoRoot "%ROOT%"
) else (
  echo [WARNING] scripts\dashboard_probe.ps1 is missing - skipping the Windows process probe.
)

rem ---------------------------------------------------------------------------
rem  Python resolution (repository venv first, then PATH, then the py launcher)
rem ---------------------------------------------------------------------------
set "PY="
if exist "%ROOT%\.venv\Scripts\python.exe" set "PY=%ROOT%\.venv\Scripts\python.exe"
if not defined PY if exist "%ROOT%\venv\Scripts\python.exe" set "PY=%ROOT%\venv\Scripts\python.exe"
if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"
)
set "DIAG=%ROOT%\backend\tools\dashboard_diagnostic.py"

if not defined PY (
  echo.
  echo ==============================================================================
  echo   [BLOCKED] No Python interpreter was found.
  echo   Run Prerequisite.bat once to create the environment, then run this again.
  echo ==============================================================================
  goto :finish
)

if not exist "%DIAG%" (
  echo.
  echo [BLOCKED] "%DIAG%" is missing - the repository copy is incomplete.
  echo           Update the checkout (Prerequisite.bat / git pull) and run this file again.
echo           Nothing was changed by this diagnostic.

  goto :finish
)

rem ---------------------------------------------------------------------------
rem  Python-side diagnostic: git identity, frontend identity, HTTP identity,
rem  served index and assets, V5 feature signature, cache/service worker,
rem  all frontend copies, launchers, final verdict.
rem ---------------------------------------------------------------------------
"%PY%" "%DIAG%" --root "%ROOT%" --url "%DASHURL%" --json-out "%REPORT%"
set "DIAGEXIT=%ERRORLEVEL%"

echo.
echo ==============================================================================
echo   DIAGNOSTIC FINISHED
echo ==============================================================================
echo   A JSON copy was written for support to:
echo     %REPORT%
echo   ^(exit code %DIAGEXIT% - 0 means the diagnostic itself ran; the PASS/FAIL
echo    verdict is the RUNNING_DASHBOARD_MATCHES_GITHUB line above^)
echo.

:finish
echo ==============================================================================
echo   END - copy everything above this line and send it back.
echo ==============================================================================
echo.
pause
endlocal
