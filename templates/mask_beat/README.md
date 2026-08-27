# 蒙版卡点模板

后续用户要求“蒙版卡点”时，默认使用 `template.json` 中的五段蒙版顺序。

- 蒙版顺序：横向条带、十字切片、扇形放射、阶梯矩形、多圆形。
- 每个蒙版对应一个卡点，蒙版内使用从不同交战时刻抽取的独立定格图片。
- 横向条带的三条、十字的横竖两组、每个扇叶、每个阶梯矩形和每个圆孔均使用不同图片。
- 游戏版视觉中心默认对准准星与枪体区域。
- 底层持续播放，蒙版之外轻微压暗降饱和，蒙版内提升对比和色彩。
- 只保留底层游戏原声，不添加额外音效。

渲染入口：`tools/render_mask_beat_preview.py`

```powershell
python tools\render_mask_beat_preview.py `
  --ffmpeg "ffmpeg" `
  --source-dir "output\delta_force_selects" `
  --template-config "templates\mask_beat\template.json" `
  --work-dir "output\analysis\mask_beat_preview_v1" `
  --out "output\mask_beat_preview_v2.mp4"
```
