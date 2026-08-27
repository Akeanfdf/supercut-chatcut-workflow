# 镜面滑屏模板

项目中的标准“镜面滑屏”效果。后续用户要求使用“镜面滑屏”时，默认使用本目录中的 `template.json` 参数。

效果定义：

- 底层为同一时刻的灰度画面。
- 上层为同步彩色画面，通过约 `-60°` 的斜向条带蒙版显示。
- 斜带下方追加羽化暗影与轻微高光边缘。
- 三关键帧运动为快速进入、主体区域减速、快速离开。
- 仅保留原素材声音，不添加滑屏呼啸或其他额外音效。

渲染入口：`tools/render_mirror_slide_preview.py`

```powershell
python tools\render_mirror_slide_preview.py `
  --ffmpeg "ffmpeg" `
  --source "input\source\source.mp4" `
  --template-config "templates\mirror_slide\template.json" `
  --work-dir "output\analysis\mirror_slide" `
  --out "output\mirror_slide.mp4"
```
