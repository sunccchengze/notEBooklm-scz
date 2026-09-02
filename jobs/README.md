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

## Schema（kind = `research_report`）

```jsonc
{
  "id": "rpt-20260902-001",        // 必填，结果靠它对账
  "kind": "research_report",       // 目前只支持这一种

  "notebook": {
    "title": "调研：xxx",           // title 和 id 至少给一个
    "id": null                      // 给 id = 复用已有笔记本（优先！别动不动新建）
  },

  "sources": [                      // 必填，非空；Standard 档上限 50 条
    { "type": "url",      "value": "https://…" },
    { "type": "file",     "value": "docs/x.md" },   // 相对仓库根，必须真实存在
    { "type": "text",     "value": "直接粘贴的正文" },
    { "type": "youtube",  "value": "https://youtube.com/watch?v=…" }
  ],

  "ask": [                          // 可选；同一 conversation 内后轮受益于前轮
    "问题一。完全基于已上传的文档内容回答，不要搜索网络。",
    "问题二。完全基于文档回答。"
  ],

  "report": {
    "format": "briefing-doc",       // briefing-doc | study-guide | blog-post | custom
    "prompt": null,                 // format=custom 时**必填**（走位置参数）
    "language": "zh_Hans"           // 可选；zh_Hans / zh_Hant / en / ja …
  },

  "output": { "dir": "out" },       // 报告落盘目录
  "policy": { "confirm_destructive": true }
}
```

### 两条容易踩的规矩（来自 notebooklm-py 上游）

1. **`format: "custom"` 时 prompt 必须给**。CLI 的 `--append` 在 `--format custom` 下
   **被静默忽略**，所以 custom 的 prompt 走位置参数。`nbjob.py` 已经按这个分流，
   校验层也会拦住缺 prompt 的 custom 工单。
2. **`type: "file"` 的路径必须真实存在**。校验时就检查，避免上传阶段才报错。

## 三个子命令

```bash
python3 tools/nbjob.py validate jobs/samples/report-demo.job.json   # 只校验
python3 tools/nbjob.py plan     jobs/samples/report-demo.job.json   # 打印将执行的命令，**不碰网络**
python3 tools/nbjob.py execute  jobs/pending/xxx.job.json           # 真跑（需凭据 + 出网）
```

`plan` 是离线可跑的 —— 在 Google 不可达的沙箱里，它是 Agent 唯一能做的自证。

## 结果文件

```jsonc
{
  "id": "rpt-20260902-001",
  "status": "ok",                   // ok | failed | planned
  "failed_at": null,                // 失败时是第几步
  "captured": {                     // ID-pinned 链路，每一步的 id 都留下来了
    "notebook_id": "…", "source_0": "…", "task_id": "…"
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
