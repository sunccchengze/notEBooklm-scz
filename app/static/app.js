import { smooth } from "/static/smooth.js";

let NBS = [];
let CUR = null;
let CONV = null;
let BUSY = false;
let RMODE = "fast";
let RTASK = null;
let CFG = { length: "default", goal: "default", custom_prompt: "" };

let scChat = null, scNb = null;
const scPages = {};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- 基础

async function api(path, opts) {
  const r = await fetch(path, opts);
  const ct = r.headers.get("content-type") || "";
  if (!ct.includes("json")) {
    if (!r.ok) throw new Error(await r.text());
    return r;
  }
  const j = await r.json();
  // 先说什么错了，再说怎么办 —— 之前反着拼，读起来很别扭
  if (!r.ok || j.error) {
    throw new Error((j.error || "请求失败") + (j.hint ? "\n" + j.hint : ""));
  }
  return j;
}

let tt;
function toast(msg, isErr) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(tt);
  tt = setTimeout(() => (t.className = "toast"), isErr ? 5200 : 2600);
}

/* 单引号一并转义：值要放进 HTML 属性，属性里出现裸的引号会提前闭合。 */
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmt = (s) =>
  esc(s)
    .replace(/\[(\d+)\]/g, '<span class="cite">$1</span>')
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");

/** 单行化：把换行折成空格，用于按钮副标题 */
const oneline = (s) => String(s ?? "").replace(/\s*\n\s*/g, " ").trim();

// ---------------------------------------------------------------- 弹层

let dlgResolve = null;
function dialog(title, desc, placeholder, multiline, initial) {
  // 上一个弹层还挂着就先结算掉，否则那个 await 永远不返回，
  // 调用方会卡死在半途（例如"重命名"点两次）。
  if (dlgResolve) { dlgResolve(null); dlgResolve = null; }
  $("dlgTitle").textContent = title;
  $("dlgDesc").textContent = desc || "";
  const single = $("dlgInput"), multi = $("dlgArea");
  single.style.display = multiline ? "none" : "";
  multi.style.display = multiline ? "" : "none";
  const field = multiline ? multi : single;
  field.value = initial || "";
  field.placeholder = placeholder || "";
  $("mask").classList.add("show");
  setTimeout(() => field.focus(), 60);
  return new Promise((res) => (dlgResolve = res));
}
function closeDialog() {
  $("mask").classList.remove("show");
  if (dlgResolve) { dlgResolve(null); dlgResolve = null; }
}
function confirmDialog() {
  const multi = $("dlgArea").style.display !== "none";
  const v = (multi ? $("dlgArea").value : $("dlgInput").value).trim();
  $("mask").classList.remove("show");
  if (dlgResolve) { dlgResolve(v || null); dlgResolve = null; }
}
$("dlgInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") confirmDialog();
  if (e.key === "Escape") closeDialog();
});
$("dlgArea").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) confirmDialog();
  if (e.key === "Escape") closeDialog();
});

const SPIKE = '<svg><use href="#spike"/></svg>';
const ico = (n) => `<svg class="ico"><use href="#i-${n}"/></svg>`;

// ---------------------------------------------------------------- 启动

async function boot() {
  scChat = smooth($("chat"));
  scNb = smooth($("nbList"));
  ["sources", "studio", "research", "notes"].forEach((t) => {
    scPages[t] = smooth($("page-" + t));
  });

  loadQuota();
  // 显示代码版本，方便确认浏览器跑的是不是最新代码
  try {
    const v = await api("/api/version");
    $("build").textContent = `版本 ${v.build} · ${v.endpoints} 接口`;
  } catch {}

  try {
    const a = await api("/api/auth");
    $("account").textContent = a.ok ? a.email : "未登录";
    if (!a.ok) {
      $("nbList").innerHTML =
        '<div class="empty">未登录<br><br>请在终端运行<br><code>scripts\\nb.ps1 login</code><br><br>然后刷新本页</div>';
      return;
    }
  } catch {
    $("account").textContent = "连接失败";
  }
  await loadNotebooks();
}

async function loadNotebooks() {
  try {
    NBS = await api("/api/notebooks");
    renderNotebooks();
  } catch (e) {
    $("nbList").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

function renderNotebooks() {
  const q = $("search").value.trim().toLowerCase();
  const list = q ? NBS.filter((n) => n.title.toLowerCase().includes(q)) : NBS;
  $("nbList").innerHTML = list.length
    ? list.map((n) => `<div class="nb ${CUR?.id === n.id ? "on" : ""}" data-act="pick" data-a0="${esc(n.id)}">
        <span class="nb-emo">${esc(n.emoji)}</span>
        <div class="nb-body">
          <div class="nb-title">${esc(n.title || "(未命名笔记本)")}</div>
          <div class="nb-meta">${n.sources} 个资料 · ${esc(n.created)}</div>
        </div>
        <span class="nb-ops">
          <button class="x" title="重命名"
            data-act="renameNotebook" data-stop="1" data-a0="${n.id}">✎</button>
          <button class="x" title="删除笔记本"
            data-act="delNotebook" data-stop="1" data-a0="${n.id}">✕</button>
        </span></div>`).join("")
    : '<div class="empty">没有匹配的笔记本</div>';
  scNb?.sync();
}

// ---------------------------------------------------------------- 选择

async function pick(id) {
  CUR = NBS.find((n) => n.id === id);
  CONV = null;
  renderNotebooks();
  $("nbTitle").textContent = CUR.title || "(未命名笔记本)";
  $("nbSub").textContent = `${CUR.sources} 个资料`;
  $("chat").innerHTML = `<div class="welcome">
      <svg class="mark"><use href="#spike"/></svg>
      <h2>${esc(CUR.title || "(未命名笔记本)")}</h2>
      <p>正在载入历史对话…</p>
    </div>`;
  scChat?.sync();
  $("input").disabled = false;
  $("sendBtn").disabled = false;
  $("input").focus();
  $("tasks").innerHTML = "";
  $("rResult").innerHTML = "";
  RTASK = null;
  loadSources();
  loadNotes();
  loadLabels();
  loadHistory();      // ← 先补历史，再给建议
  restoreResearch();  // 刷新页面后把进行中的研究接回来
  restoreTasks();     // 生成任务同理
}

/** 载入过去的问答，让上下文可见 */
async function loadHistory() {
  const mine = CUR?.id;
  let turns = [], err = "";
  try {
    const r = await api(`/api/history/${mine}`);
    turns = r.turns || [];
    err = r.error || "";
    // 接回原会话。不回填的话，刷新后每次提问都会新开一个会话，
    // AI 记不住前面聊过什么，「追问」也就无从谈起。
    if (r.conversation_id) CONV = r.conversation_id;
  } catch (e) { err = e.message; }
  if (CUR?.id !== mine) return;   // 期间切换了笔记本

  if (err) toast(err, true);

  if (!turns.length) {
    $("chat").innerHTML = `<div class="welcome">
        <svg class="mark"><use href="#spike"/></svg>
        <h2>${esc(CUR.title || "(未命名笔记本)")}</h2>
        <p>问点什么，回答会基于这个笔记本里的资料。</p>
      </div>`;
    scChat?.sync();
    loadSuggest();
    return;
  }

  $("chat").innerHTML =
    `<div class="hist-tip">以下是过去的对话
       <button class="mini danger" data-act="clearHistory">清空</button>
     </div>` +
    turns.map((t) => renderTurn(t.q, t.a)).join("");
  // 历史不做入场动画，直接落到底部
  $("chat").querySelectorAll(".msg").forEach((m) => (m.style.animation = "none"));
  requestAnimationFrame(() => {
    const w = $("chat");
    w.scrollTop = w.scrollHeight;
    scChat?.sync();
  });
  loadSuggest();
}

async function clearHistory() {
  if (!CUR) return;
  if (!confirm("清空这个笔记本的全部对话记录？不可恢复。")) return;
  try {
    await api(`/api/history/${CUR.id}`, { method: "DELETE" });
    CONV = null;
    toast("已清空对话");
    loadHistory();
  } catch (e) { toast(e.message, true); }
}

function renderTurn(q, a) {
  const parts = [];
  if (q) {
    parts.push(`<div class="msg me">
      <div class="avatar">你</div>
      <div class="bubble"><div class="who">你</div>
        <div class="text">${esc(q)}</div></div></div>`);
  }
  if (a) {
    parts.push(`<div class="msg ai" data-raw="${esc(a)}" data-q="${esc(q || "").slice(0, 40)}">
      <div class="avatar">${SPIKE}</div>
      <div class="bubble"><div class="who">Notebook</div>
        <div class="text">${fmt(a)}
          <div class="msg-actions">
            <button class="mini" data-act="copyTxt">${ico("copy")}<span>复制</span></button>
            <button class="mini" data-act="saveNote">${ico("note")}<span>存为笔记</span></button>
          </div>
        </div></div></div>`);
  }
  return parts.join("");
}

/** 推荐问题：后端现在返回 {title, prompt}，渲染成两行卡片 */
async function loadSuggest() {
  $("suggest").innerHTML = "";
  if (!CUR) return;
  try {
    const mine = CUR.id;
    const s = await api(`/api/suggest/${mine}`);
    if (CUR?.id !== mine) return;
    renderSuggest(s);
  } catch {}
}

function renderSuggest(items) {
  // 过滤掉空条目，否则会出现点不出内容的空白卡片
  const list = (items || []).filter((x) => {
    const t = typeof x === "string" ? x : (x?.title || x?.prompt || "");
    return String(t).trim();
  });
  if (!list.length) { $("suggest").innerHTML = ""; return; }
  $("suggest").innerHTML = list.map((x) => {
    const title = typeof x === "string" ? x : (x.title || "");
    const prompt = typeof x === "string" ? x : (x.prompt || "");
    // 副标题只在中文时展示，英文原文仅作 tooltip，避免界面中英夹杂
    const sub = oneline(x.en ? "" : prompt);
    const showSub = sub && sub !== title && sub.length <= 40;
    return `<button class="sg" data-p="${esc(prompt)}" data-act="useSuggest"
              title="${esc(oneline(prompt))}">
        <div class="sg-t">${esc(title || oneline(prompt).slice(0, 16))}</div>
        ${showSub ? `<div class="sg-p">${esc(sub)}</div>` : ""}
        <svg class="sg-go"><use href="#i-arrow"/></svg>
      </button>`;
  }).join("");
}

function useSuggest(btn) {
  $("input").value = btn.dataset.p || "";
  send();
}

// ---------------------------------------------------------------- 聊天

function autoGrow(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 200) + "px";
}

function onKey(e) {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
}

function addMsg(who, html) {
  const w = $("chat");
  const wel = w.querySelector(".welcome");
  if (wel) wel.remove();          // 只移除欢迎块，保留历史消息
  const d = document.createElement("div");
  d.className = "msg " + who;
  d.innerHTML = `<div class="avatar">${who === "me" ? "你" : SPIKE}</div>
    <div class="bubble">
      <div class="who">${who === "me" ? "你" : "Notebook"}</div>
      <div class="text">${html}</div>
    </div>`;
  w.appendChild(d);
  scrollChat();
  return d;
}

function scrollChat() {
  const w = $("chat");
  if (scChat) scChat.toBottom();
  else w.scrollTop = w.scrollHeight;
}

async function send() {
  const q = $("input").value.trim();
  if (!q || !CUR || BUSY) return;
  BUSY = true;
  $("input").value = "";
  autoGrow($("input"));
  $("sendBtn").disabled = true;
  $("suggest").innerHTML = "";

  addMsg("me", esc(q));
  const p = addMsg("ai", '<div class="dots"><i></i><i></i><i></i></div>');

  try {
    const r = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, question: q, conversation_id: CONV }),
    });
    CONV = r.conversation_id;

    let html = fmt(r.answer);
    if (r.references?.length) {
      html += `<details class="refs"><summary>${r.references.length} 条引用</summary>` +
        r.references.map((x) => `<div class="ref"><b>[${x.n ?? "·"}]</b>${esc(x.text)}</div>`).join("") +
        `</details>`;
    }
    html += `<div class="msg-actions">
        <button class="mini" data-act="copyTxt">${ico("copy")}<span>复制</span></button>
        <button class="mini" data-act="saveNote">${ico("note")}<span>存为笔记</span></button>
      </div>`;
    p.querySelector(".text").innerHTML = html;
    p.dataset.raw = r.answer;
    p.dataset.q = q;

    // 追问：后端返回纯字符串数组
    if (r.next_steps?.length) {
      renderSuggest(r.next_steps.map((t) => ({ title: t, prompt: t })));
    }
  } catch (e) {
    p.querySelector(".text").innerHTML =
      `<span style="color:var(--error)">${esc(e.message)}</span>`;
  } finally {
    BUSY = false;
    $("sendBtn").disabled = false;
    scrollChat();
  }
}

function copyTxt(btn) {
  navigator.clipboard.writeText(btn.closest(".msg").dataset.raw || "").then(() => {
    // 就地变成勾，比弹 toast 更直观
    if (btn.dataset.busy) return;
    btn.dataset.busy = "1";
    const html = btn.innerHTML;
    btn.innerHTML = ico("check") + "<span>已复制</span>";
    btn.classList.add("done");
    setTimeout(() => {
      btn.innerHTML = html;
      btn.classList.remove("done");
      delete btn.dataset.busy;
    }, 1400);
  });
}

async function saveNote(btn) {
  const m = btn.closest(".msg");
  try {
    const r = await api("/api/notes/from-answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        notebook_id: CUR.id,
        title: (m.dataset.q || "笔记").slice(0, 40),
        content: m.dataset.raw || "",
      }),
    });
    toast(r.rich ? "已存为笔记（保留引用）" : "已存为笔记");
    loadNotes();
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- 笔记本

async function createNotebook() {
  const t = await dialog("新建笔记本", "给它起个名字", "例如：期末复习");
  if (!t) return;
  try {
    const nb = await api("/api/notebooks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: t }),
    });
    toast("已创建");
    await loadNotebooks();
    pick(nb.id);
    if (!$("app").classList.contains("panel-open")) togglePanel();
  } catch (e) { toast(e.message, true); }
}

async function renameNotebook(id) {
  const nb = NBS.find((n) => n.id === id);
  const t = await dialog("重命名笔记本", `当前名称：${nb?.title || ""}`, "新名称");
  if (!t) return;
  try {
    await api("/api/notebooks/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: id, target_id: id, name: t }),
    });
    toast("已重命名");
    await loadNotebooks();
    if (CUR?.id === id) { CUR.title = t; $("nbTitle").textContent = t; }
  } catch (e) { toast(e.message, true); }
}

async function delNotebook(id) {
  const nb = NBS.find((n) => n.id === id);
  if (!confirm(`删除笔记本「${nb?.title || ""}」？\n里面的资料和笔记会一并删除，不可恢复。`)) return;
  try {
    await api(`/api/notebooks/${id}`, { method: "DELETE" });
    toast("已删除");
    if (CUR?.id === id) { CUR = null; resetMain(); }
    loadNotebooks();
  } catch (e) { toast(e.message, true); }
}

function resetMain() {
  $("nbTitle").textContent = "选择一个笔记本";
  $("nbSub").textContent = "";
  $("chat").innerHTML = `<div class="welcome">
      <svg class="mark"><use href="#spike"/></svg>
      <h2>NotebookLM 桌面版</h2>
      <p>从左侧选一个笔记本开始。</p>
    </div>`;
  $("input").disabled = true;
  $("sendBtn").disabled = true;
  $("suggest").innerHTML = "";
  $("srcList").innerHTML = "";
  $("noteList").innerHTML = "";
}

// ---------------------------------------------------------------- 资料

async function loadSources() {
  if (!CUR) return;
  const mine = CUR.id;
  try {
    const s = await api(`/api/sources/${mine}`);
    if (CUR?.id !== mine) return;
    $("srcList").innerHTML = s.length
      ? s.map((x) => `<div class="item">
          <span class="dot ${x.status}"></span>
          <div class="item-body">
            <div class="item-title" title="${esc(x.title)}">${esc(x.title)}</div>
            <div class="item-sub">${x.status === "processing" ? "处理中…"
              : x.status === "error" ? "处理失败"
              : x.words ? esc(x.words) + " 词" : "就绪"}</div>
          </div>
          <span class="nb-ops">
            <button class="x" data-act="srcGuide" data-a0="${esc(x.id)}" title="AI 摘要">☰</button>
            <button class="x" data-act="srcRefresh" data-a0="${esc(x.id)}" title="重新抓取">↻</button>
            <button class="x" data-act="srcRename" data-a0="${esc(x.id)}" title="重命名">✎</button>
            <button class="x" data-act="delSource" data-a0="${esc(x.id)}" title="删除">✕</button>
          </span>
        </div>`).join("")
      : '<div class="empty">还没有资料</div>';
    scPages.sources?.sync();
    if (s.some((x) => x.status === "processing")) {
      setTimeout(() => { if (CUR?.id === mine) loadSources(); }, 4000);
    }
  } catch (e) {
    $("srcList").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

async function addUrl() {
  const u = $("urlInput").value.trim();
  if (!CUR) return toast("请先选择笔记本", true);
  if (!u) return toast("请输入网址", true);
  $("urlInput").value = "";
  toast("正在添加…");
  try {
    await api("/api/sources/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, url: u }),
    });
    toast("已添加，正在处理");
    loadSources();
  } catch (e) { toast(e.message, true); }
}

async function addFile(input) {
  const f = input.files[0];
  if (!f || !CUR) return;
  input.value = "";
  toast(`正在上传 ${f.name}`);
  const fd = new FormData();
  fd.append("notebook_id", CUR.id);
  fd.append("file", f);
  try {
    await api("/api/sources/file", { method: "POST", body: fd });
    toast("上传成功");
    loadSources();
  } catch (e) { toast(e.message, true); }
}

async function addTextPrompt() {
  if (!CUR) return toast("请先选择笔记本", true);
  const c = await dialog("粘贴文字", "把内容作为一份资料加入（Ctrl+Enter 确定）", "粘贴到这里", true);
  if (!c) return;
  try {
    await api("/api/sources/text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, title: oneline(c).slice(0, 30), content: c }),
    });
    toast("已添加");
    loadSources();
  } catch (e) { toast(e.message, true); }
}

async function srcRename(id) {
  const t = await dialog("重命名资料", "", "新标题");
  if (!t) return;
  try {
    await api("/api/sources/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, target_id: id, name: t }),
    });
    loadSources();
  } catch (e) { toast(e.message, true); }
}

async function srcRefresh(id) {
  toast("正在重新抓取…");
  try {
    await api(`/api/sources/refresh/${CUR.id}/${id}`, { method: "POST" });
    toast("已触发更新");
    loadSources();
  } catch (e) { toast(e.message, true); }
}

async function srcGuide(id) {
  toast("正在读取摘要…");
  try {
    const g = await api(`/api/source-guide/${CUR.id}/${id}`);
    if (g.error) return toast(g.error, true);
    let html = `<div class="text">${fmt(g.summary || "（没有摘要）")}`;
    // SourceGuide 只给 summary 和 keywords，没有现成问题
    if (g.keywords?.length) {
      html += `<p class="sec-label" style="margin-top:16px">关键词</p>
        <div class="tags">` +
        g.keywords.map((k) =>
          `<button class="tag as-btn" data-p="${esc("围绕「" + k + "」讲讲这份资料的内容")}"
             data-act="useSuggest" data-then="closeInfo">${esc(k)}</button>`).join("") +
        `</div>`;
    }
    html += `<div class="dialog-actions" style="justify-content:flex-start;margin-top:18px">
        <button class="mini" data-act="srcFulltext" data-a0="${esc(id)}">查看全文</button>
      </div></div>`;
    showInfo("资料摘要", html);
  } catch (e) { toast(e.message, true); }
}

async function srcFulltext(id) {
  toast("正在读取全文…");
  try {
    const r = await api(`/api/source-text/${CUR.id}/${id}`);
    showInfo(r.title || "资料全文",
      `<p class="hint">共 ${r.chars} 字</p><div class="text pre">${esc(r.text)}</div>`);
  } catch (e) { toast(e.message, true); }
}

async function delSource(id) {
  if (!confirm("删除这个资料？")) return;
  try {
    await api(`/api/sources/${CUR.id}/${id}`, { method: "DELETE" });
    loadSources();
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- 笔记

async function loadNotes() {
  if (!CUR) return;
  const mine = CUR.id;
  try {
    const n = await api(`/api/notes/${mine}`);
    if (CUR?.id !== mine) return;
    NOTES_CACHE = n;
    $("noteList").innerHTML = n.length
      ? n.map((x) => `<div class="item"><div class="item-body">
            <div class="item-title">${esc(x.title || "(无标题)")}</div>
            <div class="item-sub">${esc(oneline(x.content).slice(0, 70)) || "（空笔记）"}</div>
          </div>
          <span class="nb-ops">
            <button class="x" data-act="viewNote" data-a0="${esc(x.id)}" title="查看">☰</button>
            <button class="x" data-act="editNote" data-a0="${esc(x.id)}" title="编辑">✎</button>
            <button class="x" data-act="delNote" data-a0="${esc(x.id)}" title="删除">✕</button>
          </span>
        </div>`).join("")
      : '<div class="empty">还没有笔记<br>在回答下方点「存为笔记」</div>';
    scPages.notes?.sync();
  } catch (e) {
    $("noteList").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

let NOTES_CACHE = [];

function viewNote(id) {
  const n = NOTES_CACHE.find((x) => x.id === id);
  if (!n) return;
  showInfo(n.title, `<div class="text">${fmt(n.content)}</div>`);
}

async function delNote(id) {
  if (!confirm("删除这条笔记？")) return;
  try {
    await api(`/api/notes/${CUR.id}/${id}`, { method: "DELETE" });
    loadNotes();
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- 研究

let QUOTA = null;

/* 拉取账号配额。深度研究在免费账号是 10 次/月（唯一按月计的配额），
   用完后 Google 会直接丢弃任务而不给明确报错 ——
   这是「研究莫名其妙失败」最常见的原因，所以要显式告诉用户。 */
let DEEP_USAGE = null;

async function loadQuota() {
  try {
    const r = await fetch("/api/settings");
    QUOTA = await r.json();
  } catch { QUOTA = null; }
  try {
    const r = await fetch("/api/deep-usage");
    DEEP_USAGE = await r.json();
  } catch { DEEP_USAGE = null; }
  renderQuota();
}

function renderQuota() {
  const el = $("quotaTip");
  if (!el) return;
  if (!QUOTA || !QUOTA.quota || !QUOTA.quota.name) { el.textContent = ""; return; }
  const q = QUOTA.quota;
  const isFree = QUOTA.tier === 1;
  if (RMODE === "deep") {
    const u = DEEP_USAGE;
    let usage = "";
    if (u && u.used > 0) {
      const left = isFree ? Math.max(0, 10 - u.used) : null;
      usage = `<br>近 30 天本机已发起 <b>${u.used}</b> 次` +
        (u.wasted ? `（其中 ${u.wasted} 次没拿到结果）` : "") +
        (left !== null ? ` · 估计剩余 <b>${left}</b> 次` : "");
    }
    el.innerHTML = `你的账号：${esc(q.name)} · 深度研究 <b>${esc(q.deep || "未知")}</b>${usage}` +
      (isFree ? `<br><span style="color:var(--error)">免费账号每月仅 10 次，
         用完后 Google 会直接丢弃任务且不给明确报错。建议优先用快速模式。</span>` : "");
  } else {
    el.innerHTML = `你的账号：${esc(q.name)} · 快速研究不占深度配额`;
  }
}

function setMode(m) {
  RMODE = m;
  $("mode-fast").classList.toggle("on", m === "fast");
  $("mode-deep").classList.toggle("on", m === "deep");
  renderQuota();
}

/* 生成任务在刷新后同样会丢进度条。把它们接回来：
   还在跑的继续轮询，已完成的直接给下载按钮。 */
async function restoreTasks() {
  const mine = CUR?.id;
  if (!mine) return;
  let list = [];
  try {
    const r = await fetch(`/api/tasks-latest/${encodeURIComponent(mine)}`);
    list = await r.json();
  } catch { return; }
  if (CUR?.id !== mine || !Array.isArray(list) || !list.length) return;

  $("tasks").innerHTML = "";
  list.reverse().forEach((t) => {
    const name = NAMES[t.kind] || t.kind;
    const row = document.createElement("div");
    row.className = "task";
    if (t.state === "done") {
      row.innerHTML = `<div class="task-name">${esc(name)} 已完成</div>
        ${taskDlButtons(mine, t.kind)}`;
    } else if (t.state === "error") {
      row.innerHTML = `<div class="task-name err">${esc(name)} 失败</div>
        <span class="hint-inline">${esc(t.error || "")}</span>`;
    } else {
      row.innerHTML = `<div class="spin"></div>
        <div class="task-name">${esc(name)} 生成中…（已在后台进行）</div>
        <button class="mini" data-act="dismissTask" data-a0="${esc(t.task_id)}"
          title="Google 侧无法取消，这里只是不再跟踪">不再跟踪</button>`;
      pollTask(t.task_id, t.kind, row, mine);
    }
    $("tasks").appendChild(row);
  });
}

/* 刷新页面后前端会丢掉 task_id，但研究还在服务器上跑。
   这里按笔记本把它找回来，接着轮询或直接显示结果。 */
async function restoreResearch() {
  const mine = CUR?.id;
  if (!mine) return;
  let r;
  try {
    const resp = await fetch(`/api/research-latest/${encodeURIComponent(mine)}`);
    r = await resp.json();
  } catch { return; }
  if (CUR?.id !== mine || !r || r.state === "none" || !r.task_id) return;

  RTASK = r.task_id;
  if (r.state === "running") {
    // 刷新、切 tab、切笔记本再回来都会走到这里，
    // 说"刷新前"不准确，用中性说法。
    $("rResult").innerHTML =
      `<div class="task"><div class="spin"></div>
         <div class="task-name">正在联网研究…（已在后台进行，结果不会丢）</div></div>`;
    pollResearch(r.task_id, mine);
  } else if (r.state === "done" && r.sources?.length) {
    renderResearchResult(r.sources);
  } else if (r.state === "error") {
    if (RMODE === "deep") DEEP_FAILS++;
    $("rResult").innerHTML = errBlock(r.error, r.detail);
  }
}

async function startResearch() {
  const q = $("rqInput").value.trim();
  if (!CUR) return toast("请先选择笔记本", true);
  const mine = CUR.id;
  if (!q) return toast("请输入研究主题", true);

  // 深度研究烧的是月配额，免费账号只有 10 次，
  // 点错一次就少一次，所以必须先确认。
  if (RMODE === "deep" && QUOTA?.tier === 1) {
    const used = DEEP_USAGE?.used || 0;
    const left = Math.max(0, 10 - used);
    const ok = await dialog(
      "确认发起深度研究？",
      `免费账号每月只有 10 次。本机近 30 天已发起 ${used} 次，估计还剩 ${left} 次。\n` +
      `快速模式不占这个配额，多数检索任务够用。\n\n` +
      `确认请输入「确认」，或直接关闭改用快速模式。`,
      "输入 确认 继续",
    );
    if (ok !== "确认") {
      toast("已取消。可以改用快速模式");
      return;
    }
  }

  $("rResult").innerHTML =
    `<div class="task"><div class="spin"></div><div class="task-name">正在联网研究…${
      RMODE === "deep" ? "（深度模式约 5-10 分钟）" : ""}</div></div>`;

  try {
    const r = await api("/api/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: mine, query: q, mode: RMODE }),
    });
    RTASK = r.task_id;
    pollResearch(r.task_id, CUR.id);
  } catch (e) {
    // 之前这里用 .empty 裸文字，既没标题也没重试按钮，
    // 而且 api() 把 hint 拼在 error 前面，读起来是倒的
    if (RMODE === "deep") DEEP_FAILS++;
    $("rResult").innerHTML = errBlock(e.message);
  }
}

async function pollResearch(tid, nbId) {
  for (let i = 0; i < 600; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    if (CUR?.id !== nbId) return;      // 切走了就停，别再改别人的界面
    let s;
    // 这里不能用 api()：状态体里的 error 是「研究失败」的正常字段，
    // api() 见到 error 就抛异常，会被 catch 吞掉导致永远转圈。
    try {
      const r = await fetch(`/api/research/${tid}`);
      s = await r.json();
    } catch { continue; }
    if (!s || !s.state) continue;

    if (s.state === "done") {
      const list = s.sources || [];
      if (!list.length) {
        $("rResult").innerHTML = '<div class="empty">没有找到结果</div>';
        return;
      }
      if (RMODE === "deep") { DEEP_FAILS = 0; loadQuota(); }
      renderResearchResult(list);
      return;
    }
    if (s.state === "error") {
      if (RMODE === "deep") { DEEP_FAILS++; loadQuota(); }
      $("rResult").innerHTML = errBlock(s.error, s.detail);
      return;
    }
    // 还在跑：把真实状态和已用时间显示出来，
    // 否则用户面对一个哑转圈，完全不知道是在动还是卡死了
    if (s.state === "running") {
      const mm = Math.floor((s.elapsed || 0) / 60);
      const ss = (s.elapsed || 0) % 60;
      // 把所有可能的状态都翻出来，别让用户对着一个模糊的兜底文案发呆
      const LABELS = {
        in_progress: "正在搜集资料",
        no_research: "等待 Google 认领任务",
        not_found: "任务已丢失",
        completed: "即将出结果",
        failed: "失败了",
      };
      const label = LABELS[s.status_text] || "正在联网研究";
      // 卡在等待认领超过 2 分钟，提前告诉用户这次多半不会成
      const stuck = s.status_text === "no_research" && (s.elapsed || 0) > 120;
      $("rResult").innerHTML =
        `<div class="task"><div class="spin"></div>
           <div class="task-name">${label}…
             <span class="hint-inline">已用 ${mm} 分 ${ss} 秒</span></div>
         </div>
         <p class="hint" style="margin-top:8px">${stuck
           ? "Google 迟迟没有认领这次任务，多半不会有结果了。建议取消后改用「快速」模式。"
           : "深度模式通常 5-10 分钟。可以切到别的页面，甚至刷新，结果不会丢。"}</p>
         <div class="add-row" style="margin-top:10px">
           <button class="mini danger" data-act="cancelResearch">取消研究</button>
         </div>`;
    }
    if (s.state === "unknown") {
      $("rResult").innerHTML =
        '<div class="empty">任务丢失了，可能是后台重启过，请重新研究</div>';
      return;
    }
  }
  // 30 分钟还没完，给个交代，别一直转圈
  $("rResult").innerHTML =
    '<div class="empty">等待超时。深度研究有时要更久，稍后回到这个页面再看看</div>';
}

async function dismissTask(tid) {
  try { await api(`/api/task/${encodeURIComponent(tid)}`, { method: "DELETE" }); } catch {}
  const btn = document.querySelector(`[data-act="dismissTask"][data-a0="${CSS.escape(tid)}"]`);
  btn?.closest(".task")?.remove();
  toast("已从列表移除，生成仍在 Google 侧继续");
}

async function cancelResearch() {
  if (!CUR || !RTASK) return;
  if (!confirm("取消这次研究？")) return;
  try {
    await api(`/api/research/${encodeURIComponent(CUR.id)}/${encodeURIComponent(RTASK)}`,
              { method: "DELETE" });
    RTASK = null;
    $("rResult").innerHTML = '<div class="empty">已取消</div>';
    toast("已取消");
  } catch (e) { toast(e.message, true); }
}

/* 研究失败的展示块。
   之前复用 .empty（那是「暂无内容」的居中样式），
   长句居中很难读，裸 <br> 还会留下渲染瑕疵。 */
let DEEP_FAILS = 0;

function errBlock(msg, detail) {
  // 上游实测深度研究约 6 分钟可完成，所以不能断言「账号没开放」，
  // 连续失败更可能是 Google 侧临时不稳。
  const hint = (RMODE === "deep" && DEEP_FAILS >= 2)
    ? `<p class="hint" style="margin-top:10px">
         深度模式已连续失败 ${DEEP_FAILS} 次。它正常需要 6 分钟左右，
         这种情况多半是 Google 侧临时不稳，可以隔一会儿再试，
         或先用快速模式拿结果。</p>`
    : "";
  return `<div class="err-box">
      <div class="err-head">
        <svg class="ico"><use href="#i-warn"/></svg>
        <span>这次研究没能完成</span>
      </div>
      <p class="err-msg">${esc(msg || "研究失败")}</p>
      ${hint}
      ${detail ? `<details class="refs">
          <summary>技术细节</summary>
          <div class="ref">${esc(detail)}</div>
        </details>` : ""}
      <div class="err-acts">
        <button class="mini" data-act="startResearch">重新研究</button>
        <button class="mini" data-act="useFastMode">改用快速模式</button>
      </div>
    </div>`;
}

/** 一键切到快速模式并重新发起 */
function useFastMode() {
  setMode("fast");
  startResearch();
}

function renderResearchResult(list) {
  $("rResult").innerHTML =
    `<div class="sec-head" style="margin-top:16px">
       <p class="sec-label">找到 ${list.length} 个来源</p>
       <button class="mini" data-act="toggleAllResearch">全选 / 全不选</button>
     </div>` +
    list.map((x) => `<label class="rsrc">
        <input type="checkbox" checked value="${esc(x.url)}">
        <div style="min-width:0">
          <div class="rsrc-t">${esc(x.title || "无标题")}
            ${x.is_report ? '<span class="badge run">研究报告</span>' : ""}</div>
          <div class="rsrc-u">${esc(x.url || "（报告正文，无链接）")}</div>
        </div></label>`).join("") +
    `<div class="add-row" style="margin-top:12px">
       <button style="flex:1" data-act="importResearch">导入选中的来源</button>
     </div>`;
  scPages.research?.sync();
}

function toggleAllResearch() {
  const boxes = [...document.querySelectorAll("#rResult input[type=checkbox]")];
  const on = boxes.some((b) => !b.checked);
  boxes.forEach((b) => (b.checked = on));
}

async function importResearch() {
  const boxes = [...document.querySelectorAll("#rResult input:checked")];
  const urls = boxes.map((i) => i.value);
  if (!urls.length) return toast("请至少选一个", true);
  const nbId = CUR?.id;
  if (!nbId) return toast("请先选择笔记本", true);

  const btn = document.querySelector('#rResult [data-act="importResearch"]');
  if (btn) { btn.disabled = true; btn.textContent = "正在导入，请稍候…"; }
  toast(`正在导入 ${urls.length} 个来源，可能要等十几秒`);

  try {
    const r = await api("/api/research/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: nbId, task_id: RTASK, urls }),
    });

    // 之前不管结果如何都清空结果并跳转，导入 0 个也看着像成功
    if (!r.count) {
      if (btn) { btn.disabled = false; btn.textContent = "重试导入"; }
      toast("一个都没导入成功，来源可能已失效，换几个再试", true);
      return;
    }

    toast(r.note ? `已导入 ${r.count} 个，${r.note}` : `已导入 ${r.count} 个来源`);
    $("rResult").innerHTML = "";
    switchTab("sources");
    loadSources();
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = "重试导入"; }
    toast(e.message, true);
  }
}

// ---------------------------------------------------------------- 生成

const NAMES = {
  audio: "播客", video: "视频", study: "学习指南", briefing: "简报",
  blog: "博客稿", concept: "概念解释", quiz: "测验", flashcards: "闪卡",
  mindmap: "思维导图", slides: "幻灯片", infographic: "信息图", datatable: "数据表",
};

/* 每种产物可配置的选项。对应网页版点「生成」后弹出的面板。 */
const GEN_OPTS = {
  audio: [
    { key: "audio_format", label: "节目形式", def: "deep_dive", opts: [
      ["deep_dive", "深度对谈"], ["brief", "简报速览"],
      ["critique", "评论分析"], ["debate", "辩论"]] },
    { key: "audio_length", label: "时长", def: "default", opts: [
      ["short", "简短"], ["default", "标准"], ["long", "较长"]] },
  ],
  video: [
    { key: "video_format", label: "视频形式", def: "explainer", opts: [
      ["explainer", "讲解型"], ["brief", "简报"],
      ["cinematic", "电影感"], ["short", "短视频"]] },
    { key: "video_style", label: "画面风格", def: "auto_select", opts: [
      ["auto_select", "自动"], ["classic", "经典"], ["whiteboard", "白板"],
      ["anime", "动漫"], ["kawaii", "可爱"], ["watercolor", "水彩"],
      ["retro_print", "复古印刷"], ["heritage", "古典"], ["paper_craft", "剪纸"],
      ["custom", "自定义"]] },
  ],
  quiz: [
    { key: "quantity", label: "题量", def: "standard", opts: [
      ["fewer", "较少"], ["standard", "标准"], ["more", "较多"]] },
    { key: "difficulty", label: "难度", def: "medium", opts: [
      ["easy", "简单"], ["medium", "中等"], ["hard", "困难"]] },
  ],
  flashcards: [
    { key: "quantity", label: "卡片数量", def: "standard", opts: [
      ["fewer", "较少"], ["standard", "标准"], ["more", "较多"]] },
    { key: "difficulty", label: "难度", def: "medium", opts: [
      ["easy", "简单"], ["medium", "中等"], ["hard", "困难"]] },
  ],
  slides: [
    { key: "slide_format", label: "版式", def: "detailed_deck", opts: [
      ["detailed_deck", "详细讲义"], ["presenter_slides", "演讲用"]] },
    { key: "slide_length", label: "篇幅", def: "default", opts: [
      ["default", "标准"], ["short", "精简"]] },
  ],
  infographic: [
    { key: "orientation", label: "画幅", def: "landscape", opts: [
      ["landscape", "横向"], ["portrait", "纵向"], ["square", "方形"]] },
    { key: "detail_level", label: "信息密度", def: "standard", opts: [
      ["concise", "精简"], ["standard", "标准"], ["detailed", "详尽"]] },
    { key: "infographic_style", label: "视觉风格", def: "auto_select", opts: [
      ["auto_select", "自动"], ["professional", "商务"], ["sketch_note", "手绘笔记"],
      ["bento_grid", "网格"], ["editorial", "杂志"], ["instructional", "教学"],
      ["bricks", "积木"], ["clay", "黏土"], ["anime", "动漫"],
      ["kawaii", "可爱"], ["scientific", "学术"]] },
  ],
};

let GEN_KIND = null;
let GEN_SEL = {};

/** 点生成卡片 → 先弹配置面板 */
async function gen(kind) {
  if (!CUR) return toast("请先选择笔记本", true);
  GEN_KIND = kind;
  GEN_SEL = {};

  const fields = GEN_OPTS[kind] || [];
  fields.forEach((f) => (GEN_SEL[f.key] = f.def));

  let html = "";
  fields.forEach((f) => {
    html += `<p class="sec-label" style="margin-top:16px">${f.label}</p>
      <div class="seg wrap" data-key="${f.key}">` +
      f.opts.map(([v, t]) =>
        `<button data-v="${v}" class="${v === f.def ? "on" : ""}"
           data-act="pickGen">${t}</button>`).join("") +
      `</div>`;
  });

  if (kind === "video") {
    // 画面描述只有「自定义」风格才生效，Google 侧对其余组合会直接报错
    html += `<div id="styleWrap" style="display:none">
        <p class="sec-label" style="margin-top:16px">画面描述</p>
        <input id="genStyle" placeholder="例如：偏冷色调，多用示意图">
        <p class="hint">选「自定义」风格时必须填写，其余风格不支持。</p>
      </div>
      <p class="hint" id="videoTip" style="margin-top:12px"></p>`;
  }

  // 限定资料范围
  const srcs = await getSourceList();
  if (srcs.length) {
    html += `<p class="sec-label" style="margin-top:16px">
        使用哪些资料<span class="hint-inline">（不选＝全部）</span></p>
      <div class="src-pick" id="genSrcs">` +
      srcs.map((x) => `<label><input type="checkbox" value="${esc(x.id)}">
        <span>${esc(x.title)}</span></label>`).join("") + `</div>`;
  }

  html += `<p class="sec-label" style="margin-top:16px">补充要求（选填）</p>
    <textarea id="genIns2" placeholder="例如：重点讲第 3 章，用通俗的比喻"></textarea>`;

  $("genTitle").textContent = `生成${NAMES[kind]}`;
  $("genBody").innerHTML = html;
  $("genMask").classList.add("show");
  if (kind === "video") syncVideoRules();
}

function pickGen(btn) {
  const wrap = btn.closest(".seg");
  [...wrap.children].forEach((b) => b.classList.remove("on"));
  btn.classList.add("on");
  GEN_SEL[wrap.dataset.key] = btn.dataset.v;
  if (GEN_KIND === "video") syncVideoRules();
}

/* 视频的组合约束（SDK 侧会直接拒绝，这里提前挡住）：
     短视频   —— 画面风格固定，不能选风格、不能填描述
     电影感   —— 不支持画面描述
     自定义   —— 必须填画面描述
     其余风格 —— 填了描述也不生效 */
function syncVideoRules() {
  const fmt = GEN_SEL.video_format;
  const styleSeg = document.querySelector('.seg[data-key="video_style"]');
  const wrap = $("styleWrap");
  const tip = $("videoTip");
  if (!styleSeg || !wrap) return;

  const fixedStyle = fmt === "short";

  // 短视频：整组风格禁用并强制回到自动
  styleSeg.classList.toggle("locked", fixedStyle);
  [...styleSeg.children].forEach((b) => (b.disabled = fixedStyle));
  if (fixedStyle && GEN_SEL.video_style !== "auto_select") {
    GEN_SEL.video_style = "auto_select";
    [...styleSeg.children].forEach((b) =>
      b.classList.toggle("on", b.dataset.v === "auto_select"));
  }

  // 电影感不支持画面描述，自定义风格离了描述不成立，一并退回自动
  if (fmt === "cinematic" && GEN_SEL.video_style === "custom") {
    GEN_SEL.video_style = "auto_select";
    [...styleSeg.children].forEach((b) =>
      b.classList.toggle("on", b.dataset.v === "auto_select"));
  }
  const customBtn = styleSeg.querySelector('[data-v="custom"]');
  if (customBtn) customBtn.style.display = fmt === "cinematic" ? "none" : "";

  const needPrompt = !fixedStyle && GEN_SEL.video_style === "custom";
  wrap.style.display = needPrompt ? "" : "none";
  if (!needPrompt && $("genStyle")) $("genStyle").value = "";

  tip.textContent = fixedStyle
    ? "短视频的画面风格由 Google 固定，不能自选。"
    : fmt === "cinematic"
      ? "电影感视频不支持画面描述。"
      : "";
}

function closeGen() { $("genMask").classList.remove("show"); }

let SRC_CACHE = [];
async function getSourceList() {
  try {
    SRC_CACHE = await api(`/api/sources/${CUR.id}`);
    return SRC_CACHE;
  } catch { return []; }
}

/** 配置面板点「开始生成」 */
async function runGen() {
  const kind = GEN_KIND;
  const nbId = CUR?.id;          // 锁定，避免生成期间切换笔记本导致错位
  if (!nbId) return toast("请先选择笔记本", true);
  closeGen();

  const picked = [...document.querySelectorAll("#genSrcs input:checked")].map((i) => i.value);
  const body = {
    notebook_id: nbId,
    kind,
    language: "zh",
    instructions: ($("genIns2")?.value || "").trim() || null,
    source_ids: picked.length ? picked : null,
    ...GEN_SEL,
  };
  if (kind === "video") {
    const sp = $("genStyle")?.value?.trim() || "";
    if (body.video_format === "short") {
      // 短视频风格固定，带上这两个参数 Google 会直接拒绝
      body.video_style = null;
      body.style_prompt = null;
    } else if (body.video_style === "custom") {
      if (!sp) {
        toast("选了「自定义」风格就必须填画面描述", true);
        gen("video");   // 把面板重新打开，别让用户白填
        return;
      }
      body.style_prompt = sp;
    } else {
      // 描述只对自定义风格生效，其余情况一律不传
      body.style_prompt = null;
      if (body.video_format === "cinematic" && body.video_style === "custom") {
        body.video_style = null;
      }
    }
  }

  const row = document.createElement("div");
  row.className = "task";
  row.innerHTML = `<div class="spin"></div>
    <div class="task-name">${NAMES[kind]} 生成中…</div>`;
  $("tasks").prepend(row);

  try {
    const r = await api("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    pollTask(r.task_id, kind, row, nbId);
  } catch (e) {
    row.innerHTML = `<div class="task-name err">${NAMES[kind]} 失败</div>`;
    toast(e.message, true);
  }
}

async function pollTask(tid, kind, row, nbId) {
  for (let i = 0; i < 600; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    if (!document.body.contains(row)) return;   // 那行已被清掉
    let s;
    try { s = await api(`/api/task/${tid}`); } catch { continue; }

    if (s.state === "done") {
      row.innerHTML = `<div class="task-name">${NAMES[kind]} 已完成</div>
        ${taskDlButtons(nbId, kind)}`;
      if ($("page-studio").classList.contains("active")) loadArtifacts();
      toast(`${NAMES[kind]} 生成完成`);
      return;
    }
    if (s.state === "error") {
      row.innerHTML = `<div class="task-name err">${esc(NAMES[kind])} 失败</div>
        <span class="hint-inline">${esc(s.error || "")}</span>`;
      toast(`${NAMES[kind]}失败：${s.error || "未知原因"}`, true);
      return;
    }
  }
  row.innerHTML = `<div class="task-name warn">${NAMES[kind]} 超时</div>`;
}

async function openFolder() {
  try {
    const r = await api("/api/open-folder");
    toast(r.ok ? "已打开下载目录" : "打开失败", !r.ok);
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- 信息弹层

function showInfo(title, html) {
  $("infoTitle").textContent = title;
  $("infoBody").innerHTML = html;
  $("infoMask").classList.add("show");
}
function closeInfo() { $("infoMask").classList.remove("show"); }

// ---------------------------------------------------------------- 标签

async function loadLabels() {
  if (!CUR) return;
  const mine = CUR.id;
  try {
    const ls = await api(`/api/labels/${mine}`);
    if (CUR?.id !== mine) return;
    $("labelList").innerHTML = ls.length
      ? ls.map((l) => `<span class="tag">
           ${esc(l.emoji || "")} ${esc(l.name)}
           <button data-act="delLabel" data-a0="${esc(l.id)}" title="删除标签">✕</button>
         </span>`).join("")
      : '<div class="empty" style="padding:12px">还没有标签</div>';
  } catch { $("labelList").innerHTML = ""; }
}

async function addLabel() {
  if (!CUR) return toast("请先选择笔记本", true);
  const n = await dialog("新建标签", "给资料分类用", "例如：重点章节");
  if (!n) return;
  try {
    await api("/api/labels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, name: n }),
    });
    loadLabels();
  } catch (e) { toast(e.message, true); }
}

async function autoLabel() {
  if (!CUR) return toast("请先选择笔记本", true);
  toast("AI 正在分类…");
  try {
    const r = await api("/api/labels/auto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id }),
    });
    toast(`已生成 ${r.count} 个标签`);
    loadLabels();
  } catch (e) { toast(e.message, true); }
}

async function delLabel(id) {
  try {
    await api(`/api/labels/${CUR.id}/${id}`, { method: "DELETE" });
    loadLabels();
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- 笔记编辑

async function editNote(id) {
  const n = NOTES_CACHE.find((x) => x.id === id);
  if (!n) return toast("找不到这条笔记", true);
  const c = await dialog("编辑笔记", n.title || "(无标题)",
                         "内容（Ctrl+Enter 保存）", true, n.content || "");
  if (c === null) return;
  try {
    await api("/api/notes/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        notebook_id: CUR.id, note_id: id,
        title: n.title || "(无标题)", content: c,
      }),
    });
    toast("已保存");
    loadNotes();
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- 对话设置

async function openSettings() {
  if (!CUR) return toast("请先选择笔记本", true);
  // 拉取已有的自定义人设
  try {
    const c = await api(`/api/chat-config/${CUR.id}`);
    CFG.custom_prompt = c.custom_prompt || "";
  } catch {}
  $("cfgCustom").value = CFG.custom_prompt;
  markSeg("segLen", CFG.length);
  markSeg("segGoal", CFG.goal);
  $("cfgCustomWrap").style.display = CFG.goal === "custom" ? "" : "none";
  $("cfgMask").classList.add("show");
}

function closeSettings() { $("cfgMask").classList.remove("show"); }

function markSeg(id, val) {
  [...$(id).children].forEach((b) => b.classList.toggle("on", b.dataset.v === val));
}

function pickLen(btn) {
  CFG.length = btn.dataset.v;
  markSeg("segLen", CFG.length);
}

function pickGoal(btn) {
  CFG.goal = btn.dataset.v;
  markSeg("segGoal", CFG.goal);
  $("cfgCustomWrap").style.display = CFG.goal === "custom" ? "" : "none";
}

async function saveSettings() {
  const custom = $("cfgCustom").value.trim();
  try {
    await api("/api/chat-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        notebook_id: CUR.id,
        length: CFG.length,
        goal: CFG.goal,
        custom_prompt: CFG.goal === "custom" ? custom : null,
      }),
    });
    CFG.custom_prompt = custom;
    closeSettings();
    const L = { shorter: "简短", default: "默认", longer: "详尽" }[CFG.length];
    toast(`已设置为「${L}」回答`);
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- 产物库

//: 支持下载的产物类型（与后端 _DL_BY_TYPE 对应）
const DOWNLOADABLE = new Set(["audio", "video", "report", "quiz", "flashcards",
                              "mind_map", "infographic", "slide_deck", "data_table"]);

//: 各类型下载下来是什么文件，显示在按钮提示里
const TYPE_EXT = {
  audio: "MP3", video: "MP4", report: "Markdown", quiz: "Markdown",
  flashcards: "Markdown", mind_map: "JSON", infographic: "PNG",
  slide_deck: "PPTX", data_table: "CSV",
};

//: 支持多格式的产物（与后端 _DL_FORMATS 对应）。
//: 幻灯片的 PPTX 是可编辑的，之前只给了 PDF。
const MULTI_FMT = {
  slide_deck: [["pptx", "PPTX 可编辑"], ["pdf", "PDF"]],
  quiz: [["markdown", "Markdown"], ["html", "网页"], ["json", "JSON"]],
  flashcards: [["markdown", "Markdown"], ["html", "网页"], ["json", "JSON"]],
};

//: 生成类型 -> 产物类型
const KIND_TYPE = { slides: "slide_deck", quiz: "quiz", flashcards: "flashcards" };

function taskDlButtons(nbId, kind) {
  const base = `/api/download/${encodeURIComponent(nbId)}/${encodeURIComponent(kind)}`;
  const fmts = MULTI_FMT[KIND_TYPE[kind]];
  if (!fmts) {
    return `<a class="dl-btn" href="${base}" download>
              <svg class="ico"><use href="#i-download"/></svg><span>下载</span></a>`;
  }
  return `<span class="dl-group">` + fmts.map(([f, label], i) =>
    `<a class="dl-btn${i ? " ghost" : ""}" href="${base}?format=${f}" download
        title="下载 ${esc(label)}">${i ? "" :
        '<svg class="ico"><use href="#i-download"/></svg>'}<span>${esc(label)}</span></a>`
  ).join("") + `</span>`;
}

function dlButtons(nbId, x) {
  const base = `/api/download-artifact/${encodeURIComponent(nbId)}/${encodeURIComponent(x.id)}`;
  const fmts = MULTI_FMT[x.type];
  if (!fmts) {
    return `<a class="dl-btn" href="${base}" download
              title="下载 ${esc(TYPE_EXT[x.type] || "文件")}">
              <svg class="ico"><use href="#i-download"/></svg><span>下载</span></a>`;
  }
  // 第一个是推荐格式，其余收在后面
  return `<span class="dl-group">` + fmts.map(([f, label], i) =>
    `<a class="dl-btn${i ? " ghost" : ""}" href="${base}?format=${f}" download
        title="下载 ${esc(label)}">${i ? "" :
        '<svg class="ico"><use href="#i-download"/></svg>'}<span>${esc(label)}</span></a>`
  ).join("") + `</span>`;
}

const TYPE_CN = {
  audio: "播客", video: "视频", report: "报告", quiz: "测验",
  flashcards: "闪卡", mind_map: "思维导图", infographic: "信息图",
  slide_deck: "幻灯片", data_table: "数据表", file: "文件", unknown: "其他",
};

async function loadArtifacts() {
  if (!CUR) return;
  const mine = CUR.id;
  const box = $("artList");
  box.innerHTML = '<div class="empty">载入中…</div>';
  try {
    const items = await api(`/api/artifacts/${mine}`);
    if (CUR?.id !== mine) return;
    if (!items.length) {
      box.innerHTML = '<div class="empty">还没有生成过内容</div>';
      return;
    }
    box.innerHTML = items.map((x) => {
      const st = x.failed ? '<span class="badge err">失败</span>'
        : x.running ? '<span class="badge run">生成中</span>' : "";
      const dur = x.duration ? ` · ${Math.round(x.duration / 60)} 分钟` : "";
      return `<div class="item">
        <div class="item-body">
          <div class="item-title">${esc(x.title || TYPE_CN[x.type] || "未命名")} ${st}</div>
          <div class="item-sub">${esc(TYPE_CN[x.type] || "其他")}${dur}${
            x.created ? " · " + esc(x.created) : ""}</div>
        </div>
        ${x.done && DOWNLOADABLE.has(x.type) ? dlButtons(mine, x) : ""}
        <span class="nb-ops">
          ${x.done ? `<button class="x" data-act="artifactPrompt" data-a0="${esc(x.id)}" title="查看生成提示词">☰</button>` : ""}
          ${x.done ? `<button class="x" data-act="exportArtifact" data-a0="${esc(x.id)}" data-a1="${esc(x.title)}" title="导出到 Google 文档">↗</button>` : ""}
          ${x.failed ? `<button class="x" data-act="retryArtifact" data-a0="${esc(x.id)}" title="重试">↻</button>` : ""}
          <button class="x" data-act="renameArtifact" data-a0="${esc(x.id)}" title="重命名">✎</button>
          <button class="x" data-act="delArtifact" data-a0="${esc(x.id)}" title="删除">✕</button>
        </span></div>`;
    }).join("");
    scPages.studio?.sync();
  } catch (e) {
    box.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

async function delArtifact(id) {
  if (!confirm("删除这个生成内容？")) return;
  try {
    await api(`/api/artifacts/${CUR.id}/${id}`, { method: "DELETE" });
    loadArtifacts();
  } catch (e) { toast(e.message, true); }
}

async function renameArtifact(id) {
  const t = await dialog("重命名", "", "新标题");
  if (!t) return;
  try {
    await api("/api/artifacts/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, target_id: id, name: t }),
    });
    loadArtifacts();
  } catch (e) { toast(e.message, true); }
}

async function retryArtifact(id) {
  toast("正在重试…");
  try {
    await api(`/api/artifacts/retry/${CUR.id}/${id}`, { method: "POST" });
    toast("已重新开始");
    loadArtifacts();
  } catch (e) { toast(e.message, true); }
}

async function artifactPrompt(id) {
  try {
    const r = await api(`/api/artifact-prompt/${CUR.id}/${id}`);
    showInfo("生成时用的提示词",
      `<div class="text">${r.prompt ? fmt(r.prompt) : "（没有记录）"}</div>`);
  } catch (e) { toast(e.message, true); }
}

async function exportArtifact(id, title) {
  toast("正在导出…");
  try {
    const r = await api("/api/artifacts/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, artifact_id: id, title: title || "导出" }),
    });
    toast(r.ok ? "已导出到 Google 文档" : (r.error || "导出失败"), !r.ok);
  } catch (e) { toast(e.message, true); }
}

/* 下载失败时给提示。
   <a download> 直连接口，出错会收到 JSON 而不是文件，
   浏览器不会报错，用户只会觉得"点了没反应"。 */
// 研究还在跑时关页面，提醒一下（研究本身不会断，只是提示）
window.addEventListener("beforeunload", (e) => {
  const running = $("rResult")?.querySelector(".spin");
  if (running) { e.preventDefault(); e.returnValue = ""; }
});

document.addEventListener("click", async (e) => {
  const a = e.target.closest("a.dl-btn");
  if (!a) return;
  e.preventDefault();
  if (a.dataset.busy) return;
  a.dataset.busy = "1";
  const span = a.querySelector("span");
  const old = span ? span.textContent : "";
  if (span) span.textContent = "准备中…";
  try {
    const r = await fetch(a.href);
    const ct = r.headers.get("content-type") || "";
    if (!r.ok || ct.includes("json")) {
      let msg = "下载失败";
      try { const j = await r.json(); msg = j.error || msg; if (j.hint) msg += "\n" + j.hint; } catch {}
      throw new Error(msg);
    }
    const blob = await r.blob();
    const name = decodeURIComponent(
      (r.headers.get("content-disposition") || "").match(/filename\*?=(?:UTF-8'')?"?([^";]+)/)?.[1] || "download");
    const u = URL.createObjectURL(blob);
    const tmp = document.createElement("a");
    tmp.href = u; tmp.download = name;
    document.body.appendChild(tmp); tmp.click(); tmp.remove();
    setTimeout(() => URL.revokeObjectURL(u), 4000);
    if (span) span.textContent = "已下载";
    setTimeout(() => { if (span) span.textContent = old; }, 1600);
  } catch (err) {
    toast(err.message, true);
    if (span) span.textContent = old;
  } finally {
    delete a.dataset.busy;
  }
});

// ---------------------------------------------------------------- 分享

async function openShare() {
  if (!CUR) return toast("请先选择笔记本", true);
  $("shareMask").classList.add("show");
  $("shareUsers").innerHTML = '<div class="empty">载入中…</div>';
  try {
    const r = await api(`/api/share-users/${CUR.id}`);
    $("sharePublic").checked = !!r.public;
    $("shareUsers").innerHTML = r.users?.length
      ? r.users.map((u) => `<div class="item">
          <div class="item-body">
            <div class="item-title">${esc(u.name || u.email)}</div>
            <div class="item-sub">${esc(u.name ? u.email + " · " : "")}${esc(u.role_cn || "")}</div>
          </div>
          ${u.is_owner ? ""
            : `<button class="x" data-act="delShareUser" data-a0="${esc(u.email)}" title="移除">✕</button>`}
        </div>`).join("")
      : '<div class="empty" style="padding:12px">还没有共享给别人</div>';
  } catch (e) {
    $("shareUsers").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

function closeShare() { $("shareMask").classList.remove("show"); }

async function addShareUser() {
  const email = $("shareEmail").value.trim();
  if (!email) return toast("请输入邮箱", true);
  const role = $("shareRole").value;
  try {
    await api("/api/share-users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, email, role }),
    });
    $("shareEmail").value = "";
    toast("已共享");
    openShare();
  } catch (e) { toast(e.message, true); }
}

async function delShareUser(email) {
  try {
    await api(`/api/share-users/${CUR.id}/${encodeURIComponent(email)}`, { method: "DELETE" });
    openShare();
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- 面板

function togglePanel() {
  $("app").classList.toggle("panel-open");
}

function switchTab(name) {
  if (name === "studio" && CUR) loadArtifacts();
  ["sources", "studio", "research", "notes"].forEach((t) => {
    $("tab-" + t).classList.toggle("active", t === name);
    $("page-" + t).classList.toggle("active", t === name);
  });
  scPages[name]?.sync();
}

$("urlInput").addEventListener("keydown", (e) => { if (e.key === "Enter") addUrl(); });
$("rqInput").addEventListener("keydown", (e) => { if (e.key === "Enter") startResearch(); });

/* ------------------------------------------------------------------
   事件委托
   动态列表不再用内联 onclick —— HTML 属性会先解码实体，
   标题里的单引号（如 Andrew Ng 后面那个撇号）会截断 JS 字符串，
   导致整行按钮失效。
   改为把参数放进 data-*，由 HTML 属性转义保证安全。
   ------------------------------------------------------------------ */
const ACTIONS = {};

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  const fn = ACTIONS[el.dataset.act];
  if (!fn) return;
  if (el.dataset.stop) e.stopPropagation();

  // 需要元素本身的（copyTxt / saveNote / pickGen / useSuggest）
  if (["copyTxt", "saveNote", "pickGen", "useSuggest"].includes(el.dataset.act)) {
    fn(el);
  } else {
    const args = [];
    for (let i = 0; el.dataset["a" + i] !== undefined; i++) args.push(el.dataset["a" + i]);
    fn(...args);
  }
  if (el.dataset.then && ACTIONS[el.dataset.then]) ACTIONS[el.dataset.then]();
});

// 暴露给 HTML 内联事件
Object.assign(ACTIONS, {

  pick, createNotebook, send, onKey, autoGrow, copyTxt, saveNote,
  addUrl, addFile, addTextPrompt, delSource, delNote, renderNotebooks,
  setMode, startResearch, importResearch, toggleAllResearch, restoreResearch,
  restoreTasks, cancelResearch, dismissTask, useFastMode, loadQuota, gen, openFolder,
  togglePanel, switchTab, closeDialog, confirmDialog, useSuggest,
  openSettings, closeSettings, pickLen, pickGoal, saveSettings,
  renameNotebook, delNotebook, clearHistory,
  srcRename, srcRefresh, srcGuide, srcFulltext,
  addLabel, autoLabel, delLabel,
  viewNote, editNote, showInfo, closeInfo,
  pickGen, closeGen, runGen,
  loadArtifacts, delArtifact, renameArtifact, retryArtifact,
  artifactPrompt, exportArtifact, openShare, closeShare, addShareUser, delShareUser,
});

Object.assign(window, ACTIONS);

boot();
