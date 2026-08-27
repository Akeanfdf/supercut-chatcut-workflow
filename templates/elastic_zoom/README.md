# 弹性缩放拉镜模板

使用中心对齐、缩放过冲、径向模糊和轻闪白完成动作接切，适合击杀、放大、人物转身和主播反应。

```powershell
python tools\render_research_effect.py --ffmpeg "ffmpeg" --outgoing "output\delta_force_selects\04_doorway_kill_reload.mp4" --incoming "output\delta_force_selects\08_final_kill_victory.mp4" --template-config "templates\elastic_zoom\template.json" --work-dir "output\analysis\elastic_zoom" --out "output\elastic_zoom_preview_v2.mp4"
```
