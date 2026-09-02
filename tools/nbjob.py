#!/usr/bin/env python3
"""NotebookLM 工单执行器 —— 路线 A（本地直连）与路线 B（外部 worker 中继）共用同一份逻辑。

设计约束（逐条来自 notebooklm-py 的 SKILL.md / AGENTS.md，见 docs/调研/01-生态分析.md）：

1. **只用 CLI + `--json`**，不直接 import 库 —— 上游明确建议 Agent 走 CLI 以获得
   字节稳定的 JSON 封套（ADR-0015）和一致的 exit code。
2. **ID-pinned**：每一步都把返回的 id 显式传给下一步（`-n <notebook>` / `-a <artifact>`），
   **绝不**依赖 `notebooklm use` 的隐式上下文 —— 并行 Agent 会互相覆盖 context.json。
3. **不猜状态**：`source add` 的返回里没有 status 字段，必须 `source wait` 才算就绪。
4. **只用标准库** —— worker 机器上不要求有 venv。

用法：
    python3 tools/nbjob.py plan    jobs/samples/report-demo.job.json   # 只打印将要执行的命令
    python3 tools/nbjob.py execute jobs/samples/report-demo.job.json   # 真跑（需要凭据 + 出网）
    python3 tools/nbjob.py validate jobs/samples/report-demo.job.json  # 只校验 schema

退出码：0 成功 / 1 校验或执行失败 / 2 超时
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
NB = str(ROOT / "scripts" / "nb")
NB_PS1 = str(ROOT / "scripts" / "nb.ps1")

# 上游 SKILL.md 的「Generation Types」表 + 处理时间表
REPORT_FORMATS = {"briefing-doc", "study-guide", "blog-post", "custom"}
SOURCE_TYPES = {"url", "file", "text", "youtube"}
TIMEOUTS = {
    "source_wait": 600,      # 资料索引 30s–10min
    "report_wait": 900,      # 报告 5–15min
    "audio_wait": 1200,      # 播客 10–20min
    "video_wait": 2700,      # 视频 15–45min
}


# ──────────────────────────────────────────────────────────── 校验

class JobError(ValueError):
    """工单不合法。消息直接给 Agent 看，所以要说明「怎么改」。"""


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise JobError(msg)


def validate(job: dict[str, Any]) -> None:
    """校验工单。刻意严格：宁可现在报错，也不要跑到一半才失败。"""
    _need(isinstance(job, dict), "工单必须是 JSON object")
    _need(bool(job.get("id")), "缺少 id —— 结果要靠它对账")
    _need(job.get("kind") == "research_report",
          f"kind 必须是 'research_report'，收到 {job.get('kind')!r}")

    nb = job.get("notebook") or {}
    _need(isinstance(nb, dict), "notebook 必须是 object")
    _need(bool(nb.get("title")) or bool(nb.get("id")),
          "notebook.title 和 notebook.id 至少要给一个（给 id 表示复用已有笔记本）")

    srcs = job.get("sources") or []
    _need(isinstance(srcs, list) and len(srcs) > 0, "sources 必须是非空数组")
    for i, s in enumerate(srcs):
        _need(isinstance(s, dict), f"sources[{i}] 必须是 object")
        _need(s.get("type") in SOURCE_TYPES,
              f"sources[{i}].type 必须是 {sorted(SOURCE_TYPES)} 之一，收到 {s.get('type')!r}")
        val = s.get("value")
        _need(isinstance(val, str) and val.strip(), f"sources[{i}].value 必须是非空字符串")
        if s["type"] == "file":
            _need(Path(val).is_file(), f"sources[{i}].value 指向的文件不存在: {val}")

    rep = job.get("report") or {}
    _need(isinstance(rep, dict), "report 必须是 object")
    fmt = rep.get("format", "briefing-doc")
    _need(fmt in REPORT_FORMATS, f"report.format 必须是 {sorted(REPORT_FORMATS)} 之一，收到 {fmt!r}")
    if fmt == "custom":
        # 上游坑：--append 在 --format custom 下被静默忽略，prompt 必须走位置参数
        _need(bool(rep.get("prompt")), "report.format=custom 时必须给 report.prompt")

    for i, q in enumerate(job.get("ask") or []):
        _need(isinstance(q, str) and q.strip(), f"ask[{i}] 必须是非空字符串")

    _need(len(srcs) <= 50,
          f"sources 有 {len(srcs)} 条，超过 Standard 档单笔记本 50 源上限（见 docs/quota-limits）")


# ──────────────────────────────────────────────────────────── 命令计划

@dataclass
class Step:
    """一条将要执行的命令，以及怎么从它的 JSON 输出里取值。"""
    label: str
    argv: list[str]
    capture: str | None = None          # 结果里存到哪个键
    jq_path: str | None = None          # 从 stdout JSON 取哪个字段
    ok_codes: tuple[int, ...] = (0,)
    needs: list[str] = field(default_factory=list)   # 依赖的前置 capture 键


def _nb(*args: str) -> list[str]:
    """构造 CLI 调用。Windows 上走 .ps1。"""
    launcher = [NB_PS1] if os.name == "nt" else [NB]
    return launcher + list(args)


def build_plan(job: dict[str, Any]) -> list[Step]:
    """把工单翻译成有序命令。纯函数，不碰网络 —— 所以 `plan` 子命令离线可跑。"""
    nb = job.get("notebook") or {}
    rep = job.get("report") or {}
    steps: list[Step] = []

    # 1) 笔记本：复用已有 or 新建
    if nb.get("id"):
        # 复用：只登记，不发命令（id 已知）
        steps.append(Step(label=f"复用笔记本 {nb['id']}", argv=[], capture="notebook_id"))
    else:
        steps.append(Step(
            label=f"创建笔记本「{nb['title']}」",
            argv=_nb("create", nb["title"], "--json"),
            capture="notebook_id", jq_path="notebook.id",
        ))

    # 2) 加资料（每条一个 source_id）
    for i, s in enumerate(job.get("sources") or []):
        steps.append(Step(
            label=f"添加资料[{i}] {s['type']}: {s['value'][:70]}",
            argv=_nb("source", "add", s["value"], "-n", "{notebook_id}", "--json"),
            capture=f"source_{i}", jq_path="source.id", needs=["notebook_id"],
        ))

    # 3) 等索引（上游：source add 的返回里没有 status，必须 wait）
    for i in range(len(job.get("sources") or [])):
        steps.append(Step(
            label=f"等待资料[{i}] 索引完成",
            argv=_nb("source", "wait", f"{{source_{i}}}", "-n", "{notebook_id}",
                     "--timeout", str(TIMEOUTS["source_wait"])),
            ok_codes=(0,), needs=["notebook_id", f"source_{i}"],
        ))

    # 4) 提问
    for i, q in enumerate(job.get("ask") or []):
        steps.append(Step(
            label=f"提问[{i}] {q[:60]}",
            argv=_nb("ask", q, "-n", "{notebook_id}", "--json"),
            capture=f"answer_{i}", jq_path=".", needs=["notebook_id"],
        ))

    # 5) 生成报告
    fmt = rep.get("format", "briefing-doc")
    gen = ["generate", "report", "--format", fmt, "-n", "{notebook_id}", "--json"]
    prompt = rep.get("prompt")
    if prompt:
        if fmt == "custom":
            gen.insert(2, prompt)        # custom 必须走位置参数
        else:
            gen += ["--append", prompt]  # 内置模板只能用 --append
    if rep.get("language"):
        gen += ["--language", rep["language"]]
    steps.append(Step(
        label=f"生成报告（format={fmt}）",
        argv=_nb(*gen), capture="task_id", jq_path="task_id", needs=["notebook_id"],
    ))

    # 6) 等生成
    steps.append(Step(
        label="等待报告生成完成",
        argv=_nb("artifact", "wait", "{task_id}", "-n", "{notebook_id}",
                 "--timeout", str(TIMEOUTS["report_wait"])),
        ok_codes=(0, 2), needs=["notebook_id", "task_id"],
    ))

    # 7) 下载
    outdir = Path((job.get("output") or {}).get("dir", "out"))
    outfile = str(outdir / f"{job['id']}-report.md")
    steps.append(Step(
        label=f"下载报告 → {outfile}",
        argv=_nb("download", "report", outfile, "-a", "{task_id}", "-n", "{notebook_id}"),
        capture="report_file", needs=["notebook_id", "task_id"],
    ))

    return steps


def render_plan(steps: list[Step]) -> str:
    lines = ["将要执行的命令（plan 模式不碰网络）：", ""]
    for i, s in enumerate(steps, 1):
        lines.append(f"{i:2}. {s.label}")
        if s.argv:
            cmd = " ".join(_shq(a) for a in s.argv)
            lines.append(f"      $ {cmd}")
            if s.capture:
                lines.append(f"      ↳ 取 {s.jq_path or '(整个 JSON)'} 存为 {s.capture}")
            lines.append(f"      ↳ 可接受退出码 {list(s.ok_codes)}")
        else:
            lines.append("      (无命令 —— 直接沿用已知 id)")
    lines.append("")
    lines.append(f"共 {len(steps)} 步。")
    return "\n".join(lines)


# 只有「纯 ASCII 安全字符」才不加引号。中文标点（，、（）：）在 shell 里虽多数无害，
# 但 plan 的输出是要给人和 Agent 照抄的，宁可一律加引号，不给歧义留口子。
_SAFE = re.compile(r"^[A-Za-z0-9_./:=@-]+$")


def _shq(a: str) -> str:
    if _SAFE.match(a):
        return a
    return "'" + a.replace("'", "'\\''") + "'"


# ──────────────────────────────────────────────────────────── 执行

def _dig(obj: Any, path: str) -> Any:
    if path in (".", "", None):
        return obj
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def execute(job: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    """按 plan 逐步执行，把结果累积成一份带 provenance 的 result。"""
    steps = build_plan(job)
    cap: dict[str, Any] = {}
    if (job.get("notebook") or {}).get("id"):
        cap["notebook_id"] = job["notebook"]["id"]

    result: dict[str, Any] = {
        "id": job["id"],
        "kind": job["kind"],
        "status": "running",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "steps": [],
        "captured": {},
        "answers": [],
        "provenance": {
            "provider": "google-notebooklm",
            "via": "notebooklm-py-cli",
            "grounding": "user-provided-sources",
            "ai_generated": True,
            "note": "内容由 Gemini 基于所给来源生成，未经人工核验不得直接当作事实或生产代码。",
        },
    }
    if dry_run:
        result["status"] = "planned"
        result["plan"] = [s.argv for s in steps if s.argv]
        return result

    for i, step in enumerate(steps, 1):
        argv = [a.format(**cap) for a in step.argv]
        rec: dict[str, Any] = {"n": i, "label": step.label, "cmd": " ".join(argv)}

        if not argv or argv == [NB] or argv == [NB_PS1]:
            rec["ok"] = True
            rec["skipped"] = "无命令"
            result["steps"].append(rec)
            continue

        if "{" in " ".join(argv):
            rec["ok"] = False
            rec["error"] = "前置 id 未解析，命令里有未替换的占位符"
            result["steps"].append(rec)
            result["status"] = "failed"
            return result

        print(f"[{i}/{len(steps)}] {step.label}", flush=True)
        proc = subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT))
        rec["exit"] = proc.returncode

        parsed: Any = None
        if proc.stdout.strip():
            try:
                parsed = json.loads(proc.stdout)
            except json.JSONDecodeError:
                parsed = None

        ok = proc.returncode in step.ok_codes
        rec["ok"] = ok
        if not ok:
            rec["stderr"] = (proc.stderr or "")[-800:]
            rec["stdout"] = (proc.stdout or "")[-800:]

        if ok and step.capture and parsed is not None:
            val = _dig(parsed, step.jq_path or ".")
            cap[step.capture] = val
            rec["captured"] = {step.capture: val}
            if step.capture.startswith("answer_"):
                result["answers"].append({
                    "question": (job.get("ask") or [])[int(step.capture.split("_")[1])],
                    "answer": (parsed or {}).get("answer"),
                    "references": (parsed or {}).get("references") or [],
                })

        result["steps"].append(rec)
        if not ok:
            result["status"] = "failed"
            result["failed_at"] = i
            print(f"   ✗ 第 {i} 步失败 (exit={proc.returncode})", file=sys.stderr, flush=True)
            if (proc.stderr or "").strip():
                print(f"   {proc.stderr.strip()[:400]}", file=sys.stderr, flush=True)
            result["captured"] = cap
            return result

    result["status"] = "ok"
    result["captured"] = cap
    result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return result


# ──────────────────────────────────────────────────────────── CLI

def main() -> int:
    ap = argparse.ArgumentParser(description="NotebookLM 工单执行器")
    ap.add_argument("cmd", choices=["validate", "plan", "execute"])
    ap.add_argument("job", help="工单 JSON 路径")
    ap.add_argument("--result", help="把结果 JSON 写到这里（默认 jobs/.local/<id>.result.json）")
    args = ap.parse_args()

    try:
        job = json.loads(Path(args.job).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[X] 读不到工单 {args.job}: {e}", file=sys.stderr)
        return 1

    try:
        validate(job)
    except JobError as e:
        print(f"[X] 工单不合法: {e}", file=sys.stderr)
        return 1

    if args.cmd == "validate":
        print(f"✅ 工单合法: {job['id']} ({job['kind']})")
        return 0

    if args.cmd == "plan":
        print(render_plan(build_plan(job)))
        return 0

    # execute
    if not Path(NB).exists() and os.name != "nt":
        print(f"[X] 找不到 {NB} —— 先 ./scripts/setup.sh", file=sys.stderr)
        return 1

    result = execute(job)

    out = Path(args.result) if args.result else ROOT / "jobs" / ".local" / f"{job['id']}.result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {out}")
    print(f"状态: {result['status']}")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
