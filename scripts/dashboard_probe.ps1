# EvolutionaryTradingV5 - Windows-side part of the runtime dashboard diagnostic.
#
# READ-ONLY. This script never stops, starts or modifies anything: it only reads
# the machine identity, the tool paths and the process that owns the dashboard
# port, so the operator can copy the whole console output back.
#
# Called by CHECK_RUNNING_DASHBOARD.bat. Safe to run on its own:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dashboard_probe.ps1 -Port 8787
param(
  [int]$Port = 8787,
  [string]$RepoRoot = ""
)

$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Write-Header([string]$text) {
  Write-Output ""
  Write-Output ("=" * 78)
  Write-Output $text
  Write-Output ("=" * 78)
}

if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }

# ---------------------------------------------------------------------------
# A. MACHINE / PATH
# ---------------------------------------------------------------------------
Write-Header "A. MACHINE / PATH"
Write-Output ("Machine:              " + $env:COMPUTERNAME)
Write-Output ("User:                 " + $env:USERNAME)
$os = try { (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop) } catch { $null }
if ($os) {
  Write-Output ("Windows version:      " + $os.Caption + " (" + $os.Version + ", build " + $os.BuildNumber + ")")
} else {
  Write-Output ("Windows version:      " + [System.Environment]::OSVersion.VersionString)
}
Write-Output ("Current directory:    " + (Get-Location).Path)
Write-Output ("BAT location:         " + (Join-Path $RepoRoot "CHECK_RUNNING_DASHBOARD.bat"))
Write-Output ("Resolved repo root:   " + $RepoRoot)
Write-Output ("Repository exists:    " + (Test-Path (Join-Path $RepoRoot "frontend")))
Write-Output ("PowerShell:           " + $PSVersionTable.PSVersion.ToString())

function Write-Tool([string]$label, [string]$exe) {
  if (-not $exe) { Write-Output ("{0,-22}{1}" -f $label, "(not found)"); return }
  Write-Output ("{0,-22}{1}" -f $label, $exe)
}

$pyCandidates = @(
  (Join-Path $RepoRoot ".venv\Scripts\python.exe"),
  (Join-Path $RepoRoot "venv\Scripts\python.exe")
)
$pythonExe = $null
foreach ($candidate in $pyCandidates) { if (Test-Path $candidate) { $pythonExe = $candidate; break } }
if (-not $pythonExe) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { $pythonExe = $cmd.Source }
}
$nodeExe = (Get-Command node -ErrorAction SilentlyContinue)
$npmExe  = (Get-Command npm  -ErrorAction SilentlyContinue)
$gitExe  = (Get-Command git  -ErrorAction SilentlyContinue)

Write-Tool "Python executable:" $pythonExe
if ($pythonExe) { Write-Output ("Python version:       " + (& $pythonExe --version 2>&1)) } else { Write-Output "Python version:       (not found)" }
if ($nodeExe) { Write-Tool "Node executable:" $nodeExe.Source; Write-Output ("Node version:         " + (& $nodeExe.Source --version 2>&1)) } else { Write-Tool "Node executable:" $null }
if ($npmExe)  { Write-Output ("npm version:          " + (& npm --version 2>&1)) }
if ($gitExe)  { Write-Tool "Git executable:" $gitExe.Source; Write-Output ("Git version:          " + (& $gitExe.Source --version 2>&1)) }
Write-Output ""
Write-Output "NOTE: no environment variables, tokens, credentials or secrets are printed."

# ---------------------------------------------------------------------------
# Sec.6. PORT OWNER (read-only: nothing is stopped)
# ---------------------------------------------------------------------------
Write-Header ("PORT {0} OWNER  (read-only - nothing is killed)" -f $Port)

$netstat = (& netstat -ano -p tcp 2>$null | Select-String (":" + $Port + "\s"))
if (-not $netstat) {
  Write-Output ("Nothing is listening on port {0}." -f $Port)
} else {
  foreach ($line in $netstat) { Write-Output ("  netstat: " + ($line.ToString().Trim())) }
  $pids = @()
  foreach ($line in $netstat) {
    $cols = ($line.ToString().Trim() -split "\s+")
    if ($cols.Length -ge 5 -and $cols[3] -eq "LISTENING") { $pids += $cols[4] }
  }
  $pids = $pids | Select-Object -Unique
  Write-Output ("  Listening PIDs: " + ($pids -join ", "))
  foreach ($procId in $pids) {
    $proc = $null
    try { $proc = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $procId) -ErrorAction Stop } catch { }
    if (-not $proc) {
      Write-Output ("  PID {0}: not resolvable (it may have exited, or access is denied)" -f $procId)
      continue
    }
    Write-Output ""
    Write-Output ("  PID:               " + $proc.ProcessId)
    Write-Output ("  Process name:      " + $proc.Name)
    Write-Output ("  Executable path:   " + $proc.ExecutablePath)
    Write-Output ("  Command line:      " + $proc.CommandLine)
    Write-Output ("  Parent PID:        " + $proc.ParentProcessId)
    Write-Output ("  Started:           " + $proc.CreationDate)
    try {
      $owner = (Get-CimInstance Win32_Process -Filter ("ProcessId = " + $proc.ParentProcessId) -ErrorAction Stop)
      if ($owner) { Write-Output ("  Parent:            " + $owner.Name + " -> " + $owner.CommandLine) }
    } catch { }
    $looksLikeBackend = $false
    if ($proc.CommandLine -and ($proc.CommandLine -match "app\.main:app")) { $looksLikeBackend = $true }
    Write-Output ("  Looks like app.main:app: " + $looksLikeBackend)
    if ($proc.CommandLine) {
      foreach ($marker in @("--host", "--port", "uvicorn")) {
        if ($proc.CommandLine -match [regex]::Escape($marker)) { Write-Output ("  Command line contains: " + $marker) }
      }
    }
    try {
      $mods = (Get-Process -Id $procId -ErrorAction Stop).Path
      if ($mods) { Write-Output ("  Loaded image:      " + $mods) }
    } catch { }
  }
  Write-Output ""
  Write-Output "  Working directory: Windows does not expose a process working directory;"
  Write-Output "                     the backend prints its own cwd in GET /system/build"
  Write-Output "                     (reported below in RUNNING BACKEND PROCESS)."
}

# ---------------------------------------------------------------------------
# Sec.13. LAUNCHER FILES ACTUALLY PRESENT
# ---------------------------------------------------------------------------
Write-Header "LAUNCHER FILES PRESENT AT THE REPOSITORY ROOT"
$launchers = @(
  "CHECK_RUNNING_DASHBOARD.bat",
  "START.bat", "start.bat", "RUN_BACKEND.bat", "Prerequisite.bat", "pre-requisite.bat",
  "repair.bat", "doctor.bat",
  "scripts\build_and_serve.bat", "scripts\build_exe.bat", "scripts\run_backend.bat", "scripts\start_lab.bat"
)
foreach ($rel in $launchers) {
  $full = Join-Path $RepoRoot $rel
  if (Test-Path $full) {
    $item = Get-Item $full
    $hash = ""
    try { $hash = (Get-FileHash -Algorithm SHA256 -Path $full).Hash.Substring(0, 16) } catch { }
    Write-Output ("  {0,-34} size {1,7}  modified {2:yyyy-MM-ddTHH:mm:ssZ}  sha256 {3}" -f $rel, $item.Length, $item.LastWriteTimeUtc, $hash)
  }
}
Write-Output ""
Write-Output "The launcher actually being used is the one the operator double-clicked;"
Write-Output "its identity (tracked in Git, hash, first/last lines) is listed by the"
Write-Output "Python diagnostic below in NORMAL LAUNCHER IDENTITY."
