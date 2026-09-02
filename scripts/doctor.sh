#!/usr/bin/env bash
# 环境体检：装没装好 / 有没有凭据 / 能不能连到 Google —— 三件事一次说清。
#
# 为什么要有它：NotebookLM 失败的原因 99% 落在这三类里，而它们的修法完全不同。
# 让 Agent 在动手前先跑一次，比事后从 traceback 里猜要便宜得多。
#
#   ./scripts/doctor.sh          人类可读
#   ./scripts/doctor.sh --json   机器可读（Agent 用这个）
#
# 退出码：0 = 三项全绿；1 = 有阻塞项
set -uo pipefail
cd "$(dirname "$0")/.."

JSON=0
[ "${1:-}" = "--json" ] && JSON=1

ROOT="$PWD"
export NOTEBOOKLM_HOME="${NOTEBOOKLM_HOME:-$ROOT/.notebooklm}"
PROFILE_DIR="$NOTEBOOKLM_HOME/profiles/${NOTEBOOKLM_PROFILE:-default}"

pass=0; fail=0
declare -a LINES=()
declare -a JKEYS=()

chk() { # chk <key> <ok:0|1> <human text>
  local key="$1" ok="$2" msg="$3"
  if [ "$ok" -eq 0 ]; then pass=$((pass+1)); LINES+=("  ✅ $msg"); JKEYS+=("\"$key\": true")
  else fail=$((fail+1)); LINES+=("  ❌ $msg"); JKEYS+=("\"$key\": false"); fi
}

# ---------- 1. 安装 ----------
VENV_PY=".venv/bin/python"
NB_CLI=".venv/bin/notebooklm"

[ -x "$VENV_PY" ]; chk venv_present $? "本仓库 .venv（$([ -x "$VENV_PY" ] && echo 存在 || echo 缺失) → ./scripts/setup.sh）"

NB_VERSION="none"
if [ -x "$NB_CLI" ]; then
  NB_VERSION="$("$NB_CLI" --version 2>/dev/null || echo none)"
  chk cli_runs 0 "notebooklm CLI: $NB_VERSION"
else
  chk cli_runs 1 "notebooklm CLI 不可执行（$NB_CLI 缺失 → ./scripts/setup.sh）"
fi

if [ -x "$VENV_PY" ] && "$VENV_PY" -c "import gpsoauth" 2>/dev/null; then
  chk headless_extra 0 "headless extra（gpsoauth）已装 —— master token 认证可用"
else
  chk headless_extra 1 "headless extra 缺失 —— 装不了 master token（pip install 'notebooklm-py[headless]'）"
fi

if [ -x "$VENV_PY" ] && "$VENV_PY" -c "import fastmcp" 2>/dev/null; then
  chk mcp_extra 0 "mcp extra（fastmcp）已装 —— scripts/agent-mcp 可用"
else
  chk mcp_extra 1 "mcp extra 缺失 —— MCP 入口不可用（pip install 'notebooklm-py[mcp]'）"
fi

# ---------- 2. 凭据 ----------
HAVE_TOKEN=0; HAVE_STORAGE=0
[ -f "$PROFILE_DIR/master_token.json" ] && HAVE_TOKEN=1
[ -f "$PROFILE_DIR/storage_state.json" ] && HAVE_STORAGE=1

if [ "$HAVE_TOKEN" -eq 1 ]; then
  # 只报元数据，绝不打印内容
  PERM="$(stat -c '%a' "$PROFILE_DIR/master_token.json" 2>/dev/null || stat -f '%Lp' "$PROFILE_DIR/master_token.json" 2>/dev/null || echo '?')"
  chk master_token 0 "master_token.json 已注入（权限 $PERM，期望 600）"
  [ "$PERM" = "600" ] || LINES+=("     ⚠️  权限不是 600，建议 chmod 600")
elif [ "$HAVE_STORAGE" -eq 1 ]; then
  chk master_token 0 "storage_state.json 存在（cookie 快照；上游说明约 10 分钟会被其它客户端顶替，仅适合单次验证）"
else
  chk master_token 1 "没有凭据：$PROFILE_DIR 下既无 master_token.json 也无 storage_state.json
       → ./scripts/inject-token.sh   （见 docs/arena-agent.md）"
fi

# ---------- 3. 出网 ----------
probe() { # probe <url> -> http code（curl 失败时 -w 已经输出 000，不要再补一个）
  local code
  code="$(timeout 12 curl -4 -sS -o /dev/null -w '%{http_code}' "$1" 2>/dev/null)"
  printf '%s' "${code:-000}"
}
CODE_NB="$(probe https://notebooklm.google.com/)"
CODE_GH="$(probe https://api.github.com/)"

[ "$CODE_GH" = "200" ]; chk egress_github $? "api.github.com → HTTP $CODE_GH（GitHub 可达，工单中继可用）"

if [ "$CODE_NB" = "200" ] || [ "$CODE_NB" = "302" ] || [ "$CODE_NB" = "401" ] || [ "$CODE_NB" = "403" ]; then
  chk egress_google 0 "notebooklm.google.com → HTTP $CODE_NB（**Google 可达，路线 A 直连可用**）"
else
  chk egress_google 1 "notebooklm.google.com → $CODE_NB（TLS 被切断，直连不可用；只能走路线 B 工单中继）"
fi

# ---------- 4. 端到端认证（只有前两项都绿才有意义） ----------
AUTH_OK=0
if [ -x "$NB_CLI" ] && { [ "$HAVE_TOKEN" -eq 1 ] || [ "$HAVE_STORAGE" -eq 1 ]; }; then
  AUTH_JSON="$("$NB_CLI" auth check --test --json 2>/dev/null || echo '{}')"
  # SKILL.md 的双条件：status==ok 且 checks.token_fetch==true。裸 --json 是假阳性陷阱。
  if [ -x "$VENV_PY" ]; then
    AUTH_OK="$("$VENV_PY" - <<PY 2>/dev/null || echo 0
import json,sys
try:
    d=json.loads('''$AUTH_JSON''')
except Exception:
    print(0); sys.exit()
print(1 if d.get("status")=="ok" and (d.get("checks") or {}).get("token_fetch") is True else 0)
PY
)"
  fi
  if [ "${AUTH_OK:-0}" = "1" ]; then
    chk auth_live 0 "auth check --test → status=ok 且 token_fetch=true（凭据真的能认证）"
  else
    chk auth_live 1 "auth check --test 未通过（凭据在，但认证失败或 Google 不可达）"
  fi
else
  chk auth_live 1 "跳过端到端认证检查（缺 CLI 或缺凭据）"
fi

# ---------- 输出 ----------
if [ "$JSON" -eq 1 ]; then
  printf '{"checks":{%s},"pass":%d,"fail":%d,"version":"%s","http":{"notebooklm":"%s","github":"%s"}}\n' \
    "$(IFS=,; echo "${JKEYS[*]}")" "$pass" "$fail" "$NB_VERSION" "$CODE_NB" "$CODE_GH"
else
  echo "NotebookLM 环境体检"
  echo "  NOTEBOOKLM_HOME = $NOTEBOOKLM_HOME"
  echo "  profile         = ${NOTEBOOKLM_PROFILE:-default}"
  echo
  printf '%s\n' "${LINES[@]}"
  echo
  echo "  合计：$pass 项通过 / $fail 项阻塞"
fi

[ "$fail" -eq 0 ]
