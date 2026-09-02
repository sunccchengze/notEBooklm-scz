# 一键安装 notebooklm-py 到本仓库的 .venv (Windows / PowerShell)
# 本文件必须保存为 UTF-8 with BOM + CRLF，否则 PowerShell 5.1 会乱码

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Fail {
    param([string[]]$Lines)
    Write-Host ""
    foreach ($l in $Lines) { Write-Host $l -ForegroundColor Red }
    Write-Host ""
    exit 1
}

# ---------- 1. 找一个能用的 Python ----------
# Windows 上 python.exe 常常是 Microsoft Store 占位符，必须实际执行来验证
function Find-Python {
    $candidates = @(
        @{ Exe = "py";      Pre = @("-3") },
        @{ Exe = "python";  Pre = @() },
        @{ Exe = "python3"; Pre = @() }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $probe = $c.Pre + @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")
            $ver = & $c.Exe @probe 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver -match '^(\d+)\.(\d+)$') {
                $maj = [int]$Matches[1]
                $min = [int]$Matches[2]
                if ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 10)) {
                    return @{ Exe = $c.Exe; Pre = $c.Pre; Version = $ver }
                }
                Write-Host ("    跳过 " + $c.Exe + " (版本 " + $ver + "，需要 3.10+)") -ForegroundColor DarkGray
            }
        } catch { }
    }
    return $null
}

Write-Host "==> 查找 Python ..." -ForegroundColor Cyan
$py = Find-Python

if (-not $py) {
    Fail @(
        "没有找到可用的 Python 3.10 或更高版本。",
        "",
        "请先安装 Python，然后【重新打开 PowerShell 窗口】再跑本脚本:",
        "",
        "    winget install Python.Python.3.12",
        "",
        "如果 winget 不可用，去 https://www.python.org/downloads/ 下载，",
        "安装时务必勾选 Add python.exe to PATH。",
        "",
        "补充: 如果敲 python 会弹出 Microsoft Store，那是系统占位符。",
        "关掉它: 设置 - 应用 - 高级应用设置 - 应用执行别名，",
        "把 python.exe 和 python3.exe 两个开关关掉。"
    )
}

Write-Host ("    找到 Python " + $py.Version + "  (" + $py.Exe + " " + ($py.Pre -join " ") + ")") -ForegroundColor Green

# ---------- 2. 创建 venv ----------
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    if (Test-Path ".venv") {
        Write-Host "==> 发现残缺的 .venv，删除重建 ..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force ".venv"
    }
    Write-Host "==> 创建虚拟环境 .venv ..." -ForegroundColor Cyan
    $venvArgs = $py.Pre + @("-m", "venv", ".venv")
    & $py.Exe @venvArgs
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPython)) {
        Fail @(
            "创建虚拟环境失败。",
            "",
            "手动跑这条命令看具体报错:",
            ("    " + $py.Exe + " " + ($py.Pre -join " ") + " -m venv .venv"),
            "",
            "如果提示缺少 venv 模块，重装 Python 并勾选完整组件。"
        )
    }
} else {
    Write-Host "==> 复用已有的 .venv" -ForegroundColor DarkGray
}

# ---------- 3. 安装依赖 ----------
Write-Host "==> 升级 pip ..." -ForegroundColor Cyan
& $VenvPython -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Fail @("pip 升级失败，可能是网络问题。")
}

Write-Host "==> 安装 notebooklm-py (约 1-3 分钟) ..." -ForegroundColor Cyan
& $VenvPython -m pip install --quiet "notebooklm-py[browser,cookies]"
if ($LASTEXITCODE -ne 0) {
    Write-Host "    完整安装失败，退回最小安装 ..." -ForegroundColor Yellow
    & $VenvPython -m pip install --quiet "notebooklm-py"
    if ($LASTEXITCODE -ne 0) {
        Fail @("安装 notebooklm-py 失败，请检查网络连接。")
    }
}

Write-Host "==> 安装图形界面依赖 ..." -ForegroundColor Cyan
& $VenvPython -m pip install --quiet fastapi "uvicorn[standard]" python-multipart
if ($LASTEXITCODE -ne 0) {
    Write-Host "    界面依赖安装失败，命令行仍可用。" -ForegroundColor Yellow
}

# ---------- 4. 验证 ----------
$NbExe = Join-Path $Root ".venv\Scripts\notebooklm.exe"
if (-not (Test-Path $NbExe)) {
    Fail @("安装完成但找不到 notebooklm.exe，环境异常。", "试试删除 .venv 后重跑本脚本。")
}

$ver = & $NbExe --version

Write-Host ""
Write-Host ("[OK] " + $ver) -ForegroundColor Green
Write-Host ""
Write-Host "下一步 - 登录 (首次会下载 Chromium 约 170MB):" -ForegroundColor Cyan
Write-Host "  .\scripts\nb.ps1 login"
Write-Host ""
Write-Host "登录后，双击 启动.bat 即可打开图形界面" -ForegroundColor Green
Write-Host ""
Write-Host "嫌下载慢？直接从已登录的浏览器读 cookie:" -ForegroundColor Cyan
Write-Host "  .\scripts\nb.ps1 login --browser-cookies edge"
Write-Host ""
Write-Host "然后验证:" -ForegroundColor Cyan
Write-Host "  .\scripts\nb.ps1 auth check --test"
Write-Host "  .\scripts\nb.ps1 list"
