#!/usr/bin/env python3
"""核对 tools/nbjob.py 的 KINDS 表与**已安装的** notebooklm CLI 是否仍然吻合。

为什么需要它
------------
KINDS 表里每一项（flag 名、枚举值、有没有 --language、prompt 走位置参数还是选项）
都是照着某个版本的 CLI 手工填的。requirements.txt 钉的是 >=0.8.1,<0.9，
所以 0.8.x 一升级，这张表就可能悄悄过期。

这类漂移的危险在于**它是静默的**：
  - 枚举值变了 → CLI 报 usage error，还算好；
  - flag 改名了 → 同上；
  - 但 prompt 从位置参数改成选项（或反过来）→ 命令照样跑、退出码 0、
    产物也出来了，只是用户的 prompt 被无声丢弃。

quiz 的 prompt_mode 就是这么坏过一次的（写成 "none"，validate 全绿，
prompt 从未到达命令）。所以这里专门核对 Usage 行里的位置参数。

与 tests/run_tests.py 的区别
--------------------------
run_tests.py 完全离线、用 mock，不需要装 CLI，任何时候都能跑。
本脚本**必须**有装好的 CLI，因为它读的是真实 --help 输出。
没装 CLI 时会明确跳过并说明原因，不假装通过。

用法：  python3 tests/check_cli_contract.py
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 每个 kind 对应的 CLI 动词；download 动词单独列（download_format 用）
DL_VERB = {
    "slides": ["download", "slide-deck"],
    "quiz": ["download", "quiz"],
    "flashcards": ["download", "flashcards"],
}

PASS, FAIL, SKIP = 0, 0, 0


def report(ok: bool | None, label: str, detail: str = "") -> None:
    global PASS, FAIL, SKIP
    if ok is None:
        SKIP += 1
        print(f"  - {label}   {detail}")
    elif ok:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label}   {detail}")


def find_cli() -> str | None:
    """找到 notebooklm 可执行文件。优先仓库 venv，其次 PATH。"""
    for cand in (REPO / ".venv" / "bin" / "notebooklm",
                 REPO / ".venv" / "Scripts" / "notebooklm.exe"):
        if cand.exists():
            return str(cand)
    return shutil.which("notebooklm")


def help_text(cli: str, argv: list[str]) -> str:
    p = subprocess.run([cli, *argv, "--help"], capture_output=True, text=True)
    return p.stdout or ""


def upstream_enums(cli: str, argv: list[str]) -> dict[str, set[str]]:
    """从 --help 抓 `--flag [a|b|c]` 的枚举集合。"""
    out: dict[str, set[str]] = {}
    for flag, body in re.findall(r"(--[a-z][a-z0-9-]*)\s+\[([^\]]+)\]", help_text(cli, argv)):
        out[flag] = set(body.split("|"))
    return out


def usage_line(cli: str, argv: list[str]) -> str:
    for ln in help_text(cli, argv).splitlines():
        if ln.strip().startswith("Usage:"):
            return ln.strip()
    return ""


def load_kinds():
    spec = importlib.util.spec_from_file_location("nbjob", REPO / "tools" / "nbjob.py")
    mod = importlib.util.module_from_spec(spec)
    # @dataclass 解析注解需要模块已在 sys.modules 里
    sys.modules["nbjob"] = mod
    spec.loader.exec_module(mod)
    return mod.KINDS, mod.SOURCE_TYPES


def main() -> int:
    cli = find_cli()
    if not cli:
        print("未找到 notebooklm CLI（仓库 .venv 与 PATH 都没有）。")
        print("先跑 ./scripts/setup.sh 安装，再重跑本检查。")
        print("注意：tests/run_tests.py 不需要 CLI，可以先跑那个。")
        return 2

    ver = subprocess.run([cli, "--version"], capture_output=True, text=True).stdout.strip()
    print(f"CLI: {cli}\n版本: {ver}\n")

    KINDS, SOURCE_TYPES = load_kinds()

    # ── 1. 声明的 option 名必须对应真实 flag ───────────────────────
    print("【1】声明的 option 名 → 上游真实 flag")
    for kind, sp in KINDS.items():
        verb = sp.get("gen") or []
        for opt, meta in (sp.get("options") or {}).items():
            if opt == "download_format":
                dv = DL_VERB.get(kind)
                if not dv:
                    report(False, f"{kind}.download_format", "KINDS 里有它但没有对应 download 动词")
                    continue
                report("--format" in help_text(cli, dv),
                       f"{kind}.download_format → {' '.join(dv)} --format")
                continue
            flag = f"--{opt.replace('_', '-')}"
            report(flag in help_text(cli, verb), f"{kind}.{opt} → {flag}",
                   f"上游 {' '.join(verb)} 的 --help 里没有 {flag}")

    # ── 2. 枚举值必须逐字一致 ─────────────────────────────────────
    print("\n【2】枚举值逐字比对")
    for kind, sp in KINDS.items():
        verb = sp.get("gen") or []
        for opt, meta in (sp.get("options") or {}).items():
            mine = set(meta.get("choices") or [])
            if opt == "download_format":
                dv = DL_VERB.get(kind)
                ue = upstream_enums(cli, dv).get("--format") if dv else None
            else:
                ue = upstream_enums(cli, verb).get(f"--{opt.replace('_', '-')}")
            if ue is None:
                report(None, f"{kind}.{opt}", "上游未给出枚举（可能是自由文本），跳过")
            else:
                report(mine == ue, f"{kind}.{opt} = {sorted(mine)}",
                       f"上游={sorted(ue)} 仅我有={sorted(mine - ue)} 仅上游={sorted(ue - mine)}")

    # ── 3. --language 的有无 ──────────────────────────────────────
    print("\n【3】has_language 与上游 --language 的有无")
    for kind, sp in KINDS.items():
        verb = sp.get("gen") or []
        if "has_language" not in sp:
            report(False, f"{kind}.has_language",
                   "未在 KINDS 里显式声明，依赖 spec.get(..., True) 的隐式默认值 —— 请写明")
            continue
        if verb[:1] == ["artifact"]:
            report(None, f"{kind}.has_language", f"{' '.join(verb)} 不是 generate，无此概念")
            continue
        up = "--language " in help_text(cli, verb)
        report(sp["has_language"] == up, f"{kind}.has_language={sp['has_language']}",
               f"上游有 --language = {up}")

    # ── 4. prompt_mode 与 Usage 行的位置参数 ──────────────────────
    # 这是 quiz 那个 bug 的专项检查：prompt_mode 写成 "none" 时 validate 全绿，
    # 但 prompt 从未进入命令，且没有任何报错。
    print("\n【4】prompt_mode 与 Usage 行位置参数")
    for kind, sp in KINDS.items():
        verb = sp.get("gen") or []
        mode = sp.get("prompt_mode")
        u = usage_line(cli, verb)
        if not u:
            report(None, f"{kind}.prompt_mode", f"取不到 {' '.join(verb)} 的 Usage 行")
            continue
        has_desc = bool(re.search(r"\[?DESCRIPTION\]?", u))
        if mode in ("positional", "required-positional", "append-or-positional"):
            report(has_desc, f"{kind}.prompt_mode={mode}",
                   f"Usage 行里没有 DESCRIPTION 位置参数：{u}")
        elif mode == "none":
            report(not has_desc, f"{kind}.prompt_mode=none",
                   f"Usage 行里**有** DESCRIPTION，prompt 会被静默丢弃：{u}")
        elif mode == "instructions":
            report("--instructions" in help_text(cli, verb),
                   f"{kind}.prompt_mode=instructions → --instructions")
        else:
            report(False, f"{kind}.prompt_mode", f"未知取值 {mode!r}")

    # ── 5. SOURCE_TYPES 与 source add --type ─────────────────────
    print("\n【5】SOURCE_TYPES 与 source add --type")
    ue = upstream_enums(cli, ["source", "add"]).get("--type")
    if ue is None:
        report(None, "SOURCE_TYPES", "上游 source add 未给出 --type 枚举")
    else:
        report(SOURCE_TYPES == ue, f"SOURCE_TYPES = {sorted(SOURCE_TYPES)}",
               f"上游={sorted(ue)}")

    print(f"\n{'=' * 56}\n通过 {PASS} / 失败 {FAIL} / 跳过 {SKIP}")
    if FAIL:
        print("\n失败清单见上。判断依据：")
        print("  上游改了 → 更新 KINDS 表（并同步 docs/arena-agent.md §8）；")
        print("  上游没改而这里红了 → 说明 KINDS 表本来就填错了。")
        return 1
    print("KINDS 表与该 CLI 版本完全吻合")
    return 0


if __name__ == "__main__":
    sys.exit(main())
