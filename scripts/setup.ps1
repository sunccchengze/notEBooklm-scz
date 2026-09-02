# 一键安装 notebooklm-py 到本仓库的 .venv (Windows / PowerShell)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

# 浏览器登录需要 Playwright；从已登录浏览器抓 cookie 需要 [cookies]
& .\.venv\Scripts\python.exe -m pip install --quiet "notebooklm-py[browser,cookies]"

$ver = & .\.venv\Scripts\notebooklm.exe --version
Write-Host "OK  $ver" -ForegroundColor Green
Write-Host ""
Write-Host "下一步 —— 登录（Windows 有图形界面，可以直接浏览器登录）:"
Write-Host "  .\scripts\nb.ps1 login"
Write-Host "  .\scripts\nb.ps1 auth check --test"
