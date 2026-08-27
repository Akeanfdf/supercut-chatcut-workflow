<div align="center">

# Supercut × ChatCut

**让参考风格、真实节拍与叙事，落进 ChatCut 可编辑终版。**

[在线展示](https://akeanfdf.github.io/supercut-chatcut-workflow/) · [工作流规范](packages/supercut-chatcut-workflow/references/WORKFLOW.md) · [案例](docs/tunnel-case-study.md) · [安装](packages/supercut-chatcut-workflow/references/INSTALL.md)

</div>

![Supercut 产品界面](site/public/og.png)

Supercut 是一套特效感知剪辑工作流：Codex 负责需求、参考片、真实 Beat / Onset、镜头语义、特效和文字规划；本地运行时负责确定性分析与代理；ChatCut 保留多轨、原始素材关系和可视化审阅。

最简输入可以是 **两个公开 B 站视频链接＋一段本地音乐**。专业模式也支持本地参考片/目标素材，原创模式则可以从文案和素材开始。网页只生成任务 JSON 和 Codex 触发词，不上传媒体或宣称浏览器直接渲染。

> 《通往夏天的隧道》实际案例使用一条 B 站参考链接、用户合法持有的本地目标素材和本地音乐；不是两条 B 站链接。

## 三条路线

| 路线 | 输入 | 适合 |
|---|---|---|
| 快速链接 | 两个 B 站链接＋BGM | 最少准备、快速启动 |
| 专业本地 | 本地参考片/目标素材＋BGM＋可选标注 | 帧级素材控制 |
| 原创规划 | 文案/主题＋素材＋可选生成镜头 | 没有参考片的原创剪辑 |

```mermaid
flowchart LR
  A[授权检查] --> B[素材获取]
  B --> C[参考 / 原创视觉语法]
  C --> D[真实 Beat / Onset]
  D --> E[素材覆盖]
  E --> F[镜头蓝图]
  F --> G{质量门一 ≥ 85}
  G -->|通过| H[剪辑 / 特效 / 文字]
  G -->|缺失| N[needs_input]
  H --> I[ChatCut 多轨审阅]
  I --> J{质量门二}
  J --> K[可编辑终版 / 导出]
```

## 快速开始

```bash
python supercut.py all \
  --reference "https://www.bilibili.com/video/BV..." \
  --source "https://www.bilibili.com/video/BV..." \
  --music "music.mp3" \
  --confirm-media-rights \
  --out "output/draft.mp4"
```

不加 `--confirm-media-rights` 时，远程媒体不会下载，只生成 `needs_input` 获取报告。工作流不读取浏览器 Cookie，不绕过登录、付费、地区或其他访问控制；受限素材请改用本地合法文件。旧版 `--source-dir` 继续兼容。

安装 Skill 后，可直接触发：

```text
$supercut-chatcut-workflow
执行超级剪辑终版工作流
参考片复刻终版
原创规划终版
```

## 可审阅产物

- `edit_brief.json`：目标、平台、时长、约束和权限边界。
- `visual_grammar.json`：叙事、镜头、运动、转场、文字与声音语法。
- `acquisition_manifest.json`：原 URL、BV 号、分P、格式、时长、SHA-256 和授权状态。
- `asset_coverage.json`：每个镜头的素材覆盖与缺口。
- `shot_blueprint.json`：镜头首帧、动作、运镜、尾帧和衔接。
- `supercut-edit-plan-v2`：入出点、速度、声音、特效、理由和备选方案。
- 两道质量报告与 ChatCut 多轨同步清单。

## ChatCut 轨道

| 轨道 | 内容 |
|---|---|
| `SC_STORYBOARD` | 缺失或待生成镜头占位 |
| `SC_MAIN_A / SC_MAIN_B` | 可编辑原始素材与基础剪辑 |
| `SC_VFX_PREVIEW` | 精确特效代理 |
| `SC_TITLES` | 字幕、歌词、标题与文字动效 |
| `SC_AUDIO / SC_SFX` | 原声、BGM、旁白与音效 |
| `SC_NOTES` | 效果理由、风险和待确认项 |

ChatCut 不调用内置 AI 替代规划。最终输出仍保留原始镜头关系；本地扁平视频不能冒充可编辑终版。

## 真实案例：通往夏天的隧道

参考片：[BV17a8CzLEgZ](https://www.bilibili.com/video/BV17a8CzLEgZ/)

| 指标 | 结果 |
|---|---:|
| 目标源素材时长 | 4966.206 秒 |
| 终版 | 68 秒 / 60 fps / 4080 帧 |
| 镜头 / 短转场 | 70 / 27 |
| BGM 入点 | +2.720 秒 |
| 多频段音频相关度 | 0.696807 |
| 质量门一 / 二 | 96 / 97 |

案例详细拆解见 [docs/tunnel-case-study.md](docs/tunnel-case-study.md)，机器可读数据见 [docs/tunnel-case.json](docs/tunnel-case.json)。仓库不包含完整参考片、完整电影、原始 BGM、本机路径或 ChatCut 工程 ID。

## 测试与发布

```bash
python -X utf8 -m unittest discover -s tests
python -X utf8 packages/supercut-chatcut-workflow/scripts/build_release.py
cd site && npm ci && npm run build
```

发布构建会递归检查 Skill ZIP 内嵌的配置 ZIP，阻止盘符路径、下载目录标识、ChatCut UUID 和常见令牌格式进入发行包。

## English summary

Supercut is an effect-aware, beat-grounded editing workflow that turns a reference video or an original brief into an editable ChatCut timeline. Its smallest input mode accepts two public Bilibili links and one local music file; local-file and original-planning routes are also supported. Every run exposes its intermediate JSON artifacts, passes two quality gates, and preserves source relationships in ChatCut. Remote acquisition is rights-gated and never reads browser cookies or bypasses access controls.

## License

[MIT](LICENSE)
