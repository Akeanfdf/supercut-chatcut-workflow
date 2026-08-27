# 贡献指南

感谢你改进 Supercut × ChatCut 工作流。

1. 先建立 Issue，说明素材类型、入口模式、预期行为和可复现步骤。
2. 分支保持单一目的；不要提交原始媒体、下载缓存、本机路径、ChatCut 工程 ID 或凭据。
3. 代码变更运行 `python -X utf8 -m unittest discover -s tests`。
4. Skill 或配置变更运行 `python -X utf8 packages/supercut-chatcut-workflow/scripts/build_release.py`，确认递归隐私扫描通过。
5. 站点变更在 `site/` 运行 `npm ci` 与 `npm run build`，并检查桌面、移动端和低动态模式。

提交 PR 时说明变更范围、验证结果、隐私影响和是否影响已有 CLI 参数。媒体案例只接受拥有公开再分发权的最小片段。
