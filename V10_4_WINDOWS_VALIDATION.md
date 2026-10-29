# V10.4 — safe Windows 11 / Python 3.14.6 validation

This procedure does **not** launch the application, touch `E:\evolutionary-trading-lab\DATA`, copy authoritative DATA, run a research campaign, connect MT5 or place orders. It uses a fresh source clone and fresh TEMP fixtures. Leave power mode, affinity, CPU target, preset, memory/risk/broker settings and Python installation unchanged. Keep the authoritative native DB, WAL/SHM, backups and failed-preflight evidence intact. Do not checkpoint/delete sidecars or call a failed native preflight green.

## 1. Identify the existing interpreter and published source

In PowerShell, use the **existing environment's** Python 3.14.6 executable, not an automatic reinstall/downgrade. The GitHub remote must already be accessible with your normal Git authentication. Never paste a PAT into these commands/logs.

```powershell
$ErrorActionPreference = 'Stop'
$Py = Read-Host 'Full path to existing Python 3.14.6 python.exe'
& $Py -c "import sys; print(sys.version); assert sys.version_info[:3] == (3,14,6)"
if ($LASTEXITCODE -ne 0) { throw 'Wrong interpreter; stop without changing Python' }
& $Py -c "import psutil,numpy,pandas,pyarrow,pytest,fastapi; print('Benchmark dependencies available')"
if ($LASTEXITCODE -ne 0) { throw 'Missing dependency: report it; do not replace your environment automatically' }

$Remote = 'https://github.com/awais225/evolutionary-trading-lab.git'
$After = Read-Host 'Full 40-character V10.4 commit SHA from the publication receipt'
if ($After -notmatch '^[0-9a-f]{40}$') { throw 'A verified full commit SHA is required' }
$Before = '8abf3042a34bd484cdbbf806af5dc00851a9a1be'
$Root = Join-Path ([IO.Path]::GetTempPath()) ('ETL-v104-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Root | Out-Null
$Repo = Join-Path $Root 'after-source'
$Base = Join-Path $Root 'before-source'
$Temp = Join-Path $Root 'temp'
New-Item -ItemType Directory -Path $Temp | Out-Null
$OldLfs = $env:GIT_LFS_SKIP_SMUDGE
$env:GIT_LFS_SKIP_SMUDGE = '1' # Source only: do NOT download/copy authoritative market/research DATA.
try {
    git clone --no-checkout --single-branch --branch v10.4-throughput $Remote $Repo
    if ($LASTEXITCODE -ne 0) { throw 'Source clone failed' }
    $RemoteSha = (git -C $Repo rev-parse origin/v10.4-throughput).Trim()
    if ($LASTEXITCODE -ne 0 -or $RemoteSha -ne $After) { throw 'Branch does not match publication receipt; investigate without forcing anything' }
    git -C $Repo checkout --detach $After
    if ($LASTEXITCODE -ne 0) { throw 'Source checkout failed' }
    git -C $Repo worktree add --detach $Base $Before
    if ($LASTEXITCODE -ne 0) { throw 'Baseline worktree failed' }
} finally { $env:GIT_LFS_SKIP_SMUDGE = $OldLfs }
$Title = (git -C $Repo log -1 --format=%s).Trim()
if ($Title -ne 'V10.4 optimize generation and DeepTesting throughput') { throw 'Unexpected commit title' }
$BeforeData = (git -C $Base rev-parse HEAD:DATA).Trim()
$AfterData = (git -C $Repo rev-parse HEAD:DATA).Trim()
if ($BeforeData -ne $AfterData) { throw 'Tracked DATA tree changed' }
```

Do not run `git reset --hard`, `git clean`, force-push, update the original worktree, or repair its metadata destructively. These fresh worktrees avoid unrelated local edits and invalid old metadata.

## 2. Run identical isolated before/after benchmarks

Keep other heavy jobs and the app's workloads quiescent for a comparison; do not change hardware/power/affinity/quotas to manufacture utilization. Each command creates its own **nonexistent external DATA root**. A rerun needs new root/output names (the runner deliberately refuses existing roots). It uses deterministic synthetic regression bars/genomes, the real historical engine, real SQLite persistence and full-trade comparisons. This is not broker history or market-performance evidence.

```powershell
$Bench = Join-Path $Repo 'scripts\v104_benchmark.py'
$Compare = Join-Path $Repo 'scripts\v104_compare.py'
$OldTemp = $env:TEMP; $OldTmp = $env:TMP
$env:TEMP = $Temp; $env:TMP = $Temp
try {
    foreach ($Profile in @('light','off')) {
        & $Py $Bench --source $Base --data-root (Join-Path $Root "$Profile-before-data") --output (Join-Path $Root "$Profile-before.json") --profile $Profile --rounds 3 --nodes 100 --bars 12000 --workers 1 2 4
        if ($LASTEXITCODE -ne 0) { throw "Baseline $Profile failed; retain diagnostics" }
        & $Py $Bench --source $Repo --data-root (Join-Path $Root "$Profile-after-data") --output (Join-Path $Root "$Profile-after.json") --profile $Profile --rounds 3 --nodes 100 --bars 12000 --workers 1 2 4
        if ($LASTEXITCODE -ne 0) { throw "Modified $Profile failed; retain diagnostics" }
        & $Py $Compare (Join-Path $Root "$Profile-before.json") (Join-Path $Root "$Profile-after.json")
        if ($LASTEXITCODE -ne 0) { throw "Outcome/settings mismatch under $Profile; do not claim speedup" }
    }
} finally { $env:TEMP = $OldTemp; $env:TMP = $OldTmp }
```

Read **effective workers** in each JSON, not just requested workers. The native ~32-logical-CPU machine might actually admit four; the sandbox did not. Existing memory/CPU guards still apply. App default remains serial. No saved worker setting is changed: benchmark sweeps are in-memory only. Generation remains serial. OFF candidate counters are `null`/unavailable, not zero measured attempts.

Compare median batch time/scenarios per minute, actual persisted trades, generation births per minute, CPU seconds/RSS and partial/failed counts; report formula `speedup = before_seconds / after_seconds`. Per-task compute includes queue/startup/IPC when parallel. Requested/effective mismatch, cold pool initialization and concurrent persistence can dominate short runs. Do not present input bars/s as trades/s, one-core CPU as whole-machine utilization, or sampled sums as exact CPU accounting. All full-trade/non-observational-metric/record/RNG digests must match; the comparator exits nonzero on a mismatch.

**Short generation noise check:** use fresh names with `--generation-only --rounds 8 --nodes 100 --profile light`, first against `$Base`, then `$Repo`; repeat three process pairs and pool all 24 round rates per side. Repeat separately with `--profile off`. Compare each pair with the comparator. This is the longer generation validation used in the report after one adverse three-round Light sample; do not discard slow samples. These are isolated fixtures, not authoritative campaign runs.

**Larger isolated workload:** optionally repeat the same two commands with fresh names and `--bars 70954`, first `--workers 1 2`, then four if guards admit it. Keep all settings, inputs and profile identical on each side. This creates many durable trade rows: stop if storage/memory is insufficient; do not relax safety guards. A second run in reversed before/after order helps assess thermal/cache noise. Do not extrapolate a 100-birth fresh-DB result to a fully evaluated 20,000-node population.

## 3. Native backend/UI regressions, still external to DATA

The new backend cases are self-contained synthetic fixtures; do not import production history to silence legacy fixture failures.

```powershell
$OldData = $env:EVOLUTIONARY_LAB_DATA_ROOT
$OldTemp = $env:TEMP; $OldTmp = $env:TMP
$env:EVOLUTIONARY_LAB_DATA_ROOT = Join-Path $Root 'native-tests-data'
$env:TEMP = $Temp; $env:TMP = $Temp
Push-Location $Repo
try {
    & $Py -m pytest tests/test_v10_4_throughput.py tests/test_v10_2_processing.py tests/test_v10_2_dashboard.py tests/test_v10_3_speed.py -q --junitxml (Join-Path $Root 'native-focused.xml')
    if ($LASTEXITCODE -ne 0) { throw 'Native focused regression failed; preserve evidence, not a green preflight' }
} finally {
    Pop-Location
    $env:EVOLUTIONARY_LAB_DATA_ROOT = $OldData
    $env:TEMP = $OldTemp; $env:TMP = $OldTmp
}
```

For frontend validation, in the **fresh clone** use its locked dependencies (if not already installed):

```powershell
Push-Location (Join-Path $Repo 'frontend')
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies failed' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    node tests/v10_4_ui.mjs
    if ($LASTEXITCODE -ne 0) { throw 'V10.4 UI regression failed' }
    node tests/v10_3_speed_ui.mjs
    if ($LASTEXITCODE -ne 0) { throw 'V10.3 UI regression failed' }
    node tests/v10_2_dashboard_ui.mjs
    if ($LASTEXITCODE -ne 0) { throw 'V10.2 UI regression failed' }
} finally { Pop-Location }
```

Existing expanded Linux results are **369 passed / 17 failed / 4 errors**, with exact nonpassing identities equal to baseline. Missing real market/inventory fixtures must remain explicitly unavailable, never replaced with dummy research. Python 3.14.6/Windows shutdown, multiprocessing, file-lock/SQLite and cancellation behavior require the actual native run above; Linux success is not certification of them.

## 4. Optional real-market acceptance — separately authorized only

The commands above do not authorize an authoritative campaign. For real-market validation, arrange an explicit isolated, read-only bounded dataset through existing supported APIs; do not copy whole DATA or point writable test paths at production research. Match node/genome/version/source fingerprint/timeframe/requested dates/fees/slippage/risk/settings and initial population between revisions. Preserve source data and all protected evidence. Do not create an authoritative 800-node campaign just for this check.

Capture existing SPEED LOG before/after, with source/eligible/processed counts and actual timestamps, queue/startup, feature/compute/normalization/serialization/persistence/export/API/display stages, exact full-trade counts/CSV beyond 3,000, rejects/duplicates/PARTIAL/failed/cancelled and process-tree CPU/RSS. Compare complete trades/metrics/RNG/identity before timing conclusions. Confirm browser close/poll disconnect does not cancel a scenario; explicit cancellation retains committed rows and prevents completion. Test deletion only on disposable scenarios: refusal while active/stopping, then selected/all deletion, no resurrection, preserved unrelated nodes/lineage/stars/market/settings. Check STARRED populated/empty views, unchanged historical DeepTest metrics in Live Testing, SPEED LOG display pause/recording separation, and complete COPY ALL including clipboard fallback.

## 5. GPU compatibility: evidence only, not acceleration

The benchmark already probes `nvidia-smi` and CUDA driver initialization without installing large libraries. An optional manual driver check is:

```powershell
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu --format=csv
```

These values describe the **device**, not application GPU computation. Expected application status for this release remains **NOT IMPLEMENTED/UNVERIFIED**, even when driver initialization succeeds. No CUDA dependency, GPU kernel or GPU workload flag was added. Do not enable a GPU setting just to increase utilization, change drivers/Python automatically, or treat a CUDA version banner as Python 3.14.6 library compatibility.

A future GPU experiment requires a supported native wheel/runtime, bounded independently equivalent numerical batches, transfer/compilation/serialization-inclusive end-to-end timing, exact full-trade/metric equivalence, VRAM budget/admission/oom/cancellation tests and verified CPU fallback. If those conditions are absent or slower, retain CPU execution and report GPU N/A. Existing py-spy Windows/Python 3.14.6 compatibility is not certified; no debugger/profiler installation is required here.

Retain the TEMP result JSONs/JUnit/logs until reviewed. Remove only explicitly identified disposable TEMP fixtures afterward; **never** remove original DATA, DB sidecars, backups, failed-preflight evidence or unrelated work.
