param([switch]$Demo, [switch]$SkipInstall, [string]$Python = '')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
if (-not $Python) {
  $foundPython = Get-Command python -ErrorAction SilentlyContinue
  if ($foundPython) { $Python = $foundPython.Source }
  elseif (Test-Path -LiteralPath '.venv\Scripts\python.exe') { $Python = Join-Path $taskRoot '.venv\Scripts\python.exe' }
  else { throw '需要 Python 3.12+；也可用 -Python 指定完整可执行文件路径。' }
}
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
  & $Python -m venv .venv
  if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败' }
}
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not $SkipInstall) {
  & $taskPython -m pip install -r backend/requirements.lock.txt
  if ($LASTEXITCODE -ne 0) { throw '安装后端依赖失败' }
  Push-Location frontend
  try { npm.cmd ci; if ($LASTEXITCODE -ne 0) { throw '安装前端依赖失败' }; npm.cmd run build; if ($LASTEXITCODE -ne 0) { throw '前端构建失败' } } finally { Pop-Location }
}
& $taskPython scripts/manage.py init
if ($LASTEXITCODE -ne 0) { throw '初始化失败' }
if ($Demo) { & $taskPython scripts/manage.py demo; if ($LASTEXITCODE -ne 0) { throw 'Mock 演示失败' } }
Write-Output '初始化完成。运行 .\scripts\start.ps1 启动网页。密码见本项目 .env。'
