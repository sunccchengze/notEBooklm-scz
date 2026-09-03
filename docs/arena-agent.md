# Arena Agent × NotebookLM

本文说清两件事：**凭据怎么进来**，**两条路线各自怎么跑**。
行为约束（什么必须问、什么不许做）在 [../AGENTS.md](../AGENTS.md)，不重复。

---

## 1. 现状：沙箱打不通 Google

实测（可复现，见 [调研/02-环境实测.md](调研/02-环境实测.md)）：

- 所有出网走 E2B 的 MITM 代理（`issuer: O=E2B; CN=E2B Proxy CA`），按 **SNI 白名单**放行。
- 通：`github.com`、`api.github.com`、`pypi.org`、`files.pythonhosted.org`、`registry.npmjs.org`
- 不通：`*.google.com`、`googleapis.com`，连 `example.com`、`raw.githubusercontent.com` 也不通
- 拦截在 **TLS 层**：TCP 握手能成，Client Hello 发出后被 reset
- 沙箱无 `DISPLAY`、无 Chromium ⇒ 交互式 `notebooklm login` 和一切浏览器自动化都不可用

推论：自建隧道（Cloudflare / Tailscale / ngrok）同样不通，因为它们的域名也是白名单外的 SNI。

但 `notebooklm-py` 本身**装得上、跑得动**，`gh` 也已认证 ⇒ GitHub 可以当通信信道。

---

## 2. 凭据：只走 master token

沙箱里没浏览器，所以凭据只能**外部注入**。三种凭据里只有一种合适：

| 凭据 | 能不能用 | 原因 |
|---|---|---|
| `master_token.json` | ✅ **用这个** | 不轮换，过期自动 re-mint，无人值守可用 |
| `storage_state.json` | ⚠️ 仅单次验证 | 上游文档：cookie 快照约 10 分钟就被其它客户端顶替 |
| `NOTEBOOKLM_AUTH_JSON` 内联 | ⚠️ 仅单次调用 | 不触发 re-mint，无法持久化轮换 |

### 2.1 一次性 bootstrap（在有浏览器的机器上）

```bash
pip install "notebooklm-py[browser,headless]"
notebooklm login --master-token --account you@example.com
# → 写出 ~/.notebooklm/profiles/default/master_token.json
```

> **用专用小号。** 上游 `docs/security.md` 的原话：master token 是
> "full-account, durable, and infostealer-grade"，**改密码不能撤销它**，
> 只能去 Google 账号 → 安全性 → 你的设备 里显式移除。

### 2.2 注入沙箱

```bash
# 方式 1（推荐）：受保护持久化路径
export NOTEBOOKLM_MASTER_TOKEN_FILE=/受保护路径/master_token.json
./scripts/inject-token.sh

# 方式 2：内联（用完必须 unset —— 环境变量会被子进程继承，文件不会）
export NOTEBOOKLM_MASTER_TOKEN_JSON="$(cat /受保护路径/master_token.json)"
./scripts/inject-token.sh && unset NOTEBOOKLM_MASTER_TOKEN_JSON
```

`inject-token.sh` 会：验证 JSON 合法 → 落盘 `.notebooklm/profiles/<profile>/master_token.json`
→ `chmod 600` → **只报大小和权限，绝不打印内容**。

`.notebooklm/` 已在 `.gitignore`（`git check-ignore` 可验证）。

### 2.3 验证

```bash
./scripts/doctor.sh --json
```

看 `auth_live`：它对应上游的双条件 ——
`auth check --test --json` 要同时 `status=="ok"` **且** `checks.token_fetch==true`。

---

## 3. 路线 A：直连（等出网放开）

需要 Arena 侧放行：`notebooklm.google.com` + Google 的 cookie/token 域名
（`accounts.google.com`、`*.google.com`、`googleapis.com`、`oauth2.googleapis.com`）。

放开后不用改任何代码 —— `doctor.sh` 的 `egress_google` 会自己转绿，然后：

```bash
python3 tools/nbjob.py execute jobs/pending/<id>.job.json
```

长任务用后台跑（Arena 的 `start_process` 就行）：

```bash
python3 tools/nbjob.py execute jobs/pending/<id>.job.json --result jobs/.local/<id>.result.json
```

---

## 4. 路线 B：工单中继（现在就能跑通）

```
Arena Agent（沙箱）                       你的机器（有 Google 出网）
  写 jobs/pending/<id>.job.json  ──push──▶  ./scripts/worker.sh watch
  读 jobs/done/<id>.result.json  ◀──push──    └─ tools/nbjob.py execute
```

### Agent 侧

```bash
cp jobs/samples/report-demo.job.json jobs/pending/rpt-001.job.json
python3 tools/nbjob.py validate jobs/pending/rpt-001.job.json
python3 tools/nbjob.py plan     jobs/pending/rpt-001.job.json    # 自证命令序列
git add jobs/pending && git commit -m "job: rpt-001" && git push
```

然后轮询（`api.github.com` 可达，所以 `gh` 能用）：

```bash
gh api repos/:owner/:repo/contents/jobs/done/rpt-001.result.json --jq '.download_url'
```

### Worker 侧（你的机器）

```bash
git clone https://github.com/sunccchengze/notEBooklm-scz && cd notEBooklm-scz
git checkout arena/01a06208-notebooklm-scz
./scripts/setup.sh
./scripts/nb login                     # 本机有浏览器，直接登录最简单
./scripts/doctor.sh                    # 应全绿
./scripts/worker.sh watch              # 循环；或 once 配 cron
```

worker 每轮：`git pull` → 找 `jobs/pending/*.job.json` → 逐个 `execute` →
结果写 `jobs/done/<id>.result.json` → 工单挪进 `jobs/running/`（**避免重复执行烧配额**）→
`commit` + `push`。

### 大文件

mp3 / mp4 / pdf 不适合走 Git。当前只回传小产物（md / json / csv / png）。
要回传大文件，让 worker 传 GitHub Release 再把下载链接写进结果 —— 尚未实现，见 §6。

---

## 5. 可选：项目级 MCP

`.mcp.json` 已配好，指向 `scripts/agent-mcp` → `python -m notebooklm.mcp`。
`agent-mcp` 在启动前会检查凭据是否存在，缺失就打印可读指引并 `exit 1`，
而不是把 notebooklm-py 的长 traceback 甩给 Agent。

> ⚠️ **未验证**：Arena 是否真的加载项目级 `.mcp.json`。本会话的工具列表里没有任何 MCP 工具，
> 但"看不到"既可能是平台不支持，也可能是没配置 —— 这一点需要在 Arena 侧确认，
> 不能当成已知事实。MCP 入口是**锦上添花**，不是必需：`tools/nbjob.py` 走 CLI，不依赖 MCP。

MCP 侧默认注册 33 个工具（实测 fastmcp 3.4.2）：`chat_ask` / `source_add` / `source_wait` /
`studio_generate` / `studio_status` / `studio_download` / `notebook_*` / `research_*` /
`share_*` / `suggest_prompts` / `server_info` 等。

---

## 6. 还没做的

- **真实 API 端到端**：十种工单都在 mock CLI 上验证过编排逻辑，但**没有**在真实
  NotebookLM 上跑通过 —— 沙箱打不通 Google。这是当前最大的未验证项。
- **大产物回传的上传分支未实测**：`ship` 子命令已实现分流（小文本走 Git、
  二进制走 GitHub Release），但**上传那一段在沙箱内跑不通**，原因见下条。
  分流判定、失败处理、回滚都验证过了；真机上的成功路径**没验证过**。
- **笔记本按标题「模糊」匹配**：现在只做**精确**同名匹配（`list --json` 后逐条比对
  去首尾空格的 title），命中即复用。近似匹配、跨账号去重还没做。
- **`cinematic-video` 别名、`revise-slide` 单页改写、`artifact retry`** 还没做成工单动作。

### 一条实测出来的出网边界（影响上面的判断）

沙箱的放行名单是**按域名**的，GitHub 只放行了 API 和主站：

| 域名 | 实测 | 说明 |
|---|---|---|
| `api.github.com` | `200` | 建/删 release、改 issue 都行 |
| `github.com` | `200` | clone / push 正常 |
| `uploads.github.com` | `000` | **Release 传附件走这里**，TLS 在 Client Hello 后 `SSL_ERROR_SYSCALL` |
| `objects.githubusercontent.com` | `000` | 下载 release 附件走这里，同样不通 |

也就是说：**沙箱内能建 release、能 push，但传不上附件、也下不下来**。
所以大产物回传只能在 Route B 的 worker（用户自己的机器）上做，
那里没有这层 MITM 代理 —— 但这一条我**无法在沙箱内证实**，只是推断。

`ship` 因此设计成：失败不谎报成功（`channel: "failed"` + `exit 1`），
且**回滚**本次新建却没传上东西的空 release —— 否则每次失败都在用户仓库留垃圾。
这条回滚路径已实测：沙箱内上传必然失败，跑完 release 数仍是 `0`、无残留 tag。

---

## 7. API 核对记录

`examples/research_report.py` 用到的每个符号都对着**已安装的 0.8.1** 核对过，不是凭记忆写的：

```
ReportFormat: BLOG_POST / BRIEFING_DOC / CONCEPT_EXPLANATION / CUSTOM / STUDY_GUIDE
NotebooksAPI.create(title) -> Notebook
SourcesAPI.add_url(notebook_id, url, *, wait, wait_timeout, title) -> Source
SourcesAPI.wait_all_until_ready(...) -> list[Source | SourceNotFoundError
                                            | SourceProcessingError | SourceTimeoutError]
ArtifactsAPI.generate_report(notebook_id, report_format, source_ids, language,
                             custom_prompt, extra_instructions) -> GenerationStatus
ArtifactsAPI.wait_for_completion(notebook_id, task_id, ..., timeout) -> GenerationStatus
ArtifactsAPI.download_report(notebook_id, output_path, artifact_id) -> str
ChatAPI.ask(notebook_id, question, source_ids, conversation_id) -> AskResult
NotesAPI.create(notebook_id, title, content) -> Note
```

两个值得记的差异：

1. `ReportFormat` 枚举里有 `CONCEPT_EXPLANATION`，但 **CLI 只暴露 4 种**
   （`notebooklm generate report --help` 实测：`briefing-doc|study-guide|blog-post|custom`）。
   所以 `tools/nbjob.py` 的校验集合是 4 个，跟 CLI 对齐，不跟枚举对齐。
2. `wait_all_until_ready` 返回的是 `list[Source | 异常对象]`，**不是**清一色 `Source`。
   直接当 Source 用会在失败来源上炸 —— 示例里显式分流了。

---

## 8. CLI 参数核对记录

`tools/nbjob.py` 的 `KINDS` 表里每一个可选值，都是对着**已安装 CLI 的 `--help` 输出**
逐个抄的，不是从上游 README 或 SKILL.md 抄的 —— 这两者会和实际 CLI 脱节（上面第 7 节的
`CONCEPT_EXPLANATION` 就是一例）。

核对覆盖 **0.8.1 与 0.8.2 两个版本**：`requirements.txt` 写的是 `>=0.8.1,<0.9`，
全新安装现在会拿到 0.8.2，所以两版逐条对照过。结论：

- 九种 `generate` 的**枚举取值**两版**逐字一致**
- `--language` 的有无两版一致（`quiz` / `flashcards` 没有，其余七种有）
- `mind-map` 两版都**没有** `--prompt-file`
- 九种的 Usage 行两版一致：八种是 `[OPTIONS] [DESCRIPTION]`，只有 `mind-map` 是 `[OPTIONS]`

| 命令 | 参数与可选值（实测） |
|---|---|
| `generate report` | `--format [briefing-doc\|study-guide\|blog-post\|custom]`（默认 briefing-doc）、`--append`、`--prompt-file`、`--language` |
| `generate audio` | `--format [deep-dive\|brief\|critique\|debate]`、`--length [short\|default\|long]`、`--language`、`--prompt-file` |
| `generate slide-deck` | `--format [detailed\|presenter]`、`--length [default\|short]`、`--language`、`--prompt-file` |
| `generate quiz` | `--quantity [fewer\|standard\|more]`、`--difficulty [easy\|medium\|hard]`（**没有** description 位置参数、**没有** `--language`） |
| `generate flashcards` | `--quantity [fewer\|standard\|more]`、`--difficulty [easy\|medium\|hard]`、`--prompt-file`（**没有** `--language`，与 quiz 同） |
| `generate video` | `--format [explainer\|brief\|cinematic\|short]`、`--style [auto\|custom\|classic\|whiteboard\|kawaii\|anime\|watercolor\|retro-print\|heritage\|paper-craft]`、`--style-prompt`、`--language`、`--prompt-file` |
| `generate infographic` | `--orientation [landscape\|portrait\|square]`、`--detail [concise\|standard\|detailed]`、`--style [auto\|sketch-note\|professional\|bento-grid\|editorial\|instructional\|bricks\|clay\|anime\|kawaii\|scientific]`、`--language`、`--prompt-file` |
| `generate data-table` | `--prompt-file`、`--language`（**没有任何枚举选项**，DESCRIPTION 必填） |
| `generate mind-map` | `--kind [interactive\|note-backed]`、`--instructions`、`--language`（**没有** `--prompt-file`、**没有** description 位置参数） |
| `download slide-deck` | `--format [pdf\|pptx]`（默认 pdf）、`-a`、`-n`、`--all`、`--name`、`--dry-run`、`--force`、`--no-clobber` |
| `download quiz` | `--format [json\|markdown\|html]`（默认 json）、同上 |
| `download flashcards` | `--format [json\|markdown\|html]`、同上 |
| `download audio` / `video` / `infographic` / `data-table` / `mind-map` | `-a`、`-n`、`--latest/--earliest/--all`、`--name`（**都没有** `--format`） |
| `artifact retry` | `ARTIFACT_ID`（位置参数，支持唯一前缀）、`-n`、`--wait/--no-wait`、`--timeout`、`--interval` |
| `generate revise-slide` | `-a <slide deck id>`（**必填**）、`--slide <0-based 序号>`（**必填**）、`--prompt-file`、`-n`、`--wait/--no-wait` |
| `generate cinematic-video` | `generate video --format cinematic` 的**别名**；`--format` 被锁死为 cinematic，传别的值直接报错 |

由此定下来的实现决策（每条都对应 `KINDS` 表里的一个字段）：

1. **`quiz` / `flashcards` 不给 `--language`**。它们的 `--help` 里没有这个参数，硬加会被
   click 拒。所以这两个 kind 的 `has_language: False`。
2. ~~**`quiz` 没有 description 位置参数**~~ —— **这条是错的，已纠正**。
   `generate quiz --help` 的 Usage 行是 `quiz [OPTIONS] [DESCRIPTION]`，
   0.8.1 与 0.8.2 **两版都有**位置参数。当初记成「没有」是读漏了 Usage 行，
   后果是 `prompt_mode: "none"` 会把用户写的 `prompt` **静默丢弃**（不报错、也不生效）。
   现已改为 `prompt_mode: "positional"`。
   教训：判定「某参数不存在」必须看 Usage 行的位置参数，不能只扫 `--xxx` 选项列表。
3. **`mind-map` 没有 `--prompt-file`** → `prompt_mode: "instructions"`，prompt 翻译成
   `--instructions`；校验会直接拦住给 `prompt_file` 的工单，而不是静默丢弃。
4. **`mind-map` 同步返回** `{mind_map, note_id, kind}`，没有 `task_id` →
   `capture_task: "note_id"` + `skip_wait: True`（计划 5 步而不是 6 步）。
   这条是从源码 `cli/generate_cmd.py:151` 的
   `json_output_response({"mind_map": …, "note_id": …, "kind": …})` 读出来的，不是猜的。
5. **`data-table` 没有任何枚举选项**，DESCRIPTION 必填 → `prompt_mode: "required-positional"`，
   缺 prompt 的工单在校验阶段就被拦。
   依据不再只是转述 SKILL.md，而是源码 + 实跑：`generate_cmd.py:792` 调
   `resolve_prompt(description, prompt_file, "description", required=True)`，
   而其余八种（audio/video/slide-deck/quiz/flashcards/infographic/report）
   调的是不带 `required` 的版本。直接调该函数实测：
   `required=True` 且两者皆空 → `UsageError: Provide a description argument or --prompt-file.`；
   `required=False` 同样输入 → 返回 `''`。
   顺带证实了 `prompt` 与 `prompt_file` 的互斥规则与本地校验层**逐字一致**
   （`Cannot use both the description argument and --prompt-file. Choose one.`）。
6. **`download audio/video/infographic/data-table/mind-map` 都没有 `--format`**，
   所以这些 kind 的 `KINDS` 里没有 `download_format`，下载段不会拼这个 flag ——
   而 slide-deck / quiz / flashcards 有。
7. **`--style-prompt` 配 `--style custom` 使用** → 校验层直接拦。
   注意这条的依据**弱于**其余各条：上游 `generate_cmd.py` 的 video 命令体对
   `style_prompt` **没有任何分支、校验或警告**，`ArtifactsAPI.generate_video(...)`
   也无条件接收它 —— 也就是说 CLI 会照传。唯一依据是 `--style` 的帮助文本
   `Use 'custom' with --style-prompt`。
   所以「非 custom 时会被忽略」是**未经验证的推断**（服务端行为，沙箱内无法证实）。
   拦下来是本地策略：宁可让工单显式表达意图，也不发一个效果不明的参数。
8. **`artifact retry` 不带 `--wait` 时返回 `{task_id, status, url, error, error_code}`**
   —— 源码 `cli/artifact_cmd.py:691` 核实（带 `--wait` 时键名换成 `artifact_id`）。
   取 `task_id` 正好契合既有 capture 模式，所以 `retry_artifact` 不需要新的取值路径。
9. **`cinematic-video` 不做成独立 kind**。它是 `generate video --format cinematic` 的别名，
   用 `video` + `format: "cinematic"` 即可覆盖；做成独立 kind 只会多一个等价入口。
10. **`revise-slide` 不做成 kind**。它要求 `-a <已生成的 slide deck id> --slide <序号>`，
    是对已有产物的**局部编辑**而非新产物，和「建本→加料→生成→下载」的骨架不兼容。
    将来若要支持，应该是第三种动作类型（局部修改），不是第十一种产物。
11. **两个 wait 命令的超时退出码不同**，`ok_codes` 必须分开写。
    源码核实（`exit_with_code` 就是 `raise SystemExit(exit_code)`，**不做任何映射**）：

    | 命令 | 退出码 | 依据 |
    |---|---|---|
    | `source wait` | `0`=ready / `1`=missing 或处理失败 / **`2`=timeout** | `source_cmd.py` docstring |
    | `artifact wait` | `0`=完成 / **`1`=超时**（也是 1） | `artifact_cmd.py` 的 `except TimeoutError: … exit_with_code(1)` |

    我此前把 `artifact wait` 的 `ok_codes` 写成 `(0, 2)`，是把 `source wait` 的契约错套了
    过来。当下**恰好无害**（2 永不出现，超时走 1 被判失败，行为正确），但是个陷阱：
    一旦上游真返回 2，就会被当成功并去下载一个还不存在的产物。已改为 `(0,)`。

    `source wait` 保持 `(0,)` 是**刻意的**：没索引完就去提问或生成只会拿到空结果白烧配额，
    所以超时(2)也要判失败。

    实测：mock 让 `artifact wait` 返回 2 → `status=failed`、`failed_at=7`、
    **下载步骤未被执行**（改之前会被当成功继续下载）。
12. **`source add` 必须显式传 `--type`**，不能依赖上游的自动判别。
    `Usage: notebooklm source add [OPTIONS] CONTENT`，`--type [url|text|file|youtube]`
    的帮助文本写着 "Source type is auto-detected"。我的枚举与它**逐字一致**
    （0.8.2 实测；`requirements.txt` 钉 `>=0.8.1,<0.9`，0.8.1 侧的 venv 已不在，未复核）。

    但同一段帮助文本记载了误判后果：
    > A path-shaped argument that does not exist on disk is still ingested as
    > **inline text** but a stderr warning is emitted; pass `--type text` to suppress.

    即声明成 `file` 的资料若路径写错，会被**静默降级成内联文本**而不是报错。
    于是生成照跑、退出码为 0、产物看着也正常 —— 但依据的是那串路径字符串本身。
    工单里既然已经声明了 `type`，就交下去，别让 CLI 猜。

    我此前只把 `type` 用在 label 里，从未传进命令。已修，并在回归套件加了断言
    （回退该修复 → 9 条红）。

复现核对：

```bash
for c in report audio slide-deck quiz flashcards video infographic data-table mind-map \
         revise-slide cinematic-video; do
  echo "=== generate $c ==="; .venv/bin/notebooklm generate $c --help | grep -E '^  --|^  -'
done
for c in audio video slide-deck quiz flashcards infographic data-table mind-map; do
  echo "=== download $c ==="; .venv/bin/notebooklm download $c --help | sed -n '/^Options/,$p' | grep -E '^  -'
done
echo "=== artifact retry ==="; .venv/bin/notebooklm artifact retry --help

# retry 的 JSON 键名（task_id vs artifact_id）从源码核，不看 --help：
grep -n 'json_output_response' -A 8 \
  ~/notebooklm-py/src/notebooklm/cli/artifact_cmd.py | sed -n '/task_id/,/}/p'
```
