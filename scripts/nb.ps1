# notebooklm CLI 封装 (Windows / PowerShell)
# 自动使用本仓库 .venv，并把认证数据存在仓库内 .notebooklm\
$Root = Split-Path -Parent $PSScriptRoot
$NbExe = Join-Path $Root ".venv\Scripts\notebooklm.exe"

if (-not (Test-Path $NbExe)) {
    Write-Host "[X] 环境还没装好，找不到 $NbExe" -ForegroundColor Red
    Write-Host "    请先运行:  .\scripts\setup.ps1" -ForegroundColor Yellow
    exit 1
}

$env:NOTEBOOKLM_HOME = if ($env:NOTEBOOKLM_HOME) { $env:NOTEBOOKLM_HOME } else { Join-Path $Root ".notebooklm" }
New-Item -ItemType Directory -Force -Path $env:NOTEBOOKLM_HOME | Out-Null

& $NbExe @args
exit $LASTEXITCODE
