@echo off
REM ===========================================================================
REM  EVOLUTIONARY TRADING RESEARCH LAB - WINDOWS EXE PACKAGER (V3.2)
REM  Compiles standalone EvolutionaryTradingLab.exe via PyInstaller
REM ===========================================================================
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"

echo ===========================================================================
echo   EVOLUTIONARY TRADING RESEARCH LAB - EXE BUILD PROCESS [V3.2]
echo ===========================================================================
echo  Root Directory: %ROOT_DIR%
echo.

REM 1. Verify Virtual Environment
set "VENV_PYTHON="
if exist "%ROOT_DIR%\.venv\Scripts\python.exe" set "VENV_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"
if exist "%ROOT_DIR%\backend\.venv\Scripts\python.exe" set "VENV_PYTHON=%ROOT_DIR%\backend\.venv\Scripts\python.exe"

if "%VENV_PYTHON%"=="" (
    echo [ACTION] Virtual environment missing. Running pre-requisite.bat...
    call "%ROOT_DIR%\pre-requisite.bat"
    if errorlevel 1 (
        echo [ERROR] Prerequisites failed.
        pause
        exit /b 1
    )
    if exist "%ROOT_DIR%\.venv\Scripts\python.exe" set "VENV_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"
)

REM 2. Verify Frontend Dist - it must be built from the CURRENT frontend\src.
REM    The executable embeds frontend\dist verbatim (EvolutionaryTradingLab.spec),
REM    so packaging a stale bundle would ship the old interface again. Presence
REM    of index.html is not enough: the bundle identity is verified against the
REM    source fingerprint and rebuilt when it does not match.
if "%VENV_PYTHON%"=="" set "VENV_PYTHON=python"
set "GUARD=%ROOT_DIR%\backend\tools\frontend_build_guard.py"

"%VENV_PYTHON%" "%GUARD%" --root "%ROOT_DIR%" --check
if not errorlevel 1 (
    echo [OK] frontend\dist is already built from the current frontend\src.
    goto :FE_READY
)

echo [ACTION] frontend\dist does not match frontend\src. Rebuilding it before packaging.
where npm >nul 2>nul
if errorlevel 1 goto :FE_NO_NPM

pushd "%ROOT_DIR%\frontend"
if exist "node_modules" goto :FE_BUILD

echo [..] Installing frontend dependencies (npm install)...
call npm install --no-audit --no-fund
if errorlevel 1 goto :FE_INSTALL_FAILED

:FE_BUILD
echo [..] Compiling frontend\src into frontend\dist (npm run build)...
call npm run build
if errorlevel 1 goto :FE_BUILD_FAILED
popd

"%VENV_PYTHON%" "%GUARD%" --root "%ROOT_DIR%" --stamp
if errorlevel 1 goto :FE_STAMP_FAILED

"%VENV_PYTHON%" "%GUARD%" --root "%ROOT_DIR%" --check
if errorlevel 1 goto :FE_UNVERIFIED

:FE_READY
echo [OK] frontend\dist is built from the current frontend\src - this is the bundle the executable will embed.
"%VENV_PYTHON%" "%GUARD%" --root "%ROOT_DIR%" --summary
goto :FE_VERIFIED

:FE_NO_NPM
echo [ERROR] npm was not found on PATH, so frontend\dist cannot be rebuilt.
echo         Install Node.js 18+ and run this script again.
pause
exit /b 1

:FE_INSTALL_FAILED
popd
echo [ERROR] npm install failed in frontend\. See LOGS\build_exe.log.
pause
exit /b 1

:FE_BUILD_FAILED
popd
echo [ERROR] npm run build failed in frontend\. See LOGS\build_exe.log.
pause
exit /b 1

:FE_STAMP_FAILED
echo [ERROR] The freshly built bundle could not be stamped (frontend\dist\build-info.json).
echo         Refusing to package a bundle whose origin cannot be proven.
pause
exit /b 1

:FE_UNVERIFIED
echo [ERROR] frontend\dist still does not match frontend\src after a successful build.
echo         Refusing to package a stale frontend. Run REPAIR.bat, then try again.
pause
exit /b 1

:FE_VERIFIED

REM 3. Ensure PyInstaller is installed
echo [..] Checking PyInstaller...
"%VENV_PYTHON%" -m pip install pyinstaller >> "%ROOT_DIR%\LOGS\build_exe.log" 2>&1

REM 4. Build Standalone Executable
echo [..] Building EvolutionaryTradingLab.exe with PyInstaller...
cd /d "%ROOT_DIR%"
"%VENV_PYTHON%" -m PyInstaller --clean EvolutionaryTradingLab.spec

if not exist "%ROOT_DIR%\dist\EvolutionaryTradingLab.exe" (
    echo.
    echo [ERROR] PyInstaller build failed. Check LOGS\build_exe.log for details.
    pause
    exit /b 1
)

echo.
echo ===========================================================================
echo  [OK] EXE BUILD COMPLETED SUCCESSFULLY!
echo  Executable: %ROOT_DIR%\dist\EvolutionaryTradingLab.exe
echo ===========================================================================
echo.
pause
