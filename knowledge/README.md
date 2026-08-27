# 特效知识库

`effects/*.json` 是单个效果卡片，`effect_catalog.json` 是供程序检索的总索引。`bilibili_research_manifest.json` 与 `bilibili_research_manifest_v3.json` 分别保存 v2、v3 临时研究文件的校验与验证状态。

内容类型采用以下固定值：

- `game`：游戏录屏、击杀集锦、技能展示。
- `anime`：动漫混剪、角色揭示、情绪卡点。
- `short_drama`：短剧、影视混剪、对白与情绪转折。
- `live_stream`：实况、直播切片、多机位和主播反应。

每个效果的 `selection_prompt` 都可以直接交给后续剪辑规划代理。提示词同时写明触发动作、素材前提、推荐时长和禁用场景，不能只根据效果名称盲目套用。

v3 累计收录 38 个效果、14 个视觉原理；新增 15 个效果中有 6 个新原理代表可直接渲染。教程临时文件和审阅图已删除，不属于知识库交付物。

重新生成并校验：

```powershell
python tools\build_effect_knowledge.py
python -m unittest tests.test_effect_knowledge
```
