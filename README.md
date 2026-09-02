# notEBooklm-scz

**让 AI Agent 在自己的工作分支上调用 Google NotebookLM 产出东西。**

不是又一个 NotebookLM 套壳界面 —— 这里没有前端。这个仓库是一套**给 Agent 用的接入层**：
体检、凭据注入、工单、执行、产物落盘、留痕，一条链路。

底座是 [notebooklm-py](https://github.com/teng-lin/notebooklm-py) v0.8.1。为什么是它、
为什么不是另外五个同类项目，写在 [docs/调研/01-生态分析.md](docs/调研/01-生态分析.md)。

---

## 三十秒上手

```bash
./scripts/setup.sh                    # 装 .venv + notebooklm-py[mcp,headless,markdown]
./scripts/doctor.sh                   # 体检：装好了吗 / 有凭据吗 / 连得到 Google 吗
```

体检会告诉你走哪条路：

```bash
# 路线 A —— Google 可达，直接跑
python3 tools/nbjob.py plan    jobs/samples/report-demo.job.json    # 先看命令序列（不碰网络）
python3 tools/nbjob.py execute jobs/samples/report-demo.job.json    # 真跑

# 路线 B —— Google 不可达（Arena 沙箱当前就是这种）
cp jobs/samples/report-demo.job.json jobs/pending/rpt-001.job.json
git push                                        # 交给你本机的 worker
./scripts/worker.sh watch                       # ← 这条在你本机跑，不在沙箱里
```

---

## 为什么需要"两条路线"

**实测结论**：Arena 沙箱的出网是 SNI 白名单制（所有流量过 E2B 的 MITM 代理），
`github.com` / `pypi.org` / `npmjs.org` 通，**`*.google.com` 在 TLS 握手阶段被切断**。
命令和原始输出都在 [docs/调研/02-环境实测.md](docs/调研/02-环境实测.md)。

所以：

| | 路线 A：直连 | 路线 B：工单中继 |
|---|---|---|
| 前提 | 沙箱能访问 Google | 只需 GitHub 可达（已验证） |
| 凭据在哪 | 注入沙箱 | **永不离开你的机器** |
| 延迟 | 实时 | 一个 push/pull 周期 |
| 状态 | 等出网放开 | **现在就能跑通** |

关键点：**Agent 侧的动作在两条路线上完全一样 —— 都是写工单。** 区别只在谁执行。
所以出网放开与否，不改变 Agent 的写法，只改变工单在哪落地。

```
路线 A   Agent ──▶ tools/nbjob.py execute ──▶ scripts/nb ──▶ Google
路线 B   Agent ──▶ jobs/pending/*.job.json ──git──▶ worker ──▶ 同一条链路
```

---

## 目录

| 路径 | 说明 |
|---|---|
| `AGENTS.md` | **Agent 行为契约 —— 先读这个** |
| `tools/nbjob.py` | 工单校验 / 计划器 / 执行器（纯标准库，路线 A、B 共用） |
| `jobs/` | 工单与结果。格式见 [jobs/README.md](jobs/README.md) |
| `scripts/doctor.sh` | 环境体检（`--json` 给 Agent 读） |
| `scripts/inject-token.sh` | 注入 master token（0600） |
| `scripts/worker.sh` | 路线 B 的外部 worker（跑在你本机） |
| `scripts/nb` `.ps1` | `notebooklm` CLI 薄封装（Windows / Linux 两套，参数相同） |
| `scripts/agent-mcp` | 项目级 MCP 入口 |
| `prompts/slides/` | 中文化幻灯片风格库（6 种），配 `generate.prompt_file` 用 |
| `examples/` | Python API 版示例（看懂链路用） |
| `out/` | 产物落盘，已 gitignore |
| `docs/调研/` | 六个同类仓库的逐文件分析 + 沙箱实测 + 方案讨论 |

---

## 第一条链路：研究报告

选它当第一条链路是因为它**全走文本** —— 没有二进制产物、没有几十分钟的生成等待，
是验证"凭据 → 来源 → 提问 → 产物 → 落盘 → 留痕"整条通路最便宜的方式。

```bash
python3 tools/nbjob.py plan jobs/samples/report-demo.job.json
```

会打印 10 步：建笔记本 → 加 2 个来源 → 各自等索引 → 2 轮提问 → 生成简报 → 等完成 → 下载到
`out/`。每一步的 id 都显式传给下一步（ID-pinned），失败会**立即停在那一步**，
不会继续发起生成任务白烧配额。

## 九种产物

骨架完全一样，只是最后三步的命令形状不同（由 `tools/nbjob.py` 的 `KINDS` 表声明式描述）：

| kind | 产物 | 落盘 | 样例 |
|---|---|---|---|
| `research_report` | 简报 / 学习指南 / 博客稿 | `.md` | `report-demo` |
| `podcast` | 音频概览 | `.m4a` | `podcast-demo` |
| `slides` | 幻灯片 | `.pdf` / `.pptx` | `slides-demo` |
| `quiz` | 测验 | `.md` / `.json` / `.html` | `quiz-demo` |
| `flashcards` | 闪卡 | `.md` / `.json` / `.html` | `flashcards-demo` |
| `video` | 视频概览 | `.mp4` | `video-demo` |
| `infographic` | 信息图 | `.png` | `infographic-demo` |
| `data_table` | 数据表 | `.csv` | `datatable-demo` |
| `mind_map` | 思维导图 | `.json` | `mindmap-demo` |

样例都在 `jobs/samples/`。幻灯片还配了一套**中文化风格库**（`prompts/slides/`，6 种风格），
素材来自 awesome-notebookLM-prompts，改写后对齐了 notebooklm-py 的真实接口 ——
比如 slide-deck **没有** `--orientation` 参数（只有 infographic 有），竖版只能写进 prompt。

---

## 文档

- 🤖 [AGENTS.md](AGENTS.md) —— Agent 行为契约（凭据、ID-pinned、限流、破坏性操作、红线）
- 🔌 [docs/arena-agent.md](docs/arena-agent.md) —— 两条路线的完整说明 + 凭据获取步骤
- 📋 [jobs/README.md](jobs/README.md) —— 工单 schema
- 🔍 [docs/调研/01-生态分析.md](docs/调研/01-生态分析.md) —— 六个同类仓库逐个拆解
- 🧪 [docs/调研/02-环境实测.md](docs/调研/02-环境实测.md) —— 沙箱出网实测（可复现）
- 🗺️ [docs/调研/03-方案讨论.md](docs/调研/03-方案讨论.md) —— 路线取舍的理由

---

> ⚠️ notebooklm-py 是非官方库，走 Google 未公开接口，**随时可能失效**。
> 适合原型、研究与个人项目。凭据是账号级的，请用专用小号。
>
> NotebookLM 的产物是 AI 生成内容，未经核验不得直接当作事实或生产代码。
