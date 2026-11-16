param([string]$Bind = '127.0.0.1', [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw '请先运行 scripts/init.ps1' }
if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) { throw '请先构建前端 npm run build' }
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host $Bind --port $Port --no-access-log
