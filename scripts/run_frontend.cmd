@echo off
rem HERO1 frontend service window - started by START.bat (or run directly).
rem Same proven pattern as run_backend.cmd: cd to the vite app, run it,
rem stay open on any exit. NO pipes, NO PowerShell dependency.
setlocal EnableExtensions

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%\apps\web" || (echo [HERO1][FATAL] cannot cd to "%ROOT%\apps\web" & pause & exit /b 1)
if not exist "node_modules" (
  echo [HERO1][FATAL] apps\web\node_modules is missing. Run PREREQUISITES.bat first.
  pause
  exit /b 1
)

if not "%HERO1_DATA_ROOT%"=="" (
  set "DATA_ROOT=%HERO1_DATA_ROOT%"
) else (
  set "DATA_ROOT=%ROOT%\DATA"
)
if not exist "%DATA_ROOT%\LOGS" mkdir "%DATA_ROOT%\LOGS" >nul 2>&1
set "HERO1_DATA_ROOT=%DATA_ROOT%"
if "%HERO1_REPO_ROOT%"=="" set "HERO1_REPO_ROOT=%ROOT%"

title HERO1 Frontend :5173
echo ===========================================================
echo  HERO1 FRONTEND  -  http://127.0.0.1:5173/
echo ===========================================================
echo  Project root : %ROOT%
echo  Vite config  : strictPort 5173, proxy /api + /health + /ws -> 127.0.0.1:8788
echo  Stop with    : Ctrl+C, or close this window
echo ===========================================================
echo.

call npm run dev
set "RC=%ERRORLEVEL%"

echo.
echo ===========================================================
echo  HERO1 FRONTEND STOPPED (exit code %RC%)
echo ===========================================================
echo  If port 5173 was taken by another app, vite exits instead of
echo  silently moving to another port (strictPort). Free port 5173
echo  or close the conflicting application and launch again.
echo  This window stays open on purpose. Press any key to close it.
pause >nul
exit /b %RC%
