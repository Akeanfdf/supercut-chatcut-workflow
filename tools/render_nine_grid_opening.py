"""Render the project-standard nine-grid opening effect."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np


def ease_out_back(value: float) -> float:
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * (value - 1.0) ** 3 + c1 * (value - 1.0) ** 2


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


def grid_rects(width: int, height: int, gutter: int) -> list[tuple[int, int, int, int]]:
    cell_w = (width - gutter * 4) // 3
    cell_h = (height - gutter * 4) // 3
    rects = []
    for row in range(3):
        for col in range(3):
            x = gutter + col * (cell_w + gutter)
            y = gutter + row * (cell_h + gutter)
            w = cell_w if col < 2 else width - gutter - x
            h = cell_h if row < 2 else height - gutter - y
            rects.append((x, y, w, h))
    return rects


def scaled_rect(rect: tuple[int, int, int, int], scale: float, canvas_center: tuple[float, float], pull: float) -> tuple[int, int, int, int]:
    x, y, width, height = rect
    cx = x + width / 2.0
    cy = y + height / 2.0
    cx = cx * (1.0 - pull) + canvas_center[0] * pull
    cy = cy * (1.0 - pull) + canvas_center[1] * pull
    new_w = max(2, round(width * scale))
    new_h = max(2, round(height * scale))
    return round(cx - new_w / 2), round(cy - new_h / 2), new_w, new_h


def lerp_rect(start: tuple[int, int, int, int], end: tuple[int, int, int, int], value: float) -> tuple[int, int, int, int]:
    return tuple(round(a + (b - a) * value) for a, b in zip(start, end))


def paste_tile(canvas: np.ndarray, frame: np.ndarray, rect: tuple[int, int, int, int], alpha: float, border: int, border_color: tuple[int, int, int]) -> None:
    canvas_h, canvas_w = canvas.shape[:2]
    x, y, width, height = rect
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(canvas_w, x + width), min(canvas_h, y + height)
    if x2 <= x1 or y2 <= y1 or alpha <= 0.0:
        return
    tile = cover_resize(frame, width, height)
    sx1, sy1 = x1 - x, y1 - y
    sx2, sy2 = sx1 + (x2 - x1), sy1 + (y2 - y1)
    crop = tile[sy1:sy2, sx1:sx2]
    canvas[y1:y2, x1:x2] = cv2.addWeighted(canvas[y1:y2, x1:x2], 1.0 - alpha, crop, alpha, 0)
    if border > 0 and alpha > 0.35:
        cv2.rectangle(canvas, (x1, y1), (x2 - 1, y2 - 1), border_color, border, cv2.LINE_AA)


def render_video(frames: list[np.ndarray], fps: float, ffmpeg: str, output: Path) -> None:
    height, width = frames[0].shape[:2]
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps:.3f}", "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    for frame in frames:
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    if process.wait() != 0:
        raise RuntimeError(stderr[-4000:])


def mux_audio(ffmpeg: str, visual: Path, focus_source: Path, duration: float, output: Path) -> None:
    filters = f"[1:a]atrim=start=0:duration={duration},asetpts=PTS-STARTPTS,aresample=48000[aout]"
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-i", str(visual), "-i", str(focus_source),
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
    parser.add_argument("--template-config", default="templates/nine_grid_opening/template.json")
    args = parser.parse_args()

    config = json.loads(Path(args.template_config).resolve().read_text(encoding="utf-8"))
    video = config["video"]
    timing = config["timing"]
    effect = config["effect"]
    width, height, fps = int(video["width"]), int(video["height"]), float(video["fps"])
    duration = float(timing["duration"])
    assembly_start = float(timing["assembly_start"])
    assembly_end = float(timing["assembly_end"])
    grid_hold_end = float(timing["grid_hold_end"])
    focus_end = float(timing["focus_end"])
    source_dir = Path(args.source_dir).resolve()
    paths = [source_dir / name for name in config["source_order"]]
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing sources: {missing}")
    captures = [cv2.VideoCapture(str(path)) for path in paths]
    if not all(cap.isOpened() for cap in captures):
        raise RuntimeError("Cannot open one or more grid sources")

    rects = grid_rects(width, height, int(effect["gutter_px"]))
    canvas_center = (width / 2.0, height / 2.0)
    appear_order = effect["appear_order"]
    rank = {tile: index for index, tile in enumerate(appear_order)}
    focus_tile = int(effect["focus_tile"])
    output_frames: list[np.ndarray] = []
    total_frames = round(duration * fps)

    for frame_index in range(total_frames):
        time = frame_index / fps
        current = [read_frame(cap) for cap in captures]
        canvas = np.full((height, width, 3), np.array(effect["background_bgr"], dtype=np.uint8), dtype=np.uint8)

        if time < grid_hold_end:
            step_span = (assembly_end - assembly_start) / max(1.0, len(rects) - effect["appear_overlap"] * (len(rects) - 1))
            tile_duration = step_span * (1.0 + effect["appear_overlap"])
            for tile_index in range(9):
                start = assembly_start + rank[tile_index] * step_span * (1.0 - effect["appear_overlap"])
                progress = np.clip((time - start) / max(tile_duration, 1e-6), 0.0, 1.0)
                if progress <= 0.0:
                    continue
                scale = effect["initial_tile_scale"] + (1.0 - effect["initial_tile_scale"]) * ease_out_back(float(progress))
                pull = (1.0 - progress) * 0.58
                rect = scaled_rect(rects[tile_index], scale, canvas_center, pull)
                paste_tile(canvas, current[tile_index], rect, min(1.0, progress * 1.6), int(effect["tile_border_px"]), tuple(effect["tile_border_bgr"]))
        elif time < focus_end:
            progress = smoothstep((time - grid_hold_end) / (focus_end - grid_hold_end))
            other_alpha = 1.0 - smoothstep(min(1.0, progress * 1.35))
            for tile_index in range(9):
                if tile_index == focus_tile:
                    continue
                drift_scale = 1.0 - 0.12 * progress
                rect = scaled_rect(rects[tile_index], drift_scale, canvas_center, -0.08 * progress)
                paste_tile(canvas, current[tile_index], rect, other_alpha, int(effect["tile_border_px"]), tuple(effect["tile_border_bgr"]))
            focus_rect = lerp_rect(rects[focus_tile], (0, 0, width, height), progress)
            paste_tile(canvas, current[focus_tile], focus_rect, 1.0, round((1.0 - progress) * effect["tile_border_px"]), tuple(effect["tile_border_bgr"]))
            blur = int(round(effect["focus_blur_strength"] * np.sin(progress * np.pi)))
            if blur >= 2:
                kernel = blur * 2 + 1
                canvas = cv2.GaussianBlur(canvas, (kernel, 1), 0)
        else:
            canvas = cover_resize(current[focus_tile], width, height)

        flash = effect["focus_flash_strength"] * np.exp(-((time - focus_end) / 0.055) ** 2)
        if flash > 0.003:
            canvas = cv2.addWeighted(canvas, 1.0 - flash, np.full_like(canvas, 255), flash, 0)
        output_frames.append(canvas)

    for cap in captures:
        cap.release()
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    output = Path(args.out).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    visual = work / "nine_grid_visual.mp4"
    render_video(output_frames, fps, args.ffmpeg, visual)
    mux_audio(args.ffmpeg, visual, paths[focus_tile], duration, output)
    print(output)


if __name__ == "__main__":
    main()
