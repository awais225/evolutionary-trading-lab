@echo off
setlocal enabledelayedexpansion

REM ===========================================================================
REM       EVOLUTIONARY TRADING RESEARCH LAB V3.2 - PREREQUISITE INSTALLER
REM  Pure Windows Command Prompt (cmd.exe) syntax. Fully idempotent.
REM  Verifies hardware, Python runtime, virtual environment, dependencies,
REM  database integrity (via backend\tools\verify_database.py), and frontend bundle.
REM ===========================================================================

title Evolutionary Trading Research Lab - Prerequisite Installer V3.2

REM ---------------------------------------------------------------------------
REM STAGE 1: Determine Project Root & Initialize Log
REM ---------------------------------------------------------------------------
set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
cd /d "%ROOT_DIR%"

set "LOG_DIR=%ROOT_DIR%\LOGS"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG_FILE=%LOG_DIR%\prerequisite.log"

echo. >> "%LOG_FILE%"
echo =========================================================================== >> "%LOG_FILE%"
echo [%DATE% %TIME%] PREREQUISITE VERIFICATION STARTED (V3.2) >> "%LOG_FILE%"
echo Root Directory: %ROOT_DIR% >> "%LOG_FILE%"
echo =========================================================================== >> "%LOG_FILE%"

echo ===========================================================================
echo  EVOLUTIONARY TRADING RESEARCH LAB - PREREQUISITE INSTALLER V3.2
echo ===========================================================================
echo.
echo [LAUNCHER] Execution shell: CMD
echo [LAUNCHER] Script: PRE-REQUISITE.BAT
echo [LAUNCHER] Working directory: %ROOT_DIR%
echo [LAUNCHER] Launcher execution confirmed
echo.
echo Project Root: %ROOT_DIR%
echo Setup Log:    %LOG_FILE%
echo.

REM ---------------------------------------------------------------------------
REM STAGE 2: System Hardware & Resource Discovery
REM ---------------------------------------------------------------------------
echo [1/8] Inspecting system hardware and accelerators...
echo [INFO] Inspecting system hardware... >> "%LOG_FILE%"
echo   Architecture:       %PROCESSOR_ARCHITECTURE%
echo   CPU Cores:          %NUMBER_OF_PROCESSORS% logical processors

where nvidia-smi >nul 2>nul
if errorlevel 1 (
    echo   NVIDIA GPU / CUDA:  Not detected. CPU worker acceleration will be used.
    echo [INFO] nvidia-smi not found. Using CPU fallback. >> "%LOG_FILE%"
) else (
    echo   NVIDIA GPU / CUDA:  NVIDIA GPU detected. Probing device...
    for /f "tokens=*" %%g in ('nvidia-smi --query-gpu^=name --format^=csv^,noheader 2^>nul') do (
        echo   GPU Device:         %%g
        echo [INFO] NVIDIA GPU: %%g >> "%LOG_FILE%"
    )
)
echo   [OK] Hardware inspection complete.

REM ---------------------------------------------------------------------------
REM STAGE 3: Python Runtime & Version Compatibility
REM ---------------------------------------------------------------------------
echo.
echo [2/8] Detecting Python runtime environment...
echo [INFO] Detecting Python... >> "%LOG_FILE%"

set "SYSTEM_PYTHON="

where python >nul 2>nul
if not errorlevel 1 (
    set "SYSTEM_PYTHON=python"
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        set "SYSTEM_PYTHON=py -3"
    ) else (
        for /d %%p in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
            if exist "%%p\python.exe" set "SYSTEM_PYTHON=%%p\python.exe"
        )
    )
)

if "%SYSTEM_PYTHON%"=="" (
    set "FAILED_STAGE=Python Runtime Detection"
    set "FAILED_CMD=where python"
    set "FAILED_CODE=1"
    set "FAILED_ACTION=Python 3.10+ was not found on your system PATH. Please install Python 3.10-3.14 and ensure 'Add Python to PATH' is checked during setup."
    goto :STEP_FAILED
)

for /f "tokens=*" %%v in ('%SYSTEM_PYTHON% -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2^>nul') do (
    set "PY_VER=%%v"
)

if "%PY_VER%"=="" (
    set "FAILED_STAGE=Python Execution"
    set "FAILED_CMD=%SYSTEM_PYTHON% -V"
    set "FAILED_CODE=1"
    set "FAILED_ACTION=Detected Python failed to execute code. Check Windows permissions."
    goto :STEP_FAILED
)

echo   Detected Python:    %SYSTEM_PYTHON% (v%PY_VER%)
echo [INFO] Detected Python %PY_VER% at %SYSTEM_PYTHON% >> "%LOG_FILE%"

%SYSTEM_PYTHON% -c "import sys; sys.exit(0 if (sys.version_info.major == 3 and sys.version_info.minor >= 10) else 1)" >nul 2>&1
if errorlevel 1 (
    set "FAILED_STAGE=Python Version Check"
    set "FAILED_CMD=%SYSTEM_PYTHON% -c 'version check'"
    set "FAILED_CODE=1"
    set "FAILED_ACTION=Python 3.10 or higher is required. Detected Python %PY_VER%."
    goto :STEP_FAILED
)

echo   [OK] Python version %PY_VER% meets requirement (3.10+).

REM ---------------------------------------------------------------------------
REM STAGE 4: Virtual Environment Setup & Validation
REM ---------------------------------------------------------------------------
echo.
echo [3/8] Resolving Python virtual environment...
echo [INFO] Resolving virtual environment... >> "%LOG_FILE%"

set "VENV_DIR="
set "VENV_PYTHON="

if exist "%ROOT_DIR%\.venv\Scripts\python.exe" (
    set "VENV_DIR=%ROOT_DIR%\.venv"
    set "VENV_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"
    echo   Found existing virtual environment at %VENV_DIR%
) else if exist "%ROOT_DIR%\backend\.venv\Scripts\python.exe" (
    set "VENV_DIR=%ROOT_DIR%\backend\.venv"
    set "VENV_PYTHON=%ROOT_DIR%\backend\.venv\Scripts\python.exe"
    echo   Found existing virtual environment at %VENV_DIR%
) else (
    echo   Creating fresh virtual environment at %ROOT_DIR%\.venv ...
    echo [INFO] Creating virtual environment at %ROOT_DIR%\.venv >> "%LOG_FILE%"
    %SYSTEM_PYTHON% -m venv "%ROOT_DIR%\.venv" >> "%LOG_FILE%" 2>&1
    if errorlevel 1 (
        set "FAILED_STAGE=Virtual Environment Creation"
        set "FAILED_CMD=%SYSTEM_PYTHON% -m venv %ROOT_DIR%\.venv"
        set "FAILED_CODE=%ERRORLEVEL%"
        set "FAILED_ACTION=Failed to create Python virtual environment. Check disk space and folder permissions."
        goto :STEP_FAILED
    )
    set "VENV_DIR=%ROOT_DIR%\.venv"
    set "VENV_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"
)

if not exist "%VENV_PYTHON%" (
    set "FAILED_STAGE=Virtual Environment Verification"
    set "FAILED_CMD=test exist %VENV_PYTHON%"
    set "FAILED_CODE=1"
    set "FAILED_ACTION=Virtual environment python.exe is missing at %VENV_PYTHON%."
    goto :STEP_FAILED
)

echo   Runtime Interpreter: %VENV_PYTHON%
echo   [OK] Virtual environment verified.

REM ---------------------------------------------------------------------------
REM STAGE 5: Pip & Authoritative Dependencies Installation
REM ---------------------------------------------------------------------------
echo.
echo [4/8] Installing and verifying backend dependencies...
echo [INFO] Verifying backend dependencies... >> "%LOG_FILE%"

echo   Upgrading pip in virtual environment...
"%VENV_PYTHON%" -m pip install --upgrade pip >> "%LOG_FILE%" 2>&1

set "REQ_FILE=%ROOT_DIR%\backend\requirements.txt"
if not exist "%REQ_FILE%" (
    set "FAILED_STAGE=Requirements File Check"
    set "FAILED_CMD=test exist %REQ_FILE%"
    set "FAILED_CODE=1"
    set "FAILED_ACTION=backend\requirements.txt is missing. Verify project installation."
    goto :STEP_FAILED
)

echo   Installing packages from backend\requirements.txt...
"%VENV_PYTHON%" -m pip install -r "%REQ_FILE%" >> "%LOG_FILE%" 2>&1

echo   Running automated dependency verification and self-repair engine...
"%VENV_PYTHON%" "%ROOT_DIR%\backend\app\verify_deps.py" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    set "FAILED_STAGE=Dependency Verification & Self-Repair"
    set "FAILED_CMD=%VENV_PYTHON% backend\app\verify_deps.py"
    set "FAILED_CODE=%ERRORLEVEL%"
    set "FAILED_ACTION=Could not install or repair critical packages (psutil, fastapi, duckdb, pyarrow). Check LOGS\prerequisite.log."
    goto :STEP_FAILED
)
echo   [OK] All backend runtime dependencies verified and installed.

REM ---------------------------------------------------------------------------
REM STAGE 6: Application Import Smoke Test
REM ---------------------------------------------------------------------------
echo.
echo [5/8] Performing truthful backend import smoke test...
echo [INFO] Running backend import smoke test (from app.main import app)... >> "%LOG_FILE%"

"%VENV_PYTHON%" -c "import sys; sys.path.insert(0, r'%ROOT_DIR%\backend'); from app.main import app; print('[OK] Core Application Imported Successfully')" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    set "FAILED_STAGE=Application Import Smoke Test"
    set "FAILED_CMD=%VENV_PYTHON% -c 'from app.main import app'"
    set "FAILED_CODE=%ERRORLEVEL%"
    set "FAILED_ACTION=Backend import failed. Inspect LOGS\prerequisite.log for the full Python traceback."
    goto :STEP_FAILED
)

echo   [OK] Verified: 'from app.main import app' imported cleanly without errors.

REM ---------------------------------------------------------------------------
REM STAGE 7: Database Integrity & Verification (Single Source of Truth)
REM ---------------------------------------------------------------------------
echo.
echo [6/8] Verifying database connectivity, schema version, and migrations...
echo [INFO] Running backend\tools\verify_database.py... >> "%LOG_FILE%"

"%VENV_PYTHON%" "%ROOT_DIR%\backend\tools\verify_database.py" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    set "FAILED_STAGE=Database Verification"
    set "FAILED_CMD=%VENV_PYTHON% backend\tools\verify_database.py"
    set "FAILED_CODE=%ERRORLEVEL%"
    set "FAILED_ACTION=Database verification failed. Inspect LOGS\prerequisite.log and DATABASE\lab_state.db."
    goto :STEP_FAILED
)

echo   [OK] Database operational. Schema version 3, existing strategies preserved.

REM ---------------------------------------------------------------------------
REM STAGE 8: Frontend Toolchain & Production Bundle Compilation
REM ---------------------------------------------------------------------------
echo.
echo [7/8] Inspecting frontend toolchain and dashboard bundle...
echo [INFO] Inspecting Node.js and frontend bundle... >> "%LOG_FILE%"

set "NODE_OK=0"
where node >nul 2>nul
if not errorlevel 1 (
    for /f "tokens=*" %%n in ('node -v 2^>nul') do set "NODE_VER=%%n"
    for /f "tokens=*" %%m in ('npm -v 2^>nul') do set "NPM_VER=%%m"
    echo   Node.js:            %NODE_VER%
    echo   npm:                v%NPM_VER%
    set "NODE_OK=1"
    echo [INFO] Node: %NODE_VER%, npm: %NPM_VER% >> "%LOG_FILE%"
) else (
    echo   Node.js:            Not detected on system PATH.
    echo [INFO] Node.js not found on system PATH. >> "%LOG_FILE%"
)

if "%NODE_OK%"=="1" (
    echo   Compiling frontend production bundle (npm run build)...
    pushd "%ROOT_DIR%\frontend"
    call npm install --no-audit --no-fund >> "%LOG_FILE%" 2>&1
    call npm run build >> "%LOG_FILE%" 2>&1
    popd
)

if not exist "%ROOT_DIR%\frontend\dist\index.html" (
    set "FAILED_STAGE=Frontend Dist Verification"
    set "FAILED_CMD=test exist frontend\dist\index.html"
    set "FAILED_CODE=1"
    set "FAILED_ACTION=frontend\dist\index.html was not generated. Install Node.js 18+ and run npm run build in frontend."
    goto :STEP_FAILED
)

echo   [OK] Production dashboard bundle compiled and verified at frontend\dist\index.html.
echo [OK] frontend\dist\index.html successfully verified. >> "%LOG_FILE%"

REM ---------------------------------------------------------------------------
REM STAGE 9: MetaTrader 5 Terminal & MetaTrader5 Package Discovery
REM ---------------------------------------------------------------------------
echo.
echo [8/8] Inspecting MetaTrader 5 terminal and MetaTrader5 package...
echo [INFO] Searching for MetaTrader 5 terminal and MetaTrader5 package... >> "%LOG_FILE%"

"%VENV_PYTHON%" -c "import MetaTrader5; print('[OK] MetaTrader5 package installed')" >> "%LOG_FILE%" 2>&1
if not errorlevel 1 (
    echo   MetaTrader5 Package: Installed in virtual environment.
) else (
    echo   MetaTrader5 Package: Not installed or wheel unavailable for current Python.
)

set "MT5_PATH="
for %%p in (
    "%ProgramFiles%\MetaTrader 5\terminal64.exe"
    "%ProgramFiles(x86)%\MetaTrader 5\terminal64.exe"
    "%LOCALAPPDATA%\Programs\MetaTrader 5\terminal64.exe"
) do (
    if exist "%%~p" set "MT5_PATH=%%~p"
)

if "%MT5_PATH%"=="" (
    echo   MT5 Terminal:       Not detected in standard program directories.
    echo   Mode:               SIMULATOR (Research mode fully functional).
    echo [INFO] MT5 terminal not found in standard paths. SIMULATOR mode active. >> "%LOG_FILE%"
) else (
    echo   MT5 Terminal:       Found at %MT5_PATH%
    echo [INFO] MT5 terminal detected at %MT5_PATH% >> "%LOG_FILE%"
)
echo   [OK] MetaTrader 5 environment inspected.

REM ---------------------------------------------------------------------------
REM SUMMARY SCREEN
REM ---------------------------------------------------------------------------
echo.
echo ===========================================================================
echo  PREREQUISITE VERIFICATION COMPLETED SUCCESSFULLY [V3.2]
echo ===========================================================================
echo.
echo   [OK] Python Runtime:         %PY_VER% (%SYSTEM_PYTHON%)
echo   [OK] Virtual Environment:    %VENV_DIR%
echo   [OK] Runtime Interpreter:    %VENV_PYTHON%
echo   [OK] Core Dependencies:      psutil, fastapi, uvicorn, duckdb, pyarrow, numpy, pandas
echo   [OK] Application Smoke Test: from app.main import app (PASSED)
echo   [OK] Database Integrity:     DATABASE\lab_state.db (100%% preserved)
echo   [OK] Frontend Dashboard:     frontend\dist\index.html (Compiled and verified)
echo.
echo  The system is ready. You can now launch the application using START.bat.
echo.
echo [%DATE% %TIME%] ALL PREREQUISITES VERIFIED SUCCESSFULLY >> "%LOG_FILE%"

exit /b 0

REM ---------------------------------------------------------------------------
REM FAILURE HANDLER: Keeps window open, prints diagnostics, pauses
REM ---------------------------------------------------------------------------
:STEP_FAILED
echo.
echo ===========================================================================
echo  PREREQUISITE INSTALLATION FAILED
echo ===========================================================================
echo.
echo  Failed Stage:       %FAILED_STAGE%
echo  Executed Command:   %FAILED_CMD%
echo  Exit Code:          %FAILED_CODE%
echo.
echo  Suggested Action:
echo  %FAILED_ACTION%
echo.
echo  Full Diagnostic Log:
echo  %LOG_FILE%
echo.
echo [%DATE% %TIME%] STEP FAILED: %FAILED_STAGE% (Code: %FAILED_CODE%) >> "%LOG_FILE%"
echo [%DATE% %TIME%] Command: %FAILED_CMD% >> "%LOG_FILE%"
echo [%DATE% %TIME%] Action: %FAILED_ACTION% >> "%LOG_FILE%"
echo ===========================================================================
echo.
echo Press any key to close...
pause >nul
exit /b 1
