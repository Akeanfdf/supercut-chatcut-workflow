# 信号干扰故障模板

用横向切片、RGB 色散、扫描线和跳帧隐藏镜头断点。适合死亡、传送、直播信号异常和强反应，不依赖商业插件。

```powershell
python tools\render_research_effect.py --ffmpeg "ffmpeg" --outgoing "output\delta_force_selects\04_doorway_kill_reload.mp4" --incoming "output\delta_force_selects\08_final_kill_victory.mp4" --template-config "templates\signal_glitch\template.json" --work-dir "output\analysis\signal_glitch" --out "output\signal_glitch_preview_v2.mp4"
```
