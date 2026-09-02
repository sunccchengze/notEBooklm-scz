@echo off
chcp 65001 >nul
cd /d "%~dp0"
title NotebookLM 桌面版

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   还没有安装环境，正在自动安装...
    echo.
    powershell -ExecutionPolicy Bypass -File "scripts\setup.ps1"
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo   安装失败，请手动运行:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
        echo.
        pause
        exit /b 1
    )
)

.venv\Scripts\python.exe -c "import fastapi" 2>nul
if errorlevel 1 (
    echo   正在安装界面依赖，请稍候...
    .venv\Scripts\python.exe -m pip install --quiet fastapi "uvicorn[standard]" python-multipart
)

echo.
echo   正在启动 NotebookLM 桌面版...
echo   浏览器会自动打开，如果没有请访问 http://127.0.0.1:8765
echo   关闭本窗口即可退出
echo.

.venv\Scripts\python.exe app\server.py

pause
