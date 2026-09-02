# notEBooklm-scz

基于 [notebooklm-py](https://github.com/teng-lin/notebooklm-py) 的 NotebookLM / Gemini Notebook 自动化工作台。

用 CLI、Python 脚本或 AI agent 程序化地驱动 NotebookLM：批量导入资料、做带引用的问答、生成播客/视频/幻灯片/测验/思维导图，并把产物批量导出到本地。

## 快速开始

**Windows / PowerShell：**
```powershell
.\scripts\setup.ps1                      # 1. 装环境
.\scripts\nb.ps1 login                   # 2. 浏览器登录 Google
.\scripts\nb.ps1 auth check --test       # 3. 验证，期望 status: ok
.\scripts\nb.ps1 list                    # 4. 开跑
```

**Linux / macOS：**
```bash
./scripts/setup.sh
./scripts/nb login
./scripts/nb auth check --test
./scripts/nb list
```

> 在无图形界面的服务器/沙箱里，登录要走 cookie 导入，见 [使用指南 §2-L](docs/使用指南.md)。

## 常用命令

以下以 `./scripts/nb` 书写，Windows 换成 `.\scripts\nb.ps1`，参数相同。

```bash
./scripts/nb create "我的研究" && ./scripts/nb use 我的研究
./scripts/nb source add https://example.com/paper.pdf
./scripts/nb source add-research "2026 年 AI Agent 进展" --mode deep
./scripts/nb ask "这些资料的核心论点是什么？"
./scripts/nb generate audio "中文深度对谈"
./scripts/nb download audio -o out/
```

## Python 示例

```bash
./scripts/py examples/quickstart.py                                    # Linux/macOS
.\scripts\py.ps1 examples\quickstart.py                                # Windows

./scripts/py examples/research_pipeline.py "标题" https://url1 https://url2
```

## 文档

📖 **[完整中文使用指南 → docs/使用指南.md](docs/使用指南.md)**（登录方式、CLI 全集、Python API、MCP 接入）

## 目录

| 路径 | 说明 |
|------|------|
| `scripts/setup.sh` / `setup.ps1` | 一键安装（Linux/macOS · Windows）|
| `scripts/nb` / `nb.ps1` | CLI 封装（自动用仓库内 venv 与认证目录）|
| `scripts/py` / `py.ps1` | Python 脚本封装 |
| `examples/` | 可直接运行的示例 |
| `out/` | 产物输出（gitignore）|

> ⚠️ notebooklm-py 是非官方库，使用 Google 未公开接口，可能随时失效。适合原型、研究与个人项目。
