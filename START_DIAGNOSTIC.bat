@echo off
REM HERO1 - START DIAGNOSTIC (Windows cmd.exe)
REM ============================================================================
REM  Double-click this file to diagnose the startup failure WITHOUT starting
REM  or changing anything: no databases are created, migrated, repaired or
REM  deleted; every check below is read-only. The window ALWAYS stays open;
REM  everything printed here is also written to <DATA_ROOT>\LOGS\diagnostic.log
REM ============================================================================
setlocal EnableExtensions EnableDelayedExpansion
title HERO1 START DIAGNOSTIC

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
if not "%HERO1_DATA_ROOT%"=="" (
  set "DATA_ROOT=%HERO1_DATA_ROOT%"
) else (
  set "DATA_ROOT=%ROOT%\DATA"
)
if not defined HERO1_DATA_ROOT set "HERO1_DATA_ROOT=%DATA_ROOT%"
set "LOGDIR=%DATA_ROOT%\LOGS"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
set "LOG=%LOGDIR%\diagnostic.log"
echo HERO1 diagnostic - %DATE% %TIME% > "%LOG%"
echo  root=%ROOT%  data=%DATA_ROOT% >> "%LOG%"

echo ===========================================================
echo  HERO1 - START DIAGNOSTIC  (read-only; changes nothing)
echo ===========================================================
echo  Diagnostic file : %~f0
echo  Project root    : %ROOT%
echo  Working dir     : %CD%
echo  Data root       : %DATA_ROOT%
echo  Data exists     : 
if exist "%DATA_ROOT%" (echo   yes) else (echo   NO - nothing to check against)
for %%I in ("%~f0") do echo  Diagnostic size : %%~zI bytes  Modified: %%~tI
where git >nul 2>&1 && for /f "delims=" %%G in ('git -C "%ROOT%" rev-parse HEAD 2^>nul') do echo  Git HEAD        : %%G
where git >nul 2>&1 && for /f "delims=" %%G in ('git -C "%ROOT%" status --short 2^>nul') do echo  Git dirty files : %%G
echo ===========================================================
echo.

call :STEP "1. Python virtual environment"
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  call :ROW ".venv python" MISSING "run PREREQUISITES.bat first"
  goto :DIAG_END
)
call :ROW ".venv python" FOUND "%VENV_PY%"
"%VENV_PY%" --version >> "%LOG%" 2>&1
"%VENV_PY%" --version 2>&1
set "RC=%errorlevel%"
call :ROW "python --version rc" %RC% ""
echo.

call :STEP "2. Required files"
for %%F in (services\api\app\main.py apps\web\package.json scripts\init_db.py scripts\check_port.py scripts\wait_ready.py scripts\verify_instance.py scripts\run_backend.cmd scripts\run_frontend.cmd packages\storage\src\hero1_storage\__init__.py) do (
  if exist "%ROOT%\%%F" (call :ROW "%%F" OK "") else (call :ROW "%%F" MISSING "git pull / complete checkout")
)
echo.

call :STEP "3. Backend import verification"
"%VENV_PY%" -c "import sys; sys.path.insert(0, r'%ROOT%\services\api'); import app.main; print('import app.main OK')" > "%TEMP%\hero1_diag_import.tmp" 2>&1
set "RC=%errorlevel%"
type "%TEMP%\hero1_diag_import.tmp"
type "%TEMP%\hero1_diag_import.tmp" >> "%LOG%"
del "%TEMP%\hero1_diag_import.tmp" >nul 2>&1
call :ROW "backend import" %RC% "0=OK"
echo.

call :STEP "4. Database integrity check (read-only)"
echo     Command: "%VENV_PY%" %ROOT%\scripts\init_db.py --check
"%VENV_PY%" "%ROOT%\scripts\init_db.py" --check > "%TEMP%\hero1_diag_dbcheck.tmp" 2>&1
set "RC=%errorlevel%"
type "%TEMP%\hero1_diag_dbcheck.tmp"
type "%TEMP%\hero1_diag_dbcheck.tmp" >> "%LOG%"
del "%TEMP%\hero1_diag_dbcheck.tmp" >nul 2>&1
call :ROW "init_db --check" %RC% "0=healthy, 1=unhealthy (report above), 2=crashed (traceback above)"
echo.

call :STEP "5. SQLite PRAGMA integrity_check (read-only)"
"%VENV_PY%" -c "import sqlite3,os,sys; root=sys.argv[1]; paths=[os.path.join(root,'DATABASE','hero1_registry.db')]+[os.path.join(root,s,'DATABASE','hero1.db') for s in os.listdir(root) if s.isupper() and os.path.isfile(os.path.join(root,s,'DATABASE','hero1.db'))]; [print(p, '->', sqlite3.connect('file:'+p+'?mode=ro', uri=True).execute('PRAGMA integrity_check').fetchall()[0][0]) for p in paths]" "%DATA_ROOT%" > "%TEMP%\hero1_diag_pragma.tmp" 2>&1
set "RC=%errorlevel%"
type "%TEMP%\hero1_diag_pragma.tmp"
type "%TEMP%\hero1_diag_pragma.tmp" >> "%LOG%"
del "%TEMP%\hero1_diag_pragma.tmp" >nul 2>&1
call :ROW "PRAGMA integrity_check" %RC% "0=read OK; each DB must print -> ok"
echo.

call :STEP "6. Ports 8788 / 5173 (who is answering?)"
"%VENV_PY%" "%ROOT%\scripts\check_port.py" 8788
set "RC=%errorlevel%"
call :ROW "port 8788" %RC% "0=free, 10=verified Hero1, 20=foreign"
"%VENV_PY%" "%ROOT%\scripts\check_port.py" --frontend 5173
set "RC=%errorlevel%"
call :ROW "port 5173" %RC% "0=free, 10=verified Hero1, 20=foreign"
echo.

call :STEP "7. Environment"
call :ROW "HERO1_DATA_ROOT" "%HERO1_DATA_ROOT%" "(this session)"
call :ROW "PATH contains venv" "" "(not required - launcher uses absolute paths)"
echo   see %LOG% for the full log.
echo.

call :STEP "8. MetaTrader5 package in backend venv (read-only)"
echo     Command: "%VENV_PY%" %ROOT%\scripts\check_mt5_env.py --json
"%VENV_PY%" "%ROOT%\scripts\check_mt5_env.py" --json > "%TEMP%\hero1_diag_mt5.tmp" 2>&1
set "RC=%errorlevel%"
type "%TEMP%\hero1_diag_mt5.tmp"
type "%TEMP%\hero1_diag_mt5.tmp" >> "%LOG%"
del "%TEMP%\hero1_diag_mt5.tmp" >nul 2>&1
call :ROW "MetaTrader5 env" %RC% "0=importable, 1=missing, 2=broken install"

:DIAG_END
echo.
echo ===========================================================
echo  DIAGNOSTIC COMPLETE - window stays open.
echo  Log written to: %LOG%
echo  Send that file (and a photo of this window) with the report.
echo  To fix it yourself first: open TROUBLESHOOTING.txt in the
echo  repository root and search for the exact error text shown above.
echo ===========================================================
pause >nul
exit /b 0

:STEP
echo.
echo ---- %~1
echo ---- %~1 >> "%LOG%"
exit /b 0

:ROW
echo   [%~2] %~1 %~3
echo   [%~2] %~1 %~3 >> "%LOG%"
exit /b 0
