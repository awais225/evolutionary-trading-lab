@echo off
rem HERO1 backend service window - started by START.bat (or run directly).
rem Stays open on ANY exit so the operator always sees why the backend stopped.
setlocal EnableExtensions

pushd "%~dp0.." || (echo ERROR: cannot locate the Hero1 project root from "%~dp0" & pause & exit /b 1)
set "ROOT=%CD%"
popd

if not "%HERO1_DATA_ROOT%"=="" (
  set "DATA_ROOT=%HERO1_DATA_ROOT%"
) else (
  set "DATA_ROOT=%ROOT%\DATA"
)
set "LOG_DIR=%DATA_ROOT%\LOGS"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
set "BACKEND_LOG=%LOG_DIR%\backend.log"

title HERO1 Backend :8788
cd /d "%ROOT%" || (echo ERROR: cannot cd to "%ROOT%" & pause & exit /b 1)
set "HERO1_DATA_ROOT=%DATA_ROOT%"
set "PYTHONIOENCODING=utf-8"

echo [HERO1] backend      : http://127.0.0.1:8788  (health: /health)
echo [HERO1] project root : %ROOT%
echo [HERO1] DATA root    : %DATA_ROOT%
echo [HERO1] logging to   : %BACKEND_LOG%
echo [HERO1] stop with    : Ctrl+C, or close this window
echo.

"%ROOT%\.venv\Scripts\python.exe" -m uvicorn services.api.app.main:app --host 127.0.0.1 --port 8788 2>&1 | powershell -NoProfile -ExecutionPolicy Bypass -Command "$input | Tee-Object -FilePath '%BACKEND_LOG%' -Append"
set "RC=%ERRORLEVEL%"

echo.
echo [HERO1] backend exited with code %RC%.
echo [HERO1] Log file: %BACKEND_LOG%
echo This window stays open on purpose. Press any key to close it.
pause >nul
exit /b %RC%
