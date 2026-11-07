@echo off
rem HERO1 backend service window - started by START.bat (or run directly).
rem Adapted from the proven legacy RUN_BACKEND.bat / scripts/run_backend.bat pattern:
rem resolve the venv interpreter explicitly, cd to the backend package dir,
rem run `uvicorn app.main:app`, and STAY OPEN on any exit so failures are visible.
rem NO pipes, NO PowerShell dependency, NO nested quoting.
setlocal EnableExtensions

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%\services\api" || (echo [HERO1][FATAL] cannot cd to "%ROOT%\services\api" & pause & exit /b 1)

if not "%HERO1_DATA_ROOT%"=="" (
  set "DATA_ROOT=%HERO1_DATA_ROOT%"
) else (
  set "DATA_ROOT=%ROOT%\DATA"
)
if not exist "%DATA_ROOT%\LOGS" mkdir "%DATA_ROOT%\LOGS" >nul 2>&1
if "%HERO1_REPO_ROOT%"=="" set "HERO1_REPO_ROOT=%ROOT%"
if "%HERO1_START_TOKEN%"=="" set "HERO1_START_TOKEN=HERO1-STANDALONE-%RANDOM%%RANDOM%"
set "HERO1_DATA_ROOT=%DATA_ROOT%"
set "PYTHONIOENCODING=utf-8"

if not exist "%ROOT%\.venv\Scripts\python.exe" (
  echo [HERO1][FATAL] %ROOT%\.venv\Scripts\python.exe not found. Run PREREQUISITES.bat first.
  pause
  exit /b 1
)

title HERO1 Backend :8788
echo ===========================================================
echo  HERO1 BACKEND  -  http://127.0.0.1:8788
echo ===========================================================
echo  Project root : %ROOT%
echo  DATA root    : %DATA_ROOT%
echo  Health       : http://127.0.0.1:8788/health
echo  Start token  : %HERO1_START_TOKEN%
echo  Stop with    : Ctrl+C, or close this window
echo ===========================================================
echo.

"%ROOT%\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8788
set "RC=%ERRORLEVEL%"

echo.
echo ===========================================================
echo  HERO1 BACKEND STOPPED (exit code %RC%)
echo ===========================================================
echo  If this was unexpected, scroll up for the traceback.
echo  Startup/launcher log: %DATA_ROOT%\LOGS\startup.log
echo  This window stays open on purpose. Press any key to close it.
pause >nul
exit /b %RC%
