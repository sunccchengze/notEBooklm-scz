#!/usr/bin/env bash
# 测试替身：冒充 scripts/nb，返回**与上游 CLI 逐字一致**的 --json 信封。
#
# 为什么要有这个文件
# ------------------
# 早前的验证用的是 /tmp 里的一次性 mock，download 分支只 echo 了裸字符串
# `downloaded`（不是 JSON）。于是 nbjob.py 的 download 步骤漏设 jq_path、
# 把整个信封 dict 存进 captured.artifact_file 这个 bug，被完全掩盖了多轮，
# 直到 ship 真的 Path(dict) 崩掉才暴露。
#
# 所以这里的每个分支都照抄上游源码里的信封构造，并在注释里标出出处行号。
# 上游升级时若形状变了，对照注释就能立刻发现是 mock 该改还是代码该改。
#
# 形状出处（notebooklm-py 0.8.2，site-packages/notebooklm/）：
#   notebook list   -> {"notebooks":[…],"count":N}          cli/services/listing.py:136
#   notebook create -> {"notebook":{"id","title",…}}        cli/notebook_cmd.py:148
#   source add      -> {"source":{"id","title","type","url"}} cli/_source_render.py:614
#   ask             -> AskResult 字段                        _types/chat.py:437
#   generate X      -> {"task_id","status":"pending"}        cli/generate_cmd.py:186
#   generate mind-map -> {"mind_map","note_id","kind"}       （同步返回，无 task_id）
#   artifact wait   -> {"artifact_id","status","url","error"} cli/artifact_cmd.py:606
#   artifact retry  -> {"task_id","status","url","error",…}   cli/artifact_cmd.py:691
#   download X      -> {"operation","artifact","output_path","status"}
#                                                        cli/services/download.py:114
#
# 环境变量开关（供负面测试用）：
#   MOCK_ARTIFACT_WAIT_EXIT=N  让 artifact wait 以 N 退出（默认 0）
#   MOCK_LIST_NOTEBOOKS=1      让 list 返回一个同名笔记本（测复用命中）
#   MOCK_CONFLICT_RENAME=1     让 download 模拟 _resolve_conflict 的改名行为

set -uo pipefail

want_title="${MOCK_WANT_TITLE:-}"

# 从参数里取出我传入的 output_path 位置参数（download 的形状是
# `download <kind> <outfile> -a <id> -n <id>`）
pick_outpath() {
  local a
  for a in "$@"; do
    case "$a" in out/*) echo "$a"; return 0 ;; esac
  done
  return 1
}

case "$*" in
  # ── notebook list ─────────────────────────────────────────────────
  list*)
    if [ "${MOCK_LIST_NOTEBOOKS:-0}" = "1" ]; then
      # title 必须用 MOCK_WANT_TITLE 注入，才能与工单里的标题精确匹配上。
      # 用 python 拼是为了让标题里的引号/中文安全地进 JSON。
      python3 -c '
import json, os, sys
t = os.environ.get("MOCK_WANT_TITLE", "")
print(json.dumps({
    "notebooks": [{"id": "nb-EXIST-0001", "title": t, "is_owner": True,
                   "role": "owner", "created_at": "2026-08-01T00:00:00+00:00"}],
    "count": 1,
}, ensure_ascii=False))
'
    else
      echo '{"notebooks":[],"count":0}'
    fi
    ;;

  # ── notebook create ───────────────────────────────────────────────
  create*)
    echo '{"notebook":{"id":"nb-NEW-0001","title":"m","role":"owner","created_at":"2026-08-01T00:00:00+00:00"}}'
    ;;

  # ── source add / wait ─────────────────────────────────────────────
  "source add"*)
    echo '{"source":{"id":"src-MOCK-'"$RANDOM"'","title":"m","type":"file","url":null}}'
    ;;
  "source wait"*)
    # source wait 的退出码契约：0=ready / 1=missing或失败 / 2=timeout
    echo '{"status":"ready"}'
    ;;

  # ── ask ───────────────────────────────────────────────────────────
  ask*)
    cat <<'JSON'
{"answer":"答案 [1]","conversation_id":"conv-MOCK-1","turn_number":1,"is_follow_up":false,"references":[{"source_id":"src-MOCK-1","citation_number":1,"cited_text":"引用片段","answer_anchor_start":0,"answer_anchor_end":2}]}
JSON
    ;;

  # ── artifact retry（无 --wait 时返回 task_id）─────────────────────
  "artifact retry"*)
    echo '{"task_id":"task-MOCK-retry","status":"pending","url":null,"error":null,"error_code":null}'
    ;;

  # ── generate ──────────────────────────────────────────────────────
  "generate mind-map"*)
    # mind-map 同步返回，没有 task_id
    echo '{"mind_map":{"root":"m"},"note_id":"note-MOCK-mm","kind":"note_backed"}'
    ;;
  "generate report"*|"generate audio"*|"generate slide-deck"*|"generate quiz"*|\
  "generate flashcards"*|"generate video"*|"generate infographic"*|"generate data-table"*)
    echo '{"task_id":"task-MOCK-gen","status":"pending"}'
    ;;

  # ── artifact wait ─────────────────────────────────────────────────
  "artifact wait"*)
    echo '{"artifact_id":"task-MOCK-gen","status":"completed","url":null,"error":null}'
    exit "${MOCK_ARTIFACT_WAIT_EXIT:-0}"
    ;;

  # ── download ──────────────────────────────────────────────────────
  download*)
    req="$(pick_outpath "$@")" || { echo "mock: download 缺少 out/ 路径参数" >&2; exit 1; }
    real="$req"
    # 模拟 _app/download.py:578-586 的 _resolve_conflict：
    # 目标已存在且未给 --force 时，改名为 "base (2).ext"
    if [ "${MOCK_CONFLICT_RENAME:-0}" = "1" ] && [ -e "$req" ]; then
      real="${req%.*} (2).${req##*.}"
    fi
    mkdir -p "$(dirname "$real")"
    printf 'MOCK-ARTIFACT-CONTENT-%s\n' "$(basename "$real")" > "$real"
    # 关键：output_path 是**真实落盘路径**，可能不等于请求路径
    python3 -c '
import json,sys
print(json.dumps({
    "operation":"download_single",
    "artifact":{"id":"task-MOCK-gen","title":"m","selection_reason":"only"},
    "output_path":sys.argv[1],
    "status":"downloaded",
}, ensure_ascii=False))
' "$real"
    ;;

  *)
    echo "mock_nb: 未预期的调用: $*" >&2
    exit 1
    ;;
esac
