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
  if (!r.ok || j.error) throw new Error((j.hint ? j.hint + "\n" : "") + (j.error || "请求失败"));
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
function dialog(title, desc, placeholder, multiline) {
  $("dlgTitle").textContent = title;
  $("dlgDesc").textContent = desc || "";
  const single = $("dlgInput"), multi = $("dlgArea");
  single.style.display = multiline ? "none" : "";
  multi.style.display = multiline ? "" : "none";
  const field = multiline ? multi : single;
  field.value = "";
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

// ---------------------------------------------------------------- 启动

async function boot() {
  scChat = smooth($("chat"));
  scNb = smooth($("nbList"));
  ["sources", "studio", "research", "notes"].forEach((t) => {
    scPages[t] = smooth($("page-" + t));
  });

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
          <div class="nb-title">${esc(n.title)}</div>
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
  $("nbTitle").textContent = CUR.title;
  $("nbSub").textContent = `${CUR.sources} 个资料`;
  $("chat").innerHTML = `<div class="welcome">
      <svg class="mark"><use href="#spike"/></svg>
      <h2>${esc(CUR.title)}</h2>
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
}

/** 载入过去的问答，让上下文可见 */
async function loadHistory() {
  const mine = CUR?.id;
  let turns = [], err = "";
  try {
    const r = await api(`/api/history/${mine}`);
    turns = r.turns || [];
    err = r.error || "";
  } catch (e) { err = e.message; }
  if (CUR?.id !== mine) return;   // 期间切换了笔记本

  if (err) toast(err, true);

  if (!turns.length) {
    $("chat").innerHTML = `<div class="welcome">
        <svg class="mark"><use href="#spike"/></svg>
        <h2>${esc(CUR.title)}</h2>
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
            <button class="mini" data-act="copyTxt">复制</button>
            <button class="mini" data-act="saveNote">存为笔记</button>
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
  if (!items?.length) { $("suggest").innerHTML = ""; return; }
  $("suggest").innerHTML = items.map((x) => {
    const title = typeof x === "string" ? x : (x.title || "");
    const prompt = typeof x === "string" ? x : (x.prompt || "");
    // 副标题只在中文时展示，英文原文仅作 tooltip，避免界面中英夹杂
    const sub = oneline(x.en ? "" : prompt);
    const showSub = sub && sub !== title && sub.length <= 40;
    return `<button class="sg" data-p="${esc(prompt)}" data-act="useSuggest"
              title="${esc(oneline(prompt))}">
        <div class="sg-t">${esc(title || oneline(prompt).slice(0, 16))}</div>
        ${showSub ? `<div class="sg-p">${esc(sub)}</div>` : ""}
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
        <button class="mini" data-act="copyTxt">复制</button>
        <button class="mini" data-act="saveNote">存为笔记</button>
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
  navigator.clipboard.writeText(btn.closest(".msg").dataset.raw || "")
    .then(() => toast("已复制"));
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
              : x.status === "error" ? "处理失败" : x.words ? x.words + " 词" : "就绪"}</div>
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
    if (g.questions?.length) {
      html += `<p class="sec-label" style="margin-top:16px">这份资料能回答</p>` +
        g.questions.map((q) =>
          `<button class="sg" style="width:100%;margin-bottom:6px"
             data-p="${esc(q)}" data-act="useSuggest" data-then="closeInfo">
             <div class="sg-t">${esc(q)}</div></button>`).join("");
    }
    html += `</div>`;
    showInfo("资料摘要", html);
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
            <div class="item-title">${esc(x.title)}</div>
            <div class="item-sub">${esc(oneline(x.content).slice(0, 70))}</div>
          </div>
          <span class="nb-ops">
            <button class="x" data-act="viewNote" data-a0="${esc(x.id)}" title="查看">☰</button>
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

function setMode(m) {
  RMODE = m;
  $("mode-fast").classList.toggle("on", m === "fast");
  $("mode-deep").classList.toggle("on", m === "deep");
}

async function startResearch() {
  const q = $("rqInput").value.trim();
  if (!CUR) return toast("请先选择笔记本", true);
  const mine = CUR.id;
  if (!q) return toast("请输入研究主题", true);

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
    $("rResult").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

async function pollResearch(tid, nbId) {
  for (let i = 0; i < 600; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    if (CUR?.id !== nbId) return;      // 切走了就停，别再改别人的界面
    let s;
    try { s = await api(`/api/research/${tid}`); } catch { continue; }

    if (s.state === "done") {
      const list = s.sources || [];
      if (!list.length) {
        $("rResult").innerHTML = '<div class="empty">没有找到结果</div>';
        return;
      }
      $("rResult").innerHTML =
        `<p class="sec-label" style="margin-top:16px">找到 ${list.length} 个来源</p>` +
        list.map((x) => `<label class="rsrc">
            <input type="checkbox" checked value="${esc(x.url)}">
            <div style="min-width:0">
              <div class="rsrc-t">${esc(x.title || "无标题")}</div>
              <div class="rsrc-u">${esc(x.url)}</div>
            </div></label>`).join("") +
        `<div class="add-row" style="margin-top:12px">
           <button style="flex:1" data-act="importResearch">导入选中的来源</button>
         </div>`;
      scPages.research?.sync();
      return;
    }
    if (s.state === "error") {
      $("rResult").innerHTML = `<div class="empty">${esc(s.error || "研究失败")}</div>`;
      return;
    }
  }
}

async function importResearch() {
  const urls = [...document.querySelectorAll("#rResult input:checked")].map((i) => i.value);
  if (!urls.length) return toast("请至少选一个", true);
  toast("正在导入…");
  try {
    const r = await api("/api/research/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, task_id: RTASK, urls }),
    });
    toast(`已导入 ${r.count} 个来源`);
    $("rResult").innerHTML = "";
    switchTab("sources");
    loadSources();
  } catch (e) { toast(e.message, true); }
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
      ["retro_print", "复古印刷"], ["heritage", "古典"], ["paper_craft", "剪纸"]] },
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
    html += `<p class="sec-label" style="margin-top:16px">画面补充描述（选填）</p>
      <input id="genStyle" placeholder="例如：偏冷色调，多用示意图">`;
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
}

function pickGen(btn) {
  const wrap = btn.closest(".seg");
  [...wrap.children].forEach((b) => b.classList.remove("on"));
  btn.classList.add("on");
  GEN_SEL[wrap.dataset.key] = btn.dataset.v;
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
  const sp = $("genStyle")?.value?.trim();
  if (sp) body.style_prompt = sp;

  const row = document.createElement("div");
  row.className = "task";
  row.innerHTML = `<div class="spin"></div><div class="task-name">${NAMES[kind]} 生成中…</div>`;
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
        <a class="dl" href="/api/download/${nbId}/${kind}" download>下载</a>`;
      toast(`${NAMES[kind]} 生成完成`);
      return;
    }
    if (s.state === "error") {
      row.innerHTML = `<div class="task-name err">${NAMES[kind]} 失败</div>`;
      toast(s.error || "生成失败", true);
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

async function editNote(id, title, content) {
  const c = await dialog("编辑笔记", title, "内容", true);
  if (c === null) return;
  try {
    await api("/api/notes/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, note_id: id, title, content: c }),
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
          <div class="item-title">${esc(x.title || TYPE_CN[x.type] || x.type)} ${st}</div>
          <div class="item-sub">${TYPE_CN[x.type] || x.type}${dur} · ${esc(x.created)}</div>
        </div>
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
          <div class="item-body"><div class="item-title">${esc(u.email)}</div>
          <div class="item-sub">${u.role === "EDITOR" ? "可编辑" : "可查看"}</div></div>
          <button class="x" data-act="delShareUser" data-a0="${esc(u.email)}" title="移除">✕</button>
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
  setMode, startResearch, importResearch, gen, openFolder,
  togglePanel, switchTab, closeDialog, confirmDialog, useSuggest,
  openSettings, closeSettings, pickLen, pickGoal, saveSettings,
  renameNotebook, delNotebook, clearHistory,
  srcRename, srcRefresh, srcGuide,
  addLabel, autoLabel, delLabel,
  viewNote, editNote, showInfo, closeInfo,
  pickGen, closeGen, runGen,
  loadArtifacts, delArtifact, renameArtifact, retryArtifact,
  artifactPrompt, exportArtifact, openShare, closeShare, addShareUser, delShareUser,
});

Object.assign(window, ACTIONS);

boot();
