# 九宫格开场模板

后续用户要求“九宫格开场”时，默认调用 `template.json` 中的九层排列和主格接管曲线。

- 九条视频各自保持动态，不使用静态截图填格。
- 从中心格开始，按设定顺序向外层逐格出现。
- 九格短暂完整展示后，中心主格平滑放大为全屏并继续播放。
- 音频只使用中心主格素材的原声，不添加额外音效。

渲染入口：`tools/render_nine_grid_opening.py`

```powershell
python tools\render_nine_grid_opening.py `
  --ffmpeg "ffmpeg" `
  --source-dir "output\delta_force_selects" `
  --template-config "templates\nine_grid_opening\template.json" `
  --work-dir "output\analysis\nine_grid_preview_v1" `
  --out "output\nine_grid_preview_v1.mp4"
```
