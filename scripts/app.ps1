# 启动 NotebookLM 桌面版 (Windows / PowerShell)
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "环境还没装好，正在自动安装 ..." -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "setup.ps1")
    if (-not (Test-Path $VenvPython)) { exit 1 }
}

# 确保界面依赖存在
& $VenvPython -c "import fastapi" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "正在安装界面依赖 ..." -ForegroundColor Cyan
    & $VenvPython -m pip install --quiet fastapi "uvicorn[standard]" python-multipart
}

$env:NOTEBOOKLM_HOME = if ($env:NOTEBOOKLM_HOME) { $env:NOTEBOOKLM_HOME } else { Join-Path $Root ".notebooklm" }

& $VenvPython (Join-Path $Root "app\server.py")
