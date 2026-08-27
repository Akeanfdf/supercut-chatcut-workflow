# 枪械前景保留遮切模板

把第一人称枪械与手部扣成独立层，保持屏幕坐标不动，让背景先完成换镜，再用能量灼烧和短故障移除旧前景。

渲染入口：`tools/render_burn_glitch_preview.py`。适用提示词会由 `tools/build_effect_knowledge.py` 同步到 `template.json`。

```powershell
python tools\render_burn_glitch_preview.py --ffmpeg "ffmpeg" --outgoing "output\delta_force_selects\04_doorway_kill_reload.mp4" --incoming "output\delta_force_selects\08_final_kill_victory.mp4" --work-dir "output\analysis\foreground_weapon_hold" --out "output\foreground_weapon_hold_preview_v2.mp4"
```
