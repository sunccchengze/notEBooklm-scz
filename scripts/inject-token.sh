#!/usr/bin/env bash
# 把 master token 注入到 profile 目录，权限 0600。
#
# 三个来源，按优先级：
#   1) $NOTEBOOKLM_MASTER_TOKEN_FILE  —— 受保护持久化路径（推荐；Arena Secret 挂载点）
#   2) $NOTEBOOKLM_MASTER_TOKEN_JSON  —— 内联环境变量（次选；用完必须 unset）
#   3) 命令行第一个参数               —— 文件路径（本机调试用）
#
# 安全约束（来自 notebooklm-py docs/security.md）：
#   - master token 是 account-equivalent 凭据，改密码不能撤销它，只能显式 revoke
#   - 绝不打印内容、绝不写日志、绝不进 Git（.notebooklm/ 已在 .gitignore）
#   - 建议用专用小号
set -euo pipefail
cd "$(dirname "$0")/.."

ROOT="$PWD"
export NOTEBOOKLM_HOME="${NOTEBOOKLM_HOME:-$ROOT/.notebooklm}"
PROFILE_DIR="$NOTEBOOKLM_HOME/profiles/${NOTEBOOKLM_PROFILE:-default}"
TARGET="$PROFILE_DIR/master_token.json"

SRC_FILE="${NOTEBOOKLM_MASTER_TOKEN_FILE:-${1:-}}"

if [ -n "$SRC_FILE" ]; then
  [ -f "$SRC_FILE" ] || { echo "[X] 找不到文件: $SRC_FILE" >&2; exit 1; }
  umask 077
  mkdir -p "$PROFILE_DIR"
  install -m 600 "$SRC_FILE" "$TARGET" 2>/dev/null || { cp "$SRC_FILE" "$TARGET"; chmod 600 "$TARGET"; }
  echo "✅ 已从文件注入: $SRC_FILE → $TARGET (0600)"
elif [ -n "${NOTEBOOKLM_MASTER_TOKEN_JSON:-}" ]; then
  # 内联 JSON：先验证是合法 JSON，再落盘，避免写进一个坏文件
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$NOTEBOOKLM_MASTER_TOKEN_JSON" | jq -e . >/dev/null 2>&1 \
      || { echo "[X] NOTEBOOKLM_MASTER_TOKEN_JSON 不是合法 JSON" >&2; exit 1; }
  fi
  umask 077
  mkdir -p "$PROFILE_DIR"
  printf '%s' "$NOTEBOOKLM_MASTER_TOKEN_JSON" > "$TARGET"
  chmod 600 "$TARGET"
  echo "✅ 已从 NOTEBOOKLM_MASTER_TOKEN_JSON 注入 → $TARGET (0600)"
  echo "⚠️  记得 unset NOTEBOOKLM_MASTER_TOKEN_JSON —— 环境变量会被子进程继承，文件不会"
else
  cat >&2 <<'EOF'
[X] 没有提供 master token。三选一：

  1) 从受保护路径注入（推荐）
       export NOTEBOOKLM_MASTER_TOKEN_FILE=/受保护路径/master_token.json
       ./scripts/inject-token.sh

  2) 内联注入
       export NOTEBOOKLM_MASTER_TOKEN_JSON="$(cat /受保护路径/master_token.json)"
       ./scripts/inject-token.sh && unset NOTEBOOKLM_MASTER_TOKEN_JSON

  3) 直接给文件路径
       ./scripts/inject-token.sh /path/to/master_token.json

怎么拿到 master_token.json（在一台有浏览器的机器上，只需一次）：
  pip install "notebooklm-py[browser,headless]"
  notebooklm login --master-token --account you@example.com
  # 写出 ~/.notebooklm/profiles/default/master_token.json
详见 docs/arena-agent.md
EOF
  exit 1
fi

# 只报元数据，绝不 cat 内容
echo "   大小: $(wc -c < "$TARGET") bytes"
echo "   权限: $(stat -c '%a' "$TARGET" 2>/dev/null || stat -f '%Lp' "$TARGET")"
echo
echo "下一步: ./scripts/doctor.sh"
