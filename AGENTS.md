# AGENTS.md —— 在本仓库调用 NotebookLM 的行为契约

给在本仓库工作的 AI Agent（Arena / Claude Code / Codex）。**先读完再动手。**

## 0. 三十秒背景

- 底座是 [notebooklm-py](https://github.com/teng-lin/notebooklm-py) v0.8.1，**唯一**被采纳的实现。
  为什么不用别的，见 [docs/调研/01-生态分析.md](docs/调研/01-生态分析.md)。
- 它逆向的是 Google 未公开接口（`batchexecute` RPC）。**Google 随时可能改，随时可能坏。**
- **Arena 沙箱当前打不通 Google**（TLS 层被切断，实测见
  [docs/调研/02-环境实测.md](docs/调研/02-环境实测.md)）。所以有两条路线，见 §2。

## 1. 动手之前：先体检

```bash
./scripts/doctor.sh --json
```

它会一次说清三件事：装没装好 / 有没有凭据 / 能不能连到 Google。
**不要跳过这一步** —— NotebookLM 的失败 99% 落在这三类里，而修法完全不同。

体检里的 `auth_live` 一项对应上游 SKILL.md 的双条件校验：
`auth check --test --json` 必须同时满足 `status == "ok"` **且** `checks.token_fetch == true`。
裸 `auth check --json` 只证明文件能解析，是**假阳性陷阱**。

## 2. 两条路线，按体检结果选

| `egress_google` | 走哪条 | 怎么做 |
|---|---|---|
| ✅ true | **路线 A：直连** | 直接 `tools/nbjob.py execute`，或 `./scripts/nb …` |
| ❌ false | **路线 B：工单中继** | 写工单到 `jobs/pending/`，push，等 worker 回写 `jobs/done/` |

**不管哪条路线，Agent 侧的动作是一样的：写工单。** 区别只在于谁来执行。
所以 Agent 不需要判断自己在哪条路线上 —— 写完工单，能直连就直连，不能就交出去。

在 Google 不可达时，你能做的离线自证有两条：

```bash
python3 tools/nbjob.py plan jobs/pending/<id>.job.json    # 不碰网络，只打印将执行的命令
python3 tools/nbjob.py validate jobs/pending/<id>.job.json # 只校验工单合法性
```

`ship --dry-run` 也不碰网络（只判定产物该走 Git 还是 Release），但它要的是
`execute` 产出的 result 文件，所以在完全跑不通的环境里用不上。

## 3. 硬规矩

### 3.1 凭据

- `storage_state.json` / `master_token.json` / cookie / token / 密码 —— **永不进 Git、永不进日志、
  永不写进 Issue/PR/聊天/代码**。`.gitignore` 已覆盖，但别依赖它，自己也要守。
- 注入只走 `./scripts/inject-token.sh`（落盘 0600）。**不要**手写 `echo > master_token.json`。
- master token 是**账号级**凭据，改密码不能撤销它，只能显式 revoke。建议专用小号。
- 用内联 `NOTEBOOKLM_MASTER_TOKEN_JSON` 之后**必须 `unset`** —— 环境变量会被子进程继承，文件不会。

### 3.2 ID-pinned，绝不依赖隐式上下文

每一步都把返回的 id 显式传给下一步（`-n <notebook>` / `-a <artifact>`）。
**绝不**用 `notebooklm use` 然后指望后续命令记住 —— 并行 Agent 会互相覆盖 `context.json`，
沙箱重置后上下文也会丢。`tools/nbjob.py` 已经强制这么做，别绕开它自己拼命令。

### 3.3 不要对着没就绪的来源提问

`source add` 的返回里**没有** `status` 字段。必须 `source wait`（或 `source list --json`
看 `status == "ready"`，注意是小写）之后才能提问或生成。

### 3.4 长任务不要阻塞式轮询

| 操作 | 典型耗时 | 建议 timeout |
|---|---|---|
| 资料索引 | 30s–10min | 600s |
| 报告 / 数据表 | 5–15min | 900s |
| 测验 / 闪卡 | 5–15min | 900s |
| 播客 | 10–20min | 1200s |
| 视频 | 15–45min | 2700s |

**任务失败时不要重试 `generate`** —— 那会创建重复产物、白烧配额。先
`artifact list -n <nb> --json` 看那个 id 的真实状态。确认是产物本身失败后，
用 `retry_artifact` 工单**原地重试**（ARTIFACT_ID 不变，`poll`/`wait` 继续有效），
不要重发 `generate`。样例见 `jobs/samples/retry-demo.job.json`。

### 3.5 限流分级（上游记载）

- **可靠**：notebooks / sources / chat / mind-map / report / data-table
- **易被限流**：audio / video / quiz / flashcards / infographic / slide-deck

被限流（`GENERATION_FAILED` / "No result found for RPC ID"）就等 5–10 分钟，别硬重试。

### 3.6 破坏性操作必须先问

`delete` / `source delete` / `source clean` / `note delete` / `artifact delete` /
`label delete` / `share remove` / `auth logout` / `profile delete` / `ask --new`
—— 先征得同意，再带 `--yes`。多数破坏性命令即使加 `--json` 也要求显式 `--yes`，
否则返回 `CONFIRM_REQUIRED`。

### 3.7 上传仓库内容前，先筛

明确排除：`.git/`、`.venv/`、`.notebooklm/`、`out/`、`.env`、密钥、cookie、
构建与依赖目录、个人数据、无关的大二进制。只传必要的文档和源码。

### 3.8 产物是 AI 生成内容

NotebookLM 的回答和报告**不能未经核验直接当事实或生产代码提交**。
写进仓库的研究报告必须标注来源与生成时间（`examples/research_report.py` 会顺带写一份
`*.provenance.md`）。改代码之前，回到真实源码、测试和 `git diff` 做核验。

## 4. 推荐工作流

1. `./scripts/doctor.sh --json` —— 确认环境。
2. 写工单到 `jobs/pending/<id>.job.json`，格式见 [jobs/README.md](jobs/README.md)。
   **优先复用已有笔记本**（配额有限）：只给 `notebook.title` 时，执行器会自己
   `list --json` 找精确同名的本子，命中即复用、没命中才建 —— 不用你手工查。
   已经知道 id 就直接给 `notebook.id`，那连查询都省了。
3. `python3 tools/nbjob.py plan <工单>` —— 自证命令序列正确。
4. 路线 A：`python3 tools/nbjob.py execute <工单>`。
   路线 B：`git push`，等 worker 回写 `jobs/done/<id>.result.json`。
5. 读结果的 `status` / `answers` / `captured` / `artifact`。
6. 读 `delivery` 段决定怎么拿产物：`channel: "git"` → `git pull` 后在 `out/` 里；
   `channel: "release"` → 去 `delivery.url` 下载；`"failed"` → 看 `delivery.error`。
   路线 B 上 worker 已经调过 `ship`；路线 A 上你自己调：
   `python3 tools/nbjob.py ship jobs/done/<id>.result.json`。
7. 回看来源原文核验：`./scripts/nb source fulltext <source_id> -f markdown`。
8. 跑测试、看 `git diff`，再决定是否提交。

**产物生成失败时**：不要重跑 `generate`（见 §3.4）。改用 `retry_artifact` 工单原地重试 ——
它保留原 ARTIFACT_ID，不造重复产物、不白烧配额。样例见 `jobs/samples/retry-demo.job.json`。

## 5. 提问的技巧（实测有效，来自 qiaomu 项目）

- 每题加一句 **"完全基于已上传的文档内容回答，不要搜索网络"** —— 防 NotebookLM 触发联网搜索，
  污染 grounding。
- 用 **"列出 / 拆解 / 指出 / 提取"** 这类动作词引导结构化回答，避免 yes/no 问题。
- 多轮提问放同一个 conversation，**后轮受益于前轮上下文**。三轮递进（框架 4 题 →
  深挖 5 题 → 反刍 3 题）效果最好。
- YouTube 链接**直接丢给 NotebookLM**，它原生支持字幕提取。**不要**自己用 yt-dlp /
  whisper 抓字幕。

## 6. 明确不做的事

- ❌ 付费墙绕过（UA 伪装、archive.today、Referer 伪造等）—— 规避访问控制，不引入。
- ❌ 从 Z-Library 等盗版书库下载内容。
- ❌ 浏览器自动化路线（patchright / Playwright 驱动 Web UI DOM）—— 沙箱无 display 无 Chrome，
  且 DOM 锚点随时会失效（上游 notebooklm-skill 依赖的 `div.thinking-message` 已被 Google 删除）。
- ❌ 把凭据写进任何会被提交或分享的地方。

## 7. 出错时怎么查

| 现象 | 原因 | 动作 |
|---|---|---|
| `auth check` 不过 | 凭据缺失/过期 | `./scripts/inject-token.sh` 重新注入 |
| `notebooklm.google.com` → 000 | 沙箱出网被切 | 走路线 B |
| "No notebook context" | 没传 `-n` | 补上显式 notebook id |
| "No result found for RPC ID" | 限流 | 等 5–10 分钟 |
| `GENERATION_FAILED` | Google 限流 | 等，别重发 `generate`；改用 `retry_artifact` 工单原地重试 |
| 下载失败 | 生成未完成 | `artifact list -n <nb> --json` 查那个 id |
| `delivery.channel: "failed"` | 产物回传失败（多半是 `uploads.github.com` 不可达） | 看 `delivery.error`；小产物改走 Git，大产物换有出网的机器重跑 `ship` |
| RPC protocol error | Google 改了接口 | 升级 notebooklm-py 版本 |

Exit code 约定：`0` 成功 / `1` 错误 / `2` 超时（仅 wait 类命令）。
