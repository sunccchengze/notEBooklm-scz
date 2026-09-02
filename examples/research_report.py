"""第一个端到端链路：一批来源 → 一个笔记本 → 提问 → 简报，全部落到 out/。

这是「路线 A（直连）」的 Python API 版本，纯文本、无二进制产物，是最快能跑通的链路。

    ./scripts/py examples/research_report.py "笔记本标题" url1 url2 ...
    ./scripts/py examples/research_report.py            # 用下面的默认配置

Agent 侧请优先用 `tools/nbjob.py`（走 CLI + --json，ID-pinned、有工单和结果留痕）；
本文件的价值是让人能一眼看懂整条链路在库层面是怎么串起来的。

签名均已对着已安装的 notebooklm-py 0.8.1 核对过（见 docs/arena-agent.md 的「API 核对」）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("NOTEBOOKLM_HOME", str(ROOT / ".notebooklm"))

from notebooklm import NotebookLMClient, ReportFormat  # noqa: E402
from notebooklm.exceptions import (  # noqa: E402
    SourceNotFoundError,
    SourceProcessingError,
    SourceTimeoutError,
)

OUT = ROOT / "out"

DEFAULT_TITLE = "调研：NotebookLM 开源生态"
DEFAULT_URLS = ["https://github.com/teng-lin/notebooklm-py"]

LANG = "zh_Hans"          # 输出语言；改 "en" 出英文
SOURCE_TIMEOUT = 600.0    # 上游时间表：资料索引 30s–10min
REPORT_TIMEOUT = 900.0    # 上游时间表：报告 5–15min

QUESTIONS = [
    "这批资料的核心结论是什么？完全基于已上传的文档内容回答，不要搜索网络。",
    "列出文档中记载的、会导致自动化失败的具体机制，每条说明触发条件。完全基于文档回答。",
]


async def main() -> None:
    args = sys.argv[1:]
    title = args[0] if args else DEFAULT_TITLE
    urls = args[1:] if len(args) > 1 else DEFAULT_URLS

    OUT.mkdir(exist_ok=True)

    async with NotebookLMClient.from_storage() as client:
        # 1) 笔记本
        nb = await client.notebooks.create(title)
        print(f"📓 笔记本: {nb.title}  (id={nb.id})")

        # 2) 加来源
        ids: list[str] = []
        for url in urls:
            src = await client.sources.add_url(nb.id, url)
            ids.append(src.id)
            print(f"  + {getattr(src, 'title', None) or url}  ({src.id})")
        if not ids:
            print("[X] 没有来源可加，退出", file=sys.stderr)
            return

        # 3) 等索引 —— 注意返回值是 list[Source | 异常对象]，不是清一色 Source
        settled = await client.sources.wait_all_until_ready(nb.id, ids, timeout=SOURCE_TIMEOUT)
        bad = [r for r in settled
               if isinstance(r, (SourceNotFoundError, SourceProcessingError, SourceTimeoutError))]
        for r in bad:
            print(f"  ⚠️  {type(r).__name__}: {r}", file=sys.stderr)
        ready = [r for r in settled if not isinstance(r, BaseException)]
        if not ready:
            print("[X] 没有任何来源进入 ready，别对着空笔记本提问", file=sys.stderr)
            return
        print(f"✅ {len(ready)}/{len(ids)} 个来源就绪\n")

        # 4) 提问（同一 conversation 内后轮受益于前轮上下文）
        conversation_id: str | None = None
        for q in QUESTIONS:
            print(f"❓ {q}")
            res = await client.chat.ask(nb.id, q, conversation_id=conversation_id)
            conversation_id = getattr(res, "conversation_id", conversation_id)
            print(f"💡 {getattr(res, 'answer', res)}\n")

        # 5) 简报
        print("📄 生成简报中（5–15 分钟）…")
        task = await client.artifacts.generate_report(
            nb.id, report_format=ReportFormat.BRIEFING_DOC, language=LANG
        )
        done = await client.artifacts.wait_for_completion(
            nb.id, task.task_id, timeout=REPORT_TIMEOUT
        )
        md = OUT / f"{title}-简报.md"
        await client.artifacts.download_report(nb.id, str(md))
        print(f"   ⬇️  {md}  (status={getattr(done, 'status', '?')})")

        # 6) 留痕：AI 生成内容必须标注来源与生成时间
        stamp = OUT / f"{title}-简报.provenance.md"
        stamp.write_text(
            f"# 产物溯源\n\n"
            f"- 笔记本: {nb.id}\n"
            f"- 来源: {', '.join(ids)}\n"
            f"- 生成方式: NotebookLM / Gemini，基于上述来源\n"
            f"- 性质: **AI 生成内容**，未经人工核验不得直接当作事实或生产代码\n"
            f"- 网页查看: https://notebooklm.google.com/notebook/{nb.id}\n",
            encoding="utf-8",
        )
        print(f"   🏷️  {stamp}")


if __name__ == "__main__":
    asyncio.run(main())
