#!/usr/bin/env bash
# 一键安装 notebooklm-py 到本仓库的 .venv
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "✅ 安装完成: $(.venv/bin/notebooklm --version)"
echo
echo "下一步: 导入 cookies (见 docs/使用指南.md)"
echo "  ./scripts/nb auth import-cookies cookies.json"
echo "  ./scripts/nb auth check --test"
