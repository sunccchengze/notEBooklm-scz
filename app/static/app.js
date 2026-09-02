let NBS = [];          // 所有笔记本
let CUR = null;        // 当前笔记本
let CONV = null;       // 当前会话 id
let BUSY = false;

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- 工具

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

let toastTimer;
function toast(msg, isErr) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), isErr ? 5200 : 2600);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// Markdown 轻量渲染 + 引用标记
function fmt(s) {
  return esc(s)
    .replace(/\[(\d+)\]/g, '<span class="cite">$1</span>')
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

// ---------------------------------------------------------------- 启动

async function boot() {
  try {
    const a = await api("/api/auth");
    $("account").textContent = a.ok ? a.email : "未登录";
    if (!a.ok) {
      $("nbList").innerHTML =
        '<div class="empty">未登录<br><br>请在终端运行：<br><code>scripts\\nb.ps1 login</code><br><br>然后刷新本页</div>';
      return;
    }
  } catch (e) {
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
  if (!list.length) {
    $("nbList").innerHTML = '<div class="empty">没有笔记本</div>';
    return;
  }
  $("nbList").innerHTML = list
    .map(
      (n) => `<div class="nb ${CUR?.id === n.id ? "on" : ""}" onclick="pick('${n.id}')">
        <span class="nb-emo">${esc(n.emoji)}</span>
        <div class="nb-body">
          <div class="nb-title">${esc(n.title)}</div>
          <div class="nb-meta">${n.sources} 个资料 · ${esc(n.created)}</div>
        </div>
      </div>`
    )
    .join("");
}

// ---------------------------------------------------------------- 选择笔记本

async function pick(id) {
  CUR = NBS.find((n) => n.id === id);
  CONV = null;
  renderNotebooks();
  $("nbTitle").textContent = CUR.title;
  $("nbSub").textContent = `${CUR.sources} 个资料`;
  $("chat").innerHTML = `<div class="welcome">
      <div class="welcome-icon">${esc(CUR.emoji)}</div>
      <h2>${esc(CUR.title)}</h2>
      <p>问点什么，回答会基于这个笔记本里的资料。</p>
    </div>`;
  $("input").disabled = false;
  $("sendBtn").disabled = false;
  $("input").focus();
  loadSources();
  loadNotes();
  loadSuggest();
}

async function loadSuggest() {
  $("suggest").innerHTML = "";
  if (!CUR) return;
  try {
    const s = await api(`/api/suggest/${CUR.id}`);
    $("suggest").innerHTML = s
      .map((t) => `<button onclick="quickAsk(${JSON.stringify(t).replace(/"/g, "&quot;")})">${esc(t)}</button>`)
      .join("");
  } catch (e) { /* 静默 */ }
}

function quickAsk(t) {
  $("input").value = t;
  send();
}

// ---------------------------------------------------------------- 聊天

function autoGrow(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 190) + "px";
}

function onKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
}

function addMsg(who, html, cls) {
  const w = $("chat");
  if (w.querySelector(".welcome")) w.innerHTML = "";
  const d = document.createElement("div");
  d.className = "msg " + (cls || "");
  d.innerHTML = `<div class="avatar">${who === "me" ? "你" : "📓"}</div>
    <div class="bubble">
      <div class="who">${who === "me" ? "你" : "NotebookLM"}</div>
      <div class="text">${html}</div>
    </div>`;
  w.appendChild(d);
  w.scrollTop = w.scrollHeight;
  return d;
}

async function send() {
  const q = $("input").value.trim();
  if (!q || !CUR || BUSY) return;
  BUSY = true;
  $("input").value = "";
  autoGrow($("input"));
  $("sendBtn").disabled = true;
  $("suggest").innerHTML = "";

  addMsg("me", esc(q), "me");
  const pending = addMsg("ai", '<div class="dots"><i></i><i></i><i></i></div>');

  try {
    const r = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, question: q, conversation_id: CONV }),
    });
    CONV = r.conversation_id;

    let html = fmt(r.answer);
    if (r.references?.length) {
      html += `<details class="refs"><summary>📎 ${r.references.length} 条引用</summary>` +
        r.references.map((x) => `<div class="ref"><b>[${x.n ?? "·"}]</b>${esc(x.text)}</div>`).join("") +
        `</details>`;
    }
    html += `<div class="msg-actions">
        <button class="mini" onclick="copyTxt(this)">复制</button>
        <button class="mini" onclick="saveNote(this)">存为笔记</button>
      </div>`;
    pending.querySelector(".text").innerHTML = html;
    pending.dataset.raw = r.answer;
    pending.dataset.q = q;

    if (r.next_steps?.length) {
      $("suggest").innerHTML = r.next_steps
        .map((t) => `<button onclick="quickAsk(${JSON.stringify(t).replace(/"/g, "&quot;")})">${esc(t)}</button>`)
        .join("");
    }
  } catch (e) {
    pending.querySelector(".text").innerHTML =
      `<span style="color:var(--err)">出错了：${esc(e.message)}</span>`;
  } finally {
    BUSY = false;
    $("sendBtn").disabled = false;
    $("chat").scrollTop = $("chat").scrollHeight;
  }
}

function copyTxt(btn) {
  const raw = btn.closest(".msg").dataset.raw || "";
  navigator.clipboard.writeText(raw).then(() => toast("已复制"));
}

async function saveNote(btn) {
  const m = btn.closest(".msg");
  try {
    await api("/api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        notebook_id: CUR.id,
        title: (m.dataset.q || "笔记").slice(0, 40),
        content: m.dataset.raw || "",
      }),
    });
    toast("已存为笔记");
    loadNotes();
  } catch (e) {
    toast(e.message, true);
  }
}

// ---------------------------------------------------------------- 笔记本管理

async function createNotebook() {
  const t = prompt("新笔记本的名字：");
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
    if (!document.getElementById("app").classList.contains("panel-open")) togglePanel();
  } catch (e) {
    toast(e.message, true);
  }
}

// ---------------------------------------------------------------- 资料

async function loadSources() {
  if (!CUR) return;
  $("srcList").innerHTML = '<div class="empty">加载中…</div>';
  try {
    const s = await api(`/api/sources/${CUR.id}`);
    if (!s.length) {
      $("srcList").innerHTML = '<div class="empty">还没有资料<br>用上面的输入框添加</div>';
      return;
    }
    $("srcList").innerHTML = s
      .map(
        (x) => `<div class="item">
          <span class="dot ${x.status}"></span>
          <div class="item-body">
            <div class="item-title" title="${esc(x.title)}">${esc(x.title)}</div>
            <div class="item-sub">${x.status === "processing" ? "处理中…" : x.words ? x.words + " 词" : ""}</div>
          </div>
          <button class="x" onclick="delSource('${x.id}')" title="删除">×</button>
        </div>`
      )
      .join("");
    // 有处理中的就轮询
    if (s.some((x) => x.status === "processing")) setTimeout(loadSources, 4000);
  } catch (e) {
    $("srcList").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

async function addUrl() {
  const u = $("urlInput").value.trim();
  if (!u || !CUR) return toast(CUR ? "请输入网址" : "请先选择笔记本", true);
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
  } catch (e) {
    toast(e.message, true);
  }
}

async function addFile(input) {
  const f = input.files[0];
  if (!f || !CUR) return;
  input.value = "";
  toast(`正在上传 ${f.name}…`);
  const fd = new FormData();
  fd.append("notebook_id", CUR.id);
  fd.append("file", f);
  try {
    await api("/api/sources/file", { method: "POST", body: fd });
    toast("上传成功");
    loadSources();
  } catch (e) {
    toast(e.message, true);
  }
}

async function addTextPrompt() {
  if (!CUR) return toast("请先选择笔记本", true);
  const c = prompt("粘贴文字内容：");
  if (!c) return;
  try {
    await api("/api/sources/text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, title: c.slice(0, 30), content: c }),
    });
    toast("已添加");
    loadSources();
  } catch (e) {
    toast(e.message, true);
  }
}

async function delSource(id) {
  if (!confirm("删除这个资料？")) return;
  try {
    await api(`/api/sources/${CUR.id}/${id}`, { method: "DELETE" });
    loadSources();
  } catch (e) {
    toast(e.message, true);
  }
}

// ---------------------------------------------------------------- 笔记

async function loadNotes() {
  if (!CUR) return;
  try {
    const n = await api(`/api/notes/${CUR.id}`);
    $("noteList").innerHTML = n.length
      ? n.map((x) => `<div class="item"><div class="item-body">
            <div class="item-title">${esc(x.title)}</div>
            <div class="item-sub">${esc((x.content || "").slice(0, 60))}</div>
          </div></div>`).join("")
      : '<div class="empty">还没有笔记<br>在回答下方点「存为笔记」</div>';
  } catch (e) {
    $("noteList").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

// ---------------------------------------------------------------- 生成

const NAMES = {
  audio: "🎙️ 播客", video: "🎬 视频", study: "📖 学习指南",
  briefing: "📋 简报", quiz: "❓ 测验", flashcards: "🗂️ 闪卡",
  mindmap: "🕸️ 思维导图", slides: "📊 幻灯片",
  infographic: "🖼️ 信息图", blog: "✍️ 博客稿",
};

async function gen(kind) {
  if (!CUR) return toast("请先选择笔记本", true);
  const ins = $("genIns").value.trim();
  const row = document.createElement("div");
  row.className = "task";
  row.innerHTML = `<div class="spin"></div><div class="task-name">${NAMES[kind]} 生成中…</div>`;
  $("tasks").prepend(row);

  try {
    const r = await api("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, kind, instructions: ins || null }),
    });
    poll(r.task_id, kind, row, CUR.id);
  } catch (e) {
    row.innerHTML = `<div class="task-name" style="color:var(--err)">${NAMES[kind]} 失败</div>`;
    toast(e.message, true);
  }
}

async function poll(taskId, kind, row, nbId) {
  for (let i = 0; i < 600; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    let s;
    try {
      s = await api(`/api/task/${taskId}`);
    } catch { continue; }

    if (s.state === "done") {
      row.innerHTML = `<div class="task-name">${NAMES[kind]} 完成</div>
        <a class="dl" href="/api/download/${nbId}/${kind}" download>下载</a>`;
      toast(`${NAMES[kind]} 生成完成`);
      return;
    }
    if (s.state === "error") {
      row.innerHTML = `<div class="task-name" style="color:var(--err)">${NAMES[kind]} 失败</div>`;
      toast(s.error || "生成失败", true);
      return;
    }
  }
  row.innerHTML = `<div class="task-name" style="color:var(--warn)">${NAMES[kind]} 超时</div>`;
}

// ---------------------------------------------------------------- 面板

function togglePanel() {
  const a = $("app");
  a.classList.toggle("panel-open");
  $("panelBtn").textContent = a.classList.contains("panel-open")
    ? "资料 & 生成 ◂" : "资料 & 生成 ▸";
}

function switchTab(name) {
  ["sources", "studio", "notes"].forEach((t) => {
    $("tab-" + t).classList.toggle("active", t === name);
    $("page-" + t).classList.toggle("active", t === name);
  });
}

$("urlInput").addEventListener("keydown", (e) => { if (e.key === "Enter") addUrl(); });

boot();
