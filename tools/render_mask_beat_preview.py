"""Render a five-beat montage built from animated geometric masks."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path

import cv2
import numpy as np


def smoothstep(value: float) -> float:
    value = float(np.clip(value, 0.0, 1.0))
    return value * value * (3.0 - 2.0 * value)


def read_frame(cap: cv2.VideoCapture) -> np.ndarray:
    ok, frame = cap.read()
    if not ok:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, frame = cap.read()
    if not ok or frame is None:
        raise RuntimeError("Cannot decode source stream")
    return frame


def cover_resize(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    source_h, source_w = frame.shape[:2]
    scale = max(width / source_w, height / source_h)
    resized = cv2.resize(frame, (round(source_w * scale), round(source_h * scale)), interpolation=cv2.INTER_LINEAR)
    x = max(0, (resized.shape[1] - width) // 2)
    y = max(0, (resized.shape[0] - height) // 2)
    return resized[y : y + height, x : x + width]


def load_still(path: Path, ratio: float, width: int, height: int) -> np.ndarray:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open picture source: {path}")
    frame_count = max(1, round(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, round(np.clip(ratio, 0.0, 1.0) * (frame_count - 1)))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise RuntimeError(f"Cannot extract picture from: {path}")
    return cover_resize(frame, width, height)


def saturation_grade(frame: np.ndarray, saturation: float, gain: float) -> np.ndarray:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] *= saturation
    hsv[..., 2] *= gain
    hsv = np.clip(hsv, 0, 255).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def beat_envelope(local: float) -> tuple[float, float]:
    reveal = smoothstep(local / 0.24)
    hide = 1.0 if local < 0.82 else 1.0 - smoothstep((local - 0.82) / 0.18)
    return reveal, float(np.clip(reveal * hide, 0.0, 1.0))


def horizontal_mask(xx: np.ndarray, yy: np.ndarray, reveal: float, focus_x: float, focus_y: float, effect: dict) -> np.ndarray:
    del xx, focus_x
    half_height = yy.shape[0] * effect["horizontal_half_height_ratio"] * reveal
    return (np.abs(yy - focus_y) <= half_height).astype(np.float32)


def cross_mask(xx: np.ndarray, yy: np.ndarray, reveal: float, focus_x: float, focus_y: float, effect: dict) -> np.ndarray:
    half_w = xx.shape[1] * effect["cross_half_width_ratio"] * reveal
    half_h = yy.shape[0] * effect["cross_half_height_ratio"] * reveal
    horizontal = (np.abs(yy - focus_y) <= half_h) & (np.abs(xx - focus_x) <= xx.shape[1] * 0.46 * reveal)
    vertical = (np.abs(xx - focus_x) <= half_w) & (np.abs(yy - focus_y) <= yy.shape[0] * 0.48 * reveal)
    return (horizontal | vertical).astype(np.float32)


def fan_mask(xx: np.ndarray, yy: np.ndarray, reveal: float, focus_x: float, focus_y: float, effect: dict) -> np.ndarray:
    dx = xx - focus_x
    dy = yy - focus_y
    angle = np.mod(np.arctan2(dy, dx) + math.pi * 2.0, math.pi * 2.0)
    radius = np.sqrt(dx * dx + dy * dy)
    segments = int(effect["fan_segments"])
    period = math.pi * 2.0 / segments
    wedge = np.mod(angle + period * 0.12, period) <= period * effect["fan_open_ratio"]
    max_radius = math.hypot(xx.shape[1], yy.shape[0]) * reveal
    return (wedge & (radius <= max_radius)).astype(np.float32)


def steps_mask(xx: np.ndarray, yy: np.ndarray, reveal: float, focus_x: float, focus_y: float, effect: dict) -> np.ndarray:
    del effect
    mask = np.zeros_like(xx, dtype=np.float32)
    rects = [
        (-0.35, -0.25, 0.34, 0.22),
        (-0.07, -0.06, 0.39, 0.24),
        (0.18, 0.17, 0.31, 0.21),
    ]
    height, width = xx.shape
    for index, (ox, oy, wr, hr) in enumerate(rects):
        local = smoothstep((reveal - index * 0.18) / 0.56)
        if local <= 0.0:
            continue
        rect_w = width * wr * local
        rect_h = height * hr * local
        cx = focus_x + width * ox
        cy = focus_y + height * oy
        mask = np.maximum(mask, ((np.abs(xx - cx) <= rect_w / 2) & (np.abs(yy - cy) <= rect_h / 2)).astype(np.float32))
    return mask


def circles_mask(xx: np.ndarray, yy: np.ndarray, reveal: float, focus_x: float, focus_y: float, effect: dict) -> np.ndarray:
    height, width = xx.shape
    radius_base = min(width, height) * effect["circle_radius_ratio"]
    centers = [(-0.24, -0.18), (0.02, -0.05), (0.24, 0.12), (-0.12, 0.22)]
    mask = np.zeros_like(xx, dtype=np.float32)
    for index, (ox, oy) in enumerate(centers):
        local = smoothstep((reveal - index * 0.13) / 0.61)
        radius = radius_base * local
        cx = focus_x + width * ox
        cy = focus_y + height * oy
        mask = np.maximum(mask, (((xx - cx) ** 2 + (yy - cy) ** 2) <= radius**2).astype(np.float32))
    return mask


MASK_BUILDERS = {
    "horizontal": horizontal_mask,
    "cross": cross_mask,
    "fan": fan_mask,
    "steps": steps_mask,
    "circles": circles_mask,
}


def component_masks(
    shape: str,
    xx: np.ndarray,
    yy: np.ndarray,
    reveal: float,
    focus_x: float,
    focus_y: float,
    effect: dict,
) -> list[np.ndarray]:
    """Return one alpha mask per picture instead of one shared mask."""
    height, width = xx.shape
    masks: list[np.ndarray] = []
    if shape == "horizontal":
        offsets = [-0.18, 0.0, 0.18]
        heights = [0.055, effect["horizontal_half_height_ratio"], 0.055]
        for index, (offset, half_ratio) in enumerate(zip(offsets, heights)):
            local = smoothstep((reveal - index * 0.10) / 0.80)
            half_height = height * half_ratio * local
            masks.append((np.abs(yy - (focus_y + height * offset)) <= half_height).astype(np.float32))
    elif shape == "cross":
        half_h = height * effect["cross_half_height_ratio"] * reveal
        half_w = width * effect["cross_half_width_ratio"] * reveal
        masks.append(((np.abs(yy - focus_y) <= half_h) & (np.abs(xx - focus_x) <= width * 0.46 * reveal)).astype(np.float32))
        masks.append(((np.abs(xx - focus_x) <= half_w) & (np.abs(yy - focus_y) <= height * 0.48 * reveal)).astype(np.float32))
    elif shape == "fan":
        dx = xx - focus_x
        dy = yy - focus_y
        angle = np.mod(np.arctan2(dy, dx) + math.pi * 2.0, math.pi * 2.0)
        radius = np.sqrt(dx * dx + dy * dy)
        segments = int(effect["fan_segments"])
        period = math.pi * 2.0 / segments
        max_radius = math.hypot(width, height) * reveal
        open_width = period * effect["fan_open_ratio"]
        for index in range(segments):
            start = index * period + period * 0.12
            relative = np.mod(angle - start + math.pi * 2.0, math.pi * 2.0)
            masks.append(((relative <= open_width) & (radius <= max_radius)).astype(np.float32))
    elif shape == "steps":
        rects = [
            (-0.35, -0.25, 0.34, 0.22),
            (-0.07, -0.06, 0.39, 0.24),
            (0.18, 0.17, 0.31, 0.21),
        ]
        for index, (ox, oy, wr, hr) in enumerate(rects):
            local = smoothstep((reveal - index * 0.18) / 0.56)
            rect_w = width * wr * local
            rect_h = height * hr * local
            cx = focus_x + width * ox
            cy = focus_y + height * oy
            masks.append(((np.abs(xx - cx) <= rect_w / 2) & (np.abs(yy - cy) <= rect_h / 2)).astype(np.float32))
    elif shape == "circles":
        radius_base = min(width, height) * effect["circle_radius_ratio"]
        centers = [(-0.24, -0.18), (0.02, -0.05), (0.24, 0.12), (-0.12, 0.22)]
        for index, (ox, oy) in enumerate(centers):
            local = smoothstep((reveal - index * 0.13) / 0.61)
            radius = radius_base * local
            cx = focus_x + width * ox
            cy = focus_y + height * oy
            masks.append((((xx - cx) ** 2 + (yy - cy) ** 2) <= radius**2).astype(np.float32))
    else:
        raise KeyError(f"Unknown mask shape: {shape}")
    return masks


def soften_mask(mask: np.ndarray, feather: int) -> np.ndarray:
    if feather <= 0:
        return mask
    return cv2.GaussianBlur(mask, (0, 0), feather / 2.2)


def composite_mask(base: np.ndarray, overlay: np.ndarray, mask: np.ndarray, effect: dict) -> np.ndarray:
    alpha = np.clip(mask, 0.0, 1.0)
    result = overlay.astype(np.float32) * alpha[..., None] + base.astype(np.float32) * (1.0 - alpha[..., None])
    mask_u8 = np.clip(mask * 255, 0, 255).astype(np.uint8)
    width = int(effect["rim_width_px"])
    if width > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (width * 2 + 1, width * 2 + 1))
        outer = cv2.dilate(mask_u8, kernel)
        inner = cv2.erode(mask_u8, kernel)
        rim = np.clip(outer.astype(np.int16) - inner.astype(np.int16), 0, 255).astype(np.float32) / 255.0
        rim_color = np.array(effect["rim_bgr"], dtype=np.float32)
        result = result * (1.0 - rim[..., None] * 0.62) + rim_color * rim[..., None] * 0.62
    return np.clip(result, 0, 255).astype(np.uint8)


def render_video(frames: list[np.ndarray], fps: float, ffmpeg: str, output: Path) -> None:
    height, width = frames[0].shape[:2]
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}", "-r", f"{fps:.3f}", "-i", "-", "-an", "-c:v", "libx264",
        "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    for frame in frames:
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    if process.wait() != 0:
        raise RuntimeError(stderr[-4000:])


def mux_audio(ffmpeg: str, visual: Path, source: Path, duration: float, output: Path) -> None:
    filters = f"[1:a]atrim=start=0:duration={duration},asetpts=PTS-STARTPTS,aresample=48000[aout]"
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-i", str(visual), "-i", str(source),
        "-filter_complex", filters, "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac",
        "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--template-config", default="templates/mask_beat/template.json")
    args = parser.parse_args()

    config = json.loads(Path(args.template_config).resolve().read_text(encoding="utf-8"))
    video, timing, effect = config["video"], config["timing"], config["effect"]
    width, height, fps = int(video["width"]), int(video["height"]), float(video["fps"])
    duration = float(timing["duration"])
    beat_starts = [float(value) for value in timing["beat_starts"]]
    beat_duration = float(timing["beat_duration"])
    source_dir = Path(args.source_dir).resolve()
    base_path = source_dir / config["base_source"]
    picture_paths = [source_dir / name for name in config["picture_sources"]]
    all_paths = [base_path, *picture_paths]
    missing = [path for path in all_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing sources: {missing}")
    base_cap = cv2.VideoCapture(str(base_path))
    if not base_cap.isOpened():
        raise RuntimeError("Cannot open base source")
    pictures = [
        load_still(path, ratio, width, height)
        for path in picture_paths
        for ratio in config["picture_sample_ratios"]
    ]
    pictures = [saturation_grade(image, effect["overlay_saturation"], effect["overlay_gain"]) for image in pictures]
    component_counts = {"horizontal": 3, "cross": 2, "fan": int(effect["fan_segments"]), "steps": 3, "circles": 4}
    picture_offsets: dict[str, int] = {}
    cursor = 0
    for shape in effect["mask_sequence"]:
        picture_offsets[shape] = cursor
        cursor += component_counts[shape]

    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    focus_x = width * effect["focus_x_ratio"]
    focus_y = height * effect["focus_y_ratio"]
    output_frames: list[np.ndarray] = []
    total_frames = round(duration * fps)

    for frame_index in range(total_frames):
        time = frame_index / fps
        base_raw = cover_resize(read_frame(base_cap), width, height)
        output = base_raw
        active_index = next((i for i, start in enumerate(beat_starts) if start <= time < start + beat_duration), None)
        if active_index is not None:
            local = (time - beat_starts[active_index]) / beat_duration
            reveal, envelope = beat_envelope(local)
            shape = effect["mask_sequence"][active_index]
            base = saturation_grade(base_raw, effect["base_saturation"], effect["base_gain"])
            output = base
            masks = component_masks(shape, xx, yy, reveal, focus_x, focus_y, effect)
            picture_start = picture_offsets[shape]
            for component_index, component_mask in enumerate(masks):
                mask = soften_mask(component_mask * envelope, int(effect["mask_feather_px"]))
                picture = pictures[(picture_start + component_index) % len(pictures)]
                output = composite_mask(output, picture, mask, effect)
            flash = effect["cut_flash_strength"] * math.exp(-((local - 0.035) / 0.050) ** 2)
            if flash > 0.003:
                output = cv2.addWeighted(output, 1.0 - flash, np.full_like(output, 255), flash, 0)
        output_frames.append(output)

    base_cap.release()
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    output_path = Path(args.out).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    visual = work / "mask_beat_visual.mp4"
    render_video(output_frames, fps, args.ffmpeg, visual)
    mux_audio(args.ffmpeg, visual, base_path, duration, output_path)
    print(output_path)


if __name__ == "__main__":
    main()
