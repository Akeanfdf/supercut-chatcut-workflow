# Changelog

## 1.2.0 — 2026-08-28

- 新增正式 `rhythm.py` 运行时，以多频段频谱通量检测真实 Onset，并与推断 Beat/Downbeat 网格分开记录。
- 新增 `analyze-rhythm`、`--rhythm-mode`、`--beat-sync`、`--music-offset` 与 `--max-beat-snap-ms`。
- `all`、v1/v2 计划、ChatCut `SC_AUDIO` 和最终渲染现已共享 `beat_map.json` 与 BGM 偏移。
- 严格卡点模式要求至少 70% 主切点命中率，p95 对齐误差不超过 120ms；失败进入 `needs_input`。
- Release 配置包正式包含节拍运行时和对应回归测试。

## 1.1.0 — 2026-08-27

- 新增 `--reference`、`--source`、`--music` 与 `--confirm-media-rights` 的链接工作流，保留 `--source-dir`。
- 支持 B 站 BV、移动域名、分P及公开 `b23.tv` 重定向；不读取 Cookie、不绕过访问控制。
- 新增私有缓存、获取清单、SHA-256、失败降级和 7 项媒体获取测试。
- Skill / 配置升级到 1.1.0，发布包采用运行时白名单并递归扫描内嵌 ZIP。
- 新增响应式展示站、三路线任务生成器、《通往夏天的隧道》公开案例与 GitHub 自动化。

## 1.0.0

- 初始双入口工作流、38 项特效知识库、两道质量门和 ChatCut 可编辑审阅规范。
