@echo off
REM ===========================================================================
REM  EVOLUTIONARY TRADING RESEARCH LAB - LAUNCHER DELEGATE (V3.2)
REM  Delegates directly to primary start.bat launcher.
REM ===========================================================================
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"

call "%ROOT_DIR%\start.bat" %*
exit /b %ERRORLEVEL%
