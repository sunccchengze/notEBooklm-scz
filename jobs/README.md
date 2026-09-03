# 工单（job）格式

工单是 Agent 与 NotebookLM 之间的**唯一契约**：Agent 只写工单，不直接拼 CLI 命令。
这样做的三个好处 —— ①路线 A（沙箱直连）和路线 B（外部 worker）跑的是同一份工单；
②每次调用都有留痕，能对账、能重放；③校验在动手之前发生，不会跑到一半才失败。

## 目录约定

| 路径 | 含义 | 谁写 |
|---|---|---|
| `jobs/samples/*.job.json` | 样例，永远可跑 `plan` | 人 |
| `jobs/pending/*.job.json` | 待执行 | **Agent** |
| `jobs/running/*.job.json` | worker 已取走 | worker |
| `jobs/done/*.result.json` | 执行结果（成功失败都写） | worker |
| `jobs/.local/` | 本地直跑的结果，**已 gitignore** | 你 |

## 十种 kind

九种产新产物 + 一种重试：

| kind | 产物 | 落盘 | 典型耗时 | 样例 |
|---|---|---|---|---|
| `research_report` | 简报 / 学习指南 / 博客稿 | `.md` | 5–15 min | `report-demo` |
| `podcast` | 音频概览 | `.m4a` | 10–20 min | `podcast-demo` |
| `slides` | 幻灯片 | `.pdf` / `.pptx` | 5–15 min | `slides-demo` |
| `quiz` | 测验 | `.md` / `.json` / `.html` | 5–15 min | `quiz-demo` |
| `flashcards` | 闪卡 | `.md` / `.json` / `.html` | 5–15 min | `flashcards-demo` |
| `video` | 视频概览 | `.mp4` | 15–45 min | `video-demo` |
| `infographic` | 信息图 | `.png` | 5–15 min | `infographic-demo` |
| `data_table` | 数据表 | `.csv` | 5–15 min | `datatable-demo` |
| `mind_map` | 思维导图 | `.json` | 同步返回 | `mindmap-demo` |
| `retry_artifact` | 原地重试失败产物 | 随原类型 | 视原类型 | `retry-demo` |

九种产物共用同一条骨架：**建（或复用）笔记本 → 加来源 → 各自等索引 → 可选提问 →
生成 → 等完成 → 下载**。差别只在最后三步的命令形状，由 `KINDS` 表声明式描述。

两个例外：

- `mind_map` **同步返回** `{mind_map, note_id, kind}`，没有 `task_id`，
  所以没有「等生成」这一步（步数 5 而不是 6），下载用的 id 取自 `note_id`。
- `retry_artifact` 骨架完全不同（**无来源、无提问**，共 4 步）：
  复用笔记本 → `artifact retry` → 等 → 下载。见下面专节。

### `retry_artifact` —— 失败产物的正确重试方式

`AGENTS.md` 写了「任务失败时不要重试 `generate` —— 那会创建重复产物、白烧配额」。
这条 kind 就是那条规矩的工具化替代：`artifact retry` **原地重跑**，
ARTIFACT_ID 不变，`poll` / `wait` 继续对它有效。

```jsonc
{ "id": "rty-20260902-001",
  "kind": "retry_artifact",
  "notebook": { "id": "nb_xxx" },        // **必须给 id**，重试不能新建笔记本
  "generate": {
    "artifact_id": "abc123",             // 必填；支持唯一前缀
    "artifact_kind": "podcast"           // 必填；决定重试后按哪种产物下载
  } }
```

约束（校验会逐条拦）：必须有 `notebook.id`；**不接受** `sources` 和 `ask`
（重试沿用原产物的资料，也不做问答）；`artifact_kind` 必须是九种产物之一。

> 上游还有一个 `generate revise-slide`（改单页幻灯片），但它要求 `-a <已生成的
> slide deck id> --slide <0-based 序号>`，属于对已有产物的**局部编辑**而非新产物，
> 目前没做成工单 kind。`generate cinematic-video` 则是 `generate video
> --format cinematic` 的别名 —— 用 `video` + `format: "cinematic"` 即可覆盖，
> 不需要单独的 kind（需 Google AI Ultra）。

## 通用字段

```jsonc
{
  "id": "rpt-20260902-001",        // 必填，结果靠它对账
  "kind": "research_report",       // 十种，见下表

  "notebook": {
    "title": "调研：xxx",           // 只给 title：先查精确同名的，命中就复用，没命中才建
    "id": null,                     // 给 id = 直接用，不发任何查询
    "reuse": true                   // false = 跳过复用检查，强制新建
  },

  "sources": [                      // 必填，非空；Standard 档上限 50 条
    { "type": "url",      "value": "https://…" },
    { "type": "file",     "value": "docs/x.md" },   // 相对仓库根，必须真实存在
    { "type": "text",     "value": "直接粘贴的正文" },
    { "type": "youtube",  "value": "https://youtube.com/watch?v=…" }
  ],

  "ask": [                          // 可选；同一 conversation 内后轮受益于前轮
    "问题一。完全基于已上传的文档内容回答，不要搜索网络。"
  ],

  "generate": { /* 见下，按 kind 不同 */ },
  "download": { "format": "…" },    // 仅 slides / quiz / flashcards 有；其余产物无此参数
  "output":   { "dir": "out" },
  "policy":   { "confirm_destructive": true }
}
```

## `generate` 段：按 kind 的可选值

可选值全部对着已安装的 notebooklm-py **0.8.1** 的 `--help` 逐个核对过，
核对记录见 [../docs/arena-agent.md](../docs/arena-agent.md) 第 8 节。

### `research_report`
```jsonc
{ "format": "briefing-doc",      // briefing-doc | study-guide | blog-post | custom
  "language": "zh_Hans",
  "prompt": null,                // format=custom 时**必填**
  "prompt_file": null }          // 与 prompt 互斥；长 prompt 用这个
```

### `podcast`
```jsonc
{ "format": "deep-dive",         // deep-dive | brief | critique | debate
  "length": "default",           // short | default | long
  "language": "zh_Hans",
  "prompt": "聚焦某个角度" }      // 走位置参数
```

### `slides`
```jsonc
{ "format": "detailed",          // detailed | presenter
  "length": "default",           // default | short
  "language": "zh_Hans",
  "prompt_file": "prompts/slides/报纸编辑风.txt" }   // 风格库见 prompts/slides/
```
配套 `"download": { "format": "pdf" }` 或 `"pptx"`。

> **slide-deck 没有 `--orientation` 参数**（infographic 才有）。想要竖版，只能把
> "9:16 竖版" 写进 prompt 正文，并且把页数也写进去（例如「严格 8 页，9:16 竖版」）。

### `quiz`
```jsonc
{ "quantity": "standard",        // fewer | standard | more
  "difficulty": "hard",          // easy | medium | hard
  "prompt": null }               // 可选，走位置参数（DESCRIPTION）
```
配套 `"download": { "format": "markdown" }`（或 `json` / `html`）。

> **quiz 没有 `--language`** —— 它的 `--help` 里就没有这个参数（0.8.1 与 0.8.2
> 都核对过），所以给它写 `language` 不会生效。
>
> 但它**有** description 位置参数（Usage 是 `quiz [OPTIONS] [DESCRIPTION]`），
> 所以 `prompt` 是会生效的。

### `flashcards`
```jsonc
{ "quantity": "standard",        // fewer | standard | more
  "difficulty": "hard",          // easy | medium | hard
  "prompt": null }               // 走位置参数
```
配套 `"download": { "format": "markdown" }`（或 `json` / `html`）。
和 quiz 一样**没有 `--language`**。落盘时 `markdown` 会归一成 `.md` 扩展名。

### `video`
```jsonc
{ "format": "explainer",         // explainer | brief | cinematic | short
  "style": "whiteboard",         // auto | custom | classic | whiteboard | kawaii |
                                 // anime | watercolor | retro-print | heritage | paper-craft
  "style_prompt": null,          // 仅 style=custom 时生效，其它风格下会被忽略（校验会拦）
  "language": "zh_Hans",
  "prompt": "某个角度" }          // 走位置参数
```

> `format: "cinematic"` 是 Veo 3 电影级视频：**忽略 `--style`**、耗时 30–40 分钟、
> 需要 Google AI Ultra 订阅。默认 timeout 已按 2700s 配。

### `infographic`
```jsonc
{ "orientation": "portrait",     // landscape | portrait | square
  "detail": "standard",          // concise | standard | detailed
  "style": "bento-grid",         // auto | sketch-note | professional | bento-grid |
                                 // editorial | instructional | bricks | clay |
                                 // anime | kawaii | scientific
  "language": "zh_Hans",
  "prompt": "……" }
```

> **infographic 是唯一有 `--orientation` 的产物**。幻灯片想要竖版只能写进 prompt，
> 信息图可以直接给参数。

### `data_table`
```jsonc
{ "language": "zh_Hans",
  "prompt": "整理成表格：列A | 列B | 列C" }   // **必填**，CLI 的 DESCRIPTION 是必需的
```
`data_table` 没有任何枚举选项 —— 表结构完全由自然语言描述决定。

### `mind_map`
```jsonc
{ "kind": "note-backed",         // interactive | note-backed
  "language": "zh_Hans",
  "prompt": "按某某分类组织" }    // 翻译成 --instructions
```

> `mind_map` 与其它媒体不同：**没有 `--prompt-file`**（CLI 里就没这个参数），
> 只能用 `prompt` → `--instructions`。校验会拦住给 `prompt_file` 的工单。
>
> 而且它**同步返回** `{mind_map, note_id, kind}`，没有 `task_id`，所以计划里
> **没有「等生成」这一步**（5 步而不是 6 步），下载用的 id 取自 `note_id`。

## 两条容易踩的规矩（来自 notebooklm-py 上游）

1. **`research_report` + `format: "custom"` 时 prompt 必须给**。CLI 的 `--append` 在
   `--format custom` 下**被静默忽略**，所以 custom 的 prompt 走位置参数。
   `nbjob.py` 已经按这个分流，校验层也会拦住缺 prompt 的 custom 工单。
2. **`type: "file"` 的路径必须真实存在**。校验时就检查，避免上传阶段才报错。

## 四个子命令

```bash
python3 tools/nbjob.py validate jobs/samples/report-demo.job.json   # 只校验
python3 tools/nbjob.py plan     jobs/samples/report-demo.job.json   # 打印将执行的命令，**不碰网络**
python3 tools/nbjob.py execute  jobs/pending/xxx.job.json           # 真跑（需凭据 + 出网）
python3 tools/nbjob.py ship     jobs/done/xxx.result.json           # 产物分流回传
python3 tools/nbjob.py ship     jobs/done/xxx.result.json --dry-run # 只判定，不上传
```

`plan` 和 `ship --dry-run` 是离线可跑的 —— 在 Google 不可达的沙箱里，
它们是 Agent 唯一能做的自证。

### `ship` 干什么

`execute` 只负责把产物下载到 `out/`。`ship` 决定它怎么回到 Agent 手里：

| 产物 | 通道 | 依据 |
|---|---|---|
| `.md` / `.json` / `.csv` / `.txt` / `.html` 且 ≤ 2 MB | **Git** | 留在 `out/`，`.gitignore` 已放行这些扩展名 |
| 其它（`.m4a` `.mp4` `.pptx` `.pdf` `.png` …）或 > 2 MB | **GitHub Release** | `gh release upload`，文件挪到 `out/.shipped/` |

判定结果写进 result 的 `delivery` 段，Agent 读它就知道该 `git pull` 拿文件、
还是去 `delivery.url` 下载。上传失败时 `channel` 是 `"failed"`、退出码 1，
并且**回滚**本次新建却没传上东西的空 release。

> ⚠️ 上传那一段**只在有 GitHub 出网的机器上成立**（Route B 的 worker）。
> Arena 沙箱里 `uploads.github.com` 被 SNI 封锁，只能跑 `--dry-run`。
> 详见 [../docs/arena-agent.md](../docs/arena-agent.md) 第 6 节的出网边界表。

## 结果文件

```jsonc
{
  "id": "rpt-20260902-001",
  "kind": "research_report",
  "status": "ok",                   // ok | failed | planned
  "failed_at": null,                // 失败时是第几步
  "captured": {                     // ID-pinned 链路，每一步的 id 都留下来了
    "notebook_id": "…", "source_0": "…", "task_id": "…", "artifact_file": "…"
  },
  "answers": [ { "question": "…", "answer": "…", "references": [ { "source_id": "…", "cited_text": "…" } ] } ],
  "steps": [ { "n": 1, "label": "…", "cmd": "…", "exit": 0, "ok": true } ],
  "provenance": {
    "provider": "google-notebooklm", "via": "notebooklm-py-cli",
    "grounding": "user-provided-sources", "ai_generated": true,
    "note": "内容由 Gemini 基于所给来源生成，未经人工核验不得直接当作事实或生产代码。"
  }
}
```

**失败也会写结果文件** —— Agent 读 `status` / `failed_at` / `steps[].stderr` 就知道卡在哪，
不用去猜。而且失败时会**立即停在那一步**，不会继续发起生成任务白白消耗配额。

## 验证状态

四种 kind 的编排逻辑都在 **mock CLI** 上跑通过（全绿路径 + 中途失败即停），
`plan` 输出的命令与 0.8.1 的 `--help` 逐项一致。

但**没有在真实 NotebookLM 上跑通过** —— 沙箱打不通 Google。这是当前最大的未验证项。
