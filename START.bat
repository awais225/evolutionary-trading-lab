@echo off
REM HERO1 - MAIN LAUNCHER (Windows cmd.exe) - persistent outer shell
REM ============================================================================
REM  THIS WINDOW CANNOT CLOSE BY ITSELF.
REM
REM  The actual startup procedure lives in scripts\START_INTERNAL.bat. It is
REM  executed INSIDE a persistent cmd /k session below, so NOTHING it does -
REM  a missing label, an invalid command, a Python exception, a native Python
REM  crash, a database error, a missing dependency - can close this window.
REM  If the procedure fails, the window stays open showing the exact failure,
REM  the failed command, its exit code and the tail of DATA\LOGS\startup.log.
REM
REM  Double-click this file. Do not rename it. If it closes anyway, the file
REM  you are running is not this one - see docs/WINDOWS_SETUP.md section 0.1.
REM ============================================================================
setlocal EnableExtensions
title HERO1 Trading Research Workstation (persistent launcher)

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

echo ===========================================================
echo  HERO1 - PERSISTENT OUTER LAUNCHER
echo ===========================================================
echo  This window stays open no matter what happens.
echo.
echo  Launcher path : %~f0
echo  Project root  : %ROOT%
echo  Working dir   : %CD%
for %%I in ("%~f0") do (
  echo  Launcher size : %%~zI bytes
  echo  Modified      : %%~tI
)
where certutil >nul 2>&1 && for /f "skip=1 delims=" %%H in ('certutil -hashfile "%~f0" SHA256 2^>nul') do echo  SHA-256       : %%H
where git >nul 2>&1 && for /f "delims=" %%G in ('git -C "%ROOT%" rev-parse --short HEAD 2^>nul') do echo  Git HEAD      : %%G
echo ===========================================================
echo.

if not exist "%ROOT%\scripts\START_INTERNAL.bat" (
  echo [FATAL] %ROOT%\scripts\START_INTERNAL.bat is MISSING.
  echo         This is not a complete, current checkout of Hero1.
  echo         Fix:  cd /d "%ROOT%" ^&^& git status ^&^& git pull
  echo.
  echo Press any key to close this window...
  pause >nul
  exit /b 1
)
if not exist "%ROOT%\START_DIAGNOSTIC.bat" (
  echo [WARN] START_DIAGNOSTIC.bat not found next to me - continuing anyway.
)

echo [..] Handing over to the startup procedure in a persistent session...
echo      Every stage below prints its own command, exit code and PASS/FAIL.
echo      If it fails, read the FAILED block, press any key; if it drops to a
echo      bare prompt instead, type EXIT then Enter, then any key here.
echo.

cmd /k call "%ROOT%\scripts\START_INTERNAL.bat"

echo.
echo ===========================================================
echo  The startup procedure has ended (exit code %errorlevel%).
echo  This window stays open.
echo  - If everything above says PASS/OK, Hero1 is running:
echo      Dashboard : http://127.0.0.1:5173/
echo      Backend   : http://127.0.0.1:8788/health
echo  - If anything FAILED, read the diagnostic above and
echo    %ROOT%\DATA\LOGS\startup.log, then send them with the report.
echo  - If the inner session is sitting at a dumb prompt, type EXIT to close it,
echo    then press any key here.
echo ===========================================================
echo Press any key to close this window...
pause >nul
exit /b %errorlevel%
