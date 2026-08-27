# 曝光闪白转场模板

旧镜头亮部先过曝扩散，再用亮度遮罩带出新镜头。适合爆炸、击杀、技能、相机闪光和动漫睁眼。

```powershell
python tools\render_research_effect.py --ffmpeg "ffmpeg" --outgoing "output\delta_force_selects\04_doorway_kill_reload.mp4" --incoming "output\delta_force_selects\08_final_kill_victory.mp4" --template-config "templates\exposure_flash\template.json" --work-dir "output\analysis\exposure_flash" --out "output\exposure_flash_preview_v2.mp4"
```
