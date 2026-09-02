# 幻灯片风格库

素材来自 [serenakeyitan/awesome-notebookLM-prompts](https://github.com/serenakeyitan/awesome-notebookLM-prompts)
（MIT，4.5k stars，全仓库只有 934 行 README、零代码）。原素材是日文语境的 YAML 设计规范，
这里做了三件事：

1. **中文化** —— 原文大量依赖 Hiragino / 明朝体等日文字体名，直接给中文内容会打架。
2. **对齐 notebooklm-py 的真实接口** —— 原文假设你在 Web UI 里贴 prompt；这里要能通过
   `--prompt-file` 传进去。
3. **保留原作者标注的硬性约束** —— 这些是实测有效的，不是装饰。

## 怎么用

```jsonc
// jobs/samples/slides-demo.job.json
"generate": {
  "format": "detailed",
  "prompt_file": "prompts/slides/报纸编辑风.txt"
}
```

`prompt_file` 会翻译成 `notebooklm generate slide-deck --prompt-file <路径>`。
用 `--prompt-file` 而不是内联 `prompt` 的原因：这些规范动辄上千字，**超出 shell 命令行长度限制**。

## 三条必须知道的接口事实（对着 0.8.1 的 `--help` 核对过）

1. **slide-deck 没有 `--orientation` 参数**（infographic 才有）。想要竖版，只能把
   "9:16 竖版" 写进 prompt 正文。上游 SKILL.md 的实测结论是：`.pptx` 画布可能仍是 16:9，
   但**每页内嵌图片**可以渲染成 9:16 —— 用 `python-pptx` 抽出来就是竖版素材。
2. **只有两个 `--format`**：`detailed` / `presenter`。**只有两个 `--length`**：`default` / `short`。
   风格不是靠这两个参数控制的，是靠 prompt。
3. **页数要写进 prompt**。想稳定拿到 8 页，就写 "严格 8 页"，别指望参数。
4. `--append` 在 `--format custom` 下**被静默忽略**（report 命令的坑，slide-deck 同理要小心）；
   本仓库的工单走位置参数 / `--prompt-file`，不碰 `--append`。

## 已收录的风格

| 文件 | 出处风格 | 适合 |
|---|---|---|
| `报纸编辑风.txt` | modern newspaper | 商业分析、行业观察、观点输出 |
| `极简留白风.txt` | sharp-edged minimalism | 作品集、技术方案、克制表达 |
| `黄黑编辑风.txt` | yellow × black editorial | 强对比、演讲、需要抓眼 |
| `杂志排版风.txt` | magazine style | 长文改写、故事性内容 |
| `科技霓虹风.txt` | tech / art / neon | AI、基础设施、前沿技术 |
| `研讨会极简风.txt` | for seminar use, minimal text | 教学、分享会、少字多图 |

## 所有风格共用的硬约束（原作者反复强调）

- **禁止 Markdown 符号**：幻灯片正文里任何情况下都不出现 `#` `*` `**` `-` 等符号，只用纯文本。
- **极端字号跳变**：标题与正文字号比 ≥ 10:1。"半吊子的字号差"是最常见的失败。
- **1 页 = 1 个信息**。
- **封面禁止居中**：用不对称构图制造张力。
- **标题要短**：2–5 个字的短语当视觉锚点，副标题才承载完整意思。
