#!/usr/bin/env bash
# 一键安装 notebooklm-py 到本仓库的 .venv（Linux / macOS / Arena 沙箱）
#
# 设计要点：
#   - 装到仓库内 .venv，不污染系统 Python（PEP 668 externally-managed 也能过）
#   - 认证数据落在仓库内 .notebooklm/（已 gitignore），不污染家目录
#   - 装完立刻自检：能 import、CLI 能起、MCP 模块在
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "[X] 找不到 $PY" >&2; exit 1; }

if [ ! -d .venv ]; then
  echo "→ 创建 .venv"
  "$PY" -m venv .venv
fi

echo "→ 安装依赖（notebooklm-py[mcp,headless,markdown]）"
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "→ 自检"
.venv/bin/notebooklm --version
.venv/bin/python -c "import notebooklm, fastmcp; print('   import ok: notebooklm', notebooklm.__version__, '/ fastmcp', fastmcp.__version__)"
.venv/bin/python -c "import gpsoauth; print('   headless ok: gpsoauth 已装（master token 可用）')"

cat <<'EOF'

✅ 安装完成

下一步（二选一）：

  A. 沙箱里注入 master token（推荐，无人值守）
       NOTEBOOKLM_MASTER_TOKEN_JSON="$(cat /受保护路径/master_token.json)" ./scripts/inject-token.sh
       ./scripts/doctor.sh

  B. 本机有浏览器时直接登录
       ./scripts/nb login
       ./scripts/doctor.sh

体检脚本会把「装没装好 / 有没有凭据 / 能不能连到 Google」三件事一次说清。
EOF
