@echo off
rem ===========================================================================
rem  EvolutionaryTradingV5 - CHECK_RUNNING_DASHBOARD.bat
rem
rem  READ-ONLY runtime dashboard identity diagnostic.
rem
rem  WHAT IT ANSWERS
rem    which repository commit / source / bundle / process / dashboard is
rem    actually running on the dashboard port right now:
rem      working directory, Git HEAD and origin/main, backend PID + executable,
rem      Python executable/version, frontend/src fingerprint, frontend/dist
rem      fingerprint + build stamp, runtime start token, port, /health,
rem      /system/build, changed_since_start and the runtime guard verdict.
rem
rem  THE WINDOW DOES NOT CLOSE ON ITS OWN
rem    The work is run inside "cmd /k", so the console survives even if the script
rem    ends or a key is read unexpectedly, and the last line waits for a key.
rem    A full copy is written to  LOGS\RUNTIME_IDENTITY_<stamp>.txt  as well, so the
rem    identity can still be recovered (or attached to the chat) if the window is
rem    closed by accident.
rem
rem  It does NOT stop or start any process, does NOT rebuild the frontend, does
rem  NOT modify the repository, Git, DATA, configuration, MT5 or the browser, and
rem  does NOT place any trade. It only inspects and reports.
rem ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion

rem ---------------------------------------------------------------------------
rem  Keep-alive guard: when double-clicked (no arguments) this file re-runs itself
rem  inside a console that cannot close silently. With --stay it executes the body.
rem ---------------------------------------------------------------------------
if /I "%~1"=="--stay" goto :body
start "EvolutionaryTradingV5 - runtime identity" cmd /k call "%~f0" --stay
exit /b 0

:body
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
title EvolutionaryTradingV5 - Runtime Dashboard Diagnostic (read-only)
color 0F

set "BATDIR=%~dp0"
if "%BATDIR:~-1%"=="\" set "BATDIR=%BATDIR:~0,-1%"
set "ROOT=%BATDIR%"
set "DASHURL=http://127.0.0.1:8787"
set "LOGDIR=%ROOT%\LOGS"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1

set "STAMP="
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set "STAMP=%%I"
if defined STAMP (
  set "STAMP=!STAMP:~0,8!_!STAMP:~8,6!"
) else (
  set "STAMP=run_%RANDOM%"
)
set "REPORT=%LOGDIR%\RUNTIME_IDENTITY_!STAMP!.txt"
set "DEEPREPORT=%LOGDIR%\RUNTIME_IDENTITY_DEEP_!STAMP!.txt"
set "TMPJSON=%TEMP%\evolutionary_lab_dashboard_diagnostic.json"

echo ==============================================================================
echo   EVOLUTIONARYTRADINGV5  -  RUNTIME DASHBOARD DIAGNOSTIC  (READ-ONLY)
echo ==============================================================================
echo   This window INSPECTS ONLY. Nothing is stopped, rebuilt or modified.
echo   The window will NOT close by itself - the last line waits for a key.
echo   The same text is saved to:
echo     %REPORT%
echo ==============================================================================
echo.
echo ==============================================================================>> "%REPORT%"
echo   EVOLUTIONARYTRADINGV5  -  RUNTIME DASHBOARD IDENTITY  (READ-ONLY)>> "%REPORT%"
echo   generated : %DATE% %TIME%>> "%REPORT%"
echo   root      : %ROOT%>> "%REPORT%"
echo   dashboard : %DASHURL%>> "%REPORT%"
echo ==============================================================================>> "%REPORT%"

if not exist "%ROOT%\frontend" (
  echo [WARNING] "%ROOT%" does not look like the repository root ^(no frontend folder^).
  echo           Put this file in the repository root (for example C:\EvolutionaryTradingV5)
  echo           and run it again.
  echo.
  echo [WARNING] no frontend folder under the repository root>> "%REPORT%"
)

rem ---------------------------------------------------------------------------
rem  Python resolution (repository venv first, then PATH)
rem ---------------------------------------------------------------------------
set "PY="
if exist "%ROOT%\.venv\Scripts\python.exe" set "PY=%ROOT%\.venv\Scripts\python.exe"
if not defined PY if exist "%ROOT%\backend\.venv\Scripts\python.exe" set "PY=%ROOT%\backend\.venv\Scripts\python.exe"
if not defined PY if exist "%ROOT%\venv\Scripts\python.exe" set "PY=%ROOT%\venv\Scripts\python.exe"
if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"
)

rem ---------------------------------------------------------------------------
rem  SECTION 1 - repository / process identity with plain Windows commands.
rem  This is the part the operator needs most: WHICH BUILD IS RUNNING. It works
rem  even when Python or the probe scripts are unavailable.
rem ---------------------------------------------------------------------------
echo.
echo ------------------------------------------------------------------------------
echo  SECTION 1  REPOSITORY / PROCESS IDENTITY
echo ------------------------------------------------------------------------------
echo.>> "%REPORT%"
echo ------------------------------------------------------------------------------>> "%REPORT%"
echo  SECTION 1  REPOSITORY / PROCESS IDENTITY>> "%REPORT%"
echo ------------------------------------------------------------------------------>> "%REPORT%"

echo [working directory] %ROOT%
echo [working directory] %ROOT%>> "%REPORT%"
echo [interpreter] %PY%
echo [interpreter] %PY%>> "%REPORT%"

where git >nul 2>nul
if errorlevel 1 (
  echo [git] git is not on PATH - the checkout cannot be identified from here.
  echo [git] git is not on PATH>> "%REPORT%"
) else if not exist "%ROOT%\.git" (
  rem V5.2.3 - a GitHub "Download ZIP" export has no .git: say so instead of
  rem printing raw git errors, and identify the build by FINGERPRINT instead.
  echo [git] THIS IS NOT A GIT CHECKOUT: %ROOT%\.git does not exist.
  echo [git] (a GitHub "Download ZIP" export looks exactly like this - there is
  echo [git]  no commit to compare, and "git pull" cannot work here.)
  echo [git] identity is established by fingerprint in SECTION 2 below
  echo [git] (BUILD_FINGERPRINTS.json -> src_hash + backend code hash + commit).
  echo [git] THIS IS NOT A GIT CHECKOUT: %ROOT%\.git does not exist.>> "%REPORT%"
  echo [git] identity below is established by FINGERPRINT, not by commit.>> "%REPORT%"
  echo [git] to make git identity available: git clone https://github.com/awais225/evolutionary-trading-lab.git
) else (
  set "GITHEAD="
  set "GITBRANCH="
  set "GITLAST="
  set "GITORIGIN="
  set "GITMAIN="
  for /f "delims=" %%H in ('git -C "%ROOT%" rev-parse HEAD 2^>nul') do set "GITHEAD=%%H"
  for /f "delims=" %%B in ('git -C "%ROOT%" rev-parse --abbrev-ref HEAD 2^>nul') do set "GITBRANCH=%%B"
  for /f "delims=" %%S in ('git -C "%ROOT%" log -1 --pretty=oneline 2^>nul') do set "GITLAST=%%S"
  for /f "delims=" %%O in ('git -C "%ROOT%" remote get-url origin 2^>nul') do set "GITORIGIN=%%O"
  for /f "delims=" %%M in ('git -C "%ROOT%" rev-parse origin/main 2^>nul') do set "GITMAIN=%%M"
  rem V5.3 - never print an empty git field: these lines only run when the 
  rem command actually produced a value (a ZIP checkout must not show blanks).
  if defined GITHEAD (
    echo [git] HEAD        : !GITHEAD!
    echo [git] branch      : !GITBRANCH!
    echo [git] commit      : !GITLAST!
    echo [git] origin      : !GITORIGIN!
    echo [git] HEAD        : !GITHEAD!>> "%REPORT%"
    echo [git] branch      : !GITBRANCH!>> "%REPORT%"
    echo [git] commit      : !GITLAST!>> "%REPORT%"
    echo [git] origin      : !GITORIGIN!>> "%REPORT%"
    if defined GITMAIN (
      echo [git] origin/main : !GITMAIN!   ^(as last fetched - run "git fetch origin" for the newest^)
      echo [git] origin/main : !GITMAIN!>> "%REPORT%"
      if /I "!GITHEAD!"=="!GITMAIN!" (
        echo [git] HEAD == origin/main  ^(this checkout is the GitHub main state as last fetched^)
        echo [git] HEAD == origin/main>> "%REPORT%"
      ) else (
        echo [git] NOTE: HEAD differs from the last known origin/main - a pull may be needed.
        echo [git] NOTE: HEAD differs from the last known origin/main>> "%REPORT%"
      )
    ) else (
      echo [git] origin/main : not read in this folder - run "git fetch origin" first.
      echo [git] origin/main : not read in this folder.>> "%REPORT%"
    )
  ) else (
    echo [git] no commit identity could be read here - identity comes from the
    echo [git] FINGERPRINT section below, not from a commit.
    echo [git] no commit identity could be read here - identity by FINGERPRINT.>> "%REPORT%"
  )
)

if not defined PY (
  echo [python] no Python interpreter found - run Prerequisite.bat once.
  echo [python] no Python interpreter found>> "%REPORT%"
) else (
  set "PYVER="
  for /f "delims=" %%V in ('"%PY%" --version 2^>^&1') do set "PYVER=%%V"
  echo [python] !PYVER!
  echo [python] !PYVER!>> "%REPORT%"
)

echo [port 8787] listeners (PID is the last column):
echo [port 8787] listeners:>> "%REPORT%"
netstat -ano | findstr ":8787"
netstat -ano | findstr ":8787" >> "%REPORT%" 2>&1
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8787" ^| findstr "LISTENING"') do (
  echo [port 8787] owner PID %%P:
  echo [port 8787] owner PID %%P:>> "%REPORT%"
  tasklist /FI "PID eq %%P" /FO LIST | findstr /I "Image Name PID Session"
  tasklist /FI "PID eq %%P" /FO LIST | findstr /I "Image Name PID Session" >> "%REPORT%" 2>&1
)

if not defined PY (
  echo.
  echo [BLOCKED] No Python interpreter was found, so the deep identity probe
  echo           ^(frontend fingerprint, /health, /system/build, runtime token^) cannot run.
  echo           Run Prerequisite.bat once to create the environment, then run this again.
  echo [BLOCKED] no Python interpreter - deep probe skipped>> "%REPORT%"
  goto :finish
)

set "DIAG=%ROOT%\backend\tools\dashboard_diagnostic.py"
if not exist "!DIAG!" (
  echo.
  echo [BLOCKED] "!DIAG!" is missing - the repository copy is incomplete.
  echo           Update the checkout ^(Prerequisite.bat / git pull^) and run this file again.
  echo [BLOCKED] backend\tools\dashboard_diagnostic.py missing>> "%REPORT%"
  goto :finish
)

rem ---------------------------------------------------------------------------
rem  SECTION 2 - the deep, read-only identity probe: git identity, frontend
rem  source/dist fingerprints, HTTP identity of the serving process, served
rem  index and assets, cache headers, launchers, final verdict.
rem ---------------------------------------------------------------------------
echo.
echo ------------------------------------------------------------------------------
echo  SECTION 2  RUNNING DASHBOARD IDENTITY  (HTTP + process, read-only)
echo ------------------------------------------------------------------------------
echo.
echo ------------------------------------------------------------------------------>> "%REPORT%"
echo  SECTION 2  RUNNING DASHBOARD IDENTITY  (HTTP + process, read-only)>> "%REPORT%"
echo ------------------------------------------------------------------------------>> "%REPORT%"

set "PROBE=%ROOT%\scripts\dashboard_probe.ps1"
if exist "%PROBE%" (
  echo [..] windows process probe ...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%PROBE%" -Port 8787 -RepoRoot "%ROOT%" > "%LOGDIR%\RUNTIME_IDENTITY_PROBE_!STAMP!.txt" 2>&1
  type "%LOGDIR%\RUNTIME_IDENTITY_PROBE_!STAMP!.txt"
  type "%LOGDIR%\RUNTIME_IDENTITY_PROBE_!STAMP!.txt" >> "%REPORT%"
) else (
  echo [WARNING] scripts\dashboard_probe.ps1 is missing - skipping the Windows process probe.
  echo [WARNING] scripts\dashboard_probe.ps1 missing>> "%REPORT%"
)

echo.
echo [..] full identity diagnostic ...
"%PY%" "%DIAG%" --root "%ROOT%" --url "%DASHURL%" --json-out "%TMPJSON%" > "%DEEPREPORT%" 2>&1
set "DIAGEXIT=!ERRORLEVEL!"
type "%DEEPREPORT%"
type "%DEEPREPORT%" >> "%REPORT%"

echo.
echo ==============================================================================
echo   DIAGNOSTIC FINISHED   ^(exit code !DIAGEXIT! - 0 means the diagnostic itself ran^)
echo ==============================================================================
echo   The PASS/FAIL verdict is the RUNNING_DASHBOARD_MATCHES_GITHUB line above.
echo   Full text copy  : %REPORT%
echo   JSON copy       : %TMPJSON%
echo   Sections 1+2 of this window are also in the text copy.
echo ==============================================================================
echo.>> "%REPORT%"
echo DIAGNOSTIC FINISHED (exit code !DIAGEXIT!)>> "%REPORT%"

:finish
echo.
echo ==============================================================================
echo   END - copy everything above this line and send it back.
echo   This window stays open: press any key to continue
echo   ^(then close the window with X, or type EXIT and press Enter^).
echo ==============================================================================
echo.>> "%REPORT%"
echo END OF RUNTIME IDENTITY REPORT>> "%REPORT%"
echo.
pause >nul
exit /b 0
