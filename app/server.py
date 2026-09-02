"""
NotebookLM 桌面版 —— 本地聊天界面后端

启动:
    Windows:  .\\启动.bat   或   .\\scripts\\py.ps1 app\\server.py
    Linux:    ./scripts/py app/server.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("NOTEBOOKLM_HOME", str(ROOT / ".notebooklm"))

from fastapi import FastAPI, HTTPException, UploadFile, File, Form  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from notebooklm import NotebookLMClient, ReportFormat  # noqa: E402

STATIC = Path(__file__).parent / "static"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

# ---------------------------------------------------------------- 客户端管理

_client: NotebookLMClient | None = None
_client_lock = asyncio.Lock()


async def get_client() -> NotebookLMClient:
    """惰性创建并复用一个全局客户端。"""
    global _client
    async with _client_lock:
        if _client is None:
            _client = await NotebookLMClient.from_storage().__aenter__()
        return _client


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    global _client
    if _client is not None:
        try:
            await _client.close()
        except Exception:
            pass


app = FastAPI(title="NotebookLM 桌面版", lifespan=lifespan)


# ---------------------------------------------------------------- 错误包装

@app.exception_handler(Exception)
async def on_error(request, exc: Exception):
    msg = str(exc) or exc.__class__.__name__
    hint = ""
    low = msg.lower()
    if "auth" in low or "cookie" in low or "sid" in low or "storage" in low:
        hint = "认证可能已过期，请在终端运行：scripts\\nb.ps1 login"
    return JSONResponse(
        status_code=500,
        content={"error": msg, "hint": hint, "type": exc.__class__.__name__},
    )


# ---------------------------------------------------------------- 数据模型

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
    kind: str                      # audio / video / quiz / flashcards / mindmap / report / slides / infographic
    instructions: str | None = None
    language: str = "zh"


class RenameBody(BaseModel):
    notebook_id: str
    title: str


class NoteBody(BaseModel):
    notebook_id: str
    title: str
    content: str


# ---------------------------------------------------------------- 认证

@app.get("/api/auth")
async def api_auth() -> dict[str, Any]:
    try:
        client = await get_client()
        email = await client.get_account_email()
        return {"ok": True, "email": email or "已登录"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------- 笔记本

@app.get("/api/notebooks")
async def api_notebooks() -> list[dict[str, Any]]:
    client = await get_client()
    books = await client.notebooks.list()
    return [
        {
            "id": b.id,
            "title": b.title or "(未命名)",
            "emoji": getattr(b, "emoji", "") or "📓",
            "sources": getattr(b, "sources_count", 0),
            "created": str(getattr(b, "created_at", "") or "")[:10],
        }
        for b in books
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


# ---------------------------------------------------------------- 资料

@app.get("/api/sources/{notebook_id}")
async def api_sources(notebook_id: str) -> list[dict[str, Any]]:
    client = await get_client()
    items = await client.sources.list(notebook_id)
    out = []
    for s in items:
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
    src = await client.sources.add_url(body.notebook_id, body.url)
    return {"id": src.id, "title": src.title or body.url}


@app.post("/api/sources/text")
async def api_add_text(body: AddTextBody) -> dict[str, Any]:
    client = await get_client()
    src = await client.sources.add_text(body.notebook_id, body.content, title=body.title)
    return {"id": src.id, "title": src.title or body.title}


@app.post("/api/sources/file")
async def api_add_file(notebook_id: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
    client = await get_client()
    tmp = OUT / f"_upload_{file.filename}"
    tmp.write_bytes(await file.read())
    try:
        src = await client.sources.add_file(notebook_id, str(tmp))
        return {"id": src.id, "title": src.title or file.filename}
    finally:
        tmp.unlink(missing_ok=True)


@app.delete("/api/sources/{notebook_id}/{source_id}")
async def api_del_source(notebook_id: str, source_id: str) -> dict[str, Any]:
    client = await get_client()
    await client.sources.delete(notebook_id, source_id)
    return {"ok": True}


# ---------------------------------------------------------------- 聊天

@app.post("/api/ask")
async def api_ask(body: AskBody) -> dict[str, Any]:
    client = await get_client()
    r = await client.chat.ask(
        body.notebook_id, body.question, conversation_id=body.conversation_id
    )
    refs = []
    for ref in (r.references or [])[:30]:
        refs.append(
            {
                "n": getattr(ref, "citation_number", None),
                "text": (getattr(ref, "cited_text", "") or "")[:400],
            }
        )
    return {
        "answer": r.answer,
        "conversation_id": r.conversation_id,
        "references": refs,
        "next_steps": [getattr(s, "text", str(s)) for s in (r.next_steps or [])][:4],
    }


@app.get("/api/suggest/{notebook_id}")
async def api_suggest(notebook_id: str) -> list[str]:
    client = await get_client()
    try:
        prompts = await client.notebooks.suggest_prompts(notebook_id)
        return [getattr(p, "text", str(p)) for p in prompts][:4]
    except Exception:
        return []


# ---------------------------------------------------------------- 笔记

@app.get("/api/notes/{notebook_id}")
async def api_notes(notebook_id: str) -> list[dict[str, Any]]:
    client = await get_client()
    notes = await client.notes.list(notebook_id)
    return [
        {"id": n.id, "title": n.title or "(无标题)", "content": (getattr(n, "content", "") or "")[:2000]}
        for n in notes
    ]


@app.post("/api/notes")
async def api_note_create(body: NoteBody) -> dict[str, Any]:
    client = await get_client()
    n = await client.notes.create(body.notebook_id, title=body.title, content=body.content)
    return {"id": n.id}


# ---------------------------------------------------------------- 生成

_TASKS: dict[str, dict[str, Any]] = {}

_REPORT_FORMATS = {
    "briefing": ReportFormat.BRIEFING_DOC,
    "study": ReportFormat.STUDY_GUIDE,
    "blog": ReportFormat.BLOG_POST,
}


@app.post("/api/generate")
async def api_generate(body: GenerateBody) -> dict[str, Any]:
    client = await get_client()
    a = client.artifacts
    k = body.kind
    ins = body.instructions or None
    lang = body.language

    if k == "audio":
        st = await a.generate_audio(body.notebook_id, language=lang, instructions=ins)
    elif k == "video":
        st = await a.generate_video(body.notebook_id, language=lang, instructions=ins)
    elif k == "quiz":
        st = await a.generate_quiz(body.notebook_id, language=lang, instructions=ins)
    elif k == "flashcards":
        st = await a.generate_flashcards(body.notebook_id, language=lang, instructions=ins)
    elif k == "slides":
        st = await a.generate_slide_deck(body.notebook_id, language=lang, instructions=ins)
    elif k == "infographic":
        st = await a.generate_infographic(body.notebook_id, language=lang, instructions=ins)
    elif k == "mindmap":
        st = await a.generate_mind_map(body.notebook_id)
    elif k in _REPORT_FORMATS:
        st = await a.generate_report(
            body.notebook_id, report_format=_REPORT_FORMATS[k], language=lang,
            extra_instructions=ins,
        )
    else:
        raise HTTPException(400, f"未知类型: {k}")

    task_id = st.task_id
    _TASKS[task_id] = {"kind": k, "notebook": body.notebook_id, "state": "running"}

    async def _run() -> None:
        try:
            await a.wait_for_completion(body.notebook_id, task_id, timeout=1800)
            _TASKS[task_id]["state"] = "done"
        except Exception as e:  # noqa: BLE001
            _TASKS[task_id]["state"] = "error"
            _TASKS[task_id]["error"] = str(e)

    asyncio.create_task(_run())
    return {"task_id": task_id, "kind": k}


@app.get("/api/task/{task_id}")
async def api_task(task_id: str) -> dict[str, Any]:
    return _TASKS.get(task_id, {"state": "unknown"})


_DOWNLOADERS = {
    "audio": ("download_audio", "mp3"),
    "video": ("download_video", "mp4"),
    "quiz": ("download_quiz", "md"),
    "flashcards": ("download_flashcards", "md"),
    "slides": ("download_slide_deck", "pdf"),
    "infographic": ("download_infographic", "png"),
    "mindmap": ("download_mind_map", "json"),
    "briefing": ("download_report", "md"),
    "study": ("download_report", "md"),
    "blog": ("download_report", "md"),
}


@app.get("/api/download/{notebook_id}/{kind}")
async def api_download(notebook_id: str, kind: str):
    if kind not in _DOWNLOADERS:
        raise HTTPException(400, f"不支持下载: {kind}")
    client = await get_client()
    method, ext = _DOWNLOADERS[kind]
    safe = notebook_id[:8]
    path = OUT / f"{kind}_{safe}.{ext}"
    fn = getattr(client.artifacts, method)
    try:
        if kind in ("quiz", "flashcards"):
            await fn(notebook_id, str(path), output_format="markdown")
        else:
            await fn(notebook_id, str(path))
    except TypeError:
        await fn(notebook_id, str(path))
    if not path.exists():
        raise HTTPException(404, "还没有生成好的产物，请先点生成并等待完成")
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
                "state": str(getattr(x, "state", "") or getattr(x, "status", "")),
            }
        )
    return out


# ---------------------------------------------------------------- 静态页面

app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8765"))
    host = os.environ.get("HOST", "127.0.0.1")

    if os.environ.get("NB_OPEN_BROWSER", "1") == "1" and host in ("127.0.0.1", "localhost"):
        def _open() -> None:
            import time
            time.sleep(1.5)
            webbrowser.open(f"http://127.0.0.1:{port}")

        import threading
        threading.Thread(target=_open, daemon=True).start()

    print()
    print("  NotebookLM 桌面版已启动")
    print(f"  请在浏览器打开:  http://127.0.0.1:{port}")
    print("  按 Ctrl+C 退出")
    print()

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
