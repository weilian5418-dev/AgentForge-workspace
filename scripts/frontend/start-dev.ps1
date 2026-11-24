param([string]$BackendRoot = 'D:\AgentForge')
$ErrorActionPreference = 'Stop'
$frontendRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'apps\workbench-vue'
$pythonExe = Join-Path $BackendRoot '.venv\Scripts\python.exe'
$backendExe = Join-Path $BackendRoot '.venv\Scripts\agentforge.exe'
$viteEntry = Join-Path $frontendRoot 'node_modules\vite\bin\vite.js'
$logRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'output\integration\services'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
function Test-ServiceUrl([string]$Url) {
    try { return (Invoke-WebRequest -Uri $Url -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200 }
    catch { return $false }
}
if (-not (Test-ServiceUrl 'http://127.0.0.1:8765/api/v1/health')) {
    if (-not (Test-Path -LiteralPath $backendExe)) { throw "Backend environment missing: $backendExe" }
    Push-Location $BackendRoot
    $priorOrigins = $env:AGENTFORGE_ALLOWED_ORIGINS
    try {
        $origins = & $pythonExe -c 'import json; from agentforge.core.config import Settings; print(json.dumps(list(dict.fromkeys([*Settings().allowed_origins,"http://127.0.0.1:5174","http://localhost:5174"]))))'
        if ($LASTEXITCODE -ne 0) { throw 'Unable to read backend configuration' }
        $env:AGENTFORGE_ALLOWED_ORIGINS = $origins
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backendProcess = Start-Process -FilePath $backendExe -ArgumentList 'start' -WorkingDirectory $BackendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot "api-$stamp.stdout.log") -RedirectStandardError (Join-Path $logRoot "api-$stamp.stderr.log") -PassThru
        Write-Host "Started backend launcher PID $($backendProcess.Id)"
    } finally { $env:AGENTFORGE_ALLOWED_ORIGINS = $priorOrigins; Pop-Location }
}
if (-not (Test-ServiceUrl 'http://127.0.0.1:5174/')) {
    if (-not (Test-Path -LiteralPath $viteEntry)) { throw 'Frontend dependencies missing; run pnpm install --frozen-lockfile in apps/workbench-vue' }
    $nodeExe = (Get-Command node -ErrorAction Stop).Source
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $frontendProcess = Start-Process -FilePath $nodeExe -ArgumentList @("`"$viteEntry`"", '--host', '127.0.0.1') -WorkingDirectory $frontendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot "frontend-$stamp.stdout.log") -RedirectStandardError (Join-Path $logRoot "frontend-$stamp.stderr.log") -PassThru
    Write-Host "Started frontend PID $($frontendProcess.Id)"
}
$deadline = (Get-Date).AddSeconds(25)
do {
    $apiUp = Test-ServiceUrl 'http://127.0.0.1:8765/api/v1/health'
    $frontendUp = Test-ServiceUrl 'http://127.0.0.1:5174/'
    if ($apiUp -and $frontendUp) { break }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)
if (-not ($apiUp -and $frontendUp)) { throw "Service startup failed. Check logs in $logRoot" }
Write-Host 'Frontend: http://127.0.0.1:5174/workspace'
Write-Host 'Backend:  http://127.0.0.1:8765/api/v1/health'
Write-Host 'Worker processes and migrations are managed separately.'
