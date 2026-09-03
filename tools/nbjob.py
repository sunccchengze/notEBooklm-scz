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

SOURCE_TYPES = {"url", "file", "text", "youtube"}

# 上游 SKILL.md 的处理时间表（秒）
TIMEOUTS = {
    "source_wait": 600,      # 资料索引 30s–10min
    "report_wait": 900,      # 报告 / 数据表 5–15min
    "quiz_wait": 900,        # 测验 / 闪卡 5–15min
    "audio_wait": 1200,      # 播客 10–20min
    "slides_wait": 900,      # 幻灯片 5–15min（上游未单列，取生成类中位值）
    "video_wait": 2700,      # 视频 15–45min
}

# 下列每个可选值都对着已安装的 notebooklm-py 0.8.1 的 `--help` 逐个核对过，
# 不是从上游 README 抄的 —— 详见 docs/arena-agent.md 的「CLI 参数核对」。
KINDS: dict[str, dict[str, Any]] = {
    "research_report": {
        "gen": ["generate", "report"],
        "wait": TIMEOUTS["report_wait"],
        "download": ["download", "report"],
        "ext": "md",
        "options": {
            "format": {"choices": {"briefing-doc", "study-guide", "blog-post", "custom"},
                       "default": "briefing-doc"},
        },
        "prompt_mode": "append-or-positional",   # custom 走位置参数，其余走 --append
        "needs_sources_for_gen": False,
    },
    "podcast": {
        "gen": ["generate", "audio"],
        "wait": TIMEOUTS["audio_wait"],
        "download": ["download", "audio"],
        "ext": "m4a",
        "options": {
            "format": {"choices": {"deep-dive", "brief", "critique", "debate"},
                       "default": "deep-dive"},
            "length": {"choices": {"short", "default", "long"}, "default": "default"},
        },
        "prompt_mode": "positional",
        "needs_sources_for_gen": True,     # 上游：source-less 对 audio 会直接报错
    },
    "slides": {
        "gen": ["generate", "slide-deck"],
        "wait": TIMEOUTS["slides_wait"],
        "download": ["download", "slide-deck"],
        "ext": "pdf",
        "options": {
            "format": {"choices": {"detailed", "presenter"}, "default": "detailed"},
            "length": {"choices": {"default", "short"}, "default": "default"},
            "download_format": {"choices": {"pdf", "pptx"}, "default": "pdf"},
        },
        "prompt_mode": "positional",
        "needs_sources_for_gen": False,
    },
    "quiz": {
        "gen": ["generate", "quiz"],
        "wait": TIMEOUTS["quiz_wait"],
        "download": ["download", "quiz"],
        "ext": "md",
        "options": {
            "quantity": {"choices": {"fewer", "standard", "more"}, "default": "standard"},
            "difficulty": {"choices": {"easy", "medium", "hard"}, "default": "medium"},
            "download_format": {"choices": {"json", "markdown", "html"}, "default": "markdown"},
        },
        # quiz 的 Usage 是 `quiz [OPTIONS] [DESCRIPTION]` —— 0.8.1 与 0.8.2 实测都有
        # 位置参数。（此前记成「没有」是读错了 --help，导致 prompt 被静默丢弃。）
        "prompt_mode": "positional",
        "needs_sources_for_gen": True,
        "has_language": False,             # --help 里确实没有 --language（两版核对过）
    },
    "flashcards": {
        "gen": ["generate", "flashcards"],
        "wait": TIMEOUTS["quiz_wait"],
        "download": ["download", "flashcards"],
        "ext": "md",
        "options": {
            "quantity": {"choices": {"fewer", "standard", "more"}, "default": "standard"},
            "difficulty": {"choices": {"easy", "medium", "hard"}, "default": "medium"},
            "download_format": {"choices": {"json", "markdown", "html"}, "default": "markdown"},
        },
        "prompt_mode": "positional",
        "needs_sources_for_gen": True,
        "has_language": False,
    },
    "video": {
        "gen": ["generate", "video"],
        "wait": TIMEOUTS["video_wait"],
        "download": ["download", "video"],
        "ext": "mp4",
        "options": {
            "format": {"choices": {"explainer", "brief", "cinematic", "short"},
                       "default": "explainer"},
            "style": {"choices": {"auto", "custom", "classic", "whiteboard", "kawaii",
                                  "anime", "watercolor", "retro-print", "heritage",
                                  "paper-craft"}, "default": "auto"},
        },
        "prompt_mode": "positional",
        "needs_sources_for_gen": False,
        "has_language": True,
        # --style-prompt：按上游帮助文本应配 --style custom；非 custom 时效果未经验证，
        # 故由 validate 拦下（本地策略，不是上游会拒）
        "extra": "style_prompt",
    },
    "infographic": {
        "gen": ["generate", "infographic"],
        "wait": TIMEOUTS["report_wait"],
        "download": ["download", "infographic"],
        "ext": "png",
        "options": {
            "orientation": {"choices": {"landscape", "portrait", "square"},
                            "default": "landscape"},
            "detail": {"choices": {"concise", "standard", "detailed"}, "default": "standard"},
            "style": {"choices": {"auto", "sketch-note", "professional", "bento-grid",
                                  "editorial", "instructional", "bricks", "clay",
                                  "anime", "kawaii", "scientific"}, "default": "auto"},
        },
        "prompt_mode": "positional",
        "needs_sources_for_gen": False,
        "has_language": True,
    },
    "data_table": {
        "gen": ["generate", "data-table"],
        "wait": TIMEOUTS["report_wait"],
        "download": ["download", "data-table"],
        "ext": "csv",
        "options": {},
        "prompt_mode": "required-positional",   # 上游：data-table 的 description 是必填的
        "needs_sources_for_gen": False,
        "has_language": True,
    },
    "mind_map": {
        "gen": ["generate", "mind-map"],
        "wait": 0,                          # 同步返回，没有 artifact wait 这一步
        "download": ["download", "mind-map"],
        "ext": "json",
        "options": {
            "kind": {"choices": {"interactive", "note-backed"}, "default": "interactive"},
        },
        "prompt_mode": "instructions",      # --instructions，不是位置参数也不是 --prompt-file
        "needs_sources_for_gen": False,
        "has_language": True,
        "capture_task": "note_id",          # 返回 {mind_map, note_id, kind}，没有 task_id
        "skip_wait": True,
    },
    # 不是新产物，而是对**已失败**产物的原地重试。
    # AGENTS.md 明确写了「任务失败时不要重试 generate —— 那会创建重复产物、白烧配额」，
    # 这条 kind 就是那条规矩的工具化替代：ARTIFACT_ID 不变，poll/wait 继续对它有效。
    "retry_artifact": {
        "gen": ["artifact", "retry"],
        "wait": TIMEOUTS["audio_wait"],     # 重试的可能是任何产物，取最长常见值
        "download": [],                     # 由 generate.artifact_kind 对应的 spec 决定
        "ext": "bin",
        "options": {},
        "prompt_mode": "none",
        "needs_sources_for_gen": False,
        "has_language": False,
        "is_retry": True,                   # 无来源、无提问、要 notebook.id 和 artifact_id
    },
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

    kind = job.get("kind")
    _need(kind in KINDS, f"kind 必须是 {sorted(KINDS)} 之一，收到 {kind!r}")
    spec = KINDS[kind]

    nb = job.get("notebook") or {}
    _need(isinstance(nb, dict), "notebook 必须是 object")

    # retry_artifact 是对已有产物的原地重试：必须有 notebook.id，且不接受来源/提问
    if spec.get("is_retry"):
        opts = job.get("generate") or {}
        _need(bool(nb.get("id")),
              "retry_artifact 必须给 notebook.id（重试的是已有笔记本里的产物，不能新建）")
        _need(isinstance(opts.get("artifact_id"), str) and opts["artifact_id"].strip(),
              "retry_artifact 必须给 generate.artifact_id（要重试哪个产物）")
        ak = opts.get("artifact_kind")
        _need(ak in KINDS and not KINDS[ak].get("is_retry"),
              f"generate.artifact_kind 必须是 {sorted(k for k in KINDS if k != 'retry_artifact')} "
              f"之一（决定重试后按哪种产物下载），收到 {ak!r}")
        _need(not (job.get("sources") or []),
              "retry_artifact 不接受 sources —— 重试沿用原产物的资料，不会新增")
        _need(not (job.get("ask") or []),
              "retry_artifact 不接受 ask —— 它只重试产物，不做问答")
        return

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

    # 生成参数：逐个对着 KINDS 里的 choices 校验
    opts = job.get("generate") or {}
    _need(isinstance(opts, dict), "generate 必须是 object")
    for name, meta in spec["options"].items():
        if name.startswith("download_"):
            continue                      # 下载格式在 download 段校验
        if name in opts:
            _need(opts[name] in meta["choices"],
                  f"generate.{name} 必须是 {sorted(meta['choices'])} 之一，收到 {opts[name]!r}")

    dl = job.get("download") or {}
    _need(isinstance(dl, dict), "download 必须是 object")
    if "download_format" in spec["options"] and "format" in dl:
        meta = spec["options"]["download_format"]
        _need(dl["format"] in meta["choices"],
              f"download.format 必须是 {sorted(meta['choices'])} 之一，收到 {dl['format']!r}")

    # prompt 的落点因 kind 而异，校验也要跟着分
    prompt = opts.get("prompt")
    prompt_file = opts.get("prompt_file")
    mode = spec["prompt_mode"]

    _need(not (prompt and prompt_file), "generate.prompt 和 generate.prompt_file 不能同时给")
    if prompt_file:
        _need(mode != "instructions",
              f"{kind} 不支持 generate.prompt_file（它的 CLI 没有 --prompt-file），请用 generate.prompt")
        _need(Path(prompt_file).is_file(), f"generate.prompt_file 不存在: {prompt_file}")

    if mode == "required-positional":
        # data-table 的 DESCRIPTION 是必填的（上游 SKILL.md 记载）
        _need(bool(prompt or prompt_file), f"{kind} 必须给 generate.prompt 或 generate.prompt_file")
    elif kind == "research_report" and opts.get("format") == "custom":
        # 上游坑：--append 在 --format custom 下被静默忽略，prompt 必须走位置参数
        _need(bool(prompt or prompt_file), "generate.format=custom 时必须给 prompt 或 prompt_file")

    # video 的 --style-prompt：上游 CLI 其实**无条件透传**（generate_cmd.py 里对它
    # 没有任何分支/校验，ArtifactsAPI.generate_video 也无条件接收），只有帮助文本
    # 写着 "Use 'custom' with --style-prompt"。所以「非 custom 时会被忽略」是未经验证的
    # 推断 —— 这里拦下来是**本地策略**：宁可让工单显式表达意图，也不要发一个
    # 效果不明的参数出去。
    if spec.get("extra") == "style_prompt" and opts.get("style_prompt"):
        _need(opts.get("style") == "custom",
              "generate.style_prompt 按上游帮助文本应配 generate.style=custom 使用；"
              "其它风格下效果未知（CLI 会照传，但服务端如何处理未经验证），"
              "要用就显式写 style=custom")

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
    # 条件步骤：argv 是「探测」命令，argv2 是「探测未命中时的回退」命令，
    # 具体判定逻辑由 execute() 里同名的 handler 负责。目前只有 resolve_notebook。
    handler: str | None = None
    argv2: list[str] = field(default_factory=list)
    # 下载步骤专用：产物预期落盘路径。ship 子命令靠它做分流，
    # 不能只依赖 CLI 返回的 artifact_file（下载失败/非 JSON 输出时会取不到）。
    artifact_path: str | None = None


def _nb(*args: str) -> list[str]:
    """构造 CLI 调用。Windows 上走 .ps1。"""
    launcher = [NB_PS1] if os.name == "nt" else [NB]
    return launcher + list(args)


def build_plan(job: dict[str, Any]) -> list[Step]:
    """把工单翻译成有序命令。纯函数，不碰网络 —— 所以 `plan` 子命令离线可跑。"""
    nb = job.get("notebook") or {}
    steps: list[Step] = []

    # 1) 笔记本：显式 id → 直接用；只给标题 → 先查同名的，查不到才建
    #    （AGENTS.md 要求「优先复用已有笔记本」—— 配额有限，别动不动新建）
    if nb.get("id"):
        steps.append(Step(label=f"复用笔记本 {nb['id']}", argv=[], capture="notebook_id"))
    elif nb.get("reuse") is False:
        steps.append(Step(
            label=f"创建笔记本「{nb['title']}」（reuse=false，跳过复用检查）",
            argv=_nb("create", nb["title"], "--json"),
            capture="notebook_id", jq_path="notebook.id",
        ))
    else:
        steps.append(Step(
            label=f"解析笔记本「{nb['title']}」（先查同名，查不到再建）",
            argv=_nb("list", "--json"),
            argv2=_nb("create", nb["title"], "--json"),
            capture="notebook_id", jq_path="notebook.id", handler="resolve_notebook",
        ))

    # retry_artifact：没有来源、没有提问，直接原地重试 → 等 → 下载。
    # 单独成一条路径，因为它的骨架和其余九种差别太大，硬塞进共用流程会全是分支。
    if KINDS[job["kind"]].get("is_retry"):
        opts = job.get("generate") or {}
        aid = opts["artifact_id"]
        aspec = KINDS[opts["artifact_kind"]]
        # artifact retry 不带 --wait 时返回 {task_id, status, url, error, error_code}
        # —— 源码 cli/artifact_cmd.py:691 核实，task_id 正好契合既有 capture 模式
        steps.append(Step(
            label=f"原地重试产物 {aid}（不新建，ARTIFACT_ID 不变）",
            argv=_nb("artifact", "retry", aid, "-n", "{notebook_id}", "--json"),
            capture="task_id", jq_path="task_id", needs=["notebook_id"],
        ))
        steps.append(Step(
            label=f"等待重试后的 {opts['artifact_kind']} 完成",
            argv=_nb("artifact", "wait", "{task_id}", "-n", "{notebook_id}",
                     "--timeout", str(TIMEOUTS["audio_wait"])),
            # artifact wait 只有 0/1：超时也是 exit 1（artifact_cmd.py 的
            # `except TimeoutError: … exit_with_code(1)`）。exit 2 只属于 source wait。
            ok_codes=(0,), needs=["notebook_id", "task_id"],
        ))
        steps.append(_download_step(job, opts["artifact_kind"], aspec))
        return steps

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
            # source wait 的退出码契约与 artifact wait **不同**：
            # 0=ready / 1=missing 或处理失败 / 2=timeout（source_cmd.py:970 docstring）。
            # 这里刻意只接受 0 —— 没索引完就去提问或生成，只会拿到空结果白烧配额，
            # 所以超时(2)也要判失败。别顺手改成 (0, 2)。
            ok_codes=(0,), needs=["notebook_id", f"source_{i}"],
        ))

    # 4) 提问
    for i, q in enumerate(job.get("ask") or []):
        steps.append(Step(
            label=f"提问[{i}] {q[:60]}",
            argv=_nb("ask", q, "-n", "{notebook_id}", "--json"),
            capture=f"answer_{i}", jq_path=".", needs=["notebook_id"],
        ))

    # 5) 生成产物 —— 由 kind 决定命令形状
    spec = KINDS[job["kind"]]
    opts = job.get("generate") or {}
    gen = list(spec["gen"])
    shown: list[str] = []

    # 5a) prompt 的落点，每个 kind 不一样（都对着 --help 核对过）
    prompt = opts.get("prompt")
    prompt_file = opts.get("prompt_file")
    mode = spec["prompt_mode"]
    if mode == "positional" and (prompt or prompt_file):
        if prompt:
            gen.append(prompt)
            shown.append("prompt")
        else:
            gen += ["--prompt-file", prompt_file]
            shown.append(f"prompt-file={Path(prompt_file).name}")
    elif mode == "required-positional":
        # data-table 的 DESCRIPTION 是必填的（上游 SKILL.md 记载）
        if prompt:
            gen.append(prompt)
            shown.append("prompt")
        elif prompt_file:
            gen += ["--prompt-file", prompt_file]
            shown.append(f"prompt-file={Path(prompt_file).name}")
    elif mode == "instructions":
        # mind-map 既没有位置参数也没有 --prompt-file，只有 --instructions
        if prompt:
            gen += ["--instructions", prompt]
            shown.append("instructions")
    elif mode == "append-or-positional":
        if opts.get("format") == "custom":
            # 上游坑：--append 在 --format custom 下被静默忽略，必须走位置参数
            if prompt:
                gen.append(prompt)
                shown.append("prompt(custom)")
            elif prompt_file:
                gen += ["--prompt-file", prompt_file]
                shown.append("prompt-file(custom)")
        elif prompt:
            gen += ["--append", prompt]
            shown.append("append")

    # 5b) 枚举选项（format / length / quantity / difficulty / orientation / detail / style / kind）
    for name, meta in spec["options"].items():
        if name.startswith("download_"):
            continue
        val = opts.get(name, meta["default"])
        gen += [f"--{name.replace('_', '-')}", val]
        shown.append(f"{name}={val}")

    # 5c) kind 特有的自由文本参数（目前只有 video 的 --style-prompt）
    if spec.get("extra") and opts.get(spec["extra"]):
        gen += [f"--{spec['extra'].replace('_', '-')}", opts[spec["extra"]]]
        shown.append(spec["extra"])

    # 5d) 语言 —— 按 has_language 判定，不写死 kind 名单
    #     （quiz / flashcards 的 --help 里没有 --language，硬加会被 click 拒）
    if spec.get("has_language", True) and opts.get("language"):
        gen += ["--language", opts["language"]]
        shown.append(f"language={opts['language']}")

    gen += ["-n", "{notebook_id}", "--json"]
    # mind-map 返回 {mind_map, note_id, kind}，其余返回 {task_id, status}
    task_key = spec.get("capture_task", "task_id")
    steps.append(Step(
        label=f"生成 {job['kind']}（{', '.join(shown) or '默认参数'}）",
        argv=_nb(*gen), capture="task_id", jq_path=task_key, needs=["notebook_id"],
    ))

    # 6) 等生成 —— mind-map 同步返回，没有这一步
    if not spec.get("skip_wait"):
        steps.append(Step(
            label=f"等待 {job['kind']} 生成完成",
            argv=_nb("artifact", "wait", "{task_id}", "-n", "{notebook_id}",
                     "--timeout", str(spec["wait"])),
            # 同上：artifact wait 超时是 exit 1，不是 2。
            # 此前写成 (0, 2) 是把 source wait 的退出码契约错套了过来 —— 当下无害
            # （2 永不出现，超时走 1 判失败，行为正确），但一旦上游真返回 2，
            # 就会被当成成功并去下载一个还不存在的产物。
            ok_codes=(0,), needs=["notebook_id", "task_id"],
        ))

    # 7) 下载
    steps.append(_download_step(job, job["kind"], spec))
    return steps


def _download_step(job: dict[str, Any], label_kind: str,
                   spec: dict[str, Any]) -> Step:
    """拼下载命令。普通 kind 和 retry_artifact 共用 —— 两者的下载形状是一样的，
    只有 spec 来源不同（后者取 generate.artifact_kind 对应的 spec）。"""
    dl = job.get("download") or {}
    outdir = Path((job.get("output") or {}).get("dir", "out"))
    ext = dl.get("format", spec["options"].get("download_format", {}).get("default", spec["ext"]))
    # `download quiz/flashcards --format markdown` 是 CLI 的取值名，不是文件扩展名
    if ext == "markdown":
        ext = "md"
    outfile = str(outdir / f"{job['id']}-{label_kind}.{ext}")
    dlcmd = list(spec["download"]) + [outfile]
    if "download_format" in spec["options"]:
        dlcmd += ["--format", dl.get("format", spec["options"]["download_format"]["default"])]
    dlcmd += ["-a", "{task_id}", "-n", "{notebook_id}"]
    return Step(
        label=f"下载 {label_kind} → {outfile}",
        argv=_nb(*dlcmd), capture="artifact_file", needs=["notebook_id", "task_id"],
        artifact_path=outfile,
    )


def render_plan(steps: list[Step]) -> str:
    lines = ["将要执行的命令（plan 模式不碰网络）：", ""]
    for i, s in enumerate(steps, 1):
        lines.append(f"{i:2}. {s.label}")
        if s.handler == "resolve_notebook":
            # 条件步骤：两条命令只会跑其中一条，必须都印出来，否则 plan 会误导人
            lines.append(f"      $ {' '.join(_shq(a) for a in s.argv)}   ← 探测")
            lines.append(f"      $ {' '.join(_shq(a) for a in s.argv2)}  ← 探测未命中才跑")
            lines.append(f"      ↳ 命中同名 → 取该本的 id；未命中 → 取 {s.jq_path} "
                         f"（都存为 {s.capture}）")
        elif s.argv:
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

        # 条件步骤：先探测，命中就复用，未命中才回退到 argv2
        if step.handler == "resolve_notebook":
            wanted = (job.get("notebook") or {}).get("title") or ""
            probe = subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT))
            rec["exit"] = probe.returncode
            if probe.returncode != 0:
                rec["ok"] = False
                rec["error"] = "list 失败，无法判断是否已有同名笔记本"
                rec["stderr"] = (probe.stderr or "")[-800:]
                result["steps"].append(rec)
                result["status"] = "failed"
                result["failed_at"] = i
                return result
            try:
                listing = json.loads(probe.stdout or "{}")
            except json.JSONDecodeError:
                listing = {}
            # 上游 SKILL.md 记载的形状：{"notebooks": [{"id","title",…}], "count": N}
            match = next((n for n in (listing.get("notebooks") or [])
                          if (n.get("title") or "").strip() == wanted.strip()), None)
            if match:
                cap["notebook_id"] = match.get("id")
                rec["ok"] = True
                rec["reused"] = True
                rec["candidates"] = listing.get("count")
                rec["captured"] = {"notebook_id": match.get("id")}
                print(f"   ↺ 复用已有笔记本 {match.get('id')}", flush=True)
                result["steps"].append(rec)
                continue
            # 未命中 → 建新的
            rec["reused"] = False
            rec["candidates"] = listing.get("count")
            print(f"   + 没有同名笔记本（现有 {listing.get('count')} 个），新建", flush=True)
            proc = subprocess.run(
                [a.format(**cap) for a in step.argv2],
                capture_output=True, text=True, cwd=str(ROOT),
            )
            rec["exit"] = proc.returncode
            rec["cmd"] = " ".join(step.argv2)
        else:
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
    # 预期落盘路径：CLI 的 artifact_file 不一定取得到，ship 需要一条稳定的依据
    for s in steps:
        if s.artifact_path:
            result["artifact"] = s.artifact_path
            break
    return result


# ──────────────────────────────────────────────────────── 大产物回传

# 只有这些扩展名 + 体积在阈值内才走 Git。二进制（音频/视频/pptx/pdf/图片）
# 动辄几十 MB，塞进 Git 会把仓库撑爆，一律走 GitHub Release。
_GIT_OK_EXT = {".md", ".json", ".csv", ".txt", ".html"}
_GIT_MAX_BYTES = 2 * 1024 * 1024        # 2 MB
_SHIPPED_DIR = "out/.shipped"


def _repo_slug() -> str | None:
    """从 origin 推出 owner/repo。失败返回 None，让调用方去要 --repo。"""
    p = subprocess.run(["git", "remote", "get-url", "origin"],
                       capture_output=True, text=True, cwd=str(ROOT))
    if p.returncode != 0:
        return None
    url = p.stdout.strip()
    m = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def ship(result_path: str, *, dry_run: bool = False, tag: str | None = None,
         repo: str | None = None) -> dict[str, Any]:
    """把一份 result 里的产物分流回传：小文本走 Git，二进制走 GitHub Release。

    改写 result JSON，加一个 `delivery` 段说明产物去哪了。Agent 读 result 就知道
    是该 `git pull` 拿文件，还是去 Release 下载链接。

    ⚠️ 这条路径**只在有 GitHub 出网的机器上成立**（即 Route B 的 worker）。
    Arena 沙箱里 `uploads.github.com` 被 SNI 封锁（TLS 在 Client Hello 后
    SSL_ERROR_SYSCALL），所以上传分支在沙箱内跑不通 —— 用 `--dry-run` 验判定逻辑。
    """
    rp = Path(result_path)
    result = json.loads(rp.read_text(encoding="utf-8"))

    # 产物路径：优先 CLI 返回的 artifact_file，退到 execute 记下的预期路径
    art = (result.get("captured") or {}).get("artifact_file") or result.get("artifact")
    delivery: dict[str, Any] = {"artifact": art}

    if not art or not Path(art).is_file():
        delivery.update(channel="none",
                        reason="没有产物文件（工单可能失败了，或 kind 不产出文件）")
        result["delivery"] = delivery
        if not dry_run:
            rp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return delivery

    p = Path(art)
    size = p.stat().st_size
    ext = p.suffix.lower()
    delivery["bytes"] = size

    if ext in _GIT_OK_EXT and size <= _GIT_MAX_BYTES:
        delivery.update(channel="git", path=str(p),
                        note="已留在工作树，随下一次 commit 回传（.gitignore 已放行该扩展名）")
        result["delivery"] = delivery
        if not dry_run:
            rp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return delivery

    # 走 Release
    slug = repo or _repo_slug()
    if not slug:
        delivery.update(channel="failed",
                        error="推不出 owner/repo，且没给 --repo；产物仍留在原地")
        result["delivery"] = delivery
        if not dry_run:
            rp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return delivery

    rtag = tag or f"artifact-{result.get('id', 'unknown')}"
    asset = f"{result.get('id', 'unknown')}-{p.name}"
    delivery.update(channel="release", repo=slug, tag=rtag, asset=asset,
                    reason=f"{ext} 或体积 {size} 字节不适合进 Git")

    if dry_run:
        delivery["dry_run"] = True
        delivery["commands"] = [
            f"gh release view {rtag} -R {slug}  # 不存在则 create",
            f"gh release create {rtag} -R {slug} --title '{rtag}' --notes 'NotebookLM 工单产物'",
            f"gh release upload {rtag} {p} -R {slug} --clobber",
        ]
        result["delivery"] = delivery
        return delivery

    # 1) 确保 release 存在
    created_here = False          # 本次新建的 release，上传失败要回滚掉
    view = subprocess.run(["gh", "release", "view", rtag, "-R", slug],
                          capture_output=True, text=True)
    if view.returncode != 0:
        created = subprocess.run(
            ["gh", "release", "create", rtag, "-R", slug,
             "--title", rtag, "--notes", "NotebookLM 工单产物（由 scripts/worker.sh 上传）"],
            capture_output=True, text=True)
        if created.returncode != 0:
            delivery.update(channel="failed",
                            error=f"创建 release 失败: {(created.stderr or '').strip()[-400:]}")
            result["delivery"] = delivery
            rp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return delivery
        created_here = True

    # 2) 上传
    up = subprocess.run(["gh", "release", "upload", rtag, str(p), "-R", slug, "--clobber"],
                        capture_output=True, text=True)
    if up.returncode != 0:
        err = (up.stderr or "").strip()[-400:]
        delivery.update(channel="failed", error=f"上传失败: {err}")
        # 回滚：本次新建却没传上去东西的 release 是空壳，留在仓库上是垃圾
        if created_here:
            rb = subprocess.run(["gh", "release", "delete", rtag, "-R", slug,
                                 "--yes", "--cleanup-tag"],
                                capture_output=True, text=True)
            delivery["rolled_back"] = rb.returncode == 0
            if rb.returncode != 0:
                delivery["rollback_error"] = (rb.stderr or "").strip()[-200:]
        result["delivery"] = delivery
        rp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return delivery

    # 3) 挪出 out/，避免下次 git add out 又想捡它（其实 .gitignore 已挡，双保险）
    shipped = ROOT / _SHIPPED_DIR
    shipped.mkdir(parents=True, exist_ok=True)
    try:
        p.rename(shipped / p.name)
    except OSError:
        pass  # 跨设备等情况，留在原地也不影响回传

    delivery["url"] = f"https://github.com/{slug}/releases/download/{rtag}/{asset}"
    result["delivery"] = delivery
    rp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return delivery


# ──────────────────────────────────────────────────────────── CLI

def main() -> int:
    ap = argparse.ArgumentParser(description="NotebookLM 工单执行器")
    ap.add_argument("cmd", choices=["validate", "plan", "execute", "ship"])
    ap.add_argument("job", help="工单 JSON 路径（ship 时传 result JSON 路径）")
    ap.add_argument("--result", help="把结果 JSON 写到这里（默认 jobs/.local/<id>.result.json）")
    ap.add_argument("--dry-run", action="store_true", help="ship：只判定分流，不真的上传")
    ap.add_argument("--tag", help="ship：GitHub Release 的 tag（默认 artifact-<工单 id>）")
    ap.add_argument("--repo", help="ship：owner/repo（默认从 origin 推）")
    args = ap.parse_args()

    # ship 不碰工单校验 —— 它作用于 execute 之后的 result
    if args.cmd == "ship":
        try:
            d = ship(args.job, dry_run=args.dry_run, tag=args.tag, repo=args.repo)
        except Exception as e:
            print(f"[X] 回传失败: {e}", file=sys.stderr)
            return 1
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0 if d.get("channel") != "failed" else 1

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
