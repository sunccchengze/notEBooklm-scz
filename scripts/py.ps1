# 用本仓库 .venv 的 python 跑脚本 (Windows / PowerShell)
$Root = Split-Path -Parent $PSScriptRoot
$env:NOTEBOOKLM_HOME = if ($env:NOTEBOOKLM_HOME) { $env:NOTEBOOKLM_HOME } else { Join-Path $Root ".notebooklm" }
New-Item -ItemType Directory -Force -Path $env:NOTEBOOKLM_HOME | Out-Null

& (Join-Path $Root ".venv\Scripts\python.exe") @args
exit $LASTEXITCODE
