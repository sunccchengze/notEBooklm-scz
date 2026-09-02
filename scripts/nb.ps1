# notebooklm CLI 封装 (Windows / PowerShell)
# 自动使用本仓库 .venv，并把认证数据存在仓库内 .notebooklm\
$Root = Split-Path -Parent $PSScriptRoot
$env:NOTEBOOKLM_HOME = if ($env:NOTEBOOKLM_HOME) { $env:NOTEBOOKLM_HOME } else { Join-Path $Root ".notebooklm" }
New-Item -ItemType Directory -Force -Path $env:NOTEBOOKLM_HOME | Out-Null

& (Join-Path $Root ".venv\Scripts\notebooklm.exe") @args
exit $LASTEXITCODE
