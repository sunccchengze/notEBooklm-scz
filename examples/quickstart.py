"""
notebooklm-py 快速上手：创建笔记本 -> 加资料 -> 提问 -> 把回答存成笔记

运行:
    ./scripts/py examples/quickstart.py

注意: notebooklm-py 的 Python API 是异步的 (async/await)。
"""

import asyncio
import os
from pathlib import Path

os.environ.setdefault(
    "NOTEBOOKLM_HOME", str(Path(__file__).resolve().parent.parent / ".notebooklm")
)

from notebooklm import NotebookLMClient  # noqa: E402

TITLE = "我的第一个自动化笔记本"

SOURCES = [
    "https://github.com/teng-lin/notebooklm-py",
]

QUESTIONS = [
    "这个项目最核心的能力是什么？请分点说明。",
    "它有哪些网页版 UI 做不到的功能？",
]


async def main() -> None:
    async with NotebookLMClient.from_storage() as client:
        nb = await client.notebooks.create(TITLE)
        print(f"📓 已创建笔记本: {nb.title}  (id={nb.id})")

        source_ids = []
        for url in SOURCES:
            src = await client.sources.add_url(nb.id, url)
            source_ids.append(src.id)
            print(f"  + 已添加资料: {getattr(src, 'title', None) or url}")

        if source_ids:
            await client.sources.wait_all_until_ready(nb.id, source_ids, timeout=300)
            print("✅ 资料索引完成\n")

        for q in QUESTIONS:
            print(f"❓ {q}")
            result = await client.chat.ask(nb.id, q)
            text = getattr(result, "answer", None) or str(result)
            print(f"💡 {text}\n")
            await client.notes.create(nb.id, title=q[:40], content=text)

        print("📝 回答已保存为笔记。")
        print(f"🔗 打开: https://notebooklm.google.com/notebook/{nb.id}")


if __name__ == "__main__":
    asyncio.run(main())
