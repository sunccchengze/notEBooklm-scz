# 诊断「看不到历史对话」
# 用法： .\scripts\诊断历史.ps1
$ErrorActionPreference = "Stop"
chcp 65001 > $null
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:NOTEBOOKLM_HOME = Join-Path $root ".notebooklm"

Write-Host ""
Write-Host "=== 1. 代码版本 ===" -ForegroundColor Cyan
git log --oneline -1
Write-Host ""

Write-Host "=== 2. 关键修复是否在本地 ===" -ForegroundColor Cyan
$srv = Get-Content "app\server.py" -Raw -Encoding UTF8
$js  = Get-Content "app\static\app.js" -Raw -Encoding UTF8
$checks = @(
  @{n="建议卡片修复"; ok=$srv.Contains('"en": title')},
  @{n="历史返回结构"; ok=$srv.Contains('"turns": out[-60:]')},
  @{n="事件委托";     ok=$js.Contains('data-act')},
  @{n="防缓存";       ok=$srv.Contains('NoCacheStatic')}
)
foreach ($c in $checks) {
  if ($c.ok) { Write-Host ("  [有] " + $c.n) -ForegroundColor Green }
  else       { Write-Host ("  [缺] " + $c.n + "  <- 代码不是最新，请 git pull") -ForegroundColor Red }
}
Write-Host ""

Write-Host "=== 3. Google 返回的原始历史数据 ===" -ForegroundColor Cyan
& ".venv\Scripts\python.exe" "scripts\diag_history.py"
