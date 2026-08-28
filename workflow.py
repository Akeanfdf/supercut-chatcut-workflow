"""Effect-aware planning, prompt compilation, and quality gates for SuperCut."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from effect_runtime import build_effect_registry


DEFAULT_NEGATIVE_CONSTRAINTS = [
    "人物身份、脸型、年龄、发型和服装不得漂移",
    "道具不得消失、换色、变形或无故改变手持关系",
    "不得出现多余手指、肢体融合、关节反折或穿模",
    "动作不得中途重置、瞬移、反向跳变或失去速度连续性",
    "人物、地面、物体之间的重力、碰撞和遮挡关系必须真实",
    "背景结构、光线方向、综合色温和材质纹理不得闪烁漂移",
    "不得无故翻转镜头轴线、突变焦段、重复帧或制造物理断裂",
]

CONTENT_TYPE_ALIASES = {
    "game": "game", "游戏": "game",
    "anime": "anime", "动漫": "anime", "漫剪": "anime",
    "short_drama": "short_drama", "短剧": "short_drama", "影视": "short_drama",
    "live_stream": "live_stream", "直播": "live_stream", "实况": "live_stream",
}

EFFECT_SPACING_SECONDS = {"subtle": 10.0, "balanced": 6.0, "intense": 3.0}


def _stable_id(prefix: str, *values: Any) -> str:
    payload = "|".join(str(value) for value in values).encode("utf-8")
    return f"{prefix}-{hashlib.sha1(payload).hexdigest()[:12]}"


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?:\r?\n)+|(?<=[。！？!?；;])", text.strip())
    return [part.strip() for part in parts if part.strip()]


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def build_edit_brief(request: dict[str, Any]) -> dict[str, Any]:
    """Normalize a user request for either reference-led or script-led work."""
    script = str(request.get("script") or request.get("copy") or request.get("prompt") or "").strip()
    reference = request.get("reference") or request.get("reference_path")
    input_mode = "reference" if reference else "script_assets"
    raw_type = str(request.get("content_type") or "game").strip().lower()
    content_type = CONTENT_TYPE_ALIASES.get(raw_type, raw_type)
    intensity = str(request.get("effect_intensity") or "balanced").strip().lower()
    if intensity not in EFFECT_SPACING_SECONDS:
        intensity = "balanced"
    target_duration = float(request.get("target_duration") or request.get("duration") or 30.0)
    width = int(request.get("width") or 1920)
    height = int(request.get("height") or 1080)
    fps = float(request.get("fps") or 30.0)
    events = [str(value).strip() for value in request.get("events", []) if str(value).strip()]
    return {
        "schema": "supercut-edit-brief-v1",
        "title": str(request.get("title") or "未命名剪辑").strip(),
        "objective": str(request.get("objective") or request.get("goal") or script[:160]).strip(),
        "audience": str(request.get("audience") or "通用观众").strip(),
        "input_mode": input_mode,
        "reference": str(reference) if reference else None,
        "script": script,
        "content_type": content_type,
        "events": events,
        "target": {
            "duration": max(1.0, target_duration),
            "width": max(16, width),
            "height": max(16, height),
            "fps": min(120.0, max(1.0, fps)),
            "platform": str(request.get("platform") or "unspecified"),
            "video_model": str(request.get("video_model") or "model-neutral"),
        },
        "effect_intensity": intensity,
        "allow_generation": bool(request.get("allow_generation", False)),
        "must_keep": list(request.get("must_keep", [])),
        "forbidden": list(request.get("forbidden", [])),
        "review_policy": "chatcut_required_before_final_render",
        "chatcut_ai_allowed": False,
    }


def build_script_template(brief: dict[str, Any], source_catalog: dict[str, Any]) -> dict[str, Any]:
    """Create a deterministic shot skeleton when there is no reference video."""
    beats = _sentences(brief.get("script", ""))
    if not beats:
        beats = [brief.get("objective") or "建立主题", "展开主体动作", "完成结果与收束"]
    assets = source_catalog.get("assets", [])
    target = brief["target"]
    count = min(24, max(3, len(beats)))
    duration = float(target["duration"])
    weights = [1.0 + (0.22 if index in {0, count - 1} else 0.0) for index in range(count)]
    total_weight = sum(weights)
    cursor = 0.0
    shots: list[dict[str, Any]] = []
    asset_motion = [float(asset.get("motion", 0.0)) for asset in assets] or [0.0, 1.0, 2.0]
    for index, weight in enumerate(weights):
        shot_duration = duration * weight / total_weight
        if index == count - 1:
            shot_duration = duration - cursor
        shots.append(
            {
                "index": index + 1,
                "start": round(cursor, 3),
                "end": round(cursor + shot_duration, 3),
                "duration": round(shot_duration, 3),
                "motion": asset_motion[index % len(asset_motion)],
                "luma": 128.0,
                "transition_after": "end" if index == count - 1 else "hard_cut",
                "transition_duration": 0.0,
                "layering_after": "end" if index == count - 1 else "standard_cut",
                "story_beat": beats[min(index, len(beats) - 1)],
            }
        )
        cursor += shot_duration
    return {
        "schema": "supercut-template-v1",
        "source": "designed_from_script",
        "reference": {
            "path": "",
            "fps": float(target["fps"]),
            "width": int(target["width"]),
            "height": int(target["height"]),
            "duration": duration,
        },
        "summary": {
            "shot_count": len(shots),
            "style_note": "由文案和目标时长设计；等待质量门和用户审阅。",
        },
        "shots": shots,
    }


def build_reference_timeline(
    template: dict[str, Any],
    annotations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    annotations = annotations or {}
    by_index = {int(row.get("index", 0)): row for row in annotations.get("shots", [])}
    rows: list[dict[str, Any]] = []
    required_semantic_fields = [
        "subject_anchor", "action_trajectory", "expression_change", "scene_space",
        "depth_layers", "shot_size", "lens", "composition", "depth_of_field",
        "camera_motion", "motion_speed", "light_direction", "color_temperature",
        "materials", "rhythm", "environment_sound",
    ]
    for source in template.get("shots", []):
        index = int(source["index"])
        semantic = by_index.get(index, {})
        row = {
            "index": index,
            "start": float(source["start"]),
            "end": float(source["end"]),
            "duration": float(source["duration"]),
            "start_frame": semantic.get("start_frame", "待视觉审阅"),
            "end_frame": semantic.get("end_frame", "待视觉审阅"),
            "transition_in": semantic.get("transition_in", "根据上一镜头确定"),
            "transition_out": source.get("transition_after", "hard_cut"),
            "reusable_method": semantic.get("reusable_method", "仅复用镜头逻辑与视觉方法"),
            "do_not_copy": semantic.get("do_not_copy", ["人物身份", "品牌", "台词", "具体故事"]),
        }
        for field in required_semantic_fields:
            row[field] = semantic.get(field)
        row["semantic_complete"] = all(_present(row.get(field)) for field in required_semantic_fields)
        rows.append(row)
    return {
        "schema": "supercut-reference-shot-timeline-v1",
        "reference": template.get("reference", {}),
        "shots": rows,
        "semantic_complete": bool(rows) and all(row["semantic_complete"] for row in rows),
    }


def build_visual_grammar(
    brief: dict[str, Any],
    template: dict[str, Any],
    reference_timeline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shots = template.get("shots", [])
    durations = [float(shot.get("duration", 0.0)) for shot in shots]
    transitions: dict[str, int] = {}
    for shot in shots:
        name = str(shot.get("transition_after", "hard_cut"))
        transitions[name] = transitions.get(name, 0) + 1
    source = "reference_extracted" if brief["input_mode"] == "reference" else "designed"
    return {
        "schema": "supercut-visual-grammar-v1",
        "source": source,
        "narrative_arc": ["建立", "推进", "强化", "收束"],
        "shot_duration": {
            "minimum": round(min(durations), 3) if durations else 0.0,
            "maximum": round(max(durations), 3) if durations else 0.0,
            "median": round(sorted(durations)[len(durations) // 2], 3) if durations else 0.0,
        },
        "transition_distribution": transitions,
        "camera_logic": "每次运镜必须服务主体动作、信息揭示或镜头衔接。",
        "continuity_rules": [
            "上一镜头结束动作必须成为下一镜头起始状态或明确的对比切点",
            "保持主体屏幕方向、视线、服装、道具和场景空间关系",
            "强转场必须具备动作、构图、遮挡或声音依据",
        ],
        "sound_logic": "环境声维持空间连续，动作声落在动作峰值，音乐只在结构节点强化。",
        "copyright_boundary": "只学习镜头逻辑和视觉方法，不复制人物、品牌、台词和具体故事。",
        "reference_semantics_complete": reference_timeline.get("semantic_complete") if reference_timeline else None,
    }


def build_asset_coverage(base_plan: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for shot in base_plan.get("shots", []):
        assigned = bool(shot.get("source"))
        rows.append(
            {
                "shot_index": int(shot.get("index", len(rows) + 1)),
                "source": shot.get("source"),
                "source_start": shot.get("source_start"),
                "required_duration": float(shot.get("duration", 0.0)),
                "status": "assigned" if assigned else "missing",
                "generation_allowed": False,
            }
        )
    missing = sum(row["status"] == "missing" for row in rows)
    return {
        "schema": "supercut-asset-coverage-v1",
        "shots": rows,
        "summary": {"total": len(rows), "assigned": len(rows) - missing, "missing": missing},
    }


def build_unassigned_base_plan(template: dict[str, Any]) -> dict[str, Any]:
    """Keep the quality/review workflow alive when footage is still missing."""
    shots = []
    for source in template.get("shots", []):
        shots.append(
            {
                "index": int(source["index"]),
                "source_id": None,
                "source": None,
                "source_start": None,
                "duration": float(source["duration"]),
                "speed": 1.0,
                "transition_after": source.get("transition_after", "hard_cut"),
                "transition_duration": float(source.get("transition_duration", 0.0)),
                "layering_after": source.get("layering_after", "standard_cut"),
                "match_reason": "等待用户补充素材或授权目标视频模型生成。",
            }
        )
    reference = template["reference"]
    return {
        "schema": "supercut-edit-plan-v1",
        "output": {
            "width": int(reference["width"]),
            "height": int(reference["height"]),
            "fps": float(reference["fps"]),
        },
        "shots": shots,
    }


def build_shot_blueprint(
    brief: dict[str, Any],
    base_plan: dict[str, Any],
    grammar: dict[str, Any],
    annotations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    annotations = annotations or {}
    by_index = {int(row.get("index", 0)): row for row in annotations.get("shots", [])}
    beats = _sentences(brief.get("script", ""))
    rows: list[dict[str, Any]] = []
    cursor = 0.0
    for shot in base_plan.get("shots", []):
        index = int(shot.get("index", len(rows) + 1))
        semantic = by_index.get(index, {})
        beat = semantic.get("story_beat") or (beats[min(index - 1, len(beats) - 1)] if beats else f"镜头{index}的叙事任务")
        duration = float(shot.get("duration", 0.0))
        row = {
            "id": _stable_id("shot", index, shot.get("source_id"), round(cursor, 3)),
            "index": index,
            "timeline_start": round(cursor, 3),
            "duration": duration,
            "story_beat": beat,
            "start_frame": semantic.get("start_frame", f"建立{beat}所需主体、空间和动作起点"),
            "subject_anchor": semantic.get("subject_anchor", "沿用全局主体外观、服装和道具锚点"),
            "action": semantic.get("action_trajectory", f"完成与“{beat}”对应的连续动作"),
            "camera": semantic.get("camera_motion", "使用稳定且服务主体动作的镜头运动"),
            "end_frame": semantic.get("end_frame", "在动作落点或信息完成时结束"),
            "transition_to_next": semantic.get("transition_out", shot.get("transition_after", "hard_cut")),
            "sound": semantic.get("environment_sound", "保持环境底噪并对齐关键动作声"),
            "source": shot.get("source"),
            "source_start": shot.get("source_start"),
            "speed": float(shot.get("speed", 1.0)),
            "events": list(semantic.get("events", brief.get("events", []))),
            "confidence": float(semantic.get("confidence", 0.75 if brief["input_mode"] == "script_assets" else 0.0)),
        }
        rows.append(row)
        cursor += duration
    return {
        "schema": "supercut-shot-blueprint-v1",
        "visual_grammar_source": grammar["source"],
        "shots": rows,
        "total_duration": round(cursor, 3),
    }


def build_generation_prompt_pack(
    brief: dict[str, Any],
    blueprint: dict[str, Any],
) -> dict[str, Any]:
    prompts: list[dict[str, Any]] = []
    for row in blueprint.get("shots", []):
        prompts.append(
            {
                "shot_id": row["id"],
                "duration": row["duration"],
                "prompt": (
                    f"起始画面：{row['start_frame']}。主体一致性：{row['subject_anchor']}。"
                    f"人物动作：{row['action']}。相机运动：{row['camera']}。"
                    f"结束画面：{row['end_frame']}。镜头衔接：{row['transition_to_next']}。"
                    f"声音：{row['sound']}。"
                ),
                "negative": list(DEFAULT_NEGATIVE_CONSTRAINTS),
            }
        )
    return {
        "schema": "supercut-generation-prompt-pack-v1",
        "target_model": brief["target"]["video_model"],
        "global_prompt": (
            "保持主体身份、外观、服装、道具、空间关系、光线方向和综合色温一致；"
            "动作遵守真实重力、碰撞和遮挡关系；只采用原创内容。"
        ),
        "global_negative": list(DEFAULT_NEGATIVE_CONSTRAINTS),
        "shots": prompts,
    }


def evaluate_quality(
    brief: dict[str, Any],
    reference_timeline: dict[str, Any] | None,
    coverage: dict[str, Any],
    blueprint: dict[str, Any],
    prompt_pack: dict[str, Any],
    rhythm_map: dict[str, Any] | None = None,
    rhythm_sync: dict[str, Any] | None = None,
    rhythm_required: bool = False,
) -> dict[str, Any]:
    critical: list[str] = []
    requests: list[str] = []
    if not brief.get("objective"):
        critical.append("missing_objective")
        requests.append("请说明视频希望达到的核心效果和受众。")
    if brief["input_mode"] == "reference" and (not reference_timeline or not reference_timeline.get("semantic_complete")):
        critical.append("reference_shot_semantics_missing")
        requests.append("请提供可审阅的参考视频，或补充逐镜头主体、动作、摄影、光线和声音标注。")
    if coverage["summary"]["missing"]:
        critical.append("source_coverage_missing")
        requests.append("请补充缺失镜头素材，或明确允许使用目标视频模型生成。")
    if not blueprint.get("shots"):
        critical.append("empty_timeline")
    rhythm_complete = bool(
        rhythm_map
        and rhythm_map.get("schema") == "supercut-beat-map-v2"
        and rhythm_map.get("status") == "complete"
        and rhythm_map.get("observed_onsets")
        and rhythm_map.get("strong_anchors")
    )
    alignment_ratio = float((rhythm_sync or {}).get("primary_cut_alignment_ratio", 0.0))
    if rhythm_required and not rhythm_complete:
        critical.append("rhythm_analysis_missing_or_unreliable")
        requests.append("请提供可解码的 BGM，完成真实 Beat/Onset 波形分析后再继续。")
    if rhythm_required and len(blueprint.get("shots", [])) > 1 and alignment_ratio < 0.7:
        critical.append("primary_cut_alignment_below_threshold")
        requests.append("当前强节奏段主切点命中率低于 70%；请调整素材余量、吸附窗口或卡点强度。")
    p95_offset = (rhythm_sync or {}).get("p95_cut_offset_ms")
    if rhythm_required and p95_offset is not None and float(p95_offset) > 120.0:
        critical.append("beat_alignment_error_above_threshold")
        requests.append("95% 切点的节拍对齐误差超过 120ms；请重新分析或调整切点。")

    score = 0
    score += 8 if brief.get("objective") else 0
    score += 4 if brief.get("content_type") else 0
    score += 4 if brief["target"].get("duration") else 0
    score += 4 if brief["target"].get("width") and brief["target"].get("height") else 0

    shots = blueprint.get("shots", [])
    score += 10 if shots else 0
    score += 5 if shots and all(float(row.get("duration", 0)) > 0 for row in shots) else 0
    score += 5 if shots and all(row.get("start_frame") and row.get("end_frame") for row in shots) else 0
    score += 5 if shots and all(row.get("action") and row.get("camera") for row in shots) else 0

    score += 8 if shots and all(row.get("subject_anchor") for row in shots) else 0
    score += 6 if shots and all(row.get("transition_to_next") for row in shots) else 0
    score += 6 if shots and all(row.get("sound") for row in shots) else 0

    score += 20 if coverage["summary"]["missing"] == 0 and coverage["summary"]["total"] > 0 else 0
    score += 5 if prompt_pack.get("global_negative") else 0
    score += 10 if len(prompt_pack.get("shots", [])) == len(shots) and shots else 0

    score = min(100, score)
    status = "awaiting_user_review" if score >= 85 and not critical else "needs_input"
    return {
        "schema": "supercut-analysis-qc-v1",
        "score": score,
        "threshold": 85,
        "status": status,
        "critical_failures": critical,
        "requests": requests,
        "checks": {
            "timeline_complete": bool(shots),
            "asset_coverage_complete": coverage["summary"]["missing"] == 0,
            "reference_semantics_complete": reference_timeline.get("semantic_complete") if reference_timeline else None,
            "prompts_complete": len(prompt_pack.get("shots", [])) == len(shots),
            "rhythm_required": rhythm_required,
            "rhythm_analysis_complete": rhythm_complete,
            "observed_onset_count": len((rhythm_map or {}).get("observed_onsets", [])),
            "strong_anchor_count": len((rhythm_map or {}).get("strong_anchors", [])),
            "primary_cut_alignment_ratio": alignment_ratio if rhythm_sync else None,
            "median_cut_offset_ms": (rhythm_sync or {}).get("median_cut_offset_ms"),
            "p95_cut_offset_ms": (rhythm_sync or {}).get("p95_cut_offset_ms"),
        },
    }


def _effect_candidates(
    effects_catalog: dict[str, Any],
    registry: dict[str, dict[str, Any]],
    content_type: str,
    events: set[str],
    confidence: float,
) -> list[tuple[float, dict[str, Any]]]:
    if confidence < 0.7:
        return []
    candidates: list[tuple[float, dict[str, Any]]] = []
    for effect in effects_catalog.get("effects", []):
        effect_id = effect["effect_id"]
        if effect_id not in registry:
            continue
        usage = effect.get("usage_profile", {})
        if content_type not in set(usage.get("content_types", [])):
            continue
        matched = events & set(usage.get("trigger_events", []))
        if not matched:
            continue
        score = 8.0 + len(matched) * 3.0
        if usage.get("intensity") == "strong":
            score += 0.15
        candidates.append((score, effect))
    candidates.sort(key=lambda row: (-row[0], row[1]["effect_id"]))
    return candidates


def plan_effects(
    shots: list[dict[str, Any]],
    brief: dict[str, Any],
    effects_catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    registry = build_effect_registry(effects_catalog)
    spacing = EFFECT_SPACING_SECONDS[brief["effect_intensity"]]
    effects: list[dict[str, Any]] = []
    cursor = 0.0
    last_effect_at = -spacing
    last_strong_at = -6.0
    for index, shot in enumerate(shots[:-1]):
        cursor += float(shot["duration"])
        if not shot.get("source") or not shots[index + 1].get("source"):
            continue
        if cursor - last_effect_at < spacing:
            continue
        events = set(shot.get("events", [])) | set(brief.get("events", []))
        candidates = _effect_candidates(
            effects_catalog,
            registry,
            brief["content_type"],
            events,
            float(shot.get("confidence", 0.0)),
        )
        selected: dict[str, Any] | None = None
        for _, candidate in candidates:
            intensity = candidate.get("usage_profile", {}).get("intensity", "medium")
            if intensity == "strong" and cursor - last_strong_at < 6.0:
                continue
            selected = candidate
            break
        if not selected:
            continue
        usage = selected["usage_profile"]
        low, high = [float(value) for value in usage.get("recommended_duration_seconds", [0.2, 0.5])]
        duration = min(high, max(low, 0.35), float(shot["duration"]) * 0.45, float(shots[index + 1]["duration"]) * 0.45)
        if duration <= 0.04:
            continue
        effect_id = selected["effect_id"]
        effect = {
            "id": _stable_id("effect", shot["id"], effect_id),
            "effect_id": effect_id,
            "principle": selected["principle"],
            "execution_tier": registry[effect_id]["execution_tier"],
            "role": (usage.get("transition_roles") or ["scene_transition"])[0],
            "after_shot_id": shot["id"],
            "timeline_start": round(cursor - duration, 3),
            "duration": round(duration, 3),
            "events": sorted(events & set(usage.get("trigger_events", []))),
            "confidence": float(shot.get("confidence", 0.0)),
            "selection_reason": usage.get("selection_prompt", ""),
            "requirements": usage.get("source_requirements", []),
            "avoid_when": usage.get("avoid_when", []),
            "chatcut_preview": {"mode": "exact_proxy", "proxy_path": None},
        }
        effects.append(effect)
        shot["effect_after"] = effect_id
        shot["effect_principle"] = selected["principle"]
        shot["transition_after"] = "effect"
        shot["transition_duration"] = round(duration, 3)
        last_effect_at = cursor
        if usage.get("intensity") == "strong":
            last_strong_at = cursor
    return effects


def compose_v2(
    brief: dict[str, Any],
    base_plan: dict[str, Any],
    template: dict[str, Any],
    effects_catalog: dict[str, Any],
    annotations: dict[str, Any] | None = None,
    *,
    rhythm_map: dict[str, Any] | None = None,
    rhythm_required: bool = False,
    beat_sync: str = "balanced",
    max_beat_snap_ms: float = 150.0,
) -> dict[str, Any]:
    rhythm_map = rhythm_map or base_plan.get("beat_map")
    if rhythm_map and not base_plan.get("rhythm_sync"):
        from rhythm import align_plan_to_rhythm

        base_plan = align_plan_to_rhythm(
            base_plan,
            rhythm_map,
            intensity=beat_sync,
            max_snap_ms=max_beat_snap_ms,
        )
    reference_timeline = build_reference_timeline(template, annotations) if brief["input_mode"] == "reference" else None
    grammar = build_visual_grammar(brief, template, reference_timeline)
    coverage = build_asset_coverage(base_plan)
    blueprint = build_shot_blueprint(brief, base_plan, grammar, annotations)
    prompt_pack = build_generation_prompt_pack(brief, blueprint)
    qc = evaluate_quality(
        brief,
        reference_timeline,
        coverage,
        blueprint,
        prompt_pack,
        rhythm_map,
        base_plan.get("rhythm_sync"),
        rhythm_required,
    )

    shots: list[dict[str, Any]] = []
    for source, blueprint_row in zip(base_plan.get("shots", []), blueprint["shots"]):
        row = copy.deepcopy(source)
        row["id"] = blueprint_row["id"]
        row["events"] = blueprint_row["events"]
        row["confidence"] = blueprint_row["confidence"]
        row["story_beat"] = blueprint_row["story_beat"]
        row["start_frame"] = blueprint_row["start_frame"]
        row["end_frame"] = blueprint_row["end_frame"]
        row["camera"] = blueprint_row["camera"]
        row["action"] = blueprint_row["action"]
        shots.append(row)
    effects = plan_effects(shots, brief, effects_catalog)
    revision_payload = json.dumps(
        {"brief": brief, "shots": shots, "effects": effects, "rhythm_sync": base_plan.get("rhythm_sync")},
        ensure_ascii=False,
        sort_keys=True,
    )
    plan_revision = hashlib.sha256(revision_payload.encode("utf-8")).hexdigest()[:16]
    return {
        "schema": "supercut-edit-plan-v2",
        "plan_revision": plan_revision,
        "review_status": qc["status"],
        "brief": brief,
        "output": copy.deepcopy(base_plan["output"]),
        "shots": shots,
        "effects": effects,
        "rhythm_sync": base_plan.get("rhythm_sync"),
        "audio": (
            {
                "mode": "music_only",
                "music": {
                    "source": rhythm_map.get("source", {}).get("path"),
                    "offset_seconds": float(rhythm_map.get("source", {}).get("timeline_offset_seconds", 0.0)),
                    "volume": 0.85,
                },
            }
            if rhythm_map and rhythm_map.get("source", {}).get("path") not in {None, "in-memory"}
            else None
        ),
        "artifacts": {
            "reference_timeline": reference_timeline,
            "visual_grammar": grammar,
            "asset_coverage": coverage,
            "shot_blueprint": blueprint,
            "generation_prompt_pack": prompt_pack,
            "beat_map": rhythm_map,
            "quality_gate": qc,
        },
        "sync": {
            "chatcut_ai_allowed": False,
            "mode": "bidirectional_review",
            "effect_preview": "exact_proxy",
            "project_id": None,
            "timeline_id": None,
            "remote_snapshot_hash": None,
        },
    }


def approve_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("schema") != "supercut-edit-plan-v2":
        raise ValueError("Only v2 plans use the quality-gate approval workflow")
    quality = plan.get("artifacts", {}).get("quality_gate", {})
    if quality.get("status") == "needs_input" or quality.get("score", 0) < quality.get("threshold", 85):
        raise ValueError("Plan cannot be approved before the quality gate passes")
    result = copy.deepcopy(plan)
    result["review_status"] = "approved"
    result["artifacts"]["quality_gate"]["status"] = "approved"
    return result


def save_artifacts(plan: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    mapping = {
        "reference_shot_timeline.json": plan.get("artifacts", {}).get("reference_timeline"),
        "visual_grammar.json": plan.get("artifacts", {}).get("visual_grammar"),
        "asset_coverage.json": plan.get("artifacts", {}).get("asset_coverage"),
        "shot_blueprint.json": plan.get("artifacts", {}).get("shot_blueprint"),
        "generation_prompt_pack.json": plan.get("artifacts", {}).get("generation_prompt_pack"),
        "beat_map.json": plan.get("artifacts", {}).get("beat_map"),
        "analysis_qc.json": plan.get("artifacts", {}).get("quality_gate"),
    }
    for name, value in mapping.items():
        if value is None:
            continue
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
