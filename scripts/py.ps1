# 用本仓库 .venv 的 python 跑脚本 (Windows / PowerShell)
$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "[X] 环境还没装好，找不到 $VenvPython" -ForegroundColor Red
    Write-Host "    请先运行:  .\scripts\setup.ps1" -ForegroundColor Yellow
    exit 1
}

$env:NOTEBOOKLM_HOME = if ($env:NOTEBOOKLM_HOME) { $env:NOTEBOOKLM_HOME } else { Join-Path $Root ".notebooklm" }
New-Item -ItemType Directory -Force -Path $env:NOTEBOOKLM_HOME | Out-Null

& $VenvPython @args
exit $LASTEXITCODE
