"""
NotebookLM 桌面版 —— 本地聊天界面后端

启动:
    Windows:  双击 启动.bat   或   .\\scripts\\app.ps1
    Linux:    ./scripts/py app/server.py
"""

from __future__ import annotations

import asyncio
import json
import os
import time
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
from notebooklm.rpc.types import (  # noqa: E402
    AudioFormat,
    AudioLength,
    ExportType,
    InfographicDetail,
    InfographicOrientation,
    InfographicStyle,
    QuizDifficulty,
    QuizQuantity,
    SlideDeckFormat,
    SlideDeckLength,
    VideoFormat,
    VideoStyle,
)

STATIC = Path(__file__).parent / "static"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

# ---------------------------------------------------------------- 客户端

#: 建连用的互斥锁。client 本身挂在 app.state.nblm（见 lifespan）
_lock = asyncio.Lock()


def _fmt_date(dt: Any, *, with_time: bool = False) -> str:
    """datetime -> 本地可读字符串。None 与异常值一律返回空串。"""
    if dt is None:
        return ""
    try:
        if with_time:
            return dt.strftime("%Y-%m-%d %H:%M")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        t = str(dt)
        return t[:16] if with_time else t[:10]


def _evict(store: dict[str, Any], keep: int) -> None:
    """把内存字典裁到 keep 条，丢最早的。

    这三个字典原本只增不减：研究结果里存着完整报告正文，
    长时间开着窗口反复用会一直吃内存。
    """
    while len(store) > keep:
        store.pop(next(iter(store)), None)


async def get_client() -> NotebookLMClient:
    """惰性建连，挂在 app.state 上（见 lifespan 的说明）。

    单 worker 下 FastAPI 只有一个事件循环，一个 client 即可；
    真要多 worker，每个进程各自持有自己的实例。
    """
    async with _lock:
        c = getattr(app.state, "nblm", None)
        if c is None:
            c = await NotebookLMClient.from_storage().__aenter__()
            app.state.nblm = c
        return c


def _friendly(exc: Exception) -> tuple[int, str, str]:
    """把底层异常翻译成用户能看懂的中文 + 处理建议。"""
    t = f"{type(exc).__name__}: {exc}"
    low = t.lower()
    if "storage file not found" in low or "storage_state" in low:
        return 401, "还没有登录", "请在终端运行 scripts\\nb.ps1 login 完成登录后重试"
    if "cookie" in low or "auth" in low or "401" in low or "unauthorized" in low:
        return 401, "登录已过期", "请重新运行 scripts\\nb.ps1 login"
    if "timeout" in low or "timed out" in low:
        return 504, "请求超时", "网络较慢或 Google 侧繁忙，请稍后重试"
    if "network" in low or "connect" in low or "dns" in low:
        return 502, "连不上 Google", "检查网络代理后重试"
    if "quota" in low or "exhausted" in low or "limit exceeded" in low:
        return 429, "配额已用完", (
            "免费账号的深度研究每月只有 10 次，其他生成也有每日上限。"
            "换用「快速」模式，或等配额重置（日配额 24 小时、深度研究 30 天）"
        )
    if "rate" in low or "429" in low or "too many" in low:
        return 429, "请求太频繁", "稍等几分钟再试"
    if "artifactpendingtimeout" in low:
        return 504, "任务一直在排队", "Google 的生成队列繁忙，稍后重新生成即可"
    if "artifactinprogresstimeout" in low:
        return 504, "生成没能在预期时间内完成", "任务可能仍在 Google 侧继续，稍后到「已生成的内容」里刷新看看"
    if "featureunavailable" in low:
        return 403, "这个功能当前不可用", "可能是账号权限或该类型暂未开放"
    if "short videos" in low or "fixed visual style" in low:
        return 400, "短视频不支持自选画面风格", "短视频的风格由 Google 固定，请不要设置风格或画面描述"
    if "cinematic" in low and "style_prompt" in low:
        return 400, "电影感视频不支持画面描述", "去掉画面描述后重试"
    if "style_prompt is required" in low:
        return 400, "缺少画面描述", "选了「自定义」风格就必须填画面描述"
    if "style_prompt requires" in low:
        return 400, "画面描述只对自定义风格生效", "把风格改成「自定义」，或清空画面描述"
    return 500, "操作失败", t[:300]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """把 client 挂在 app.state 上，而不是模块级全局。

    官方 docs/python-api.md 明确写着：
      "Do not stash a NotebookLMClient on a process-global outside the
       lifespan — multi-worker servers fork the process and you will end
       up with the same client object referencing different event loops."
    client 既非线程安全、也不可跨事件循环复用，
    跨 loop 使用会在 POST 热路径抛 RuntimeError。
    """
    app.state.nblm = None
    try:
        yield
    finally:
        c = getattr(app.state, "nblm", None)
        if c is not None:
            try:
                await c.close()
            except Exception:
                pass
            app.state.nblm = None


app = FastAPI(title="NotebookLM 桌面版", lifespan=lifespan)



@app.middleware("http")
async def _no_cache(request, call_next):
    """全站禁缓存。

    这是"改了代码却还是老样子"的头号原因：浏览器缓存 app.js。
    页面和接口都不大，禁掉缓存的代价可以忽略。
    """
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.exception_handler(HTTPException)
async def on_http_error(request, exc: HTTPException):
    """HTTPException 默认返回 {"detail": ...}，
    而前端 api() 只认 error/hint —— 不转换的话，
    所有 raise HTTPException 里精心写的中文提示都会变成「请求失败」。
    """
    d = exc.detail
    if isinstance(d, dict):
        return JSONResponse(status_code=exc.status_code, content=d)
    return JSONResponse(status_code=exc.status_code, content={"error": str(d)})


@app.exception_handler(Exception)
async def on_error(request, exc: Exception):
    """未捕获异常统一转成中文 JSON，前端 toast 直接可读。

    注意 JSONResponse 的第一个位置参数是 content，不是 status_code。
    """
    code, msg, hint = _friendly(exc)
    return JSONResponse(status_code=code, content={"error": msg, "hint": hint})


# ---------------------------------------------------------------- 模型

class AskBody(BaseModel):
    notebook_id: str
    question: str
    conversation_id: str | None = None
    source_ids: list[str] | None = None


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
    source_ids: list[str] | None = None      # 限定只用这几份资料
    # 播客
    audio_format: str | None = None          # deep_dive|brief|critique|debate
    audio_length: str | None = None          # short|default|long
    # 视频
    video_format: str | None = None          # explainer|brief|cinematic|short
    video_style: str | None = None           # classic|whiteboard|anime|...
    style_prompt: str | None = None
    # 测验 / 闪卡
    quantity: str | None = None              # fewer|standard|more
    difficulty: str | None = None            # easy|medium|hard
    # 幻灯片
    slide_format: str | None = None          # detailed_deck|presenter_slides
    slide_length: str | None = None          # default|short
    # 信息图
    orientation: str | None = None           # landscape|portrait|square
    detail_level: str | None = None          # concise|standard|detailed
    infographic_style: str | None = None     # professional|sketch_note|...
    # 报告
    custom_prompt: str | None = None


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
        return {"ok": False, "error": _err_cn(e)}


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
            "created": _fmt_date(getattr(b, "created_at", None)),
        }
        for b in (await client.notebooks.list() or [])
    ]


@app.post("/api/notebooks")
async def api_create(body: CreateBody) -> dict[str, Any]:
    client = await get_client()
    nb = await client.notebooks.create(body.title)
    if nb is None:
        raise HTTPException(502, "创建失败，Google 没有返回笔记本信息")
    return {"id": nb.id, "title": nb.title}


@app.post("/api/notebooks/rename")
async def api_rename(body: RenameBody) -> dict[str, Any]:
    client = await get_client()
    await client.notebooks.rename(body.notebook_id, body.name)
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
    for s in (await client.sources.list(notebook_id) or []):
        # is_processing 只覆盖 PROCESSING，PREPARING(5) 会漏成 ready，
        # 界面上看着能用其实还没就绪。这里按 is_ready 反推更保险。
        if getattr(s, "is_error", False):
            status = "error"
        elif getattr(s, "is_ready", False):
            status = "ready"
        else:
            status = "processing"
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
    if s is None:
        raise HTTPException(502, "添加失败，请确认网址可公开访问")
    return {"id": s.id, "title": s.title or body.url}


@app.post("/api/sources/text")
async def api_add_text(body: AddTextBody) -> dict[str, Any]:
    client = await get_client()
    # 签名是 add_text(notebook_id, title, content)，
    # 之前把 content 放在第二个位置又传了 title=，直接 TypeError。
    s = await client.sources.add_text(body.notebook_id, body.title, body.content)
    if s is None:
        raise HTTPException(502, "添加失败")
    return {"id": s.id, "title": s.title or body.title}


@app.post("/api/sources/file")
async def api_add_file(notebook_id: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
    """上传文件为资料。

    官方 troubleshooting.md 记录了三个坑，这里都挡掉：
      1. HTML/XHTML 会被上传端点直接拒绝
      2. 超过约 20MB 容易上传超时
      3. 纯文本/Markdown 走 add_file 可能被错误解析，
         官方建议改用 add_text
    """
    name = os.path.basename(file.filename or "upload")
    ext = Path(name).suffix.lower()

    if ext in (".html", ".htm", ".xhtml", ".mhtml"):
        raise HTTPException(
            400,
            "NotebookLM 不接受网页文件。请把网页另存为 PDF 或纯文本再上传，"
            "或者直接用上面的「网址」输入框添加链接",
        )

    data = await file.read()
    size_mb = len(data) / 1024 / 1024
    if size_mb > 20:
        raise HTTPException(
            400,
            f"文件 {size_mb:.1f} MB，超过约 20MB 的上传上限，容易超时。"
            "请拆分后再上传",
        )
    if not data:
        raise HTTPException(400, "文件是空的")

    # 纯文本类直接走 add_text，官方明确说这样更可靠
    if ext in (".txt", ".md", ".markdown"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = data.decode("gbk")      # Windows 中文环境常见
            except UnicodeDecodeError:
                text = data.decode("utf-8", errors="replace")
        src = await client_add_text(notebook_id, Path(name).stem, text)
        return {"id": src["id"], "title": src["title"]}

    # 其余类型落盘再上传。文件名只取基名，避免 ../ 穿越
    safe = _safe_name(Path(name).stem, "upload") + ext
    tmp = OUT / f"_up_{safe}"
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        client = await get_client()
        s = await client.sources.add_file(notebook_id, str(tmp))
        if s is None:
            raise HTTPException(502, "上传失败，Google 没有返回资料信息")
        return {"id": s.id, "title": s.title or name}
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


async def client_add_text(notebook_id: str, title: str, content: str) -> dict[str, Any]:
    client = await get_client()
    s = await client.sources.add_text(notebook_id, title or "文本", content)
    if s is None:
        raise HTTPException(502, "添加失败")
    return {"id": s.id, "title": s.title or title}



@app.delete("/api/sources/{notebook_id}/{source_id}")
async def api_del_source(notebook_id: str, source_id: str) -> dict[str, Any]:
    client = await get_client()
    await client.sources.delete(notebook_id, source_id)
    return {"ok": True}


@app.get("/api/source-text/{notebook_id}/{source_id}")
async def api_source_text(notebook_id: str, source_id: str) -> dict[str, Any]:
    client = await get_client()
    # get_fulltext 返回 SourceFulltext 对象（content/title/char_count），
    # 不是字符串。之前直接切片会抛 TypeError，这个接口从来没成功过。
    r = await client.sources.get_fulltext(notebook_id, source_id, output_format="markdown")
    text = getattr(r, "content", None)
    if text is None:
        text = r if isinstance(r, str) else ""
    return {
        "text": str(text)[:20000],
        "title": getattr(r, "title", "") or "",
        "chars": getattr(r, "char_count", 0) or len(str(text)),
    }


# ---------------------------------------------------------------- 深度研究

_RESEARCH: dict[str, dict[str, Any]] = {}

#: 研究状态落盘的位置。研究动辄十几分钟，
#: 只放内存的话关掉程序就全没了，等于白等。
_RESEARCH_FILE = OUT / "research_state.json"


def _research_save() -> None:
    """把研究结果写盘（objs 是 SDK 对象，转成可序列化的 dict）。"""
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        data = {}
        for tid, d in _RESEARCH.items():
            item = {k: v for k, v in d.items() if k != "objs"}
            item["_objs"] = [
                {
                    "url": getattr(x, "url", "") or "",
                    "title": getattr(x, "title", "") or "",
                    "hint": getattr(x, "hint", "") or "",
                    "research_task_id": getattr(x, "research_task_id", None),
                    "report_markdown": getattr(x, "report_markdown", "") or "",
                    "source_ordinal": getattr(x, "source_ordinal", None),
                    "result_type": int(getattr(x, "result_type", 1) or 1),
                }
                for x in d.get("objs", [])
            ]
            data[tid] = item
        _RESEARCH_FILE.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass      # 落盘失败不能影响主流程


def _research_load() -> None:
    """启动时把上次的研究结果读回来。"""
    try:
        if not _RESEARCH_FILE.exists():
            return
        from notebooklm import ResearchSource

        data = json.loads(_RESEARCH_FILE.read_text(encoding="utf-8"))
        for tid, item in data.items():
            objs = [ResearchSource.from_public_dict(o) for o in item.pop("_objs", [])]
            # 上次没跑完就被关掉的，标成中断，别让界面一直转圈
            if item.get("state") == "running":
                item["state"] = "error"
                item["error"] = "上次的研究被程序关闭打断了，请重新发起"
            item["objs"] = objs
            _RESEARCH[tid] = item
    except Exception:
        pass


_research_load()


def _err_cn(exc: Exception) -> str:
    """任何异常 -> 中文一句话（含建议）。供后台任务与容错分支使用。"""
    _, msg, hint = _friendly(exc)
    return f"{msg}：{hint}" if hint and hint != msg else msg


def _research_error_cn(exc: Exception) -> str:
    """把研究失败的异常翻成用户看得懂的话，并给出下一步怎么办。"""
    name = type(exc).__name__
    text = f"{name}: {exc}".lower()

    if "not_found" in text or "找不到这个研究任务" in str(exc):
        return ("Google 把这个深度研究任务丢弃了。深度研究本身是可用的"
                "（官方实测约 6 分钟完成），偶发丢任务多半是它那边的临时故障，"
                "直接重试一次通常就好。")
    if "no_research" in text or "始终没有认领" in str(exc):
        return ("Google 一直没有认领这次研究。通常是深度模式排队失败，"
                "建议换个说法重试，或先用「快速」模式拿结果。")
    if "timeout" in text or "timedout" in name.lower():
        return "研究超时了。Google 侧可能正忙，稍后重试，或改用「快速」模式。"
    if "ambiguous" in text:
        return "这个笔记本里有多个研究同时在跑，等前一个结束再试。"
    if "unavailable" in text or "no run" in text:
        return "Google 没能启动深度研究，换个说法或稍后再试。"
    if "quota" in text or "rate" in text or "429" in text:
        return "触发了频率限制，等几分钟再试。"
    if "auth" in text or "cookie" in text or "401" in text:
        return "登录已过期，请重新运行 scripts\\nb.ps1 login。"
    if "network" in text or "connect" in text or "dns" in text:
        return "网络连不上 Google，检查代理后重试。"
    return f"研究失败（{name}）。可以换个说法重新发起。"


async def _research_poll_loop(client: Any, notebook_id: str, tid: str,
                              timeout: float = 2400.0) -> Any:
    """自己轮询研究进度。

    不用 SDK 的 wait_for_completion，因为它按 task_id 严格过滤：
    深度研究里服务端返回的任务 id 和 start() 给的经常对不上，
    过滤后一个都不剩，就一路空转到 30 分钟超时 ——
    界面上表现为「一直显示正在研究」（实测 last_status=no_research）。

    这里的策略：先按 id 找，找不到就退而用该笔记本里唯一在飞的任务。
    """
    loop_ = asyncio.get_running_loop()
    start_at = loop_.time()
    interval = 5.0
    last_status = ""
    misses = 0
    pinned: str | None = None   # 线上真实 task_id，首次轮询后锁定

    while True:
        elapsed = loop_.time() - start_at
        if elapsed >= timeout:
            raise TimeoutError(
                f"研究超过 {int(timeout / 60)} 分钟仍未完成"
                f"（最后状态 {last_status or '未知'}）"
            )

        task = None
        try:
            # 官方 issue #886 记录的正确协议：
            #   首次用 task_id=None 轮询（只有一个任务在飞时无歧义），
            #   从返回值里拿到「线上真实的」task_id，之后再 pin 住它。
            # start() 返回的 id 与轮询用的 id 不是一回事，
            # 直接拿它去 pin 会一个都匹配不上，一路空转到超时。
            task = await client.research.poll(notebook_id, pinned)
        except Exception:
            # 多任务并发时 pinned=None 会抛 AmbiguousResearchTaskError，
            # 退回用 start() 的 id 再试一次
            if pinned is None:
                try:
                    task = await client.research.poll(notebook_id, tid)
                except Exception:
                    task = None
            else:
                task = None

        # 首次拿到真实 id 后就锁定，避免中途别的研究串台
        # （#886 明确说明不 pin 会导致 provenance 错乱）
        if pinned is None and task is not None:
            wire_id = getattr(task, "task_id", "") or ""
            if wire_id:
                pinned = wire_id
                _RESEARCH[tid]["wire_task_id"] = wire_id

        if task is not None:
            st = getattr(getattr(task, "status", None), "value", "") or ""
            if st:
                last_status = st
            _RESEARCH[tid]["status_text"] = last_status
            _RESEARCH[tid]["elapsed"] = int(elapsed)

            if st in ("completed", "failed"):
                return task

            # not_found 是终止态：Google 已经把这个任务丢了，
            # 再等下去也不会变。之前没判断，会一路转到 40 分钟超时。
            if st == "not_found":
                raise RuntimeError(
                    "Google 已经找不到这个研究任务了（not_found），"
                    "多半是它那边把任务丢弃了"
                )

            # 卡在 no_research 说明 Google 压根没认领。
            # 正常情况几十秒内就会转成 in_progress，
            # 超过 5 分钟还是这个状态就没必要继续等了。
            # 官方实测深度研究 358-374 秒完成，所以 no_research 阶段
            # 给足 3 分钟；真正启动后会转成 in_progress，那时不受此限。
            if st == "no_research" and elapsed > 180:
                raise RuntimeError(
                    f"等了 {int(elapsed / 60)} 分钟，Google 始终没有认领这个任务"
                    "（状态一直是 no_research）"
                )

            misses = 0
        else:
            misses += 1
            # 连续很久什么都查不到，说明任务确实不存在了
            if misses >= 12:
                raise RuntimeError("查不到这个研究任务，可能已被取消")

        _research_save()
        await asyncio.sleep(min(interval, timeout - elapsed))
        interval = min(interval * 1.2, 15.0)


#: 深度研究用量台账。Google 不提供「已用多少」的查询接口，
#: 只能本地记账 —— 免费账号每月仅 10 次，烧完不给明确报错，
#: 用户很容易在毫无察觉的情况下把额度耗光（本项目就发生过）。
_DEEP_LOG_FILE = OUT / "deep_research_log.json"


def _deep_log_read() -> list[dict[str, Any]]:
    try:
        if _DEEP_LOG_FILE.exists():
            return json.loads(_DEEP_LOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _deep_log_add(tid: str, query: str) -> None:
    try:
        log = _deep_log_read()
        log.append({"task_id": tid, "query": query[:80], "at": time.time()})
        OUT.mkdir(parents=True, exist_ok=True)
        _DEEP_LOG_FILE.write_text(
            json.dumps(log[-200:], ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def _deep_log_mark(tid: str, ok: bool) -> None:
    """标记这次是否真的拿到了结果，好区分「白烧的」和「有产出的」。"""
    try:
        log = _deep_log_read()
        for item in reversed(log):
            if item.get("task_id") == tid:
                item["ok"] = ok
                break
        _DEEP_LOG_FILE.write_text(
            json.dumps(log[-200:], ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


@app.get("/api/deep-usage")
async def api_deep_usage() -> dict[str, Any]:
    """本月已发起的深度研究次数（本地记账，含成功/失败）。"""
    now = time.time()
    month = 30 * 86400
    recent = [x for x in _deep_log_read() if now - x.get("at", 0) < month]
    ok = sum(1 for x in recent if x.get("ok") is True)
    return {
        "used": len(recent),
        "succeeded": ok,
        "wasted": len(recent) - ok,
        "recent": [
            {
                "query": x.get("query", ""),
                "ok": x.get("ok"),
                "at": time.strftime("%m-%d %H:%M", time.localtime(x.get("at", 0))),
            }
            for x in recent[-10:]
        ],
    }


@app.post("/api/research")
async def api_research(body: ResearchBody) -> dict[str, Any]:
    client = await get_client()
    start = await client.research.start(
        body.notebook_id, body.query, source=body.source, mode=body.mode
    )
    if start is None or not getattr(start, "task_id", ""):
        raise HTTPException(502, "研究没能启动，请稍后重试")
    tid = start.task_id
    if (body.mode or "").lower() == "deep":
        _deep_log_add(tid, body.query)
    _evict(_RESEARCH, 20)
    _RESEARCH[tid] = {
        "state": "running", "query": body.query,
        "notebook_id": body.notebook_id, "sources": [],
    }

    async def _run() -> None:
        try:
            task = await _research_poll_loop(client, body.notebook_id, tid)
            status = str(getattr(getattr(task, "status", ""), "value", "") or "")

            # 深度研究会把结果拆到子任务里，主任务的 sources 可能是空的。
            # 这里把主任务和所有子任务的来源合并去重。
            collected: list[Any] = list(getattr(task, "sources", None) or ())
            for sub in getattr(task, "tasks", None) or ():
                collected.extend(getattr(sub, "sources", None) or ())

            seen: set[str] = set()
            merged: list[Any] = []
            for src in collected:
                u = getattr(src, "url", "") or ""
                key = u or f"__report_{len(merged)}"
                if key in seen:
                    continue
                seen.add(key)
                merged.append(src)

            # 保留完整的 ResearchSource 对象，导入时原样回传。
            # 之前只存 url/title/hint 再重建，丢掉了 research_task_id、
            # result_type、source_ordinal，Google 侧会拒收。
            _RESEARCH[tid]["objs"] = merged
            _RESEARCH[tid]["sources"] = [
                {
                    "url": getattr(x, "url", "") or "",
                    "title": getattr(x, "title", "") or "未命名",
                    "hint": getattr(x, "hint", "") or "",
                    "is_report": bool(getattr(x, "report_markdown", "")),
                }
                for x in merged
            ]
            _RESEARCH[tid]["report"] = getattr(task, "report", "") or ""
            _RESEARCH[tid]["summary"] = getattr(task, "summary", "") or ""

            if status == "failed":
                _RESEARCH[tid]["state"] = "error"
                _RESEARCH[tid]["error"] = "Google 侧研究失败，换个说法或稍后再试"
            elif not merged:
                _RESEARCH[tid]["state"] = "error"
                _RESEARCH[tid]["error"] = (
                    f"研究完成但没有返回可导入的来源（状态 {status or '未知'}）"
                )
            else:
                _RESEARCH[tid]["state"] = "done"
            if (body.mode or "").lower() == "deep":
                _deep_log_mark(tid, _RESEARCH[tid]["state"] == "done")
        except Exception as e:  # noqa: BLE001
            _RESEARCH[tid]["state"] = "error"
            # 之前直接把 "ResearchTimeoutError: ... no_research" 这种
            # 英文异常原样丢给界面，用户完全看不懂。
            _RESEARCH[tid]["error"] = _research_error_cn(e)
            _RESEARCH[tid]["detail"] = f"{type(e).__name__}: {e}"[:300]
            if (body.mode or "").lower() == "deep":
                _deep_log_mark(tid, False)
        _research_save()

    _research_save()
    asyncio.create_task(_run())
    return {"task_id": tid}


@app.get("/api/research-latest/{notebook_id}")
async def api_research_latest(notebook_id: str) -> dict[str, Any]:
    """这个笔记本最近一次研究。

    刷新页面后前端会丢掉 task_id，靠这个接口把进行中或
    刚完成的研究找回来，不用重跑一遍。
    """
    for tid in reversed(list(_RESEARCH)):
        d = _RESEARCH[tid]
        if d.get("notebook_id") != notebook_id:
            continue
        out = {k: v for k, v in d.items() if k != "objs"}
        out["task_id"] = tid
        return out
    return {"state": "none"}


@app.get("/api/research/{task_id}")
async def api_research_status(task_id: str) -> dict[str, Any]:
    d = _RESEARCH.get(task_id)
    if d is None:
        return {"state": "unknown"}
    # objs 是 SDK 对象，不能序列化，返回时剔除
    return {k: v for k, v in d.items() if k != "objs"}


class ImportBody(BaseModel):
    notebook_id: str
    task_id: str
    urls: list[str]


@app.post("/api/research/import")
async def api_research_import(body: ImportBody) -> dict[str, Any]:
    """把选中的研究来源导入笔记本。

    import_sources 的返回值不可靠（官方文档明说可能少报），
    所以用导入前后的资料数差值来判断真实结果。
    """
    client = await get_client()
    cached = _RESEARCH.get(body.task_id)
    if not cached:
        raise HTTPException(400, "研究结果已过期，请重新研究一次")

    want = set(body.urls)
    objs = [x for x in cached.get("objs", []) if (getattr(x, "url", "") or "") in want]
    if not objs:
        raise HTTPException(400, "没有匹配到选中的来源")

    try:
        before = {getattr(s, "id", "") for s in await client.sources.list(body.notebook_id)}
    except Exception:
        before = set()

    # 导入也要用线上真实 task_id：SDK 会校验 provenance
    # （每个 ResearchSource 的 research_task_id 必须与之匹配），
    # 用 start() 那个 id 会被判定为跨任务而拒收。
    wire = cached.get("wire_task_id") or body.task_id
    # 优先用官方的带校验版本：内建指数退避重试与去重，
    # 正是上游为 #315「导入后卡在 Add sources 模态框」做的修复。
    # 我原来手写的轮询比它粗糙，且不处理 FAILED_PRECONDITION（#2187）。
    try:
        reported = await client.research.import_sources_with_verification(
            body.notebook_id, wire, objs, max_elapsed=300,
        )
    except Exception:
        # 带校验版本失败时退回基础版，至少把请求发出去
        try:
            reported = await client.research.import_sources(body.notebook_id, wire, objs)
        except Exception as e:
            raise HTTPException(502, _err_cn(e)) from e

    # 再用资料数差值核实一次：官方文档明说返回值可能少报
    added: set[str] = set()
    for _ in range(12):
        try:
            now = {getattr(s, "id", "") for s in
                   (await client.sources.list(body.notebook_id) or [])}
        except Exception:
            await asyncio.sleep(2.5)
            continue
        added = now - before
        if len(added) >= len(objs):
            break
        await asyncio.sleep(2.5)

    count = len(added) or len(reported or [])
    return {
        "count": count,
        "requested": len(objs),
        "verified": len(added),
        "note": "" if count >= len(objs) else "部分来源可能仍在后台处理，稍后刷新资料列表",
    }


# ---------------------------------------------------------------- 聊天

#: 每个笔记本最近一次 ask 的结果，供「存为笔记」保留引用锚点用
_LAST_ASK: dict[str, Any] = {}


@app.post("/api/ask")
async def api_ask(body: AskBody) -> dict[str, Any]:
    client = await get_client()
    r = await client.chat.ask(
        body.notebook_id,
        body.question,
        source_ids=body.source_ids or None,
        conversation_id=body.conversation_id,
    )
    if r is None:
        raise HTTPException(502, "没有收到回答，请重试")
    _evict(_LAST_ASK, 30)
    _LAST_ASK[body.notebook_id] = r
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
        items = await client.notebooks.suggest_prompts(notebook_id) or []
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
        for n in (await client.notes.list(notebook_id) or [])
    ]


@app.post("/api/notes")
async def api_note_create(body: NoteBody) -> dict[str, Any]:
    client = await get_client()
    n = await client.notes.create(body.notebook_id, title=body.title, content=body.content)
    if n is None:
        raise HTTPException(502, "笔记创建失败")
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
        return {"public": False, "url": "", "error": _err_cn(e)}


@app.post("/api/share")
async def api_share(body: ShareBody) -> dict[str, Any]:
    client = await get_client()
    await client.sharing.set_public(body.notebook_id, body.public)
    url = await client.notebooks.get_share_url(body.notebook_id)
    return {"public": body.public, "url": url}


# ---------------------------------------------------------------- 生成

#: 各类产物的等待预算。数字取自官方 docs/troubleshooting.md：
#: 音频 1200s、普通视频 1800s、电影感视频 3600s。
#: 我原来一律用 1800，电影感视频会被提前判超时。
_GEN_TIMEOUT = {
    "audio": 1200,
    "video": 1800,
    "cinematic": 3600,
    "slides": 900,
    "infographic": 900,
    "datatable": 600,
    "mindmap": 600,
    "quiz": 600,
    "flashcards": 600,
    "study": 600,
    "briefing": 600,
    "blog": 600,
    "concept": 600,
}

_TASKS: dict[str, dict[str, Any]] = {}

#: 生成任务也要落盘。音频视频动辄几分钟，
#: 刷新一下进度就没了、也不知道跑没跑完，和研究是同一个毛病。
_TASKS_FILE = OUT / "tasks_state.json"


def _tasks_save() -> None:
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        _TASKS_FILE.write_text(json.dumps(_TASKS, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _tasks_load() -> None:
    try:
        if not _TASKS_FILE.exists():
            return
        for tid, d in json.loads(_TASKS_FILE.read_text(encoding="utf-8")).items():
            if d.get("state") == "running":
                # 程序重启后后台协程已经没了，状态永远不会再更新
                d["state"] = "error"
                d["error"] = "生成被程序关闭打断，请重新生成"
            _TASKS[tid] = d
    except Exception:
        pass


_tasks_load()


@app.get("/api/tasks-latest/{notebook_id}")
async def api_tasks_latest(notebook_id: str) -> list[dict[str, Any]]:
    """这个笔记本最近的生成任务，刷新页面后据此恢复进度显示。"""
    out = []
    for tid, d in _TASKS.items():
        if d.get("notebook_id") != notebook_id:
            continue
        out.append({"task_id": tid, **{k: v for k, v in d.items() if k != "notebook_id"}})
    return out[-8:]

_REPORTS = {
    "briefing": ReportFormat.BRIEFING_DOC,
    "study": ReportFormat.STUDY_GUIDE,
    "blog": ReportFormat.BLOG_POST,
    "concept": ReportFormat.CONCEPT_EXPLANATION,
}


def _enum(cls: Any, key: str | None) -> Any:
    """把前端传来的小写字符串转成 SDK 枚举；空值或不认识的返回 None。"""
    if not key:
        return None
    try:
        return cls[key.upper()]
    except KeyError:
        return None


@app.post("/api/generate")
async def api_generate(body: GenerateBody) -> dict[str, Any]:
    client = await get_client()
    a = client.artifacts
    k, ins, lang = body.kind, body.instructions or None, body.language
    nb = body.notebook_id
    sids = body.source_ids or None

    if k == "audio":
        st = await a.generate_audio(
            nb, source_ids=sids, language=lang, instructions=ins,
            audio_format=_enum(AudioFormat, body.audio_format),
            audio_length=_enum(AudioLength, body.audio_length),
        )
    elif k == "video":
        # Google 对视频参数组合有硬性约束，不满足会直接抛错。
        # 前端已做联动，这里再兜一层，顺便把错误翻成中文。
        vfmt = _enum(VideoFormat, body.video_format)
        vstyle = _enum(VideoStyle, body.video_style)
        sprompt = (body.style_prompt or "").strip() or None

        # 注意 VideoStyle.CUSTOM.value == 0，是假值，判断必须用 is / ==，
        # 不能写 if vstyle 这种真值判断。
        if vfmt == VideoFormat.SHORT:
            # 短视频画面风格固定，带风格或描述都会被拒
            vstyle, sprompt = None, None
        elif vfmt == VideoFormat.CINEMATIC:
            # 电影感不支持描述；自定义风格离了描述又不成立，一并退回自动
            sprompt = None
            if vstyle == VideoStyle.CUSTOM:
                vstyle = None
        elif vstyle == VideoStyle.CUSTOM:
            if not sprompt:
                raise HTTPException(400, "选了「自定义」风格就必须填画面描述")
        elif sprompt:
            # 描述只对自定义风格生效，其余情况丢掉而不是报错
            sprompt = None

        st = await a.generate_video(
            nb, source_ids=sids, language=lang, instructions=ins,
            video_format=vfmt, video_style=vstyle, style_prompt=sprompt,
        )
    elif k == "cinematic":
        st = await a.generate_cinematic_video(
            nb, source_ids=sids, language=lang, instructions=ins
        )
    elif k == "quiz":
        st = await a.generate_quiz(
            nb, source_ids=sids, instructions=ins,
            quantity=_enum(QuizQuantity, body.quantity),
            difficulty=_enum(QuizDifficulty, body.difficulty),
        )
    elif k == "flashcards":
        st = await a.generate_flashcards(
            nb, source_ids=sids, instructions=ins,
            quantity=_enum(QuizQuantity, body.quantity),
            difficulty=_enum(QuizDifficulty, body.difficulty),
        )
    elif k == "slides":
        st = await a.generate_slide_deck(
            nb, source_ids=sids, language=lang, instructions=ins,
            slide_format=_enum(SlideDeckFormat, body.slide_format),
            slide_length=_enum(SlideDeckLength, body.slide_length),
        )
    elif k == "infographic":
        st = await a.generate_infographic(
            nb, source_ids=sids, language=lang, instructions=ins,
            orientation=_enum(InfographicOrientation, body.orientation),
            detail_level=_enum(InfographicDetail, body.detail_level),
            style=_enum(InfographicStyle, body.infographic_style),
        )
    elif k == "datatable":
        st = await a.generate_data_table(
            nb, source_ids=sids, language=lang, instructions=ins
        )
    elif k == "mindmap":
        st = await a.generate_mind_map(
            nb, source_ids=sids, language=lang, instructions=ins
        )
    elif k in _REPORTS:
        st = await a.generate_report(
            nb, report_format=_REPORTS[k], source_ids=sids, language=lang,
            custom_prompt=body.custom_prompt or None, extra_instructions=ins,
        )
    else:
        raise HTTPException(400, f"未知类型: {k}")

    tid = st.task_id
    _evict(_TASKS, 60)
    _TASKS[tid] = {
        "kind": k, "state": "running",
        "notebook_id": nb, "started": time.time(),
    }
    _tasks_save()

    async def _run() -> None:
        try:
            await a.wait_for_completion(nb, tid, timeout=_GEN_TIMEOUT.get(k, 900))
            _TASKS[tid]["state"] = "done"
        except Exception as e:  # noqa: BLE001
            # 同样不能把英文异常原样丢给界面
            _TASKS[tid].update(state="error", error=_err_cn(e),
                               detail=f"{type(e).__name__}: {e}"[:300])
        _tasks_save()

    asyncio.create_task(_run())
    return {"task_id": tid, "kind": k}


@app.get("/api/report-suggest/{notebook_id}")
async def api_report_suggest(notebook_id: str) -> list[dict[str, Any]]:
    """AI 推荐做哪些报告，对应网页版 Reports 里的建议。"""
    client = await get_client()
    try:
        items = await client.artifacts.suggest_reports(notebook_id) or []
    except Exception:
        return []
    out = []
    for x in items[:6]:
        out.append({
            "title": getattr(x, "title", "") or "",
            "prompt": getattr(x, "prompt", "") or getattr(x, "description", "") or "",
        })
    return [x for x in out if x["title"]]


@app.delete("/api/task/{task_id}")
async def api_task_dismiss(task_id: str) -> dict[str, Any]:
    """不再跟踪这个生成任务。

    Google 侧没有取消生成的接口，任务照跑；
    这里只是把它从列表里移走，不让它一直占着界面。
    """
    _TASKS.pop(task_id, None)
    _tasks_save()
    return {"ok": True}


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


#: ArtifactType 的 value -> (下载方法, 扩展名)。产物库按 id 下载时用。
_DL_BY_TYPE = {
    "audio": ("download_audio", "mp3"),
    "video": ("download_video", "mp4"),
    "quiz": ("download_quiz", "md"),
    "flashcards": ("download_flashcards", "md"),
    "slide_deck": ("download_slide_deck", "pdf"),
    "infographic": ("download_infographic", "png"),
    "mind_map": ("download_mind_map", "json"),
    "data_table": ("download_data_table", "csv"),
    "report": ("download_report", "md"),
}


def _safe_name(text: str, fallback: str) -> str:
    """把标题清成能当文件名的样子。"""
    bad = '<>:"/\\|?*'
    out = "".join("_" if c in bad or ord(c) < 32 else c for c in (text or "")).strip(" .")
    return out[:60] or fallback


async def _download(notebook_id: str, method: str, ext: str, stem: str,
                    artifact_id: str | None = None):
    client = await get_client()
    path = OUT / f"{stem}.{ext}"
    fn = getattr(client.artifacts, method)
    args = (notebook_id, str(path))
    kwargs: dict[str, Any] = {}
    if artifact_id:
        kwargs["artifact_id"] = artifact_id
    # quiz / flashcards 默认导出 json，这里要 markdown
    if method in ("download_quiz", "download_flashcards"):
        kwargs["output_format"] = "markdown"
    try:
        await fn(*args, **kwargs)
    except TypeError:
        kwargs.pop("output_format", None)
        await fn(*args, **kwargs)
    if not path.exists():
        raise HTTPException(404, "还没有生成好的产物，或它还在生成中")
    return FileResponse(path, filename=path.name)


@app.get("/api/download-artifact/{notebook_id}/{artifact_id}")
async def api_download_artifact(notebook_id: str, artifact_id: str):
    """按产物 id 精确下载。同一类型生成过多次时靠它区分。"""
    client = await get_client()
    art = None
    try:
        items = await client.artifacts.list(notebook_id) or []
        for x in items:
            if (getattr(x, "id", "") or getattr(x, "artifact_id", "")) == artifact_id:
                art = x
                break
    except Exception:
        pass
    if art is None:
        raise HTTPException(404, "找不到这个内容，可能已被删除")

    t = getattr(art, "kind", None) or getattr(art, "type", None)
    tv = getattr(t, "value", str(t or ""))
    if tv not in _DL_BY_TYPE:
        raise HTTPException(400, f"这个类型不支持下载：{tv}")
    method, ext = _DL_BY_TYPE[tv]
    stem = _safe_name(getattr(art, "title", "") or tv, tv)
    return await _download(notebook_id, method, ext, stem, artifact_id)


@app.get("/api/download/{notebook_id}/{kind}")
async def api_download(notebook_id: str, kind: str):
    """按生成类型下载最新的一个（刚生成完的任务行用）。"""
    if kind not in _DL:
        raise HTTPException(400, f"不支持下载: {kind}")
    method, ext = _DL[kind]
    return await _download(notebook_id, method, ext, f"{kind}_{notebook_id[:8]}")


@app.get("/api/artifacts/{notebook_id}")
async def api_artifacts(notebook_id: str) -> list[dict[str, Any]]:
    """已生成的所有产物，对应网页版 Studio 里的列表。"""
    client = await get_client()
    try:
        items = await client.artifacts.list(notebook_id) or []
    except Exception:
        return []
    out = []
    for x in items:
        t = getattr(x, "kind", None) or getattr(x, "type", None)
        urls = getattr(x, "media_urls", None) or []
        out.append(
            {
                "id": getattr(x, "id", "") or getattr(x, "artifact_id", ""),
                "type": getattr(t, "value", str(t or "")),
                "title": getattr(x, "title", "") or "",
                "status": getattr(x, "status_str", "") or "",
                "done": bool(getattr(x, "is_completed", False)),
                "failed": bool(getattr(x, "is_failed", False)),
                "running": bool(getattr(x, "is_processing", False) or getattr(x, "is_pending", False)),
                "created": _fmt_date(getattr(x, "created_at", None), with_time=True),
                "duration": getattr(x, "duration_seconds", None),
                "url": (urls[0] if urls else getattr(x, "url", "") or ""),
            }
        )
    return out


@app.post("/api/artifacts/rename")
async def api_artifact_rename(body: RenameBody) -> dict[str, Any]:
    client = await get_client()
    await client.artifacts.rename(body.notebook_id, body.target_id, body.name)
    return {"ok": True}


@app.delete("/api/artifacts/{notebook_id}/{artifact_id}")
async def api_artifact_del(notebook_id: str, artifact_id: str) -> dict[str, Any]:
    client = await get_client()
    await client.artifacts.delete(notebook_id, artifact_id)
    return {"ok": True}


@app.post("/api/artifacts/retry/{notebook_id}/{artifact_id}")
async def api_artifact_retry(notebook_id: str, artifact_id: str) -> dict[str, Any]:
    """重试失败的生成任务。"""
    client = await get_client()
    st = await client.artifacts.retry_failed(notebook_id, artifact_id)
    return {"task_id": getattr(st, "task_id", "")}


@app.get("/api/artifact-prompt/{notebook_id}/{artifact_id}")
async def api_artifact_prompt(notebook_id: str, artifact_id: str) -> dict[str, Any]:
    """查看这个产物当初是用什么提示词生成的。"""
    client = await get_client()
    try:
        return {"prompt": await client.artifacts.get_prompt(notebook_id, artifact_id) or ""}
    except Exception as e:
        return {"prompt": "", "error": _err_cn(e)}


@app.post("/api/artifacts/export")
async def api_artifact_export(body: dict[str, Any]) -> dict[str, Any]:
    """导出到 Google 文档 / 表格。"""
    client = await get_client()
    et = ExportType.SHEETS if body.get("target") == "sheets" else ExportType.DOCS
    try:
        r = await client.artifacts.export(
            body["notebook_id"],
            body.get("artifact_id"),
            title=body.get("title", "导出"),
            export_type=et,
        )
        return {"ok": True, "result": str(r)[:400]}
    except Exception as e:
        return {"ok": False, "error": _err_cn(e)}


@app.post("/api/slides/revise")
async def api_slide_revise(body: dict[str, Any]) -> dict[str, Any]:
    """修改幻灯片里的某一页。"""
    client = await get_client()
    st = await client.artifacts.revise_slide(
        body["notebook_id"], body["artifact_id"],
        int(body["slide_index"]), body["prompt"],
    )
    return {"task_id": getattr(st, "task_id", "")}


# ---------------------------------------------------------------- 分享给指定用户

@app.get("/api/share-users/{notebook_id}")
async def api_share_users(notebook_id: str) -> dict[str, Any]:
    client = await get_client()
    try:
        st = await client.sharing.get_status(notebook_id)
        # 字段名是 shared_users，元素是 SharedUser(email, permission,
        # display_name, avatar_url)。之前读 users/grants 永远是空列表，
        # 界面上「已共享给」那栏从来不显示任何人。
        out = []
        for u in getattr(st, "shared_users", None) or []:
            perm = getattr(u, "permission", None)
            pname = getattr(perm, "name", "") or ""
            out.append(
                {
                    "email": getattr(u, "email", "") or "",
                    "name": getattr(u, "display_name", "") or "",
                    "role": pname,
                    "role_cn": {"OWNER": "所有者", "EDITOR": "可编辑",
                                "VIEWER": "可查看"}.get(pname, pname or "未知"),
                    "is_owner": pname == "OWNER",
                }
            )
        return {
            "public": bool(getattr(st, "is_public", False)),
            "url": getattr(st, "share_url", "") or "",
            "view_level": getattr(getattr(st, "view_level", None), "name", ""),
            "users": out,
        }
    except Exception as e:
        return {"public": False, "users": [], "error": _err_cn(e)}


@app.post("/api/share-users")
async def api_share_add(body: dict[str, Any]) -> dict[str, Any]:
    """按邮箱共享。role: viewer | editor"""
    from notebooklm.rpc.types import SharePermission

    client = await get_client()
    perm = SharePermission.EDITOR if body.get("role") == "editor" else SharePermission.VIEWER
    await client.sharing.add_user(body["notebook_id"], body["email"], perm)
    return {"ok": True}


@app.delete("/api/share-users/{notebook_id}/{email}")
async def api_share_remove(notebook_id: str, email: str) -> dict[str, Any]:
    client = await get_client()
    await client.sharing.remove_user(notebook_id, email)
    return {"ok": True}


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
        return {"ok": False, "error": _err_cn(e)}


# ---------------------------------------------------------------- 标签

@app.get("/api/labels/{notebook_id}")
async def api_labels(notebook_id: str) -> list[dict[str, Any]]:
    """资料标签，对应网页版 Sources 里的分类。"""
    client = await get_client()
    try:
        items = await client.labels.list(notebook_id) or []
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
        items = await client.labels.sources(notebook_id, label_id) or []
    except Exception:
        return []
    return [{"id": getattr(x, "id", ""), "title": getattr(x, "title", "")} for x in items]


# ---------------------------------------------------------------- 合集

@app.get("/api/collections")
async def api_collections() -> list[dict[str, Any]]:
    """笔记本合集，对应网页版首页的分组。"""
    client = await get_client()
    try:
        items = await client.collections.list() or []
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
        return {"error": _err_cn(e)}
    # SourceGuide 只有 summary 和 keywords 两个字段，没有 questions。
    # 之前读 questions/key_questions 永远是空，界面上那块从来没出现过。
    kws = list(getattr(g, "keywords", None) or ())
    return {
        "summary": getattr(g, "summary", "") or "",
        "keywords": [str(k) for k in kws][:12],
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
    # NotebookDescription 字段是 summary + suggested_topics，
    # 没有 description；之前 getattr 兜底会把整个对象 repr 塞进去。
    try:
        d = await client.notebooks.get_description(notebook_id)
        out["description"] = getattr(d, "summary", "") or ""
        out["topics"] = [
            {
                "question": getattr(t, "question", "") or "",
                "prompt": getattr(t, "prompt", "") or "",
            }
            for t in (getattr(d, "suggested_topics", None) or [])
        ][:6]
    except Exception:
        out["description"] = ""
        out["topics"] = []
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
    """把回答存成笔记，保留 [N] 引用锚点。

    save_answer_as_note 需要完整的 AskResult 对象，无法从 HTTP 重建，
    所以 /api/ask 会把最近的结果缓存在 _LAST_ASK 里。
    缓存失效时退回普通笔记。
    """
    client = await get_client()
    nb = body["notebook_id"]
    ar = _LAST_ASK.get(nb)
    title = body.get("title") or "笔记"
    if ar is not None and getattr(ar, "references", None):
        try:
            await client.chat.save_answer_as_note(nb, ar, title=title)
            return {"ok": True, "rich": True}
        except Exception:
            pass  # 落到普通笔记
    content = body.get("content") or getattr(ar, "answer", "") or ""
    if not content:
        return {"ok": False, "error": "没有可保存的内容"}
    await client.notes.create(nb, title=title[:60], content=content)
    return {"ok": True, "rich": False}


# ---------------------------------------------------------------- 账号设置

#: 官方 docs/quota-limits.md 的配额表（2026-07-09 快照）。
#: tier 数字来自 AccountLimits.tier。深度研究是唯一按月计的配额，
#: 免费账号只有 10 次/月 —— 这是「研究莫名其妙失败」的常见原因。
_TIERS = {
    1: {"name": "标准（免费）", "deep": "10 次/月", "audio": "3 次/天",
        "video": "3 次/天", "cinematic": "不支持", "report": "10 次/天", "chat": "50 次/天"},
    4: {"name": "Google AI Plus", "deep": "3 次/天", "audio": "6 次/天",
        "video": "6 次/天", "cinematic": "不支持", "report": "20 次/天", "chat": "200 次/天"},
    2: {"name": "Google AI Pro", "deep": "20 次/天", "audio": "20 次/天",
        "video": "20 次/天", "cinematic": "2 次/天", "report": "100 次/天", "chat": "500 次/天"},
    3: {"name": "Ultra 20TB", "deep": "75 次/天", "audio": "100 次/天",
        "video": "100 次/天", "cinematic": "10 次/天", "report": "500 次/天", "chat": "2500 次/天"},
    6: {"name": "Ultra 30TB", "deep": "200 次/天", "audio": "200 次/天",
        "video": "200 次/天", "cinematic": "20 次/天", "report": "1000 次/天", "chat": "5000 次/天"},
}


@app.get("/api/settings")
async def api_settings() -> dict[str, Any]:
    """账号级设置与配额。tier 对照官方 quota-limits.md 展开成中文。"""
    client = await get_client()
    out: dict[str, Any] = {"language": "", "tier": None, "quota": {}, "limits": {}}
    try:
        out["language"] = await client.settings.get_output_language() or ""
    except Exception:
        pass
    try:
        lim = await client.settings.get_account_limits()
        tier = getattr(lim, "tier", None)
        out["tier"] = tier
        out["limits"] = {
            "notebook_limit": getattr(lim, "notebook_limit", None),
            "source_limit": getattr(lim, "source_limit", None),
        }
        if isinstance(tier, int) and tier in _TIERS:
            out["quota"] = _TIERS[tier]
        else:
            out["quota"] = {"name": f"未知套餐（tier={tier}）"}
    except Exception as e:
        out["error"] = _err_cn(e)
    return out


@app.post("/api/settings/language")
async def api_set_language(body: dict[str, Any]) -> dict[str, Any]:
    """设置回答的默认语言，设成中文后所有生成都用中文。"""
    client = await get_client()
    lang = await client.settings.set_output_language(body["language"])
    return {"ok": True, "language": lang or body["language"]}


# ---------------------------------------------------------------- 思维导图

@app.get("/api/mindmaps/{notebook_id}")
async def api_mindmaps(notebook_id: str) -> list[dict[str, Any]]:
    client = await get_client()
    try:
        items = await client.mind_maps.list(notebook_id) or []
    except Exception:
        return []
    return [
        {
            "id": getattr(x, "id", ""),
            "title": getattr(x, "title", "") or "思维导图",
            "kind": str(getattr(getattr(x, "kind", ""), "value", "") or ""),
        }
        for x in items
    ]


@app.get("/api/mindmap-tree/{notebook_id}/{mind_map_id}")
async def api_mindmap_tree(notebook_id: str, mind_map_id: str) -> dict[str, Any]:
    """思维导图的树结构，可在界面里展开查看。"""
    client = await get_client()
    try:
        return {"tree": await client.mind_maps.get_tree(notebook_id, mind_map_id) or {}}
    except Exception as e:
        return {"tree": {}, "error": _err_cn(e)}


@app.delete("/api/mindmaps/{notebook_id}/{mind_map_id}")
async def api_mindmap_del(notebook_id: str, mind_map_id: str) -> dict[str, Any]:
    client = await get_client()
    await client.mind_maps.delete(notebook_id, mind_map_id)
    return {"ok": True}


# ---------------------------------------------------------------- 标签绑定资料

@app.post("/api/labels/attach")
async def api_label_attach(body: LabelBody) -> dict[str, Any]:
    """把资料归到某个标签下。"""
    client = await get_client()
    await client.labels.add_sources(body.notebook_id, body.label_id or "", body.source_ids)
    return {"ok": True}


@app.post("/api/labels/detach")
async def api_label_detach(body: LabelBody) -> dict[str, Any]:
    client = await get_client()
    await client.labels.remove_sources(body.notebook_id, body.label_id or "", body.source_ids)
    return {"ok": True}


@app.post("/api/labels/rename")
async def api_label_rename(body: LabelBody) -> dict[str, Any]:
    client = await get_client()
    await client.labels.update(
        body.notebook_id, body.label_id or "",
        name=body.name or None, emoji=body.emoji or None,
    )
    return {"ok": True}


# ---------------------------------------------------------------- 合集补充

@app.get("/api/collections/{collection_id}/notebooks")
async def api_collection_notebooks(collection_id: str) -> list[dict[str, Any]]:
    client = await get_client()
    try:
        items = await client.collections.notebooks(collection_id) or []
    except Exception:
        return []
    return [
        {"id": getattr(x, "id", ""), "title": getattr(x, "title", "") or "未命名"}
        for x in items
    ]


@app.post("/api/collections/remove")
async def api_collection_remove(body: CollectionBody) -> dict[str, Any]:
    client = await get_client()
    await client.collections.remove_notebooks(body.collection_id or "", body.notebook_ids)
    return {"ok": True}


@app.post("/api/collections/rename")
async def api_collection_rename(body: CollectionBody) -> dict[str, Any]:
    client = await get_client()
    await client.collections.rename(body.collection_id or "", body.name)
    return {"ok": True}


# ---------------------------------------------------------------- 分享补充

@app.post("/api/share/view-level")
async def api_share_view_level(body: dict[str, Any]) -> dict[str, Any]:
    """公开链接的可见范围：整个笔记本 或 仅聊天。"""
    from notebooklm.rpc.types import ShareViewLevel

    client = await get_client()
    lv = ShareViewLevel.CHAT_ONLY if body.get("level") == "chat_only" else ShareViewLevel.FULL_NOTEBOOK
    await client.sharing.set_view_level(body["notebook_id"], lv)
    return {"ok": True}


@app.post("/api/share-users/update")
async def api_share_update(body: dict[str, Any]) -> dict[str, Any]:
    """改成员权限。"""
    from notebooklm.rpc.types import SharePermission

    client = await get_client()
    perm = SharePermission.EDITOR if body.get("role") == "editor" else SharePermission.VIEWER
    await client.sharing.update_user(body["notebook_id"], body["email"], perm)
    return {"ok": True}


# ---------------------------------------------------------------- 资料补充

@app.get("/api/source-fresh/{notebook_id}/{source_id}")
async def api_source_fresh(notebook_id: str, source_id: str) -> dict[str, Any]:
    """网页资料是否有更新可拉取。"""
    client = await get_client()
    try:
        return {"stale": bool(await client.sources.check_freshness(notebook_id, source_id))}
    except Exception as e:
        return {"stale": False, "error": _err_cn(e)}


# ---------------------------------------------------------------- 研究补充

@app.delete("/api/research/{notebook_id}/{task_id}")
async def api_research_cancel(notebook_id: str, task_id: str) -> dict[str, Any]:
    """取消正在跑的联网研究。"""
    client = await get_client()
    try:
        await client.research.cancel(notebook_id, task_id)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": _err_cn(e)}


# ---------------------------------------------------------------- 静态

#: 代码版本，用 git 短哈希或文件修改时间，界面上会显示，便于确认跑的是哪一版
def _build_id() -> str:
    import subprocess
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=3)
        if h.returncode == 0 and h.stdout.strip():
            return h.stdout.strip()
    except Exception:
        pass
    # 没有 git 时退回静态文件的最后修改时间
    try:
        t = max((STATIC / f).stat().st_mtime for f in ("app.js", "index.html", "style.css"))
        return time.strftime("%m%d-%H%M", time.localtime(t))
    except Exception:
        return "unknown"


BUILD = _build_id()


@app.get("/api/version")
async def api_version() -> dict[str, Any]:
    return {"build": BUILD, "endpoints": len([r for r in app.routes if hasattr(r, "methods")])}


class NoCacheStatic(StaticFiles):
    """禁用静态文件缓存。

    否则改了 app.js 用户按 F5 仍会跑到旧代码，
    表现为"明明修好了却还是老样子"，极难排查。
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:  # noqa: ANN001
        return False

    async def get_response(self, path: str, scope):  # noqa: ANN001, ANN201
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp


app.mount("/static", NoCacheStatic(directory=str(STATIC)), name="static")


@app.get("/")
async def index():
    """首页也禁缓存，否则改了 index.html 用户仍会看到旧界面。

    FileResponse 会自己算 etag/last-modified，必须在返回后覆盖，
    构造时传 headers 会被它盖掉。
    """
    resp = FileResponse(STATIC / "index.html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


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
