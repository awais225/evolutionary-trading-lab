@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ==========================================================================
rem  START.bat - HERO1 everyday launcher
rem  Double-click to start Hero1. Starts:
rem    - Backend  on http://127.0.0.1:8788  (persistent visible window)
rem    - Frontend on http://127.0.0.1:5173  (persistent visible window)
rem  Waits for REAL HTTP readiness, then opens the browser automatically.
rem  Default DATA root: D:\Hero1\DATA  (override: set HERO1_DATA_ROOT first)
rem  Logs: <DATA_ROOT>\LOGS\startup.log backend.log frontend.log
rem  Never kills unrelated processes. Never deletes DATA.
rem ==========================================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

if not "%HERO1_DATA_ROOT%"=="" (
  set "DATA_ROOT=%HERO1_DATA_ROOT%"
) else (
  set "DATA_ROOT=D:\Hero1\DATA"
)
set "LOG_DIR=%DATA_ROOT%\LOGS"
set "STARTUP_LOG=%LOG_DIR%\startup.log"
set "BACKEND_LOG=%LOG_DIR%\backend.log"
set "FRONTEND_LOG=%LOG_DIR%\frontend.log"

if /i not "%OS%"=="Windows_NT" (
  echo ERROR: START.bat must run on Windows.
  pause
  exit /b 1
)
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

call :log "============================================================"
call :log "START.bat launched  %DATE% %TIME%"
call :log "Project root: %ROOT%"
call :log "DATA root   : %DATA_ROOT%"

echo.
echo ======================================================
echo   HERO1 STARTUP
echo   Project root : %ROOT%
echo   Dashboard    : http://127.0.0.1:5173/
echo   DATA root    : %DATA_ROOT%
echo ======================================================
echo.

rem ------------------------------------------------ Step 1: required files
call :step "Checking required files and environment"
if not exist "%ROOT%\services\api\app\main.py" (
  set "FAIL_STEP=Required file: services\api\app\main.py"
  set "FAIL_DETAIL=This does not look like the Hero1 repository root. The two BAT files must live in the Hero1 source root."
  goto :fail
)
if not exist "%ROOT%\apps\web\package.json" (
  set "FAIL_STEP=Required file: apps\web\package.json"
  set "FAIL_DETAIL=Frontend source is missing."
  goto :fail
)
if not exist "%ROOT%\.venv\Scripts\python.exe" (
  echo.
  echo .venv not found. Please run PREREQUISITES.bat first (double-click it).
  call :log "ABORT: .venv missing - user told to run PREREQUISITES.bat"
  pause
  exit /b 1
)
if not exist "%ROOT%\apps\web\node_modules" (
  echo.
  echo Frontend dependencies are not installed. Please run PREREQUISITES.bat first (double-click it).
  call :log "ABORT: apps\web\node_modules missing - user told to run PREREQUISITES.bat"
  pause
  exit /b 1
)
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"

rem ------------------------------------------------ Step 2: database ready?
call :step "Verifying the Hero1 database (read-only check)"
set "HERO1_DATA_ROOT=%DATA_ROOT%"
cd /d "%ROOT%" || (set "FAIL_STEP=Project root" & set "FAIL_DETAIL=Cannot enter %ROOT%" & goto :fail)
"%VENV_PY%" "%ROOT%\scripts\init_db.py" --check >> "%STARTUP_LOG%" 2>&1
if errorlevel 1 (
  call :log "database check FAILED - attempting idempotent initialization (init_db.py --create; never deletes data)"
  echo Database not initialized; initializing now (idempotent, your DATA is never reset)...
  "%VENV_PY%" "%ROOT%\scripts\init_db.py" --create >> "%STARTUP_LOG%" 2>&1 || (set "FAIL_STEP=Database initialization" & set "FAIL_DETAIL=init_db.py --create failed. See %STARTUP_LOG%." & goto :fail)
  "%VENV_PY%" "%ROOT%\scripts\init_db.py" --check >> "%STARTUP_LOG%" 2>&1 || (set "FAIL_STEP=Database integrity check" & set "FAIL_DETAIL=Database integrity check still failing after initialization. See %STARTUP_LOG%." & goto :fail)
)
call :log "database check OK"

rem ------------------------------------------------ Step 3: backend up?
call :step "Backend on port 8788"
set "BACKEND_ALREADY=0"
call :http_code 8788 /health
if "!HTTP_CODE!"=="200" (
  set "BACKEND_ALREADY=1"
  call :log "backend already healthy on :8788 - NOT launching a duplicate"
  echo HERO1 backend is already running - reusing it.
) else (
  call :port_in_use 8788
  if "!PORT_IN_USE!"=="1" (
    set "FAIL_STEP=Port 8788 occupied"
    set "FAIL_DETAIL=Port 8788 is in use by something that is NOT a healthy Hero1 backend. Free port 8788 yourself - START.bat will not kill unrelated processes."
    goto :fail
  )
  call :log "launching backend window (HERO1 Backend :8788)"
  set "BACKEND_CMD=cd /d "%ROOT%" && set "HERO1_DATA_ROOT=%DATA_ROOT%" && "%VENV_PY%" -m uvicorn services.api.app.main:app --host 127.0.0.1 --port 8788 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%BACKEND_LOG%' -Append""
  start "HERO1 Backend :8788" cmd /k %BACKEND_CMD%
)

rem ------------------------------------------------ Step 4: backend readiness
if "%BACKEND_ALREADY%"=="1" goto :backend_ready
call :step "Waiting for the backend health endpoint to answer"
echo Waiting for the backend to become healthy at http://127.0.0.1:8788/health ...
call :wait_http 8788 /health 90
if errorlevel 1 (
  echo.
  echo ERROR: the backend did not become healthy within 90 seconds.
  echo Recent backend log output:
  echo ------------------------------------------------
  powershell -NoProfile -Command "if (Test-Path '%BACKEND_LOG%') { Get-Content -Tail 40 '%BACKEND_LOG%' } else { 'backend.log not created yet - check the HERO1 Backend window for errors.' }"
  echo ------------------------------------------------
  call :log "BACKEND FAILED READINESS - see %BACKEND_LOG%"
  set "FAIL_STEP=Backend readiness"
  set "FAIL_DETAIL=Backend did not answer /health within 90s. See the HERO1 Backend window and %BACKEND_LOG%."
  goto :fail
)
:backend_ready
echo Backend is healthy.
call :log "backend readiness confirmed"

rem ------------------------------------------------ Step 5: frontend up?
call :step "Frontend on port 5173"
set "FRONTEND_ALREADY=0"
call :http_code 5173 /
if "!HTTP_CODE!"=="200" (
  set "FRONTEND_ALREADY=1"
  call :log "frontend already serving on :5173 - NOT launching a duplicate"
  echo HERO1 frontend is already running - reusing it.
) else (
  call :port_in_use 5173
  if "!PORT_IN_USE!"=="1" (
    set "FAIL_STEP=Port 5173 occupied"
    set "FAIL_DETAIL=Port 5173 is in use by something that is NOT the Hero1 frontend. Free port 5173 yourself - START.bat will not kill unrelated processes. The backend IS running; fix the port and run START.bat again."
    goto :fail
  )
  call :log "launching frontend window (HERO1 Frontend :5173)"
  set "FRONTEND_CMD=cd /d "%ROOT%\apps\web" && set "HERO1_DATA_ROOT=%DATA_ROOT%" && call npm run dev 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%FRONTEND_LOG%' -Append""
  start "HERO1 Frontend :5173" cmd /k %FRONTEND_CMD%
)

rem ------------------------------------------------ Step 6: frontend readiness
if "%FRONTEND_ALREADY%"=="1" goto :frontend_ready
call :step "Waiting for the frontend to answer"
echo Waiting for the frontend at http://127.0.0.1:5173/ ...
call :wait_http 5173 / 60
if errorlevel 1 (
  echo.
  echo ERROR: the frontend did not become ready within 60 seconds.
  echo The backend IS running and healthy in its own window - this is a partial startup.
  echo Recent frontend log output:
  echo ------------------------------------------------
  powershell -NoProfile -Command "if (Test-Path '%FRONTEND_LOG%') { Get-Content -Tail 40 '%FRONTEND_LOG%' } else { 'frontend.log not created yet - check the HERO1 Frontend window for errors.' }"
  echo ------------------------------------------------
  call :log "FRONTEND FAILED READINESS - backend left running by design; see %FRONTEND_LOG%"
  set "FAIL_STEP=Frontend readiness"
  set "FAIL_DETAIL=Frontend did not answer within 60s. Backend is still running - see the HERO1 Frontend window and %FRONTEND_LOG%."
  goto :fail
)
:frontend_ready
echo Frontend is ready.
call :log "frontend readiness confirmed"

rem ------------------------------------------------ Step 7: open the browser
call :step "Opening the dashboard in the default browser"
call :log "opening http://127.0.0.1:5173/ in the default browser"
start "" "http://127.0.0.1:5173/"
call :log "startup complete (backend_already=%BACKEND_ALREADY% frontend_already=%FRONTEND_ALREADY%)"

echo.
echo ======================================================
echo   HERO1 IS RUNNING
echo   Dashboard : http://127.0.0.1:5173/
echo   Backend   : http://127.0.0.1:8788  (health: /health)
echo   Logs      : %STARTUP_LOG%
echo               %BACKEND_LOG%
echo               %FRONTEND_LOG%
echo   Services run in their own windows - close those windows
echo   (or press Ctrl+C in them) to stop Hero1.
echo ======================================================
echo.
pause
exit /b 0

rem ============================ helpers ======================================
:step
echo [STEP] %~1
call :log "[STEP] %~1"
exit /b 0

:log
>> "%STARTUP_LOG%" echo [%DATE% %TIME%] %~1
exit /b 0

rem HTTP probe without curl.exe (works on every Windows with PowerShell).
rem Sets HTTP_CODE (000 on any error).
:http_code
set "HTTP_CODE=000"
set "HERO1_PROBE_URL=http://127.0.0.1:%~1%~2"
for /f "delims=" %%C in ('powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 4 $env:HERO1_PROBE_URL).StatusCode } catch { 0 }" 2^>nul') do set "HTTP_CODE=%%C"
exit /b 0

rem Sets PORT_IN_USE=1 when something is LISTENING on the port (regardless of what).
:port_in_use
set "PORT_IN_USE=0"
netstat -ano | findstr /R /I /C:":%~1 .*LISTENING" >nul 2>&1
if %errorlevel%==0 set "PORT_IN_USE=1"
exit /b 0

rem Poll http://127.0.0.1:%1%2 until HTTP 200 or %3 attempts (2s apart). Exit 0=ready, 1=timeout.
:wait_http
set "WAIT_OK=0"
for /L %%I in (1,1,%~3) do (
  call :http_code %~1 %~2
  if "!HTTP_CODE!"=="200" (
    set "WAIT_OK=1"
    goto :wait_http_done
  )
  timeout /t 2 /nobreak >nul
)
:wait_http_done
if "%WAIT_OK%"=="1" (exit /b 0) else (exit /b 1)

rem ============================ failure path ================================
:fail
echo.
echo ======================================================
echo   HERO1 STARTUP FAILED
echo   Step  : %FAIL_STEP%
echo   Detail: %FAIL_DETAIL%
echo   Log   : %STARTUP_LOG%
echo   Backend log : %BACKEND_LOG%
echo   Frontend log: %FRONTEND_LOG%
echo   Nothing was deleted. Your DATA at %DATA_ROOT% is untouched.
echo ======================================================
call :log "FAILED at step: %FAIL_STEP% - %FAIL_DETAIL%"
pause
exit /b 1
