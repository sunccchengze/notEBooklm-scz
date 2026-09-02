"""
NotebookLM 桌面版 —— 本地聊天界面后端

启动:
    Windows:  双击 启动.bat   或   .\\scripts\\app.ps1
    Linux:    ./scripts/py app/server.py
"""

from __future__ import annotations

import asyncio
import os
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("NOTEBOOKLM_HOME", str(ROOT / ".notebooklm"))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from notebooklm import (  # noqa: E402
    ChatGoal,
    ChatResponseLength,
    NotebookLMClient,
    ReportFormat,
)

STATIC = Path(__file__).parent / "static"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

# ---------------------------------------------------------------- 客户端

_client: NotebookLMClient | None = None
_lock = asyncio.Lock()


async def get_client() -> NotebookLMClient:
    global _client
    async with _lock:
        if _client is None:
            _client = await NotebookLMClient.from_storage().__aenter__()
        return _client


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if _client is not None:
        try:
            await _client.close()
        except Exception:
            pass


app = FastAPI(title="NotebookLM 桌面版", lifespan=lifespan)


@app.exception_handler(Exception)
async def on_error(request, exc: Exception):
    msg = str(exc) or exc.__class__.__name__
    low = msg.lower()
    hint = ""
    if any(k in low for k in ("auth", "cookie", "sid", "storage file")):
        hint = "认证已过期，请在终端运行： scripts\\nb.ps1 login"
    elif "rate" in low or "429" in low:
        hint = "请求过于频繁，请稍等片刻再试"
    return JSONResponse(500, content={"error": msg, "hint": hint})


# ---------------------------------------------------------------- 模型

class AskBody(BaseModel):
    notebook_id: str
    question: str
    conversation_id: str | None = None


class CreateBody(BaseModel):
    title: str


class AddUrlBody(BaseModel):
    notebook_id: str
    url: str


class AddTextBody(BaseModel):
    notebook_id: str
    title: str
    content: str


class GenerateBody(BaseModel):
    notebook_id: str
    kind: str
    instructions: str | None = None
    language: str = "zh"


class RenameBody(BaseModel):
    notebook_id: str
    title: str


class NoteBody(BaseModel):
    notebook_id: str
    title: str
    content: str


class ResearchBody(BaseModel):
    notebook_id: str
    query: str
    mode: str = "fast"       # fast | deep
    source: str = "web"      # web | drive


class ShareBody(BaseModel):
    notebook_id: str
    public: bool


class RenameBody(BaseModel):
    notebook_id: str
    target_id: str
    name: str


class EmojiBody(BaseModel):
    notebook_id: str
    emoji: str


class LabelBody(BaseModel):
    notebook_id: str
    name: str = ""
    emoji: str = ""
    label_id: str | None = None
    source_ids: list[str] = []


class CollectionBody(BaseModel):
    name: str = ""
    collection_id: str | None = None
    notebook_ids: list[str] = []


class NoteUpdateBody(BaseModel):
    notebook_id: str
    note_id: str
    title: str
    content: str


class ChatConfigBody(BaseModel):
    notebook_id: str
    length: str = "default"   # default | longer | shorter
    goal: str = "default"     # default | learning_guide | custom
    custom_prompt: str | None = None


# ---------------------------------------------------------------- 认证

@app.get("/api/auth")
async def api_auth() -> dict[str, Any]:
    try:
        client = await get_client()
        return {"ok": True, "email": await client.get_account_email() or "已登录"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------- 笔记本

@app.get("/api/notebooks")
async def api_notebooks() -> list[dict[str, Any]]:
    client = await get_client()
    return [
        {
            "id": b.id,
            "title": b.title or "(未命名)",
            "emoji": getattr(b, "emoji", "") or "◇",
            "sources": getattr(b, "sources_count", 0),
            "created": str(getattr(b, "created_at", "") or "")[:10],
        }
        for b in await client.notebooks.list()
    ]


@app.post("/api/notebooks")
async def api_create(body: CreateBody) -> dict[str, Any]:
    client = await get_client()
    nb = await client.notebooks.create(body.title)
    return {"id": nb.id, "title": nb.title}


@app.post("/api/notebooks/rename")
async def api_rename(body: RenameBody) -> dict[str, Any]:
    client = await get_client()
    await client.notebooks.rename(body.notebook_id, body.title)
    return {"ok": True}


@app.delete("/api/notebooks/{notebook_id}")
async def api_delete(notebook_id: str) -> dict[str, Any]:
    client = await get_client()
    await client.notebooks.delete(notebook_id)
    return {"ok": True}


@app.get("/api/summary/{notebook_id}")
async def api_summary(notebook_id: str) -> dict[str, Any]:
    client = await get_client()
    return {"summary": await client.notebooks.get_summary(notebook_id)}


# ---------------------------------------------------------------- 资料

@app.get("/api/sources/{notebook_id}")
async def api_sources(notebook_id: str) -> list[dict[str, Any]]:
    client = await get_client()
    out = []
    for s in await client.sources.list(notebook_id):
        status = "ready"
        if getattr(s, "is_processing", False):
            status = "processing"
        elif getattr(s, "is_error", False):
            status = "error"
        out.append(
            {
                "id": s.id,
                "title": s.title or "(无标题)",
                "url": getattr(s, "url", "") or "",
                "status": status,
                "words": getattr(s, "word_count", 0) or 0,
            }
        )
    return out


@app.post("/api/sources/url")
async def api_add_url(body: AddUrlBody) -> dict[str, Any]:
    client = await get_client()
    s = await client.sources.add_url(body.notebook_id, body.url)
    return {"id": s.id, "title": s.title or body.url}


@app.post("/api/sources/text")
async def api_add_text(body: AddTextBody) -> dict[str, Any]:
    client = await get_client()
    s = await client.sources.add_text(body.notebook_id, body.content, title=body.title)
    return {"id": s.id, "title": s.title or body.title}


@app.post("/api/sources/file")
async def api_add_file(notebook_id: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
    client = await get_client()
    tmp = OUT / f"_up_{file.filename}"
    tmp.write_bytes(await file.read())
    try:
        s = await client.sources.add_file(notebook_id, str(tmp))
        return {"id": s.id, "title": s.title or file.filename}
    finally:
        tmp.unlink(missing_ok=True)


@app.delete("/api/sources/{notebook_id}/{source_id}")
async def api_del_source(notebook_id: str, source_id: str) -> dict[str, Any]:
    client = await get_client()
    await client.sources.delete(notebook_id, source_id)
    return {"ok": True}


@app.get("/api/source-text/{notebook_id}/{source_id}")
async def api_source_text(notebook_id: str, source_id: str) -> dict[str, Any]:
    client = await get_client()
    txt = await client.sources.get_fulltext(notebook_id, source_id)
    return {"text": (txt or "")[:20000]}


# ---------------------------------------------------------------- 深度研究

_RESEARCH: dict[str, dict[str, Any]] = {}


@app.post("/api/research")
async def api_research(body: ResearchBody) -> dict[str, Any]:
    client = await get_client()
    start = await client.research.start(
        body.notebook_id, body.query, source=body.source, mode=body.mode
    )
    tid = start.task_id
    _RESEARCH[tid] = {"state": "running", "query": body.query, "sources": []}

    async def _run() -> None:
        try:
            task = await client.research.wait_for_completion(
                body.notebook_id, tid, timeout=1800
            )
            _RESEARCH[tid]["sources"] = [
                {"url": s.url, "title": s.title, "hint": getattr(s, "hint", "")}
                for s in (task.sources or [])
            ]
            _RESEARCH[tid]["report"] = getattr(task, "report", "") or ""
            _RESEARCH[tid]["state"] = "done"
        except Exception as e:  # noqa: BLE001
            _RESEARCH[tid]["state"] = "error"
            _RESEARCH[tid]["error"] = str(e)

    asyncio.create_task(_run())
    return {"task_id": tid}


@app.get("/api/research/{task_id}")
async def api_research_status(task_id: str) -> dict[str, Any]:
    return _RESEARCH.get(task_id, {"state": "unknown"})


class ImportBody(BaseModel):
    notebook_id: str
    task_id: str
    urls: list[str]


@app.post("/api/research/import")
async def api_research_import(body: ImportBody) -> dict[str, Any]:
    client = await get_client()
    cached = _RESEARCH.get(body.task_id, {})
    picked = [s for s in cached.get("sources", []) if s["url"] in set(body.urls)]
    from notebooklm import ResearchSource

    objs = [ResearchSource.from_public_dict(s) for s in picked]
    added = await client.research.import_sources(body.notebook_id, body.task_id, objs)
    return {"count": len(added)}


# ---------------------------------------------------------------- 聊天

@app.post("/api/ask")
async def api_ask(body: AskBody) -> dict[str, Any]:
    client = await get_client()
    r = await client.chat.ask(
        body.notebook_id, body.question, conversation_id=body.conversation_id
    )
    return {
        "answer": r.answer,
        "conversation_id": r.conversation_id,
        "references": [
            {
                "n": getattr(x, "citation_number", None),
                "text": (getattr(x, "cited_text", "") or "")[:400],
            }
            for x in (r.references or [])[:30]
        ],
        "next_steps": [
            q for q in
            ((getattr(s, "question", "") or "").strip() for s in (r.next_steps or []))
            if q
        ][:4],
    }


_LENGTHS = {
    "default": ChatResponseLength.DEFAULT,
    "longer": ChatResponseLength.LONGER,
    "shorter": ChatResponseLength.SHORTER,
}

_GOALS = {
    "default": ChatGoal.DEFAULT,
    "learning_guide": ChatGoal.LEARNING_GUIDE,
    "custom": ChatGoal.CUSTOM,
}


@app.get("/api/chat-config/{notebook_id}")
async def api_chat_config_get(notebook_id: str) -> dict[str, Any]:
    """读取当前对话设置（自定义人设）。"""
    client = await get_client()
    try:
        st = await client.chat.get_settings(notebook_id)
        return {"custom_prompt": getattr(st, "custom_prompt", "") or ""}
    except Exception:
        return {"custom_prompt": ""}


@app.post("/api/chat-config")
async def api_chat_config(body: ChatConfigBody) -> dict[str, Any]:
    """设置回答长度与风格。对应网页版的「对话设置」。"""
    client = await get_client()
    length = _LENGTHS.get(body.length, ChatResponseLength.DEFAULT)
    goal = _GOALS.get(body.goal, ChatGoal.DEFAULT)
    custom = (body.custom_prompt or "").strip() or None
    if custom:
        goal = ChatGoal.CUSTOM
    await client.chat.configure(
        body.notebook_id,
        goal=goal,
        response_length=length,
        custom_prompt=custom,
    )
    return {"ok": True}


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


@app.get("/api/suggest/{notebook_id}")
async def api_suggest(notebook_id: str) -> list[dict[str, str]]:
    """AI 推荐的起始问题。PromptSuggestion 只有 title / prompt 两个字段。

    Google 按笔记本内容语言返回，英文资料会给英文建议。这里在提示词后
    追加中文指令，保证点下去得到的是中文回答。
    """
    client = await get_client()
    try:
        items = await client.notebooks.suggest_prompts(notebook_id)
    except Exception:
        return list(_FALLBACK_PROMPTS)
    out = []
    for p in items[:4]:
        title = (getattr(p, "title", "") or "").strip()
        prompt = (getattr(p, "prompt", "") or "").strip()
        if not prompt:
            continue
        # Google 按资料语言返回建议；英文资料给英文建议。
        # 标题本地化 + 追加中文回答指令，保证界面与回答都是中文。
        cn_title = _CN_TITLES.get(title.lower(), "")
        if not cn_title:
            cn_title = title if _has_cjk(title) else ""
        ask = prompt if _has_cjk(prompt) else f"{prompt}\n\n请用中文回答。"
        out.append({"title": cn_title, "prompt": ask, "en": title})

    # 标题仍是英文（未收录的新说法）时，整体换成通用中文问题，
    # 避免界面出现中英夹杂。
    if not out or any(not x["title"] for x in out):
        return list(_FALLBACK_PROMPTS)
    return out


#: 常见英文建议标题 → 中文。未命中的走通用中文起始问题。
_CN_TITLES = {
    "learning workflow": "学习路径",
    "beginner explanation": "入门讲解",
    "technical sequence": "技术流程",
    "exam strategies": "应试策略",
    "common pitfalls": "常见误区",
    "language mastery": "语言要点",
    "key concepts": "核心概念",
    "core concepts": "核心概念",
    "professional briefing": "专业简报",
    "deep dive": "深入剖析",
    "summary": "内容总结",
    "overview": "整体概览",
    "practical application": "实际应用",
    "study guide": "复习指南",
    "critical analysis": "批判分析",
    "comparison": "对比分析",
    "timeline": "时间脉络",
    "main arguments": "主要论点",
}

#: 任何笔记本都适用的中文起始问题（AI 建议不可用时兜底）
_FALLBACK_PROMPTS = [
    {"title": "核心要点", "prompt": "用中文总结这些资料最核心的 5 个要点，每点简要说明。"},
    {"title": "深入讲解", "prompt": "用中文详细讲解这些资料中最重要的概念，并举例说明。"},
    {"title": "重点梳理", "prompt": "用中文梳理这些资料的整体脉络，做成分层的提纲。"},
    {"title": "疑难解答", "prompt": "用中文指出这些资料里最容易被误解的地方，并解释清楚。"},
]


@app.get("/api/history/{notebook_id}")
async def api_history(notebook_id: str) -> dict[str, Any]:
    """历史对话。get_history 返回 list[tuple[question, answer]]。

    出错时把原因带回前端，不再静默返回空列表（那样只会看到一片空白）。
    """
    client = await get_client()
    try:
        conv_id = await client.chat.get_conversation_id(notebook_id)
    except Exception as e:
        return {"turns": [], "error": f"读取会话失败: {e}"}

    if not conv_id:
        return {"turns": [], "conversation_id": None}

    try:
        turns = await client.chat.get_history(notebook_id, limit=100,
                                              conversation_id=conv_id)
    except Exception as e:
        return {"turns": [], "conversation_id": conv_id, "error": f"读取历史失败: {e}"}

    out: list[dict[str, Any]] = []
    for t in turns or []:
        if isinstance(t, (tuple, list)) and len(t) >= 2:
            q, a = str(t[0] or ""), str(t[1] or "")
        else:
            q = str(getattr(t, "question", "") or "")
            a = str(getattr(t, "answer", "") or "")
        if q or a:
            out.append({"q": q, "a": a})
    return {"turns": out[-60:], "conversation_id": conv_id}


@app.delete("/api/history/{notebook_id}")
async def api_history_clear(notebook_id: str) -> dict[str, Any]:
    """清空当前对话。对应网页版聊天区的清除。"""
    client = await get_client()
    conv_id = await client.chat.get_conversation_id(notebook_id)
    if not conv_id:
        return {"ok": True, "note": "本来就没有对话"}
    await client.chat.delete_conversation(notebook_id, conv_id)
    return {"ok": True}


# ---------------------------------------------------------------- 笔记

@app.get("/api/notes/{notebook_id}")
async def api_notes(notebook_id: str) -> list[dict[str, Any]]:
    client = await get_client()
    return [
        {
            "id": n.id,
            "title": n.title or "(无标题)",
            "content": (getattr(n, "content", "") or "")[:4000],
        }
        for n in await client.notes.list(notebook_id)
    ]


@app.post("/api/notes")
async def api_note_create(body: NoteBody) -> dict[str, Any]:
    client = await get_client()
    n = await client.notes.create(body.notebook_id, title=body.title, content=body.content)
    return {"id": n.id}


@app.delete("/api/notes/{notebook_id}/{note_id}")
async def api_note_del(notebook_id: str, note_id: str) -> dict[str, Any]:
    client = await get_client()
    await client.notes.delete(notebook_id, note_id)
    return {"ok": True}


# ---------------------------------------------------------------- 分享

@app.get("/api/share/{notebook_id}")
async def api_share_status(notebook_id: str) -> dict[str, Any]:
    client = await get_client()
    try:
        st = await client.sharing.get_status(notebook_id)
        url = await client.notebooks.get_share_url(notebook_id)
        return {"public": bool(getattr(st, "is_public", False)), "url": url}
    except Exception as e:
        return {"public": False, "url": "", "error": str(e)}


@app.post("/api/share")
async def api_share(body: ShareBody) -> dict[str, Any]:
    client = await get_client()
    await client.sharing.set_public(body.notebook_id, body.public)
    url = await client.notebooks.get_share_url(body.notebook_id)
    return {"public": body.public, "url": url}


# ---------------------------------------------------------------- 生成

_TASKS: dict[str, dict[str, Any]] = {}

_REPORTS = {
    "briefing": ReportFormat.BRIEFING_DOC,
    "study": ReportFormat.STUDY_GUIDE,
    "blog": ReportFormat.BLOG_POST,
    "concept": ReportFormat.CONCEPT_EXPLANATION,
}


@app.post("/api/generate")
async def api_generate(body: GenerateBody) -> dict[str, Any]:
    client = await get_client()
    a = client.artifacts
    k, ins, lang = body.kind, body.instructions or None, body.language
    nb = body.notebook_id

    if k == "audio":
        st = await a.generate_audio(nb, language=lang, instructions=ins)
    elif k == "video":
        st = await a.generate_video(nb, language=lang, instructions=ins)
    elif k == "cinematic":
        st = await a.generate_cinematic_video(nb, language=lang, instructions=ins)
    elif k == "quiz":
        st = await a.generate_quiz(nb, language=lang, instructions=ins)
    elif k == "flashcards":
        st = await a.generate_flashcards(nb, language=lang, instructions=ins)
    elif k == "slides":
        st = await a.generate_slide_deck(nb, language=lang, instructions=ins)
    elif k == "infographic":
        st = await a.generate_infographic(nb, language=lang, instructions=ins)
    elif k == "datatable":
        st = await a.generate_data_table(nb, language=lang, instructions=ins)
    elif k == "mindmap":
        st = await a.generate_mind_map(nb)
    elif k in _REPORTS:
        st = await a.generate_report(
            nb, report_format=_REPORTS[k], language=lang, extra_instructions=ins
        )
    else:
        raise HTTPException(400, f"未知类型: {k}")

    tid = st.task_id
    _TASKS[tid] = {"kind": k, "state": "running"}

    async def _run() -> None:
        try:
            await a.wait_for_completion(nb, tid, timeout=1800)
            _TASKS[tid]["state"] = "done"
        except Exception as e:  # noqa: BLE001
            _TASKS[tid].update(state="error", error=str(e))

    asyncio.create_task(_run())
    return {"task_id": tid, "kind": k}


@app.get("/api/task/{task_id}")
async def api_task(task_id: str) -> dict[str, Any]:
    return _TASKS.get(task_id, {"state": "unknown"})


_DL = {
    "audio": ("download_audio", "mp3"),
    "video": ("download_video", "mp4"),
    "cinematic": ("download_video", "mp4"),
    "quiz": ("download_quiz", "md"),
    "flashcards": ("download_flashcards", "md"),
    "slides": ("download_slide_deck", "pdf"),
    "infographic": ("download_infographic", "png"),
    "mindmap": ("download_mind_map", "json"),
    "datatable": ("download_data_table", "csv"),
    "briefing": ("download_report", "md"),
    "study": ("download_report", "md"),
    "blog": ("download_report", "md"),
    "concept": ("download_report", "md"),
}


@app.get("/api/download/{notebook_id}/{kind}")
async def api_download(notebook_id: str, kind: str):
    if kind not in _DL:
        raise HTTPException(400, f"不支持下载: {kind}")
    client = await get_client()
    method, ext = _DL[kind]
    path = OUT / f"{kind}_{notebook_id[:8]}.{ext}"
    fn = getattr(client.artifacts, method)
    try:
        if kind in ("quiz", "flashcards"):
            await fn(notebook_id, str(path), output_format="markdown")
        else:
            await fn(notebook_id, str(path))
    except TypeError:
        await fn(notebook_id, str(path))
    if not path.exists():
        raise HTTPException(404, "还没有生成好的产物")
    return FileResponse(path, filename=path.name)


@app.get("/api/artifacts/{notebook_id}")
async def api_artifacts(notebook_id: str) -> list[dict[str, Any]]:
    client = await get_client()
    try:
        items = await client.artifacts.list(notebook_id)
    except Exception:
        return []
    out = []
    for x in items:
        t = getattr(x, "type", None)
        out.append(
            {
                "id": getattr(x, "id", ""),
                "type": getattr(t, "value", str(t)),
                "title": getattr(x, "title", "") or "",
            }
        )
    return out


@app.get("/api/open-folder")
async def api_open_folder() -> dict[str, Any]:
    """在系统文件管理器里打开 out 目录。"""
    import subprocess
    import sys

    try:
        if sys.platform == "win32":
            os.startfile(OUT)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(OUT)])
        else:
            subprocess.Popen(["xdg-open", str(OUT)])
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------- 标签

@app.get("/api/labels/{notebook_id}")
async def api_labels(notebook_id: str) -> list[dict[str, Any]]:
    """资料标签，对应网页版 Sources 里的分类。"""
    client = await get_client()
    try:
        items = await client.labels.list(notebook_id)
    except Exception:
        return []
    return [
        {
            "id": getattr(x, "id", ""),
            "name": getattr(x, "name", "") or "",
            "emoji": getattr(x, "emoji", "") or "",
        }
        for x in items
    ]


@app.post("/api/labels")
async def api_label_create(body: LabelBody) -> dict[str, Any]:
    client = await get_client()
    lb = await client.labels.create(body.notebook_id, body.name, body.emoji or "")
    return {"id": getattr(lb, "id", ""), "name": getattr(lb, "name", "")}


@app.post("/api/labels/auto")
async def api_label_auto(body: LabelBody) -> dict[str, Any]:
    """让 AI 自动给未分类的资料打标签。"""
    client = await get_client()
    made = await client.labels.generate(body.notebook_id, scope="unlabeled")
    return {"count": len(made or [])}


@app.delete("/api/labels/{notebook_id}/{label_id}")
async def api_label_del(notebook_id: str, label_id: str) -> dict[str, Any]:
    client = await get_client()
    await client.labels.delete(notebook_id, label_id)
    return {"ok": True}


@app.get("/api/labels/{notebook_id}/{label_id}/sources")
async def api_label_sources(notebook_id: str, label_id: str) -> list[dict[str, Any]]:
    client = await get_client()
    try:
        items = await client.labels.sources(notebook_id, label_id)
    except Exception:
        return []
    return [{"id": getattr(x, "id", ""), "title": getattr(x, "title", "")} for x in items]


# ---------------------------------------------------------------- 合集

@app.get("/api/collections")
async def api_collections() -> list[dict[str, Any]]:
    """笔记本合集，对应网页版首页的分组。"""
    client = await get_client()
    try:
        items = await client.collections.list()
    except Exception:
        return []
    return [
        {"id": getattr(x, "id", ""), "name": getattr(x, "name", "") or "未命名"}
        for x in items
    ]


@app.post("/api/collections")
async def api_collection_create(body: CollectionBody) -> dict[str, Any]:
    client = await get_client()
    c = await client.collections.create(body.name)
    return {"id": getattr(c, "id", ""), "name": getattr(c, "name", "")}


@app.delete("/api/collections/{collection_id}")
async def api_collection_del(collection_id: str) -> dict[str, Any]:
    client = await get_client()
    await client.collections.delete(collection_id)
    return {"ok": True}


@app.post("/api/collections/add")
async def api_collection_add(body: CollectionBody) -> dict[str, Any]:
    client = await get_client()
    await client.collections.add_notebooks(body.collection_id or "", body.notebook_ids)
    return {"ok": True}


# ---------------------------------------------------------------- 资料增强

@app.post("/api/sources/rename")
async def api_source_rename(body: RenameBody) -> dict[str, Any]:
    client = await get_client()
    await client.sources.rename(body.notebook_id, body.target_id, body.name)
    return {"ok": True}


@app.post("/api/sources/refresh/{notebook_id}/{source_id}")
async def api_source_refresh(notebook_id: str, source_id: str) -> dict[str, Any]:
    """重新抓取网页/Drive 资料的最新内容。"""
    client = await get_client()
    await client.sources.refresh(notebook_id, source_id)
    return {"ok": True}


@app.get("/api/source-guide/{notebook_id}/{source_id}")
async def api_source_guide(notebook_id: str, source_id: str) -> dict[str, Any]:
    """单份资料的 AI 摘要与关键问题，对应网页版点开资料看到的内容。"""
    client = await get_client()
    try:
        g = await client.sources.get_guide(notebook_id, source_id)
    except Exception as e:
        return {"error": str(e)}
    qs = getattr(g, "questions", None) or getattr(g, "key_questions", None) or []
    return {
        "summary": getattr(g, "summary", "") or "",
        "questions": [str(q) for q in qs][:6],
    }


@app.post("/api/sources/drive")
async def api_source_drive(body: dict[str, Any]) -> dict[str, Any]:
    """添加 Google Drive 文档。"""
    client = await get_client()
    await client.sources.add_drive_file(
        body["notebook_id"], body["document_id"], title=body.get("title")
    )
    return {"ok": True}


# ---------------------------------------------------------------- 笔记本增强

@app.post("/api/notebooks/emoji")
async def api_nb_emoji(body: EmojiBody) -> dict[str, Any]:
    client = await get_client()
    await client.notebooks.set_emoji(body.notebook_id, body.emoji)
    return {"ok": True}


@app.get("/api/notebook-info/{notebook_id}")
async def api_nb_info(notebook_id: str) -> dict[str, Any]:
    """笔记本简介与元数据。"""
    client = await get_client()
    out: dict[str, Any] = {}
    try:
        d = await client.notebooks.get_description(notebook_id)
        out["description"] = getattr(d, "description", "") or str(d or "")
    except Exception:
        out["description"] = ""
    try:
        out["summary"] = await client.notebooks.get_summary(notebook_id)
    except Exception:
        out["summary"] = ""
    return out


# ---------------------------------------------------------------- 笔记增强

@app.post("/api/notes/update")
async def api_note_update(body: NoteUpdateBody) -> dict[str, Any]:
    client = await get_client()
    await client.notes.update(body.notebook_id, body.note_id, body.content, body.title)
    return {"ok": True}


@app.post("/api/notes/from-answer")
async def api_note_from_answer(body: dict[str, Any]) -> dict[str, Any]:
    """把回答直接存成笔记（走官方接口，保留引用）。"""
    client = await get_client()
    try:
        await client.chat.save_answer_as_note(
            body["notebook_id"], body.get("conversation_id"), body.get("turn_key")
        )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------- 静态

app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8765"))
    host = os.environ.get("HOST", "127.0.0.1")

    if os.environ.get("NB_OPEN_BROWSER", "1") == "1" and host in ("127.0.0.1", "localhost"):
        import threading
        import time

        def _open() -> None:
            time.sleep(1.5)
            webbrowser.open(f"http://127.0.0.1:{port}")

        threading.Thread(target=_open, daemon=True).start()

    print()
    print("  NotebookLM 桌面版已启动")
    print(f"  浏览器打开:  http://127.0.0.1:{port}")
    print("  按 Ctrl+C 退出")
    print()

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
