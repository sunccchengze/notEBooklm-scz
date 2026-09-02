# Windows 常见问题排查

## 「无法将 .\.venv\Scripts\python.exe 项识别为 cmdlet…」

**原因**：`python -m venv .venv` 没成功，虚拟环境压根没建出来，后续所有命令自然找不到文件。

**先诊断**，在仓库目录跑：

```powershell
python --version
py -3 --version
Get-Command python | Select-Object Source
```

根据结果对号入座：

### 情况 1：敲 `python` 弹出 Microsoft Store（最常见）

Windows 预置了一个「应用执行别名」占位符，它不是真的 Python。

**修法 A（推荐）—— 装真 Python：**
```powershell
winget install Python.Python.3.12
```
装完 **必须重开一个 PowerShell 窗口**（PATH 才会刷新），然后重跑 `.\scripts\setup.ps1`。

**修法 B —— 关掉占位符：**
设置 → 应用 → 高级应用设置 → 应用执行别名 → 把 `python.exe` 和 `python3.exe` 两个开关关掉。

### 情况 2：`python --version` 报「不是内部或外部命令」

没装 Python，或者装了但没加进 PATH。

```powershell
winget install Python.Python.3.12
```
或去 <https://www.python.org/downloads/> 下载，**安装时务必勾选 "Add python.exe to PATH"**。
装完重开 PowerShell 窗口。

### 情况 3：`py -3 --version` 能用，但 `python` 不能

没关系，新版 `setup.ps1` 会自动优先用 `py -3`。直接重跑即可：
```powershell
.\scripts\setup.ps1
```

### 情况 4：Python 版本低于 3.10

`notebooklm-py` 要求 3.10+。装个新的：
```powershell
winget install Python.Python.3.12
```

---

## 「无法加载文件 …scripts\setup.ps1，因为在此系统上禁止运行脚本」

PowerShell 默认执行策略限制。二选一：

```powershell
# 永久放开当前用户（推荐）
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

```powershell
# 或者单次绕过，不改系统设置
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

---

## venv 建到一半坏了

删掉重来即可，`setup.ps1` 也会自动检测并重建：

```powershell
Remove-Item -Recurse -Force .venv
.\scripts\setup.ps1
```

---

## `notebooklm login` 卡住 / Chromium 下载太慢

首次登录会下载约 170MB 的 Chromium。国内网络可能很慢。绕开它，直接从你已经登录的浏览器读 cookie：

```powershell
.\scripts\nb.ps1 login --browser-cookies edge      # 或 chrome / firefox / brave
```

前提是那个浏览器里已经登录了 <https://notebooklm.google.com>。

多个 Google 账号时指定一个：
```powershell
.\scripts\nb.ps1 login --browser-cookies edge --account you@gmail.com
```

---

## 登录成功但 `auth check --test` 失败

```powershell
.\scripts\nb.ps1 auth check --test --json     # 看详细诊断
.\scripts\nb.ps1 auth refresh                 # 尝试刷新 cookie
.\scripts\nb.ps1 doctor                       # 全面体检
```

还不行就重新登录一次：
```powershell
.\scripts\nb.ps1 auth logout
.\scripts\nb.ps1 login
```

---

## 中文输出乱码

```powershell
chcp 65001
$OutputEncoding = [System.Text.Encoding]::UTF8
```

或者换用 Windows Terminal（默认 UTF-8，不会有这问题）。

---

## 还是不行？

把下面这条命令的完整输出发给我：

```powershell
Write-Host "--- python ---"; python --version 2>&1
Write-Host "--- py -3 ---";  py -3 --version 2>&1
Write-Host "--- where ---";  where.exe python 2>&1
Write-Host "--- venv ---";   Test-Path .\.venv\Scripts\python.exe
Write-Host "--- policy ---"; Get-ExecutionPolicy -List
```

---

## 「表达式或语句中包含意外的标记 }」+ 中文乱码（如 `瀹夎瀹屾垚`）

**原因**：Windows PowerShell 5.1 读取**没有 BOM** 的 UTF-8 文件时，会按系统 ANSI 代码页（简体中文机器上是 GBK）解码。中文字节被错误还原，字符串引号错位，解析器就会在莫名其妙的地方报「缺少右 }」。

**已修复**：仓库里的 `.ps1` 文件现在都带 UTF-8 BOM，并通过 `.gitattributes` 强制以 CRLF 检出。

```powershell
git pull
.\scripts\setup.ps1
```

**如果你自己改了 .ps1 文件又出现乱码**，保存时要选「UTF-8 with BOM」：

- VS Code：右下角点编码 → Save with Encoding → UTF-8 with BOM
- 记事本：另存为 → 编码选「带有 BOM 的 UTF-8」

命令行批量修复：
```powershell
Get-ChildItem scripts\*.ps1 | ForEach-Object {
    $c = Get-Content $_.FullName -Raw -Encoding UTF8
    [System.IO.File]::WriteAllText($_.FullName, $c, (New-Object System.Text.UTF8Encoding $true))
}
```

**根治**：升级到 PowerShell 7（默认按 UTF-8 读取，无需 BOM）：
```powershell
winget install Microsoft.PowerShell
```
之后用 `pwsh` 而不是 `powershell` 启动。

---

## 登录后警告 `Missing required cookies: __Secure-1PSIDTS`

**这多半不是真故障。** `__Secure-1PSIDTS` 是 `__Secure-1PSID` 的「新鲜度伙伴」cookie，
Google 按自己的节奏下发，浏览器登录瞬间可能还没写入。

notebooklm-py 内置了自愈：每次 `fetch_tokens` 都会顺带向
`accounts.google.com/RotateCookies` 发一次请求补上它。所以**直接跑下一条命令通常就好了**：

```powershell
.\scripts\nb.ps1 auth check --test
.\scripts\nb.ps1 list
```

看到 `status: ok` 就没事，前面的警告可以忽略。

### 如果 auth check 仍然失败

按顺序试：

```powershell
# 1. 主动刷新
.\scripts\nb.ps1 auth refresh

# 2. 允许无头浏览器重新认证（会复用已保存的浏览器 profile）
.\scripts\nb.ps1 auth refresh --allow-headless

# 3. 全面体检
.\scripts\nb.ps1 doctor
```

### 还不行就重新登录

在浏览器窗口里，登录完成后**多停留几秒再关窗口**，给 Google 时间下发完整 cookie：

```powershell
.\scripts\nb.ps1 auth logout
.\scripts\nb.ps1 login
```

### 多个 Google 账号

登录时明确指定账号，避免路由到错误的 authuser：

```powershell
.\scripts\nb.ps1 login --browser-cookies edge --account you@gmail.com
```

### 需要长期无人值守

配置计划任务每 15-20 分钟刷新一次，或者改用 master token 方案：

```powershell
.\.venv\Scripts\python.exe -m pip install "notebooklm-py[headless]"
.\scripts\nb.ps1 login --master-token --account you@gmail.com
```
master token 能在 cookie 完全过期后自动重新签发，无需浏览器。
