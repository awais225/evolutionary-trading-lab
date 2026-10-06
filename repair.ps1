# PowerShell Launcher Wrapper for REPAIR.BAT
# Automatically routes batch execution through native cmd.exe

Write-Host "[LAUNCHER] Invoked from PowerShell environment." -ForegroundColor Yellow
Write-Host "[LAUNCHER] Delegating to cmd.exe for pure Windows batch compatibility..." -ForegroundColor Yellow
Write-Host "[LAUNCHER] Execution shell: CMD" -ForegroundColor Cyan
Write-Host "[LAUNCHER] Script: REPAIR.BAT" -ForegroundColor Cyan
Write-Host "[LAUNCHER] Working directory: $PSScriptRoot" -ForegroundColor Cyan
Write-Host "[LAUNCHER] Launcher execution confirmed" -ForegroundColor Green
Write-Host ""

& cmd.exe /c "`"$PSScriptRoot\repair.bat`"" $args
exit $LASTEXITCODE
