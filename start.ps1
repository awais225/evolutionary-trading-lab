# PowerShell Launcher Wrapper for START.bat
# Automatically routes batch execution through native cmd.exe

Write-Host "[LAUNCHER] Invoked from PowerShell environment." -ForegroundColor Yellow
Write-Host "[LAUNCHER] Delegating to cmd.exe for pure Windows batch compatibility..." -ForegroundColor Yellow
Write-Host "[LAUNCHER] Execution shell: CMD" -ForegroundColor Cyan
Write-Host "[LAUNCHER] Script: START.BAT" -ForegroundColor Cyan
Write-Host "[LAUNCHER] Working directory: $PSScriptRoot" -ForegroundColor Cyan
Write-Host "[LAUNCHER] Launcher execution confirmed" -ForegroundColor Green
Write-Host ""

& cmd.exe /c "`"$PSScriptRoot\START.bat`"" $args
exit $LASTEXITCODE
