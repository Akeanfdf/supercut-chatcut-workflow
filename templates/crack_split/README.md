# 裂缝分割破碎模板

把旧画面分割为不规则块，冲击点后沿裂缝散开并露出下一镜头。适合击杀、重击、情绪破裂和强场景变化。

```powershell
python tools\render_research_effect.py --ffmpeg "ffmpeg" --outgoing "output\delta_force_selects\04_doorway_kill_reload.mp4" --incoming "output\delta_force_selects\08_final_kill_victory.mp4" --template-config "templates\crack_split\template.json" --work-dir "output\analysis\crack_split" --out "output\crack_split_preview_v2.mp4"
```
