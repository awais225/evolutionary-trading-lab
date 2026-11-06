@echo off
setlocal EnableExtensions
rem ==========================================================================
rem  PREREQUISITES.bat - HERO1 one-time setup and dependency installer
rem  Double-click from Windows Explorer, or run from CMD / PowerShell.
rem  Idempotent: reuses .venv and node_modules when they are valid.
rem  Never deletes, resets or wipes DATA. Research data stays outside Git.
rem  Default DATA root: D:\Hero1\DATA  (override: set HERO1_DATA_ROOT first)
rem  Log: <DATA_ROOT>\LOGS\prerequisite.log
rem ==========================================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

if not "%HERO1_DATA_ROOT%"=="" (
  set "DATA_ROOT=%HERO1_DATA_ROOT%"
) else (
  set "DATA_ROOT=D:\Hero1\DATA"
)
set "LOG_DIR=%DATA_ROOT%\LOGS"
set "LOG=%LOG_DIR%\prerequisite.log"

if /i not "%OS%"=="Windows_NT" (
  echo ERROR: PREREQUISITES.bat must run on Windows.
  pause
  exit /b 1
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if not exist "%LOG_DIR%" (
  echo ERROR: could not create log directory "%LOG_DIR%".
  echo Create it manually or choose a writable DATA root, then retry.
  pause
  exit /b 1
)

call :log "============================================================"
call :log "PREREQUISITES.bat started  %DATE% %TIME%"
call :log "Project root: %ROOT%"
call :log "DATA root   : %DATA_ROOT%"

echo.
echo ======================================================
echo   HERO1 PREREQUISITES - one-time setup
echo   Project root : %ROOT%
echo   DATA root    : %DATA_ROOT%
echo   Log file     : %LOG%
echo ======================================================
echo.

rem ---------------------------------------------------------------- Step 1
call :step "Detecting Python 3.11+"
set "PY_CMD="
where.exe py >nul 2>&1
if %errorlevel%==0 (
  py -3 --version >nul 2>&1
  if %errorlevel%==0 set "PY_CMD=py -3"
)
if not defined PY_CMD (
  where.exe python >nul 2>&1
  if %errorlevel%==0 (
    python --version >nul 2>&1
    if %errorlevel%==0 set "PY_CMD=python"
  )
)
if not defined PY_CMD (
  where.exe python3 >nul 2>&1
  if %errorlevel%==0 (
    python3 --version >nul 2>&1
    if %errorlevel%==0 set "PY_CMD=python3"
  )
)
if not defined PY_CMD (
  set "FAIL_STEP=Python detection"
  set "FAIL_DETAIL=Python 3.11+ not found. Install from https://www.python.org/downloads/ (check "Add to PATH"), then run PREREQUISITES.bat again."
  goto :fail
)
for /f "delims=" %%V in ('%PY_CMD% --version 2^>^&1') do set "PY_VERSION=%%V"
call :log "Found: %PY_VERSION% (via: %PY_CMD%)"
%PY_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  set "FAIL_STEP=Python version check"
  set "FAIL_DETAIL=Python 3.11+ is required but %PY_VERSION% was found. Install Python 3.11 or newer."
  goto :fail
)
echo Found: %PY_VERSION%

rem ---------------------------------------------------------------- Step 2
call :step "Detecting Node.js 20 LTS+ and npm"
where.exe node >nul 2>&1 || (set "FAIL_STEP=Node.js detection" & set "FAIL_DETAIL=Node.js not found. Install Node.js 20 LTS from https://nodejs.org/ then retry." & goto :fail)
where.exe npm  >nul 2>&1 || (set "FAIL_STEP=npm detection" & set "FAIL_DETAIL=npm not found. Reinstall Node.js 20 LTS from https://nodejs.org/ then retry." & goto :fail)
for /f "delims=" %%V in ('node --version 2^>^&1') do set "NODE_VERSION=%%V"
node -e "const v=parseInt(process.versions.node.split('.')[0],10); process.exit(v>=20?0:1)"
if errorlevel 1 (
  set "FAIL_STEP=Node.js version check"
  set "FAIL_DETAIL=Node.js 20 LTS or newer is required but %NODE_VERSION% was found."
  goto :fail
)
for /f "delims=" %%V in ('npm --version 2^>^&1') do set "NPM_VERSION=%%V"
call :log "Found: Node.js %NODE_VERSION%, npm %NPM_VERSION%"
echo Found: Node.js %NODE_VERSION%, npm %NPM_VERSION%

rem ---------------------------------------------------------------- Step 3
call :step "Creating or reusing the .venv virtual environment"
if exist "%ROOT%\.venv\Scripts\python.exe" (
  call :log ".venv already exists - reusing it (nothing deleted or reset)"
  echo Reusing existing .venv
) else (
  call :log "Creating .venv at %ROOT%\.venv"
  %PY_CMD% -m venv "%ROOT%\.venv" >> "%LOG%" 2>&1 || (set "FAIL_STEP=Virtual environment creation" & set "FAIL_DETAIL='python -m venv' failed. See %LOG%." & goto :fail)
)
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  set "FAIL_STEP=Virtual environment verification"
  set "FAIL_DETAIL=.venv was not created correctly (missing %VENV_PY%). Delete .venv and retry."
  goto :fail
)

rem ---------------------------------------------------------------- Step 4
call :step "Upgrading pip inside .venv"
"%VENV_PY%" -m pip install --upgrade pip >> "%LOG%" 2>&1 || (set "FAIL_STEP=pip upgrade" & set "FAIL_DETAIL=pip upgrade failed. See %LOG%." & goto :fail)

rem ---------------------------------------------------------------- Step 5
cd /d "%ROOT%" || (set "FAIL_STEP=Project root" & set "FAIL_DETAIL=Cannot enter project root %ROOT%" & goto :fail)
call :step "Installing Hero1 Python packages (editable, documented commands)"
"%VENV_PY%" -m pip install -e ".\packages\storage" -e ".\packages\eventbus" -e ".\packages\ai_orchestrator" -e ".\packages\strategy_spec" -e ".\packages\exec_semantics" -e ".\packages\indicators" -e ".\packages\research" -e ".\packages\mt5_adapter" >> "%LOG%" 2>&1 || (set "FAIL_STEP=Python package installation (packages/*)" & set "FAIL_DETAIL=pip install of packages failed. See %LOG%." & goto :fail)
call :step "Installing Hero1 API service (editable)"
"%VENV_PY%" -m pip install -e ".\services\api" >> "%LOG%" 2>&1 || (set "FAIL_STEP=Python package installation (services/api)" & set "FAIL_DETAIL=pip install -e .\services\api failed. See %LOG%." & goto :fail)
call :step "Installing development requirements"
"%VENV_PY%" -m pip install -r ".\requirements-dev.txt" >> "%LOG%" 2>&1 || (set "FAIL_STEP=Development requirements" & set "FAIL_DETAIL=pip install -r requirements-dev.txt failed. See %LOG%." & goto :fail)
call :step "Checking dependency consistency (pip check)"
"%VENV_PY%" -m pip check >> "%LOG%" 2>&1 || (set "FAIL_STEP=pip check" & set "FAIL_DETAIL=Inconsistent dependency set. See %LOG%." & goto :fail)

rem ---------------------------------------------------------------- Step 6
call :step "Verifying backend imports with the .venv interpreter"
"%VENV_PY%" -c "import hero1_storage, hero1_eventbus, hero1_ai_orchestrator, hero1_strategy_spec, hero1_exec_semantics, hero1_indicators, hero1_research, hero1_mt5_adapter; print('core packages import OK')" >> "%LOG%" 2>&1 || (set "FAIL_STEP=Backend import verification (core packages)" & set "FAIL_DETAIL=A required hero1_* package failed to import. See %LOG%." & goto :fail)
"%VENV_PY%" -c "import sys; sys.path.insert(0, r'%ROOT%\services\api'); import app.main; print('backend app.main import OK')" >> "%LOG%" 2>&1 || (set "FAIL_STEP=Backend import verification (app.main)" & set "FAIL_DETAIL=The FastAPI application failed to import. See %LOG%." & goto :fail)

rem ---------------------------------------------------------------- Step 7
call :step "Installing frontend dependencies (npm ci)"
cd /d "%ROOT%\apps\web" || (set "FAIL_STEP=Frontend directory" & set "FAIL_DETAIL=Cannot enter %ROOT%\apps\web" & goto :fail)
if not exist ".\package-lock.json" (set "FAIL_STEP=Frontend lockfile" & set "FAIL_DETAIL=apps\web\package-lock.json missing - cannot run npm ci." & goto :fail)
call npm ci >> "%LOG%" 2>&1 || (set "FAIL_STEP=npm ci" & set "FAIL_DETAIL=Frontend dependency install failed. See %LOG%." & goto :fail)
call :step "Building the frontend production bundle (npm run build)"
call npm run build >> "%LOG%" 2>&1 || (set "FAIL_STEP=Frontend production build" & set "FAIL_DETAIL=tsc/vite build failed. See %LOG%." & goto :fail)
cd /d "%ROOT%" || (set "FAIL_STEP=Project root" & set "FAIL_DETAIL=Cannot return to project root %ROOT%" & goto :fail)

rem ---------------------------------------------------------------- Step 8
call :step "Preparing DATA root and LOGS directory (never resets existing data)"
if not exist "%DATA_ROOT%" mkdir "%DATA_ROOT%"
if not exist "%DATA_ROOT%" (set "FAIL_STEP=DATA root creation" & set "FAIL_DETAIL=Cannot create %DATA_ROOT%. Create it manually or choose another HERO1_DATA_ROOT." & goto :fail)
if not exist "%DATA_ROOT%\LOGS" mkdir "%DATA_ROOT%\LOGS"
cd /d "%ROOT%"
call :step "Initializing the Hero1 database (idempotent --create; existing data is never deleted)"
"%VENV_PY%" "%ROOT%\scripts\init_db.py" --create >> "%LOG%" 2>&1 || (set "FAIL_STEP=Database initialization (init_db.py --create)" & set "FAIL_DETAIL=Database initialization failed. See %LOG% and %DATA_ROOT%." & goto :fail)
call :step "Verifying database integrity (init_db.py --check)"
"%VENV_PY%" "%ROOT%\scripts\init_db.py" --check >> "%LOG%" 2>&1 || (set "FAIL_STEP=Database integrity check" & set "FAIL_DETAIL=Database integrity check FAILED. See %LOG%." & goto :fail)

rem ---------------------------------------------------------------- Step 9
call :step "Checking optional components (MT5, Ollama) - report only, never required"
set "MT5_FOUND="
if exist "%ProgramFiles%\MetaTrader 5\terminal64.exe" set "MT5_FOUND=%ProgramFiles%\MetaTrader 5"
if not defined MT5_FOUND if exist "%ProgramFiles(x86)%\MetaTrader 5\terminal64.exe" set "MT5_FOUND=%ProgramFiles(x86)%\MetaTrader 5"
if defined MT5_FOUND (
  call :log "MT5 terminal found at: %MT5_FOUND% (Phase 5 groundwork only - no orders are ever placed)"
  echo MT5 terminal found at: %MT5_FOUND%
) else (
  call :log "MT5 terminal NOT found (optional for this phase - not required)"
  echo MT5 terminal NOT found (optional - not required)
)
where.exe ollama >nul 2>&1
if %errorlevel%==0 (
  call :log "Ollama CLI found (optional - not required for this phase)"
  echo Ollama found (optional - not required)
) else (
  call :log "Ollama NOT found on PATH (optional - not required for this phase)"
  echo Ollama NOT found (optional - not required)
)

rem ---------------------------------------------------------------- Step 10
call :step "Running doctor environment report (informational; safe, read-only)"
"%VENV_PY%" "%ROOT%\scripts\doctor.py" >> "%LOG%" 2>&1

call :log "Setup completed OK  %DATE% %TIME%"
echo.
echo ======================================================
echo   HERO1 SETUP SUCCEEDED
echo   - Backend environment : %ROOT%\.venv
echo   - Frontend bundle     : %ROOT%\apps\web\dist
echo   - DATA root           : %DATA_ROOT%
echo   - Setup log           : %LOG%
echo.
echo   NEXT STEP: double-click START.bat to launch Hero1.
echo ======================================================
echo.
pause
exit /b 0

rem ============================ helpers =======================================
:step
echo [STEP] %~1
call :log "[STEP] %~1"
exit /b 0

:log
>> "%LOG%" echo [%DATE% %TIME%] %~1
exit /b 0

rem ============================ failure path ==================================
:fail
echo.
echo ======================================================
echo   HERO1 SETUP FAILED
echo   Step  : %FAIL_STEP%
echo   Detail: %FAIL_DETAIL%
echo   Log   : %LOG%
echo.
echo   Fix the problem, then run PREREQUISITES.bat again.
echo   Nothing was deleted. Your DATA at %DATA_ROOT% is untouched.
echo ======================================================
call :log "FAILED at step: %FAIL_STEP% - %FAIL_DETAIL%"
pause
exit /b 1
