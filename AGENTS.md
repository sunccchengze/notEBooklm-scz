# Arena Agent NotebookLM 集成指南

本仓库同时支持桌面端和 Arena Agent。桌面端入口仍然是 `启动.bat`；Agent 通过项目根目录的 `.mcp.json` 使用 NotebookLM MCP。

## Agent 使用原则

- NotebookLM 只作为来源 grounded 的研究和内容生成服务；修改代码前必须回到真实源码、测试和 Git diff 做核验。
- 优先复用已有 Notebook；只有在用户明确要求或没有合适 Notebook 时才创建新的 Notebook。
- 添加来源后先等待来源进入 ready 状态，再提问或生成产物。
- 多轮问题复用同一个 `session_id`，生成音频、视频、报告等长任务使用 `task_id` 轮询，不要阻塞式反复发起任务。
- 需要引用时使用结构化引用或 footnotes；最终写入仓库的研究文档必须注明来源和生成时间。
- 上传仓库内容前排除 `.git`、`.venv`、`.notebooklm`、`.env`、密钥、cookie、构建目录、依赖目录和个人数据。
- 不得将 Cookie、master token、密码或任何认证文件提交到 Git。认证只通过受保护的运行环境提供。
- 不自动下载或绕过受版权保护内容、付费墙或访问控制；只处理用户有权使用的资料。
- NotebookLM 产物是 AI 生成内容，不能未经核验直接作为事实或生产代码提交。

## 推荐工作流

1. 使用 `server_info` / `get_health` 检查 MCP 和认证状态。
2. 使用 `notebook_list` 查找已有 Notebook；必要时创建或选择 Notebook。
3. 使用 `source_add` 导入明确允许使用的 URL、文本或文件。
4. 使用 `source_wait` 确认来源可查询。
5. 使用 `chat_ask` 提出具体、结构化、限定来源的问题，并保存 `conversation_id`。
6. 对不完整答案继续追问，要求引用和明确区分资料事实与推断。
7. 生成产物时调用 `studio_generate`，随后用 `studio_status` 轮询，最后调用 `studio_download`。
8. 将结果写入 `docs/` 或 `out/` 前检查敏感信息、来源、文件大小和格式。
9. 运行测试并检查 `git diff`，再决定是否提交当前工作分支。

## 本地启动

```bash
./scripts/setup.sh
./scripts/agent-mcp
```

Arena 或支持项目级 MCP 的 Agent 会读取 `.mcp.json`。认证数据默认放在 `.notebooklm/`，该目录已被 `.gitignore` 忽略；生产环境应优先将 `NOTEBOOKLM_HOME` 指向受保护的持久化 Secret 路径。
