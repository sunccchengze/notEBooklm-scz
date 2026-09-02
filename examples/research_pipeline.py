"""
研究流水线：一批 URL -> 一个笔记本 -> 中文播客 + 学习指南 -> 下载到 out/

用法:
    ./scripts/py examples/research_pipeline.py "笔记本标题" url1 url2 ...

不带参数时使用下面的默认配置。
"""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("NOTEBOOKLM_HOME", str(ROOT / ".notebooklm"))

from notebooklm import NotebookLMClient, ReportFormat  # noqa: E402

OUT = ROOT / "out"

DEFAULT_TITLE = "AI 周报研究"
DEFAULT_URLS = ["https://github.com/teng-lin/notebooklm-py"]

LANG = "zh"  # 生成内容的语言；改成 "en" 出英文


async def main() -> None:
    args = sys.argv[1:]
    title = args[0] if args else DEFAULT_TITLE
    urls = args[1:] if len(args) > 1 else DEFAULT_URLS

    OUT.mkdir(exist_ok=True)

    async with NotebookLMClient.from_storage() as client:
        nb = await client.notebooks.create(title)
        print(f"📓 笔记本: {nb.title} ({nb.id})")

        ids = []
        for url in urls:
            src = await client.sources.add_url(nb.id, url)
            ids.append(src.id)
            print(f"  + {url}")

        await client.sources.wait_all_until_ready(nb.id, ids, timeout=600)
        print("✅ 资料就绪\n")

        # 1) 音频概览（播客）
        print("🎙️  生成音频概览中，通常要几分钟…")
        task = await client.artifacts.generate_audio(nb.id, language=LANG)
        await client.artifacts.wait_for_completion(nb.id, task.task_id, timeout=1800)
        mp3 = OUT / f"{title}.mp3"
        await client.artifacts.download_audio(nb.id, str(mp3))
        print(f"   ⬇️  {mp3}")

        # 2) 学习指南报告
        print("📄 生成学习指南中…")
        task = await client.artifacts.generate_report(
            nb.id, report_format=ReportFormat.STUDY_GUIDE, language=LANG
        )
        await client.artifacts.wait_for_completion(nb.id, task.task_id, timeout=1800)
        md = OUT / f"{title}-学习指南.md"
        await client.artifacts.download_report(nb.id, str(md))
        print(f"   ⬇️  {md}")

        print(f"\n🔗 https://notebooklm.google.com/notebook/{nb.id}")


if __name__ == "__main__":
    asyncio.run(main())
