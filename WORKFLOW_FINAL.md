# 超级剪辑终版工作流

本规范在 `WORKFLOW_V2.md`、38 特效知识库、三角洲实战案例、自动剪辑规划器分析和 ChatCut 动态文字案例的基础上合并形成。它支持参考片驱动和原创规划驱动，并把结构剪辑、节奏语法、文字刻画、特效条件、ChatCut 双向审阅和终版验收纳入同一状态机。

## 1. 触发方式

### 自然语言触发

使用以下任一短语即进入完整流程，而不是直接跳到时间线剪切：

- `执行超级剪辑终版工作流`
- `按终版工作流剪辑`
- `参考片复刻终版`
- `原创规划终版`

推荐完整格式：

```text
执行超级剪辑终版工作流
模式：参考片驱动 | 原创规划驱动
目标：<视频目标>
参考片：<URL或文件，可选>
目标素材：<文件、目录或ChatCut工程>
BGM：<文件，可选>
时长/画幅/帧率：<要求>
特效强度：subtle | balanced | intense
文字策略：无文字 | 正式歌词 | 台词字幕 | 主题标题
最终交付：ChatCut可编辑终版 | 审阅后导出
```

如果用户只说触发短语，系统先从当前线程、打开的 ChatCut 工程和已提供文件中补全可发现信息，只询问会改变结果的缺失项。

### JSON/CLI 触发

需求写入 JSON 后，使用现有命令链：

```powershell
python supercut.py brief --request examples/workflow_trigger_request.json --out output/run/edit_brief.json
python supercut.py catalog --source-dir input/source --out output/run/catalog.json
python supercut.py analyze --reference input/reference/reference.mp4 --out output/run/reference_template.json
python supercut.py compose --brief output/run/edit_brief.json --catalog output/run/catalog.json --template output/run/reference_template.json --annotations output/run/reference_annotations.json --out output/run/edit_plan_v2.json --artifacts-dir output/run/workflow
python supercut.py chatcut-manifest --plan output/run/edit_plan_v2.json --out output/run/chatcut/manifest.json
```

原创规划模式省略 `analyze` 和 `--template`。用户在 ChatCut 审阅通过后，才执行 `approve` 与最终输出。

## 2. 状态机

```text
triggered
→ brief_ready
→ reference_or_design_grammar_ready
→ asset_coverage_ready
→ needs_input | quality_gate_1_passed
→ edit_plan_ready
→ chatcut_synced
→ user_review
→ conflict_reported | quality_gate_2_passed
→ final_edit_ready
→ approved
→ rendered_or_delivered
→ verified
```

任何关键输入不完整、参考片语义不完整、素材覆盖存在硬缺口或质量分低于 85 时，状态必须进入 `needs_input`，不得伪装完成。

## 3. 完整阶段

### 阶段 1：需求标准化

输出 `edit_brief.json`：目标、受众、平台、时长、画幅、帧率、主体约束、必须保留内容、禁用内容、特效强度、文字策略、目标模型和生成权限。

### 阶段 2A：参考片逐镜头解析

输出无缺口、无重叠的逐镜头时间轴：起止时间、镜头时长、首尾画面、主体锚点、动作轨迹、表情、空间层次、景别、构图、焦段、运镜、光色、材质、声音、转场、镜头功能、连续性锚点和不得复制内容。

### 阶段 2B：原创视觉语法

没有参考片时，从文案和素材建立叙事阶段、情绪曲线、景别递进、运动关系、镜头时长分布、转场规则、文字策略、声音策略和人物连续性规则。

### 阶段 3：五类语法

- `Narrative Grammar`：故事阶段、信息顺序和情绪曲线。
- `Rhythm Grammar`：强拍、次强拍、Onset、持续段、抬升、释放和留白。
- `Visual Grammar`：主体、景别、构图、运动、光色和材质。
- `Transition Grammar`：动作匹配、构图匹配、遮挡、亮度、形状和声音衔接。
- `Effect Grammar`：素材条件、适配评分、强度、密度、重复惩罚和禁用规则。

规划粒度固定为：`Sequence → Shot → Beat Unit → Micro Visual Event`。BPM 是时间骨架，不是每拍强制切镜规则。

运行时生成 `supercut-beat-map-v2`。波形直接观测的瞬态写入 `observed_onsets`，BPM/相位推断写入 `inferred_beats`；自动切点只能引用来源为 `observed_onset` 的强锚点。鼓类与 Downbeat 均保留推断标签和置信度，不能把规则网格冒充真实鼓点。

### 阶段 4：素材覆盖与语义分析

输出 `asset_coverage.json` 和素材语义：事件、主体身份、动作起终点、运动矢量、视线方向、主体位置、构图锚点、前景遮挡、亮度、色温、安全区域、音频节拍、动作声和置信度。

完整实录先机器初筛，再人工视觉复核；音量峰值、HUD变化、菜单和失败界面不能直接当作高光。

### 阶段 5：镜头蓝图和生成提示词

输出 `shot_blueprint.json` 与 `generation_prompt_pack.json`。每个镜头必须包含起始状态、动作过程、速度、落点、相机、结束状态、下一镜头锚点、声音、素材来源和禁止项。

### 阶段 6：质量门 1

检查文案映射、目标时长、动作起承转合、主体一致性、素材覆盖、提示词可执行性、版权边界、特效输入条件、文字来源和节拍结构。综合分低于 85 或存在关键失败时进入 `needs_input`。

### 阶段 7：剪辑、特效、文字与声音规划

输出 `supercut-edit-plan-v2`：素材顺序、入出点、时长、速度曲线、故事阶段、主要切点、镜头内部事件、特效、文字、原声、BGM、SFX、选择理由和备用方案。

特效采用 catalog-first。`balanced` 默认明显特效平均不超过每 6 秒一次，强效果间隔至少 6 秒；素材条件置信度低于 0.7 时退回硬切、动作匹配切或基础转场。

文字必须先确认正式来源并与实际音频对齐，再标记语义角色、关键词、重复关系和阅读时间。字幕位置必须结合目标帧主体与已有字幕确定，不能按固定安全区机械放置。

### 阶段 8：ChatCut 可视化同步

ChatCut 作为可编辑审阅层。标准轨道：

- `SC_STORYBOARD`：缺失或待生成镜头占位。
- `SC_MAIN_A / SC_MAIN_B`：原始素材或可编辑代理。
- `SC_VFX_PREVIEW`：精确特效预览。
- `SC_TITLES`：歌词、字幕、标题和文字动效。
- `SC_AUDIO`：原声、BGM和旁白。
- `SC_SFX`：动作声和转场声。
- `SC_NOTES`：效果名称、理由、风险和待确认项。

时间线必须保留原始素材的可编辑关系；精确代理不能替代整个可编辑工程。

### 阶段 9：用户审阅和 Pull

允许调整镜头顺序、入出点、时长、速度、特效区间、文字和音乐。Pull 后重新检查动作连续性、素材余量和特效条件。本地与 ChatCut 同时修改时生成冲突报告，禁止静默覆盖。

### 阶段 10：质量门 2

检查故事、节奏、动作、视线、空间、背景、特效必要性、转场依据、文字安全、动作声同步、黑帧、跳帧和音画断裂。必须进行结构读取、合成像素检查和带声音的整段播放感知验收。

### 阶段 11：终版与验收

用户确认后形成最终可编辑时间线；只有明确要求最终文件时才导出。最终检查完整解码、分辨率、帧率、时长、音画同步、黑帧、静音、异常闪烁、主体一致性、动作连续性、特效帧位和文字安全区。

## 4. 案例形成的硬规则

- 三角洲整局案例：完整实录只需一个入口；机器初筛后必须人工复核并剔除菜单假高光。
- 枪体锁屏案例：前景枪体、手部、HUD、旧背景、新背景和效果层必须分开建模。
- 故障燃烧案例：冻结旧枪、新背景提前进入、轮廓燃烧退出、故障只遮最后数帧；不得随意重绘原枪械。
- 镜面滑屏案例：效果模板必须记录运动曲线和音频策略，用户删除的音效不得自动恢复。
- 回忆快闪案例：允许一个 Shot 内在多个 Beat Unit 上产生背景切换、缩放、色差和曝光脉冲。
- 九宫格与蒙版卡点案例：每个格子、条带、扇叶或孔洞必须独立绑定素材，不能复用同一画面伪装多源拼贴。
- 动态歌词案例：正式歌词优先于占位文案；重复句必须保留并允许变化排版。
- AMV/MAD 案例：强拍承担主要切换，但匹配切、动作完成和情绪停留可以覆盖机械卡点。

## 5. 完成定义

只有同时满足以下条件才能报告“终版完成”：

1. 工作流状态不是 `needs_input` 或 `conflict_reported`。
2. 时间线无意外空隙、重叠、黑帧或错层。
3. 参考片/原创语法已落实到镜头选择，而不只是时长统计。
4. 主要切点经过真实 Onset 与动作落点双重检查；严格卡点任务命中率至少 70%，p95 误差不超过 120ms。
5. 特效有内容依据且素材条件成立。
6. 正式文字准确、可读并避让主体。
7. 已检查实际合成像素。
8. 已完成带声音的整段播放感知验收。
9. ChatCut 保留可编辑原始镜头关系。
