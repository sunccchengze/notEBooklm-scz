#!/usr/bin/env python3
"""离线回归套件：用 tests/mock_nb.sh 冒充 CLI，跑通十种 kind 的完整链路。

为什么需要它
------------
早前所有验证都靠 /tmp 里的一次性 mock，download 分支只 echo 裸字符串
`downloaded`（不是 JSON）。结果 nbjob.py 的 download 步骤漏设 jq_path、
把整个信封 dict 存进 captured.artifact_file 这个 bug 潜伏了多轮，
直到 ship 真的 Path(dict) 崩掉才暴露。

根因不是那个 bug，而是**测试基座本身不真实**。所以这里做两件事：
  1. mock 的每个分支都照抄上游 0.8.2 源码里的信封构造（见 mock_nb.sh 头部注释）；
  2. 断言直指那些曾经出问题的形状契约，而不是只看"退出码为 0"。

用法：  python3 tests/run_tests.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MOCK = REPO / "tests" / "mock_nb.sh"
# 注意：必须跑**沙箱里那份** nbjob.py。nbjob.py 用
# ROOT = Path(__file__).resolve().parent.parent 定位 scripts/nb，
# 跑仓库本体那份会让 ROOT 指向真仓库、NB 解析到真 scripts/nb，mock 就白装了。

PASS, FAIL = 0, 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    """记录一条断言。cond 为假时记为失败但不中断，好一次看到全部问题。"""
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}  {detail}".strip())
        print(f"  ✗ {name}   {detail}")
    return cond


def run(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """跑**沙箱里那份** nbjob.py，这样它的 ROOT/NB 才指向沙箱的 scripts/nb（mock）。"""
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(cwd / "tools" / "nbjob.py"), *args],
        cwd=str(cwd), capture_output=True, text=True, env=e,
    )


def load_result(cwd: Path, path: str) -> dict | None:
    """读结果文件；不存在时返回 None（负面测试里 execute 可能提前失败）。"""
    p = cwd / path
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def make_sandbox() -> Path:
    """把仓库拷到临时目录，并把 mock 装成 scripts/nb。

    docs/ 必须一起拷：样例工单把 docs/调研/*.md 当文件资料，
    validate() 会检查文件是否真的存在。
    """
    tmp = Path(tempfile.mkdtemp(prefix="nbtest-"))
    for item in ("tools", "scripts", "jobs", "prompts", "tests", "docs"):
        src = REPO / item
        if src.exists():
            shutil.copytree(src, tmp / item, symlinks=True,
                            ignore=shutil.ignore_patterns(".venv", "__pycache__"))
    for d in ("out", "jobs/pending", "jobs/running", "jobs/done"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    shutil.copy2(MOCK, tmp / "scripts" / "nb")
    os.chmod(tmp / "scripts" / "nb", 0o755)
    return tmp


SAMPLES = [
    ("report-demo", "research_report", "git"),
    ("podcast-demo", "podcast", "release"),
    ("slides-demo", "slides", "release"),
    ("quiz-demo", "quiz", "git"),
    ("flashcards-demo", "flashcards", "git"),
    ("video-demo", "video", "release"),
    ("infographic-demo", "infographic", "release"),
    ("datatable-demo", "data_table", "git"),
    ("mindmap-demo", "mind_map", "git"),
    # retry 的通道跟着 generate.artifact_kind 走；retry-demo 是 podcast → .m4a → release
    ("retry-demo", "retry_artifact", "release"),
]


def main() -> int:
    if not MOCK.exists():
        print(f"缺少 {MOCK}", file=sys.stderr)
        return 2

    tmp = make_sandbox()
    print(f"沙箱: {tmp}\n")

    # ── 1. 十种 validate + plan ────────────────────────────────────
    print("【1】十种 validate + plan")
    for sid, _kind, _ch in SAMPLES:
        j = f"jobs/samples/{sid}.job.json"
        v = run(tmp, "validate", j)
        p = run(tmp, "plan", j)
        check(f"{sid} validate+plan", v.returncode == 0 and p.returncode == 0,
              (v.stderr or p.stderr or "")[-160:])

    # ── 2. 十种 execute，断言形状契约 ──────────────────────────────
    print("\n【2】十种 execute —— 断言 captured 的形状契约")
    for sid, kind, _ch in SAMPLES:
        j = f"jobs/samples/{sid}.job.json"
        rp = f"jobs/done/{sid}.result.json"
        e = run(tmp, "execute", j, "--result", rp)
        if e.returncode != 0:
            check(f"{sid} execute 退出码 0", False, (e.stderr or e.stdout)[-200:])
            continue
        d = load_result(tmp, rp)

        ok = check(f"{sid} status=ok 且全步 ok",
                   d["status"] == "ok" and all(s.get("ok") for s in d["steps"]),
                   f"status={d['status']} failed_at={d.get('failed_at')}")
        if not ok:
            continue

        # 核心契约：artifact_file 必须是字符串路径。
        # 曾因 download 步骤漏设 jq_path 而存进整个信封 dict → ship 崩。
        af = (d.get("captured") or {}).get("artifact_file")
        check(f"{sid} captured.artifact_file 是 str",
              isinstance(af, str) and bool(af),
              f"实际是 {type(af).__name__}: {repr(af)[:70]}")

        # notebook_id 必须是裸 id 字符串，不能是整个 create 信封 dict。
        # 注意：retry 工单不建本、原样透传工单里的 notebook.id（样例是占位值），
        # 所以只断言"是非空字符串"，不断言前缀。
        nbid = (d.get("captured") or {}).get("notebook_id")
        check(f"{sid} captured.notebook_id 是裸字符串",
              isinstance(nbid, str) and bool(nbid),
              f"实际是 {type(nbid).__name__}: {repr(nbid)[:70]}")

        # 除 mind_map（同步返回）外，都该捕获到 task_id
        if kind != "mind_map":
            tid = (d.get("captured") or {}).get("task_id")
            check(f"{sid} captured.task_id 是裸 id",
                  isinstance(tid, str) and tid.startswith("task-"),
                  f"实际是 {type(tid).__name__}: {repr(tid)[:70]}")

        # 预期落盘路径必须记进 result，ship 靠它兜底
        check(f"{sid} result.artifact 有值", bool(d.get("artifact")),
              f"artifact={d.get('artifact')!r}")

    # ── 3. 十种 ship --dry-run，断言不崩且通道正确 ─────────────────
    print("\n【3】十种 ship --dry-run —— 断言不崩且通道正确")
    for sid, _kind, ch in SAMPLES:
        rp = f"jobs/done/{sid}.result.json"
        if not (tmp / rp).exists():
            continue
        # --repo 必给：沙箱临时目录没有 .git，_repo_slug() 推不出 owner/repo
        s = run(tmp, "ship", rp, "--dry-run", "--repo", "octo/demo")
        raw = s.stdout
        if "{" not in raw:
            check(f"{sid} ship 输出 JSON", False, (s.stderr or raw)[-160:])
            continue
        try:
            d = json.loads(raw[raw.index("{"):])
        except json.JSONDecodeError as exc:
            check(f"{sid} ship JSON 可解析", False, str(exc))
            continue

        # 曾经的崩点：Path(dict) → "expected str, bytes or os.PathLike object"
        check(f"{sid} ship 未崩", s.returncode == 0, (s.stderr or "")[-160:])
        check(f"{sid} ship channel 正确", d.get("channel") == ch,
              f"期望 {ch}，实际 {d.get('channel')}")

    # ── 4. 负面测试：artifact wait 退出码契约 ──────────────────────
    print("\n【4】artifact wait 返回 2 时必须判失败（ok_codes 契约）")
    e = run(tmp, "execute", "jobs/samples/podcast-demo.job.json",
            "--result", "jobs/done/neg1.result.json",
            env={"MOCK_ARTIFACT_WAIT_EXIT": "2"})
    d = load_result(tmp, "jobs/done/neg1.result.json")
    if d is None:
        check("exit 2 → 结果文件已写出", False, (e.stderr or e.stdout)[-200:])
    else:
        check("exit 2 → status=failed", d["status"] == "failed", f"实际 {d['status']}")
        check("exit 2 → 下载步骤未执行",
              not any("下载" in s["label"] for s in d["steps"]),
              "竟然执行了下载 —— 会去取一个不存在的产物")

    # ── 5. 冲突改名：ship 必须信任 CLI 报的真实路径 ────────────────
    print("\n【5】CLI 冲突改名时 ship 取真实落盘路径")
    e = run(tmp, "execute", "jobs/samples/report-demo.job.json",
            "--result", "jobs/done/neg2.result.json",
            env={"MOCK_CONFLICT_RENAME": "1"})
    d = load_result(tmp, "jobs/done/neg2.result.json")
    if d is None:
        check("冲突改名 → 结果文件已写出", False, (e.stderr or e.stdout)[-200:])
    else:
        af = (d.get("captured") or {}).get("artifact_file")
        expected_path = d.get("artifact")
        check("CLI 报的路径 ≠ 请求路径（改名已生效）",
              af != expected_path, f"两者都是 {af!r}")
        s = run(tmp, "ship", "jobs/done/neg2.result.json", "--dry-run",
                "--repo", "octo/demo")
        try:
            sd = json.loads(s.stdout[s.stdout.index("{"):])
            check("ship 用的是 CLI 报的真实路径",
                  sd.get("artifact") == af,
                  f"ship 取到 {sd.get('artifact')!r}，应为 {af!r}")
        except (ValueError, json.JSONDecodeError) as exc:
            check("ship 输出可解析", False, str(exc))

    # ── 6. 笔记本复用命中 ─────────────────────────────────────────
    print("\n【6】同名笔记本命中时复用而非新建")
    title = json.loads((tmp / "jobs/samples/report-demo.job.json")
                       .read_text(encoding="utf-8"))["notebook"]["title"]
    e = run(tmp, "execute", "jobs/samples/report-demo.job.json",
            "--result", "jobs/done/neg3.result.json",
            env={"MOCK_LIST_NOTEBOOKS": "1", "MOCK_WANT_TITLE": title})
    d = load_result(tmp, "jobs/done/neg3.result.json")
    if d is None:
        check("复用测试 → 结果文件已写出", False, (e.stderr or e.stdout)[-200:])
    else:
        step1 = d["steps"][0]
        check("reused=true", step1.get("reused") is True, f"reused={step1.get('reused')}")
        check("复用到 nb-EXIST",
              (d.get("captured") or {}).get("notebook_id") == "nb-EXIST-0001",
              f"实际 {(d.get('captured') or {}).get('notebook_id')!r}")

    # ── 7. quiz 的 prompt 必须真的进了命令（prompt_mode 回归）──────
    print("\n【7】quiz 的 prompt 必须出现在 generate 命令里")
    p = run(tmp, "plan", "jobs/samples/quiz-demo.job.json")
    job = json.loads((tmp / "jobs/samples/quiz-demo.job.json").read_text(encoding="utf-8"))
    prompt = (job.get("generate") or {}).get("prompt") or ""
    gen_lines = [ln for ln in p.stdout.splitlines() if "generate quiz" in ln]
    check("quiz prompt 出现在 generate quiz 命令中",
          bool(prompt) and any(prompt in ln for ln in gen_lines),
          f"prompt={prompt[:30]!r}；命令行={gen_lines[:1]}")

    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'='*56}\n断言：通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("\n失败清单：")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
