# notEBooklm-scz

**NotebookLM 桌面版** —— 给 [notebooklm-py](https://github.com/teng-lin/notebooklm-py) 套了一个聊天界面，不用敲命令。

和你的资料对话、上传文件、一键生成播客／测验／思维导图，全部在图形界面里点。

## 用起来

**双击 `启动.bat`** 就完事了。浏览器会自动打开界面。

第一次用需要先登录一次（只要一次）：

```powershell
.\scripts\nb.ps1 login
```

之后每次都只要双击 `启动.bat`。

> 想放到桌面：右键 `启动.bat` → 发送到 → 桌面快捷方式。

## 界面能做什么

| 区域 | 功能 |
|------|------|
| **左侧** | 笔记本列表、搜索、新建 |
| **中间** | 聊天。回答带引用，可复制、可存为笔记，有推荐追问 |
| **右侧 · 资料** | 加网址／YouTube、上传文件（PDF/Word/音频/图片）、粘贴文字、删除 |
| **右侧 · 生成** | 播客、视频、学习指南、简报、测验、闪卡、思维导图、幻灯片、信息图、博客稿 —— 点一下，完成后直接下载 |
| **右侧 · 笔记** | 查看存下来的笔记 |

## 命令行（可选）

不喜欢界面也可以用命令行，功能更全：

```powershell
.\scripts\nb.ps1 list
.\scripts\nb.ps1 create "我的研究" --use
.\scripts\nb.ps1 source add https://example.com/paper.pdf
.\scripts\nb.ps1 ask "核心论点是什么？"
.\scripts\nb.ps1 generate audio "中文深度对谈"
.\scripts\nb.ps1 download audio -o out\
```

Linux / macOS 用 `./scripts/nb`，参数相同。启动界面用 `./scripts/py app/server.py`。

## 文档

- 📖 [完整使用指南](docs/使用指南.md) —— 登录方式、CLI 全集、Python API、MCP 接入
- 🔧 [Windows 排查](docs/Windows排查.md) —— 装不上、登录失败、乱码等

## 目录

| 路径 | 说明 |
|------|------|
| `启动.bat` | **双击启动图形界面** |
| `app/` | 界面（FastAPI 后端 + 单页前端）|
| `scripts/` | 安装与命令行封装（`.ps1` 给 Windows，无后缀给 Linux/macOS）|
| `examples/` | Python 脚本示例 |
| `out/` | 下载的产物 |

> ⚠️ notebooklm-py 是非官方库，使用 Google 未公开接口，可能随时失效。适合原型、研究与个人项目。
