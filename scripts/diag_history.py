"""直接问 Google 要历史对话，打印原始返回，判断到底是哪一层没数据。"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("NOTEBOOKLM_HOME", str(ROOT / ".notebooklm"))

from notebooklm import NotebookLMClient  # noqa: E402


async def main() -> None:
    async with NotebookLMClient.from_storage() as c:
        try:
            email = await c.get_account_email()
            print(f"  账号: {email}")
        except Exception as e:
            print(f"  账号读取失败: {e}")

        nbs = await c.notebooks.list()
        print(f"  笔记本: {len(nbs)} 个\n")

        # 命令行给了标题关键字就只查它，否则查前 5 个
        key = sys.argv[1].lower() if len(sys.argv) > 1 else None
        targets = [n for n in nbs if key in (n.title or "").lower()] if key else nbs[:5]

        for nb in targets:
            print(f"  --- {nb.title}  ({nb.id}) ---")
            try:
                conv = await c.chat.get_conversation_id(nb.id)
                print(f"      conversation_id = {conv!r}")
            except Exception as e:
                print(f"      取 conversation_id 出错: {type(e).__name__}: {e}")
                continue

            if not conv:
                print("      -> Google 说这个笔记本没有会话记录")
                print("         （网页版的对话若未产生持久会话，API 就读不到）")
                continue

            try:
                raw = await c.chat.get_conversation_turns(nb.id, conv, limit=100)
                print(f"      原始 turns 类型 = {type(raw).__name__}")
                print(f"      原始 turns 预览 = {repr(raw)[:300]}")
            except Exception as e:
                print(f"      取 turns 出错: {type(e).__name__}: {e}")

            try:
                hist = await c.chat.get_history(nb.id, limit=100, conversation_id=conv)
                print(f"      解析出 {len(hist)} 轮问答")
                for q, a in hist[:3]:
                    print(f"        Q: {str(q)[:60]}")
                    print(f"        A: {str(a)[:60]}")
            except Exception as e:
                print(f"      get_history 出错: {type(e).__name__}: {e}")
            print()


asyncio.run(main())
