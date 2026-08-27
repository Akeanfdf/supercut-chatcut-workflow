# 回忆快闪模板

后续用户要求“回忆快闪”时，默认使用 `template.json` 中的参数。

效果定义：

- 从原始实录中自动抽取 10 个回忆时刻。
- 每个画面单元约 0.47 秒，从倾斜放大快速回归正位。
- 背景叠加色差、水平摇晃、暗角和切片曝光脉冲。
- 第一人称枪体扣像保留在前景，增加白色描边与软辉光。
- 仅保留原素材声音，不自动添加额外音效。

渲染入口：`tools/render_memory_flash_preview.py`

```powershell
python tools\render_memory_flash_preview.py `
  --ffmpeg "ffmpeg" `
  --source "input\source\source.mp4" `
  --template-config "templates\memory_flash\template.json" `
  --work-dir "output\analysis\memory_flash_preview" `
  --out "output\memory_flash_preview.mp4"
```
