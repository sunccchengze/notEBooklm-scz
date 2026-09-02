# Arena Agent × NotebookLM

本仓库的桌面应用和 Agent 集成共用 `notebooklm-py`，但入口相互独立：

```text
Arena Agent
    ↓ 项目级 MCP（.mcp.json）
./scripts/agent-mcp
    ↓
notebooklm-py MCP
    ↓
Google NotebookLM
```

## 安装

```bash
./scripts/setup.sh
```

安装脚本会创建 `.venv`，并安装 NotebookLM API、FastAPI 和 MCP 依赖。启动命令为：

```bash
./scripts/agent-mcp
```

项目级配置已经写入 `.mcp.json`，不包含密钥、不包含绝对路径，也不会改变桌面端入口。

## 认证

首次运行需要为 NotebookLM 提供登录态。认证文件默认位于 `.notebooklm/`，该目录不会提交到 Git。

在无图形界面的 Arena 环境中，推荐在受信任的本地环境完成登录或导入认证状态，然后通过受保护的持久化 Secret 路径提供 `NOTEBOOKLM_HOME`。不要把 Cookie、master token 或密码放进 Issue、PR、聊天消息或仓库。

```bash
NOTEBOOKLM_HOME=/protected/notebooklm ./scripts/agent-mcp
```

具体认证方式和环境限制见 [使用指南](使用指南.md) 的 Windows / 云端登录章节。

## 工具工作流

### 资料问答

```text
server_info / get_health
    → notebook_list
    → source_add
    → source_wait
    → chat_ask
    → 使用 conversation_id 继续追问
```

### 生成产物

```text
studio_generate
    → studio_status（使用 task_id 轮询）
    → studio_download
```

长任务不得通过重复调用 `studio_generate` 轮询，否则会创建重复产物并浪费 NotebookLM 配额。

## 代码仓库安全边界

上传仓库内容前只选择必要的文档和源码。明确排除：

- `.git/`、`.venv/`、`.notebooklm/`、`.env`；
- cookies、token、私钥和配置密钥；
- 依赖目录和构建目录；
- 用户个人文件和不必要的大型二进制文件。

NotebookLM 结果必须由 Agent 回看源码、测试和 Git diff 后才能用于代码修改。生成的报告需要标注来源和生成时间；生成的音频、视频等大型文件默认放在 `out/`，不要直接加入 Git。

## 当前阶段范围

本阶段只提供安全的项目级 MCP 接入和 Agent 工作规范，保留现有桌面端。暂不接入 Z-Library、付费墙绕过或任何需要代替用户突破访问控制的流程。
