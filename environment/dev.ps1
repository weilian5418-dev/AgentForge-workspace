param([Parameter(ValueFromRemainingArguments=$true)][string[]]$UvArgs)
$ErrorActionPreference = 'Stop'
$projectPython = Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe'
$env:UV_PYTHON = if (Test-Path -LiteralPath $projectPython) { $projectPython } else { 'D:\developTools\miniconda3\envs\langchain1.2\python.exe' }
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:UV_CACHE_DIR = 'D:\AgentForge-Tools\uv-cache'
$env:Path = 'D:\AgentForge-Tools\bin;' + $env:Path
$uvExe = 'C:\Users\legion\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe'
if (-not (Test-Path -LiteralPath $uvExe)) {
    $uvExe = (Get-Command uv -ErrorAction Stop).Source
}
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    & $uvExe @UvArgs
    if ($LASTEXITCODE -ne 0) { throw "uv exited with code $LASTEXITCODE" }
} finally { Pop-Location }
