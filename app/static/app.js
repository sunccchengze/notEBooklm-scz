let NBS = [];
let CUR = null;
let CONV = null;
let BUSY = false;
let RMODE = "fast";
let RTASK = null;

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

const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const fmt = (s) =>
  esc(s)
    .replace(/\[(\d+)\]/g, '<span class="cite">$1</span>')
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");

// 弹层（替代原生 prompt，保持设计一致）
let dlgResolve = null;
function dialog(title, desc, placeholder) {
  $("dlgTitle").textContent = title;
  $("dlgDesc").textContent = desc || "";
  $("dlgInput").value = "";
  $("dlgInput").placeholder = placeholder || "";
  $("mask").classList.add("show");
  setTimeout(() => $("dlgInput").focus(), 50);
  return new Promise((res) => (dlgResolve = res));
}
function closeDialog() {
  $("mask").classList.remove("show");
  if (dlgResolve) { dlgResolve(null); dlgResolve = null; }
}
function confirmDialog() {
  const v = $("dlgInput").value.trim();
  $("mask").classList.remove("show");
  if (dlgResolve) { dlgResolve(v || null); dlgResolve = null; }
}
$("dlgInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") confirmDialog();
  if (e.key === "Escape") closeDialog();
});

const SPIKE = '<svg><use href="#spike"/></svg>';

// ---------------------------------------------------------------- 启动

async function boot() {
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
    ? list.map((n) => `<div class="nb ${CUR?.id === n.id ? "on" : ""}" onclick="pick('${n.id}')">
        <span class="nb-emo">${esc(n.emoji)}</span>
        <div class="nb-body">
          <div class="nb-title">${esc(n.title)}</div>
          <div class="nb-meta">${n.sources} 个资料 · ${esc(n.created)}</div>
        </div></div>`).join("")
    : '<div class="empty">没有匹配的笔记本</div>';
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
      <p>问点什么，回答会基于这个笔记本里的资料。</p>
    </div>`;
  $("input").disabled = false;
  $("sendBtn").disabled = false;
  $("input").focus();
  $("tasks").innerHTML = "";
  $("rResult").innerHTML = "";
  RTASK = null;
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
      .map((t) => `<button onclick='quickAsk(${JSON.stringify(t)})'>${esc(t)}</button>`)
      .join("");
  } catch {}
}

function quickAsk(t) {
  $("input").value = t;
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
  if (w.querySelector(".welcome")) w.innerHTML = "";
  const d = document.createElement("div");
  d.className = "msg " + who;
  d.innerHTML = `<div class="avatar">${who === "me" ? "你" : SPIKE}</div>
    <div class="bubble">
      <div class="who">${who === "me" ? "你" : "Notebook"}</div>
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
        <button class="mini" onclick="copyTxt(this)">复制</button>
        <button class="mini" onclick="saveNote(this)">存为笔记</button>
      </div>`;
    p.querySelector(".text").innerHTML = html;
    p.dataset.raw = r.answer;
    p.dataset.q = q;

    if (r.next_steps?.length) {
      $("suggest").innerHTML = r.next_steps
        .map((t) => `<button onclick='quickAsk(${JSON.stringify(t)})'>${esc(t)}</button>`)
        .join("");
    }
  } catch (e) {
    p.querySelector(".text").innerHTML =
      `<span style="color:var(--error)">${esc(e.message)}</span>`;
  } finally {
    BUSY = false;
    $("sendBtn").disabled = false;
    $("chat").scrollTop = $("chat").scrollHeight;
  }
}

function copyTxt(btn) {
  navigator.clipboard.writeText(btn.closest(".msg").dataset.raw || "")
    .then(() => toast("已复制"));
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

// ---------------------------------------------------------------- 资料

async function loadSources() {
  if (!CUR) return;
  try {
    const s = await api(`/api/sources/${CUR.id}`);
    $("srcList").innerHTML = s.length
      ? s.map((x) => `<div class="item">
          <span class="dot ${x.status}"></span>
          <div class="item-body">
            <div class="item-title" title="${esc(x.title)}">${esc(x.title)}</div>
            <div class="item-sub">${x.status === "processing" ? "处理中…"
              : x.status === "error" ? "处理失败" : x.words ? x.words + " 词" : "就绪"}</div>
          </div>
          <button class="x" onclick="delSource('${x.id}')" title="删除">✕</button>
        </div>`).join("")
      : '<div class="empty">还没有资料</div>';
    if (s.some((x) => x.status === "processing")) setTimeout(loadSources, 4000);
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
  const c = await dialog("粘贴文字", "把内容作为一份资料加入", "粘贴到这里");
  if (!c) return;
  try {
    await api("/api/sources/text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, title: c.slice(0, 30), content: c }),
    });
    toast("已添加");
    loadSources();
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
  try {
    const n = await api(`/api/notes/${CUR.id}`);
    $("noteList").innerHTML = n.length
      ? n.map((x) => `<div class="item"><div class="item-body">
            <div class="item-title">${esc(x.title)}</div>
            <div class="item-sub">${esc((x.content || "").slice(0, 70))}</div>
          </div>
          <button class="x" onclick="delNote('${x.id}')" title="删除">✕</button>
        </div>`).join("")
      : '<div class="empty">还没有笔记<br>在回答下方点「存为笔记」</div>';
  } catch (e) {
    $("noteList").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
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
  if (!q) return toast("请输入研究主题", true);

  $("rResult").innerHTML =
    `<div class="task"><div class="spin"></div><div class="task-name">正在联网研究…${
      RMODE === "deep" ? "（深度模式约 5-10 分钟）" : ""}</div></div>`;

  try {
    const r = await api("/api/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: CUR.id, query: q, mode: RMODE }),
    });
    RTASK = r.task_id;
    pollResearch(r.task_id);
  } catch (e) {
    $("rResult").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

async function pollResearch(tid) {
  for (let i = 0; i < 600; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    let s;
    try { s = await api(`/api/research/${tid}`); } catch { continue; }

    if (s.state === "done") {
      const list = s.sources || [];
      if (!list.length) {
        $("rResult").innerHTML = '<div class="empty">没有找到结果</div>';
        return;
      }
      $("rResult").innerHTML =
        `<p class="sec-label">找到 ${list.length} 个来源</p>` +
        list.map((x, i) => `<label class="rsrc">
            <input type="checkbox" checked value="${esc(x.url)}">
            <div style="min-width:0">
              <div class="rsrc-t">${esc(x.title || "无标题")}</div>
              <div class="rsrc-u">${esc(x.url)}</div>
            </div></label>`).join("") +
        `<div class="add-row" style="margin-top:12px">
           <button style="flex:1" onclick="importResearch()">导入选中的来源</button>
         </div>`;
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
    pollTask(r.task_id, kind, row, CUR.id);
  } catch (e) {
    row.innerHTML = `<div class="task-name err">${NAMES[kind]} 失败</div>`;
    toast(e.message, true);
  }
}

async function pollTask(tid, kind, row, nbId) {
  for (let i = 0; i < 600; i++) {
    await new Promise((r) => setTimeout(r, 3000));
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

// ---------------------------------------------------------------- 面板

function togglePanel() {
  $("app").classList.toggle("panel-open");
}

function switchTab(name) {
  ["sources", "studio", "research", "notes"].forEach((t) => {
    $("tab-" + t).classList.toggle("active", t === name);
    $("page-" + t).classList.toggle("active", t === name);
  });
}

$("urlInput").addEventListener("keydown", (e) => { if (e.key === "Enter") addUrl(); });
$("rqInput").addEventListener("keydown", (e) => { if (e.key === "Enter") startResearch(); });

boot();
