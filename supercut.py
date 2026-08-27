from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
TRANSITION_MAP = {
    "hard_cut": "fade",
    "fade_black": "fadeblack",
    "dissolve": "dissolve",
    "whip": "smoothleft",
}

CONTENT_TYPE_ALIASES = {
    "game": "game", "游戏": "game",
    "anime": "anime", "动漫": "anime", "漫剪": "anime",
    "short_drama": "short_drama", "short-drama": "short_drama", "短剧": "short_drama",
    "live_stream": "live_stream", "live-stream": "live_stream", "直播": "live_stream", "实况": "live_stream",
}
EVENT_ALIASES = {
    "切枪": "weapon_switch", "换弹": "reload", "击杀": "kill_confirm", "技能": "skill_cast",
    "放大": "zoom_in", "拉镜": "zoom_in", "睁眼": "eye_open", "转身": "character_turn",
    "情绪反转": "emotion_turn", "场景切换": "scene_change", "主播反应": "host_reaction",
    "多机位": "camera_change", "机位切换": "camera_change", "闪回": "flashback",
    "死亡": "death", "卡点": "beat", "高光": "highlight", "开场": "opening",
    "章节变化": "chapter_change", "回忆": "memory", "片尾": "outro", "下播": "stream_end",
    "标题": "title", "金句": "quote", "人物入场": "character_entry", "英雄揭示": "hero_reveal",
    "boss登场": "boss_appear", "危险": "danger", "人物横穿": "character_cross",
}


@dataclass
class FrameFeature:
    time: float
    image: np.ndarray
    luma: float
    change: float
    motion: float


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def load_project_config() -> tuple[dict[str, Any], Path]:
    """Load the non-secret project configuration from the repository root."""
    config_path = Path(__file__).resolve().parent / "supercut.config.json"
    if not config_path.is_file():
        return {}, config_path
    return load_json(config_path), config_path


def _normalise_content_type(value: str) -> str:
    key = value.strip().lower()
    if key not in CONTENT_TYPE_ALIASES:
        choices = ", ".join(sorted(set(CONTENT_TYPE_ALIASES.values())))
        raise ValueError(f"未知内容类型：{value}；可用值：{choices}")
    return CONTENT_TYPE_ALIASES[key]


def _normalise_event(value: str) -> str:
    key = value.strip().lower().replace(" ", "_")
    return EVENT_ALIASES.get(value.strip(), EVENT_ALIASES.get(key, key))


def recommend_effects(catalog: dict[str, Any], content_type: str, events: list[str], top: int = 5) -> list[dict[str, Any]]:
    normalised_type = _normalise_content_type(content_type)
    normalised_events = {_normalise_event(event) for event in events if event.strip()}
    ranked: list[dict[str, Any]] = []
    for effect in catalog.get("effects", []):
        usage = effect.get("usage_profile", {})
        content_types = set(usage.get("content_types", []))
        triggers = set(usage.get("trigger_events", []))
        if normalised_type not in content_types:
            continue
        matched_events = sorted(normalised_events & triggers)
        score = 5.0 + 3.0 * len(matched_events)
        # Prefer effects specialised for the requested scene over broad
        # catch-all entries.  Implementation status is only a tie-breaker.
        score += 2.0 / max(1, len(content_types))
        score += 1.0 / max(1, len(triggers))
        if normalised_events and not matched_events:
            score -= 2.0
        ranked.append(
            {
                "effect_id": effect["effect_id"],
                "name": effect["name"],
                "score": score,
                "matched_events": matched_events,
                "principle": effect["principle"],
                "implemented": effect.get("implementation", {}).get("status") == "implemented",
                "source_requirements": usage.get("source_requirements", []),
                "selection_prompt": usage.get("selection_prompt", ""),
            }
        )
    ranked.sort(key=lambda row: (-row["score"], not row["implemented"], row["effect_id"]))
    return ranked[: max(1, top)]


def media_info(path: Path) -> dict[str, Any]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"无法读取视频：{path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duration = frames / fps if fps > 0 else 0.0
    cap.release()
    return {
        "path": str(path.resolve()),
        "fps": round(fps, 3),
        "frames": frames,
        "width": width,
        "height": height,
        "duration": round(duration, 3),
        "aspect_ratio": round(width / height, 4) if height else 0,
    }


def _small_gray(frame: np.ndarray, width: int = 192) -> tuple[np.ndarray, np.ndarray]:
    height = max(1, round(frame.shape[0] * width / frame.shape[1]))
    small = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    return small, cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def extract_features(
    path: Path,
    analysis_fps: float = 10.0,
    max_samples: int | None = None,
    feature_width: int = 192,
) -> tuple[dict[str, Any], list[FrameFeature]]:
    info = media_info(path)
    cap = cv2.VideoCapture(str(path))
    native_fps = max(float(info["fps"]), 1.0)
    stride = max(1, round(native_fps / analysis_fps))
    features: list[FrameFeature] = []
    previous_gray: np.ndarray | None = None
    previous_hist: np.ndarray | None = None
    frame_index = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_index % stride:
            frame_index += 1
            continue
        small, gray = _small_gray(frame, width=feature_width)
        hist = cv2.calcHist([gray], [0], None, [32], [0, 256])
        cv2.normalize(hist, hist)
        change = 0.0
        motion = 0.0
        if previous_gray is not None and previous_hist is not None:
            pixel_delta = float(np.mean(cv2.absdiff(gray, previous_gray))) / 255.0
            hist_delta = 1.0 - float(cv2.compareHist(previous_hist, hist, cv2.HISTCMP_CORREL))
            change = max(0.0, 0.72 * pixel_delta + 0.28 * hist_delta)
            flow = cv2.calcOpticalFlowFarneback(previous_gray, gray, None, 0.5, 2, 11, 2, 5, 1.1, 0)
            magnitude = cv2.magnitude(flow[..., 0], flow[..., 1])
            # Motion is often localized to a subject; a high percentile avoids
            # a static background drowning it out while remaining robust to noise.
            motion = float(np.percentile(magnitude, 96))
        features.append(
            FrameFeature(
                time=frame_index / native_fps,
                image=small,
                luma=float(np.mean(gray)),
                change=change,
                motion=motion,
            )
        )
        previous_gray = gray
        previous_hist = hist
        frame_index += 1
        if max_samples and len(features) >= max_samples:
            break
    cap.release()
    return info, features


def _median(values: Iterable[float], fallback: float = 0.0) -> float:
    array = list(values)
    return float(np.median(array)) if array else fallback


def _classify_transition(features: list[FrameFeature], index: int, threshold: float) -> tuple[str, float]:
    lo = max(0, index - 3)
    hi = min(len(features), index + 4)
    window = features[lo:hi]
    nearby_luma = [item.luma for item in window]
    nearby_motion = [item.motion for item in window if item.motion > 0]
    global_motion = [item.motion for item in features if item.motion > 0]
    motion_gate = float(np.percentile(global_motion, 80)) if global_motion else 1.0
    edge_luma = [features[lo].luma, features[hi - 1].luma]

    if nearby_luma and min(nearby_luma) < 28 and np.mean(edge_luma) > 55:
        return "fade_black", 0.28
    if nearby_motion and max(nearby_motion) > max(2.0, motion_gate * 1.8):
        return "whip", 0.18
    if features[index].change < threshold * 1.35:
        return "dissolve", 0.30
    return "hard_cut", 0.0


def _foreground_hold_metrics(features: list[FrameFeature], index: int) -> tuple[float, float, float]:
    before = features[max(0, index - 1)].image
    after = features[min(len(features) - 1, index + 1)].image
    if before.shape != after.shape:
        after = cv2.resize(after, (before.shape[1], before.shape[0]), interpolation=cv2.INTER_AREA)
    delta = cv2.absdiff(before, after).astype(np.float32) / 255.0
    height, width = delta.shape[:2]
    # Ignore the minimap and bottom HUD. The central upper area represents the
    # moving world; the lower-right-biased area contains the first-person gun.
    background = delta[int(height * 0.12) : int(height * 0.60), int(width * 0.17) : int(width * 0.90)]
    foreground = delta[int(height * 0.52) : int(height * 0.92), int(width * 0.17) : width]
    background_change = float(np.mean(background)) if background.size else 0.0
    foreground_change = float(np.mean(foreground)) if foreground.size else 0.0
    score = background_change / max(foreground_change, 1e-4)
    return background_change, foreground_change, score


def detect_shots(features: list[FrameFeature], duration: float, min_shot: float = 0.35) -> list[dict[str, Any]]:
    if not features:
        return []
    changes = np.array([feature.change for feature in features], dtype=np.float32)
    core = changes[changes > 0]
    median = float(np.median(core)) if core.size else 0.0
    mad = float(np.median(np.abs(core - median))) if core.size else 0.0
    percentile = float(np.percentile(core, 88)) if core.size else 0.0
    threshold = max(0.105, median + 5.0 * mad, percentile)

    candidate_indices: list[int] = []
    for index in range(2, len(features) - 2):
        value = features[index].change
        is_peak = value >= max(features[index - 1].change, features[index + 1].change)
        if value >= threshold and is_peak:
            candidate_indices.append(index)

    # A dip to black often spans several low-change frames, so add its darkest point.
    for index in range(2, len(features) - 2):
        if features[index].luma < 22:
            neighbors = [features[index - 2].luma, features[index + 2].luma]
            if max(neighbors) > 50:
                candidate_indices.append(index)

    selected: list[int] = []
    for index in sorted(set(candidate_indices), key=lambda item: features[item].time):
        if not selected or features[index].time - features[selected[-1]].time >= min_shot:
            selected.append(index)
        elif features[index].change > features[selected[-1]].change:
            selected[-1] = index

    boundaries = [0.0] + [features[index].time for index in selected]
    end_time = max(float(duration), features[-1].time)
    if end_time - boundaries[-1] < 0.08 and len(boundaries) > 1:
        boundaries.pop()
        selected.pop()
    boundaries.append(end_time)

    shots: list[dict[str, Any]] = []
    for shot_index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        within = [feature for feature in features if start <= feature.time < end]
        transition = ("end", 0.0)
        layering = "end"
        hold_score = 0.0
        background_change = 0.0
        foreground_change = 0.0
        if shot_index < len(selected):
            transition = _classify_transition(features, selected[shot_index], threshold)
            background_change, foreground_change, hold_score = _foreground_hold_metrics(
                features, selected[shot_index]
            )
            if background_change >= 0.12 and hold_score >= 1.15:
                layering = "foreground_hold_candidate"
            elif transition[0] in {"whip", "dissolve", "fade_black"}:
                layering = "full_frame_effect"
            else:
                layering = "standard_cut"
        shots.append(
            {
                "index": shot_index + 1,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(max(0.08, end - start), 3),
                "motion": round(_median(item.motion for item in within), 4),
                "luma": round(_median(item.luma for item in within), 2),
                "transition_after": transition[0],
                "transition_duration": transition[1],
                "layering_after": layering,
                "foreground_hold_score": round(hold_score, 3),
                "background_change": round(background_change, 4),
                "foreground_change": round(foreground_change, 4),
            }
        )
    return shots


def write_contact_sheet(items: list[tuple[str, np.ndarray]], path: Path, columns: int = 4) -> None:
    if not items:
        return
    tile_w, tile_h = 320, 190
    rows = math.ceil(len(items) / columns)
    canvas = np.full((rows * tile_h, columns * tile_w, 3), 22, dtype=np.uint8)
    for index, (label, image) in enumerate(items):
        row, column = divmod(index, columns)
        resized = cv2.resize(image, (tile_w, tile_h - 30), interpolation=cv2.INTER_AREA)
        y, x = row * tile_h, column * tile_w
        canvas[y : y + tile_h - 30, x : x + tile_w] = resized
        cv2.putText(canvas, label[:42], (x + 8, y + tile_h - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (240, 240, 240), 1, cv2.LINE_AA)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), canvas)


def analyze_reference(reference: Path, out: Path, sheet: Path | None) -> dict[str, Any]:
    info, features = extract_features(reference, analysis_fps=12.0)
    shots = detect_shots(features, float(info["duration"]))
    durations = [shot["duration"] for shot in shots]
    template = {
        "schema": "supercut-template-v1",
        "reference": info,
        "summary": {
            "shot_count": len(shots),
            "median_shot_duration": round(_median(durations), 3),
            "cuts_per_minute": round(len(shots) * 60 / max(float(info["duration"]), 0.1), 2),
            "style_note": "自动初判；复杂蒙版、叠化、匹配剪辑和变速需结合参考画面复核。",
        },
        "shots": shots,
    }
    save_json(out, template)
    if sheet:
        thumbnails: list[tuple[str, np.ndarray]] = []
        for shot in shots:
            midpoint = (shot["start"] + shot["end"]) / 2
            nearest = min(features, key=lambda feature: abs(feature.time - midpoint))
            label = f"#{shot['index']} {shot['duration']:.2f}s {shot['transition_after']}"
            thumbnails.append((label, nearest.image))
        write_contact_sheet(thumbnails, sheet)
    return template


def list_videos(source_dir: Path) -> list[Path]:
    if source_dir.is_file():
        return [source_dir] if source_dir.suffix.lower() in VIDEO_EXTENSIONS else []
    return sorted(path for path in source_dir.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS)


def catalog_sources(source_dir: Path, out: Path, sheet: Path | None) -> dict[str, Any]:
    videos = list_videos(source_dir)
    if not videos:
        raise RuntimeError(f"素材目录中没有视频：{source_dir}")
    assets: list[dict[str, Any]] = []
    thumbnails: list[tuple[str, np.ndarray]] = []
    for path in videos:
        info, features = extract_features(path, analysis_fps=2.0, max_samples=40)
        assets.append(
            {
                **info,
                "id": f"asset-{len(assets) + 1:03d}",
                "motion": round(_median(item.motion for item in features), 4),
                "luma": round(_median(item.luma for item in features), 2),
                "description": "",
                "tags": [],
                "priority": 1,
            }
        )
        if features:
            picks = [features[len(features) // 3], features[min(len(features) - 1, len(features) * 2 // 3)]]
            for pick_index, feature in enumerate(picks, start=1):
                thumbnails.append((f"{path.name} [{pick_index}] {feature.time:.1f}s", feature.image))
    catalog = {"schema": "supercut-catalog-v1", "source_dir": str(source_dir.resolve()), "assets": assets}
    save_json(out, catalog)
    if sheet:
        write_contact_sheet(thumbnails, sheet)
    return catalog


def _normalized(values: list[float]) -> np.ndarray:
    array = np.array(values, dtype=np.float32)
    positive = array[array > 0]
    scale = float(np.percentile(positive, 95)) if positive.size else 1.0
    return np.clip(array / max(scale, 1e-6), 0.0, 2.0)


def _region_delta(before: np.ndarray, after: np.ndarray, y0: float, y1: float, x0: float, x1: float) -> float:
    delta = cv2.absdiff(before, after).astype(np.float32) / 255.0
    height, width = delta.shape[:2]
    region = delta[int(height * y0) : int(height * y1), int(width * x0) : int(width * x1)]
    return float(np.mean(region)) if region.size else 0.0


def scan_raw_matches(
    source_dir: Path,
    out: Path,
    sheet: Path | None,
    max_candidates_per_video: int = 48,
) -> dict[str, Any]:
    videos = list_videos(source_dir)
    if not videos:
        raise RuntimeError(f"原始实录目录中没有视频：{source_dir}")
    assets: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    thumbnails: list[tuple[str, np.ndarray]] = []

    for video_index, path in enumerate(videos, start=1):
        info, features = extract_features(path, analysis_fps=2.0, feature_width=96)
        if len(features) < 3:
            continue
        motions = [feature.motion for feature in features]
        changes = [feature.change for feature in features]
        killfeed_changes = [0.0]
        center_changes = [0.0]
        for before, after in zip(features, features[1:]):
            killfeed_changes.append(_region_delta(before.image, after.image, 0.04, 0.30, 0.62, 0.99))
            center_changes.append(_region_delta(before.image, after.image, 0.52, 0.90, 0.30, 0.72))
        motion_n = _normalized(motions)
        change_n = _normalized(changes)
        killfeed_n = _normalized(killfeed_changes)
        center_n = _normalized(center_changes)
        scores = 0.34 * motion_n + 0.18 * change_n + 0.30 * killfeed_n + 0.18 * center_n

        desired = min(max_candidates_per_video, max(8, round(float(info["duration"]) / 60 * 2.5)))
        ranked = sorted(range(1, len(features) - 1), key=lambda index: float(scores[index]), reverse=True)
        selected: list[int] = []
        for index in ranked:
            timestamp = features[index].time
            if any(abs(timestamp - features[other].time) < 5.0 for other in selected):
                continue
            selected.append(index)
            if len(selected) >= desired:
                break
        selected.sort(key=lambda index: features[index].time)

        asset_id = f"raw-{video_index:03d}"
        assets.append({**info, "id": asset_id, "candidate_count": len(selected)})
        for index in selected:
            feature = features[index]
            bg_change, fg_change, hold_score = _foreground_hold_metrics(features, index)
            tags: list[str] = []
            if motion_n[index] >= 0.9:
                tags.append("high_motion")
            if killfeed_n[index] >= 0.9:
                tags.append("killfeed_change")
            if center_n[index] >= 0.9:
                tags.append("center_combat_event")
            if bg_change >= 0.12 and hold_score >= 1.15:
                tags.append("foreground_hold_candidate")
            candidate_id = f"candidate-{len(candidates) + 1:04d}"
            window_start = max(0.0, feature.time - 4.5)
            window_end = min(float(info["duration"]), feature.time + 2.5)
            candidate = {
                "id": candidate_id,
                "source_id": asset_id,
                "source": info["path"],
                "peak_time": round(feature.time, 3),
                "window_start": round(window_start, 3),
                "window_end": round(window_end, 3),
                "window_duration": round(window_end - window_start, 3),
                "highlight_score": round(float(scores[index]), 4),
                "signals": {
                    "motion": round(float(motion_n[index]), 3),
                    "frame_change": round(float(change_n[index]), 3),
                    "killfeed_change": round(float(killfeed_n[index]), 3),
                    "center_combat_event": round(float(center_n[index]), 3),
                    "foreground_hold_score": round(hold_score, 3),
                },
                "tags": tags,
                "review_status": "pending_codex_visual_review",
                "semantic_note": "算法仅用于缩小检索范围；需视觉确认是否真实击杀、动作是否完整以及视角是否适合成片。",
            }
            candidates.append(candidate)
            thumbnails.append(
                (
                    f"{candidate_id} {feature.time:.1f}s score={scores[index]:.2f}",
                    feature.image,
                )
            )

    result = {
        "schema": "supercut-raw-match-scan-v1",
        "source_dir": str(source_dir.resolve()),
        "assets": assets,
        "candidates": candidates,
        "summary": {
            "video_count": len(assets),
            "candidate_count": len(candidates),
            "review_status": "pending_codex_visual_review",
        },
    }
    save_json(out, result)
    if sheet:
        write_contact_sheet(thumbnails, sheet)
    return result


def _rank(values: list[float], value: float) -> float:
    if len(values) <= 1:
        return 0.5
    ordered = sorted(values)
    position = min(range(len(ordered)), key=lambda index: abs(ordered[index] - value))
    return position / (len(ordered) - 1)


def build_plan(template: dict[str, Any], catalog: dict[str, Any], seed: int = 7) -> dict[str, Any]:
    shots = template.get("shots", [])
    assets = catalog.get("assets", [])
    if not shots or not assets:
        raise RuntimeError("模板或素材目录为空。")
    rng = random.Random(seed)
    shot_motion = [float(shot.get("motion", 0)) for shot in shots]
    asset_motion = [float(asset.get("motion", 0)) for asset in assets]
    previous_id = ""
    plan_shots: list[dict[str, Any]] = []

    for shot in shots:
        target_rank = _rank(shot_motion, float(shot.get("motion", 0)))
        scored: list[tuple[float, dict[str, Any]]] = []
        for asset in assets:
            motion_rank = _rank(asset_motion, float(asset.get("motion", 0)))
            repeat_penalty = 0.35 if asset["id"] == previous_id and len(assets) > 1 else 0.0
            priority_bonus = 0.04 * max(0, int(asset.get("priority", 1)) - 1)
            score = abs(target_rank - motion_rank) + repeat_penalty - priority_bonus
            scored.append((score, asset))
        scored.sort(key=lambda item: item[0])
        best_score = scored[0][0]
        shortlist = [asset for score, asset in scored if score <= best_score + 0.06]
        asset = rng.choice(shortlist)
        duration = float(shot["duration"])
        available = max(0.0, float(asset["duration"]) - duration - 0.1)
        source_start = rng.uniform(0.0, available) if available > 0 else 0.0
        previous_id = asset["id"]
        plan_shots.append(
            {
                "index": shot["index"],
                "source_id": asset["id"],
                "source": asset["path"],
                "source_start": round(source_start, 3),
                "duration": duration,
                "speed": 1.0,
                "transition_after": shot.get("transition_after", "hard_cut"),
                "transition_duration": float(shot.get("transition_duration", 0.0)),
                "layering_after": shot.get("layering_after", "standard_cut"),
                "foreground_hold_score": float(shot.get("foreground_hold_score", 0.0)),
                "match_reason": "按参考镜头的相对运动强度自动匹配；可结合内容语义人工替换。",
            }
        )
    ref = template["reference"]
    return {
        "schema": "supercut-edit-plan-v1",
        "output": {
            "width": int(ref["width"]),
            "height": int(ref["height"]),
            "fps": min(60.0, max(24.0, float(ref["fps"]))),
        },
        "shots": plan_shots,
    }


def apply_manual_overrides(template: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    if not overrides:
        return template
    by_index = {int(shot["index"]): shot for shot in template.get("shots", [])}
    for item in overrides.get("confirmed", []):
        shot = by_index.get(int(item.get("after_shot", 0)))
        if not shot:
            continue
        shot["layering_after"] = item.get("classification", "foreground_hold_confirmed")
        shot["layering_confidence"] = float(item.get("confidence", 1.0))
        shot["foreground_subject"] = item.get("foreground", "枪体与手部")
        shot["background_treatment"] = item.get("background_treatment", "背景独立运动")
    return template


def _phase_for(start: float) -> tuple[str, str]:
    if start < 48.8:
        return "铺垫", "完整交战过程、地图建立或清晰击杀结果"
    if start < 63.867:
        return "提速", "甩枪、技能动作、角色特效或短促击杀"
    if start < 67.933:
        return "爆发", "高速横移、运动模糊、技能爆发或方向一致的连续动作"
    return "收束", "高亮击杀、武器/技能动作与明确结束落点"


def build_blueprint(template: dict[str, Any]) -> dict[str, Any]:
    shots = template.get("shots", [])
    if not shots:
        raise RuntimeError("参考片模板中没有镜头。")
    motions = np.array([float(shot.get("motion", 0.0)) for shot in shots], dtype=np.float32)
    low, high, extreme = [float(value) for value in np.percentile(motions, [35, 70, 90])]
    slots: list[dict[str, Any]] = []
    for shot in shots:
        motion = float(shot.get("motion", 0.0))
        if motion >= extreme:
            motion_level = "extreme"
        elif motion >= high:
            motion_level = "high"
        elif motion >= low:
            motion_level = "medium"
        else:
            motion_level = "low"
        phase, content = _phase_for(float(shot["start"]))
        layering = shot.get("layering_after", "standard_cut")
        handles = 0.6 if layering == "foreground_hold_confirmed" else 0.3
        slot = {
            "index": int(shot["index"]),
            "reference_start": float(shot["start"]),
            "reference_end": float(shot["end"]),
            "duration": float(shot["duration"]),
            "phase": phase,
            "content_requirement": content,
            "motion_level": motion_level,
            "transition_after": shot.get("transition_after", "hard_cut"),
            "transition_duration": float(shot.get("transition_duration", 0.0)),
            "layering_after": layering,
            "required_source_duration": round(float(shot["duration"]) + handles, 3),
            "source": None,
            "source_start": None,
            "selection_status": "unassigned",
        }
        if layering == "foreground_hold_confirmed":
            slot["foreground_requirement"] = {
                "subject": shot.get("foreground_subject", "枪体与手部"),
                "treatment": "抠出或冻结为前景层，保持屏幕坐标；背景单独替换、位移和方向模糊。",
                "background_treatment": shot.get("background_treatment", "背景独立运动"),
                "mask_handles": "切点前后至少各保留 8–12 帧完整枪体轮廓",
            }
        slots.append(slot)
    ref = template["reference"]
    return {
        "schema": "supercut-unassigned-blueprint-v1",
        "status": "awaiting_source_media",
        "output": {
            "width": int(ref["width"]),
            "height": int(ref["height"]),
            "fps": min(60.0, max(24.0, float(ref["fps"]))),
            "target_duration": float(ref["duration"]),
        },
        "summary": {
            "slot_count": len(slots),
            "assigned": 0,
            "unassigned": len(slots),
            "note": "真实素材进入 input/source 后运行 catalog 和 plan 完成自动分配。",
        },
        "shots": slots,
    }


def find_ffmpeg(explicit: str | None = None) -> str:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    configured = os.environ.get("SUPERCUT_FFMPEG")
    if configured:
        candidates.append(Path(configured))
    project_config, config_path = load_project_config()
    project_ffmpeg = project_config.get("ffmpeg", {}).get("executable")
    if project_ffmpeg:
        configured_path = Path(project_ffmpeg)
        if not configured_path.is_absolute():
            configured_path = config_path.parent / configured_path
        candidates.append(configured_path)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    raise RuntimeError(
        "渲染需要 FFmpeg。请检查 supercut.config.json，或用 --ffmpeg / SUPERCUT_FFMPEG / PATH 指定。"
    )


def _escape_filter_path(value: str) -> str:
    return value.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_plan(plan: dict[str, Any], out: Path, music: Path | None = None, ffmpeg_path: str | None = None) -> None:
    shots = plan.get("shots", [])
    if not shots:
        raise RuntimeError("剪辑计划中没有镜头。")
    ffmpeg = find_ffmpeg(ffmpeg_path)
    output = plan["output"]
    width, height = int(output["width"]), int(output["height"])
    fps = float(output["fps"])
    shot_frame_counts = [max(1, round(float(shot["duration"]) * fps)) for shot in shots]
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "warning"]
    for shot in shots:
        command += ["-stream_loop", "-1", "-ss", str(shot["source_start"]), "-i", shot["source"]]

    filters: list[str] = []
    for index, shot in enumerate(shots):
        duration = max(0.08, float(shot["duration"]))
        speed = max(0.1, float(shot.get("speed", 1.0)))
        target_frames = shot_frame_counts[index]
        filters.append(
            f"[{index}:v]setpts=(PTS-STARTPTS)/{speed:.6f},fps={fps:.3f},"
            f"trim=end_frame={target_frames},tpad=stop_mode=clone:stop_duration={1 / fps:.6f},"
            f"setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"settb=AVTB,setpts=PTS-STARTPTS,fps={fps:.3f},setsar=1,format=yuv420p[v{index}]"
        )

    # Build transitions as independent clips, then concatenate bodies and
    # transitions. This avoids chaining xfade outputs, whose frame-rate
    # metadata is unreliable in some FFmpeg builds.
    boundary_durations: list[float] = []
    for index in range(len(shots) - 1):
        shot = shots[index]
        name = shot.get("transition_after", "hard_cut")
        if name == "hard_cut":
            boundary_durations.append(0.0)
            continue
        requested = max(0.04, float(shot.get("transition_duration", 0.0)))
        clamped = min(requested, float(shots[index]["duration"]) * 0.45, float(shots[index + 1]["duration"]) * 0.45)
        boundary_durations.append(max(1, round(clamped * fps)) / fps)

    bodies: list[str] = []
    heads: dict[int, str] = {}
    tails: dict[int, str] = {}
    for index, shot in enumerate(shots):
        incoming = boundary_durations[index - 1] if index > 0 else 0.0
        outgoing = boundary_durations[index] if index < len(boundary_durations) else 0.0
        incoming_frames = round(incoming * fps)
        outgoing_frames = round(outgoing * fps)
        total_frames = shot_frame_counts[index]
        raw_labels = [f"braw{index}"]
        if incoming > 0:
            raw_labels.append(f"hraw{index}")
        if outgoing > 0:
            raw_labels.append(f"traw{index}")
        if len(raw_labels) == 1:
            filters.append(f"[v{index}]null[{raw_labels[0]}]")
        else:
            outputs = "".join(f"[{label}]" for label in raw_labels)
            filters.append(f"[v{index}]split={len(raw_labels)}{outputs}")

        body = f"body{index}"
        body_end_frame = max(incoming_frames + 1, total_frames - outgoing_frames)
        filters.append(
            f"[braw{index}]trim=start_frame={incoming_frames}:end_frame={body_end_frame},"
            f"setpts=PTS-STARTPTS[{body}]"
        )
        bodies.append(body)
        if incoming > 0:
            head = f"head{index}"
            filters.append(
                f"[hraw{index}]trim=end_frame={incoming_frames},settb=AVTB,"
                f"setpts=PTS-STARTPTS,fps={fps:.3f}[{head}]"
            )
            heads[index] = head
        if outgoing > 0:
            tail = f"tail{index}"
            filters.append(
                f"[traw{index}]trim=start_frame={total_frames - outgoing_frames}:end_frame={total_frames},"
                f"settb=AVTB,setpts=PTS-STARTPTS,fps={fps:.3f}[{tail}]"
            )
            tails[index] = tail

    transitions: dict[int, str] = {}
    for index, transition_duration in enumerate(boundary_durations):
        if transition_duration <= 0:
            continue
        transition = f"trans{index}"
        name = shots[index].get("transition_after", "dissolve")
        ff_transition = TRANSITION_MAP.get(name, "dissolve")
        filters.append(
            f"[{tails[index]}][{heads[index + 1]}]xfade=transition={ff_transition}:"
            f"duration={transition_duration:.6f}:offset=0,settb=AVTB,"
            f"setpts=PTS-STARTPTS,fps={fps:.3f}[{transition}]"
        )
        transitions[index] = transition

    timeline: list[str] = []
    for index, body in enumerate(bodies):
        timeline.append(body)
        if index in transitions:
            timeline.append(transitions[index])
    current = "timeline"
    if len(timeline) == 1:
        filters.append(f"[{timeline[0]}]null[{current}]")
    else:
        inputs = "".join(f"[{label}]" for label in timeline)
        filters.append(
            f"{inputs}concat=n={len(timeline)}:v=1:a=0,settb=AVTB,"
            f"setpts=PTS-STARTPTS,fps={fps:.3f}[{current}]"
        )
    current_duration = sum(float(shot["duration"]) for shot in shots) - sum(boundary_durations)

    if music:
        command += ["-stream_loop", "-1", "-i", str(music.resolve())]
        music_index = len(shots)
        filters.append(f"[{music_index}:a]atrim=duration={current_duration:.6f},asetpts=PTS-STARTPTS,volume=0.85[aout]")

    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{current}]",
        "-r",
        f"{fps:.3f}",
        "-fps_mode",
        "cfr",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
    ]
    if music:
        command += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    else:
        command += ["-an"]
    out.parent.mkdir(parents=True, exist_ok=True)
    command += ["-movflags", "+faststart", str(out.resolve())]
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg 渲染失败：\n{result.stderr[-4000:]}")


def cmd_init(_: argparse.Namespace) -> None:
    for directory in ["input/reference", "input/source", "input/music", "output"]:
        Path(directory).mkdir(parents=True, exist_ok=True)
    print("已创建 input/reference、input/source、input/music、output。")


def cmd_analyze(args: argparse.Namespace) -> None:
    from media_acquisition import AcquisitionError, acquisition_requirement_report, is_bilibili_url, resolve_input, write_manifest

    work_dir = Path(args.out).parent
    manifest_path = work_dir / "acquisition_manifest.json"
    if is_bilibili_url(args.reference) and not args.confirm_media_rights:
        save_json(manifest_path, acquisition_requirement_report(args.reference, None))
        raise AcquisitionError(f"需要素材权利确认；需求报告已写入 {manifest_path}")
    reference, record = resolve_input(
        args.reference,
        work_dir / ".private-cache" / "reference",
        confirm_media_rights=args.confirm_media_rights,
        ffmpeg=args.ffmpeg,
    )
    write_manifest(manifest_path, [{**record, "role": "reference"}])
    template = analyze_reference(reference, Path(args.out), Path(args.sheet) if args.sheet else None)
    print(f"参考片分析完成：{len(template['shots'])} 个镜头 -> {args.out}")


def cmd_acquire(args: argparse.Namespace) -> None:
    from media_acquisition import AcquisitionError, acquisition_requirement_report, is_bilibili_url, resolve_input, write_manifest

    manifest_path = Path(args.manifest)
    values = [("reference", args.reference), ("source", args.source)]
    remote_values = [value for _, value in values if value and is_bilibili_url(value)]
    if remote_values and not args.confirm_media_rights:
        save_json(manifest_path, acquisition_requirement_report(args.reference, args.source))
        raise AcquisitionError(f"需要素材权利确认；需求报告已写入 {manifest_path}")
    records = []
    for role, value in values:
        if not value:
            continue
        _, record = resolve_input(
            value,
            Path(args.cache_dir) / role,
            confirm_media_rights=args.confirm_media_rights,
            ffmpeg=args.ffmpeg,
        )
        records.append({**record, "role": role})
    write_manifest(manifest_path, records)
    print(f"素材获取清单完成：{len(records)} 项 -> {manifest_path}")


def cmd_catalog(args: argparse.Namespace) -> None:
    catalog = catalog_sources(Path(args.source_dir), Path(args.out), Path(args.sheet) if args.sheet else None)
    print(f"素材索引完成：{len(catalog['assets'])} 条视频 -> {args.out}")


def cmd_scan(args: argparse.Namespace) -> None:
    result = scan_raw_matches(
        Path(args.source_dir),
        Path(args.out),
        Path(args.sheet) if args.sheet else None,
        args.max_candidates,
    )
    print(
        f"整局实录扫描完成：{result['summary']['video_count']} 条视频，"
        f"{result['summary']['candidate_count']} 个候选 -> {args.out}"
    )


def cmd_plan(args: argparse.Namespace) -> None:
    template = load_json(Path(args.template))
    if args.overrides:
        template = apply_manual_overrides(template, load_json(Path(args.overrides)))
    plan = build_plan(template, load_json(Path(args.catalog)), args.seed)
    save_json(Path(args.out), plan)
    print(f"剪辑计划完成：{len(plan['shots'])} 个镜头 -> {args.out}")


def cmd_blueprint(args: argparse.Namespace) -> None:
    template = load_json(Path(args.template))
    if args.overrides:
        template = apply_manual_overrides(template, load_json(Path(args.overrides)))
    blueprint = build_blueprint(template)
    save_json(Path(args.out), blueprint)
    print(f"待配素材计划完成：{len(blueprint['shots'])} 个镜头槽位 -> {args.out}")


def cmd_render(args: argparse.Namespace) -> None:
    render_plan(load_json(Path(args.plan)), Path(args.out), Path(args.music) if args.music else None, args.ffmpeg)
    print(f"渲染完成：{args.out}")


def cmd_recommend(args: argparse.Namespace) -> None:
    events: list[str] = []
    for value in args.event or []:
        events.extend(part.strip() for part in value.split(",") if part.strip())
    results = recommend_effects(load_json(Path(args.catalog)), args.content_type, events, args.top)
    if args.json:
        print(json.dumps({"content_type": _normalise_content_type(args.content_type), "events": events, "results": results}, ensure_ascii=False, indent=2))
        return
    if not results:
        print("没有找到符合条件的特效。")
        return
    for index, result in enumerate(results, start=1):
        implementation = "可直接渲染" if result["implemented"] else "知识库方案"
        matched = "、".join(result["matched_events"]) or "内容类型匹配"
        print(f"{index}. {result['name']} [{result['effect_id']}] {implementation}；匹配：{matched}")
        print(f"   {result['selection_prompt']}")


def cmd_brief(args: argparse.Namespace) -> None:
    from workflow import build_edit_brief

    request = load_json(Path(args.request))
    brief = build_edit_brief(request)
    save_json(Path(args.out), brief)
    print(f"需求标准化完成：{brief['input_mode']} -> {args.out}")


def cmd_compose(args: argparse.Namespace) -> None:
    from workflow import build_script_template, build_unassigned_base_plan, compose_v2, save_artifacts

    brief = load_json(Path(args.brief))
    source_catalog = load_json(Path(args.catalog))
    annotations = load_json(Path(args.annotations)) if args.annotations else None
    if args.template:
        template = load_json(Path(args.template))
    else:
        if brief.get("input_mode") == "reference":
            raise RuntimeError("参考片模式必须提供 --template；请先运行 analyze。")
        template = build_script_template(brief, source_catalog)
    if args.base_plan:
        base_plan = load_json(Path(args.base_plan))
    elif not source_catalog.get("assets"):
        base_plan = build_unassigned_base_plan(template)
    else:
        base_plan = build_plan(template, source_catalog, args.seed)
    effects_catalog = load_json(Path(args.effects_catalog))
    plan = compose_v2(brief, base_plan, template, effects_catalog, annotations)
    save_json(Path(args.out), plan)
    artifact_dir = Path(args.artifacts_dir) if args.artifacts_dir else Path(args.out).parent / "workflow"
    save_artifacts(plan, artifact_dir)
    quality = plan["artifacts"]["quality_gate"]
    print(
        f"v2时间线完成：{len(plan['shots'])} 个镜头，{len(plan['effects'])} 个特效，"
        f"质量分 {quality['score']}/100，状态 {plan['review_status']} -> {args.out}"
    )


def cmd_approve(args: argparse.Namespace) -> None:
    from workflow import approve_plan

    approved = approve_plan(load_json(Path(args.plan)))
    save_json(Path(args.out), approved)
    print(f"用户审阅状态已记录：approved -> {args.out}")


def cmd_chatcut_manifest(args: argparse.Namespace) -> None:
    from chatcut_bridge import build_chatcut_manifest

    manifest = build_chatcut_manifest(
        load_json(Path(args.plan)),
        project_name=args.project_name,
        proxy_root=args.proxy_root,
        effect_proxy_root=args.effect_proxy_root,
    )
    save_json(Path(args.out), manifest)
    print(
        f"ChatCut同步清单完成：{len(manifest['assets'])} 个代理素材，"
        f"{len(manifest['items'])} 个时间线项目 -> {args.out}"
    )


def cmd_chatcut_pull(args: argparse.Namespace) -> None:
    from chatcut_bridge import pull_compatible_edits

    plan = load_json(Path(args.plan))
    snapshot = load_json(Path(args.snapshot))
    mapping_value = load_json(Path(args.mapping))
    mapping = mapping_value.get("mapping", mapping_value)
    updated, report = pull_compatible_edits(plan, snapshot, mapping)
    save_json(Path(args.out), updated)
    save_json(Path(args.report), report)
    print(f"ChatCut回写：{report['status']} -> {args.out}；报告 {args.report}")


def cmd_chatcut_proxies(args: argparse.Namespace) -> None:
    from chatcut_proxy import materialize_chatcut_proxies, save_proxy_report

    plan = load_json(Path(args.plan))
    manifest = load_json(Path(args.manifest))
    report = materialize_chatcut_proxies(plan, manifest, find_ffmpeg(args.ffmpeg), force=args.force)
    save_proxy_report(report, Path(args.report))
    print(f"ChatCut代理准备完成：{len(report['items'])} 个文件，ready={report['all_ready']} -> {args.report}")


def cmd_render_v2(args: argparse.Namespace) -> None:
    from tools.render_layered_plan import render_reviewed_plan

    plan = load_json(Path(args.plan))
    if plan.get("schema") != "supercut-edit-plan-v2":
        raise RuntimeError("render-v2 只接受 supercut-edit-plan-v2")
    if plan.get("review_status") != "approved" and not args.allow_unapproved_preview:
        raise RuntimeError("v2计划尚未通过用户审阅；预览请显式使用 --allow-unapproved-preview")
    ffmpeg = find_ffmpeg(args.ffmpeg)
    render_reviewed_plan(
        plan,
        ffmpeg,
        Path(args.out),
        Path(args.mask_preview) if args.mask_preview else None,
        False,
    )


def cmd_all(args: argparse.Namespace) -> None:
    from media_acquisition import (
        AcquisitionError,
        acquisition_requirement_report,
        is_bilibili_url,
        resolve_input,
        write_manifest,
    )

    output_path = Path(args.out)
    work_dir = output_path.parent
    manifest_path = work_dir / "acquisition_manifest.json"
    source_value = args.source or args.source_dir
    if not source_value:
        raise RuntimeError("必须提供 --source 或兼容参数 --source-dir")
    if not args.confirm_media_rights and any(is_bilibili_url(value) for value in (args.reference, source_value) if value):
        save_json(manifest_path, acquisition_requirement_report(args.reference, source_value))
        raise AcquisitionError(f"需要素材权利确认；需求报告已写入 {manifest_path}")
    cache_dir = work_dir / ".private-cache" / "media"
    records: list[dict[str, Any]] = []
    try:
        reference_path, reference_record = resolve_input(
            args.reference,
            cache_dir / "reference",
            confirm_media_rights=args.confirm_media_rights,
            ffmpeg=args.ffmpeg,
        )
        reference_record["role"] = "reference"
        records.append(reference_record)
        if args.source:
            source_path, source_record = resolve_input(
                args.source,
                cache_dir / "source",
                confirm_media_rights=args.confirm_media_rights,
                ffmpeg=args.ffmpeg,
            )
            source_record["role"] = "source"
            records.append(source_record)
        else:
            source_path = Path(args.source_dir).expanduser().resolve()
    except AcquisitionError as exc:
        save_json(manifest_path, {
            "schema": "supercut-acquisition-manifest-v1",
            "status": "needs_input",
            "reason": str(exc),
            "download_attempted": bool(args.confirm_media_rights),
        })
        raise
    write_manifest(manifest_path, records)
    template_path = work_dir / "template.json"
    catalog_path = work_dir / "catalog.json"
    plan_path = work_dir / "edit_plan.json"
    template = analyze_reference(reference_path, template_path, work_dir / "reference_sheet.jpg")
    catalog = catalog_sources(source_path, catalog_path, work_dir / "source_sheet.jpg")
    plan = build_plan(template, catalog, args.seed)
    save_json(plan_path, plan)
    render_plan(plan, output_path, Path(args.music) if args.music else None, args.ffmpeg)
    print(f"全部完成：{output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="提取参考视频剪辑语法，并用新素材复刻节奏与转场。")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="创建素材目录")
    init_parser.set_defaults(func=cmd_init)

    analyze_parser = subparsers.add_parser("analyze", help="分析参考视频")
    analyze_parser.add_argument("--reference", required=True)
    analyze_parser.add_argument("--out", default="output/template.json")
    analyze_parser.add_argument("--sheet", default="output/reference_sheet.jpg")
    analyze_parser.add_argument("--confirm-media-rights", action="store_true")
    analyze_parser.add_argument("--ffmpeg")
    analyze_parser.set_defaults(func=cmd_analyze)

    acquire_parser = subparsers.add_parser("acquire", help="带授权门获取本地或公开 B 站素材")
    acquire_parser.add_argument("--reference")
    acquire_parser.add_argument("--source")
    acquire_parser.add_argument("--confirm-media-rights", action="store_true")
    acquire_parser.add_argument("--cache-dir", default="output/.private-cache/media")
    acquire_parser.add_argument("--manifest", default="output/acquisition_manifest.json")
    acquire_parser.add_argument("--ffmpeg")
    acquire_parser.set_defaults(func=cmd_acquire)

    catalog_parser = subparsers.add_parser("catalog", help="建立素材目录")
    catalog_parser.add_argument("--source-dir", required=True)
    catalog_parser.add_argument("--out", default="output/catalog.json")
    catalog_parser.add_argument("--sheet", default="output/source_sheet.jpg")
    catalog_parser.set_defaults(func=cmd_catalog)

    scan_parser = subparsers.add_parser("scan", help="扫描未剪辑整局实录并生成高光候选")
    scan_parser.add_argument("--source-dir", required=True)
    scan_parser.add_argument("--out", default="output/raw_match_scan.json")
    scan_parser.add_argument("--sheet", default="output/raw_match_candidates.jpg")
    scan_parser.add_argument("--max-candidates", type=int, default=48, help="每条实录最多保留的候选数")
    scan_parser.set_defaults(func=cmd_scan)

    plan_parser = subparsers.add_parser("plan", help="生成剪辑计划")
    plan_parser.add_argument("--template", required=True)
    plan_parser.add_argument("--catalog", required=True)
    plan_parser.add_argument("--out", default="output/edit_plan.json")
    plan_parser.add_argument("--seed", type=int, default=7)
    plan_parser.add_argument("--overrides", help="人工确认的转场覆盖 JSON")
    plan_parser.set_defaults(func=cmd_plan)

    blueprint_parser = subparsers.add_parser("blueprint", help="在素材到位前生成待分配镜头计划")
    blueprint_parser.add_argument("--template", required=True)
    blueprint_parser.add_argument("--overrides", help="人工确认的转场覆盖 JSON")
    blueprint_parser.add_argument("--out", default="output/edit_blueprint.json")
    blueprint_parser.set_defaults(func=cmd_blueprint)

    render_parser = subparsers.add_parser("render", help="渲染剪辑计划")
    render_parser.add_argument("--plan", required=True)
    render_parser.add_argument("--out", default="output/draft.mp4")
    render_parser.add_argument("--music")
    render_parser.add_argument("--ffmpeg", help="ffmpeg 可执行文件路径")
    render_parser.set_defaults(func=cmd_render)

    recommend_parser = subparsers.add_parser("recommend", help="按内容类型和动作标签推荐特效")
    recommend_parser.add_argument("--content-type", required=True, help="game/anime/short_drama/live_stream，也支持中文")
    recommend_parser.add_argument("--event", action="append", default=[], help="动作标签，可重复或用逗号分隔")
    recommend_parser.add_argument("--top", type=int, default=5)
    recommend_parser.add_argument("--catalog", default="knowledge/effect_catalog.json")
    recommend_parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    recommend_parser.set_defaults(func=cmd_recommend)

    brief_parser = subparsers.add_parser("brief", help="把用户需求标准化为双入口剪辑简报")
    brief_parser.add_argument("--request", required=True, help="用户需求 JSON")
    brief_parser.add_argument("--out", default="output/edit_brief.json")
    brief_parser.set_defaults(func=cmd_brief)

    compose_parser = subparsers.add_parser("compose", help="生成带质量门和特效规划的 v2 时间线")
    compose_parser.add_argument("--brief", required=True)
    compose_parser.add_argument("--catalog", required=True, help="素材目录 JSON")
    compose_parser.add_argument("--template", help="参考片模板；文案模式可省略")
    compose_parser.add_argument("--base-plan", help="可选的已审阅 v1 基础计划")
    compose_parser.add_argument("--annotations", help="Codex逐镜头语义标注 JSON")
    compose_parser.add_argument("--effects-catalog", default="knowledge/effect_catalog.json")
    compose_parser.add_argument("--out", default="output/edit_plan_v2.json")
    compose_parser.add_argument("--artifacts-dir", default="output/workflow")
    compose_parser.add_argument("--seed", type=int, default=7)
    compose_parser.set_defaults(func=cmd_compose)

    approve_parser = subparsers.add_parser("approve", help="记录用户已通过质量门和时间线审阅")
    approve_parser.add_argument("--plan", required=True)
    approve_parser.add_argument("--out", default="output/edit_plan_v2_approved.json")
    approve_parser.set_defaults(func=cmd_approve)

    manifest_parser = subparsers.add_parser("chatcut-manifest", help="生成无AI的ChatCut机械同步清单")
    manifest_parser.add_argument("--plan", required=True)
    manifest_parser.add_argument("--project-name")
    manifest_parser.add_argument("--proxy-root", default="output/chatcut/proxies")
    manifest_parser.add_argument("--effect-proxy-root", default="output/chatcut/effects")
    manifest_parser.add_argument("--out", default="output/chatcut/manifest.json")
    manifest_parser.set_defaults(func=cmd_chatcut_manifest)

    pull_parser = subparsers.add_parser("chatcut-pull", help="从ChatCut快照回写兼容的基础剪辑字段")
    pull_parser.add_argument("--plan", required=True)
    pull_parser.add_argument("--snapshot", required=True)
    pull_parser.add_argument("--mapping", required=True)
    pull_parser.add_argument("--out", default="output/edit_plan_v2_from_chatcut.json")
    pull_parser.add_argument("--report", default="output/chatcut/pull_report.json")
    pull_parser.set_defaults(func=cmd_chatcut_pull)

    proxy_parser = subparsers.add_parser("chatcut-proxies", help="生成ChatCut审阅所需的等时长素材代理和精确特效代理")
    proxy_parser.add_argument("--plan", required=True)
    proxy_parser.add_argument("--manifest", required=True)
    proxy_parser.add_argument("--ffmpeg")
    proxy_parser.add_argument("--report", default="output/chatcut/proxy_report.json")
    proxy_parser.add_argument("--force", action="store_true")
    proxy_parser.set_defaults(func=cmd_chatcut_proxies)

    render_v2_parser = subparsers.add_parser("render-v2", help="按统一38特效运行时渲染v2计划")
    render_v2_parser.add_argument("--plan", required=True)
    render_v2_parser.add_argument("--out", default="output/final_v2.mp4")
    render_v2_parser.add_argument("--ffmpeg")
    render_v2_parser.add_argument("--mask-preview")
    render_v2_parser.add_argument("--allow-unapproved-preview", action="store_true")
    render_v2_parser.set_defaults(func=cmd_render_v2)

    all_parser = subparsers.add_parser("all", help="分析、匹配并渲染")
    all_parser.add_argument("--reference", required=True)
    source_group = all_parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source", help="本地视频文件或受支持的 B 站 URL")
    source_group.add_argument("--source-dir", help="兼容旧版：本地素材目录")
    all_parser.add_argument("--out", default="output/draft.mp4")
    all_parser.add_argument("--music")
    all_parser.add_argument("--confirm-media-rights", action="store_true", help="确认有权获取并使用所提供的公开媒体")
    all_parser.add_argument("--ffmpeg", help="ffmpeg 可执行文件路径")
    all_parser.add_argument("--seed", type=int, default=7)
    all_parser.set_defaults(func=cmd_all)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
        return 0
    except Exception as exc:  # Friendly CLI boundary.
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
