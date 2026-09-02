#!/usr/bin/env bash
# 路线 B：外部 worker —— 跑在**有 Google 出网**的机器上（你的电脑 / 一台小服务器）。
#
#   Arena Agent（沙箱，Google 不可达）        worker（本机，Google 可达）
#     写 jobs/pending/<id>.job.json  ──push──▶  git pull
#     轮询 jobs/done/<id>.result.json ◀─push──  tools/nbjob.py execute
#
# 凭据只在这台机器上，永远不进 Git、不进沙箱。
#
# 用法：
#   ./scripts/worker.sh once      跑一轮就退（适合 cron / 手动）
#   ./scripts/worker.sh watch     循环轮询，Ctrl-C 停
#
# 依赖：git、gh（或已配好的 git 凭据）、./scripts/setup.sh 装好的 .venv、已注入凭据。
set -euo pipefail
cd "$(dirname "$0")/.."

BRANCH="${WORKER_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
POLL="${WORKER_POLL:-60}"
MODE="${1:-once}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "[X] 缺少 $1" >&2; exit 1; }; }
need git
[ -x .venv/bin/python ] || { echo "[X] 先跑 ./scripts/setup.sh" >&2; exit 1; }

./scripts/doctor.sh >/dev/null 2>&1 || {
  echo "[X] 体检未通过，先看 ./scripts/doctor.sh 的输出" >&2
  ./scripts/doctor.sh >&2 || true
  exit 1
}

run_round() {
  git pull --rebase --quiet origin "$BRANCH" 2>/dev/null || {
    echo "[!] git pull 失败，本轮跳过"; return 1; }

  shopt -s nullglob
  local jobs=(jobs/pending/*.job.json)
  shopt -u nullglob
  if [ ${#jobs[@]} -eq 0 ]; then
    echo "· 没有待处理工单"
    return 0
  fi

  echo "→ 发现 ${#jobs[@]} 个工单"
  local changed=0
  for j in "${jobs[@]}"; do
    local id; id="$(basename "$j" .job.json)"
    echo "════ $id ════"
    mkdir -p jobs/done out
    # execute 内部已经处理失败：status=failed 也会写结果文件，Agent 能读到原因
    .venv/bin/python tools/nbjob.py execute "$j" --result "jobs/done/$id.result.json" || true
    # 产物分流：小文本留在 out/ 随 Git 回传，二进制走 GitHub Release。
    # 失败不阻塞——delivery 段已写进 result，Agent 能读到原因并重试。
    .venv/bin/python tools/nbjob.py ship "jobs/done/$id.result.json" >/dev/null 2>&1 || true
    # 无论成败都把工单挪出 pending，避免重复执行浪费配额
    git mv -f "$j" "jobs/running/$id.job.json" 2>/dev/null || mv -f "$j" "jobs/running/$id.job.json"
    changed=1
  done

  if [ "$changed" -eq 1 ]; then
    mkdir -p jobs/running
    git add jobs out
    git -c user.name="notebooklm-worker" -c user.email="worker@local" \
        commit -q -m "chore(worker): 执行 ${#jobs[@]} 个 NotebookLM 工单" || true
    git push --quiet origin "$BRANCH" && echo "✓ 已回推结果到 $BRANCH"
  fi
}

case "$MODE" in
  once)  run_round ;;
  watch) while true; do run_round || true; sleep "$POLL"; done ;;
  *) echo "用法: $0 {once|watch}" >&2; exit 1 ;;
esac
