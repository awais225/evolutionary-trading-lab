@echo off
rem HERO1 frontend service window - started by START.bat (or run directly).
rem Stays open on ANY exit so the operator always sees why the frontend stopped.
setlocal EnableExtensions

pushd "%~dp0..\apps\web" || (echo ERROR: cannot locate apps\web from "%~dp0" & pause & exit /b 1)
set "ROOT=%CD%"
popd
for %%I in ("%ROOT%\..\..") do set "PROJECT=%%~fI"

if not "%HERO1_DATA_ROOT%"=="" (
  set "DATA_ROOT=%HERO1_DATA_ROOT%"
) else (
  set "DATA_ROOT=%PROJECT%\DATA"
)
set "LOG_DIR=%DATA_ROOT%\LOGS"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
set "FRONTEND_LOG=%LOG_DIR%\frontend.log"

title HERO1 Frontend :5173
cd /d "%ROOT%" || (echo ERROR: cannot cd to "%ROOT%" & pause & exit /b 1)
set "HERO1_DATA_ROOT=%DATA_ROOT%"

echo [HERO1] dashboard    : http://127.0.0.1:5173/
echo [HERO1] project root : %PROJECT%
echo [HERO1] DATA root    : %DATA_ROOT%
echo [HERO1] logging to   : %FRONTEND_LOG%
echo [HERO1] stop with    : Ctrl+C, or close this window
echo.

call npm run dev 2>&1 | powershell -NoProfile -ExecutionPolicy Bypass -Command "$input | Tee-Object -FilePath '%FRONTEND_LOG%' -Append"
set "RC=%ERRORLEVEL%"

echo.
echo [HERO1] frontend exited with code %RC%.
echo [HERO1] Log file: %FRONTEND_LOG%
echo This window stays open on purpose. Press any key to close it.
pause >nul
exit /b %RC%
