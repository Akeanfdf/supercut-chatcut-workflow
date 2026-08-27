"""Render the CapCut-style diagonal mirror-mask slide effect."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np


def load_frames(path: Path, start: float, duration: float, fps: float, size: tuple[int, int]) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {path}")
    source_fps = float(cap.get(cv2.CAP_PROP_FPS) or fps)
    start_frame = round(start * source_fps)
    count = round(duration * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    frames: list[np.ndarray] = []
    source_index = start_frame - 1
    current: np.ndarray | None = None
    for output_index in range(count):
        desired = start_frame + round(output_index * source_fps / fps)
        while source_index < desired:
            ok, frame = cap.read()
            if not ok:
                break
            current = frame
            source_index += 1
        if current is None:
            break
        frames.append(cv2.resize(current, size, interpolation=cv2.INTER_AREA))
    cap.release()
    if not frames:
        raise RuntimeError(f"No frames decoded from {path}")
    while len(frames) < count:
        frames.append(frames[-1].copy())
    return frames


def gray_grade(frame: np.ndarray, alpha: float, beta: float) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.convertScaleAbs(gray, alpha=alpha, beta=beta)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def ease_out_quint(x: float) -> float:
    return 1.0 - (1.0 - x) ** 5


def ease_in_quint(x: float) -> float:
    return x**5


def slide_position(progress: float, width: int, height: int, motion: dict[str, float]) -> float:
    """Three-keyframe motion: fast entry, slow middle, fast exit."""
    first_key = motion["first_key_progress"]
    left = motion["start_width"] * width
    middle_left = motion["middle_width"] * width + motion["middle_height"] * height
    right = motion["end_width"] * width + motion["end_height"] * height
    if progress <= first_key:
        local = progress / first_key
        return left + (middle_left - left) * ease_out_quint(local)
    local = (progress - first_key) / (1.0 - first_key)
    return middle_left + (right - middle_left) * ease_in_quint(local)


def mirror_slide_frame(frame: np.ndarray, progress: float, effect: dict) -> np.ndarray:
    height, width = frame.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    # A constant x + slope*y forms a strip rising from lower-left to upper-right.
    axis = xx + effect["axis_slope"] * yy
    center = slide_position(progress, width, height, effect["motion"])
    half_width = width * effect["strip_half_width_ratio"]
    distance = np.abs(axis - center)

    # Main colored layer: crisp mirror-mask edge with slight anti-aliasing.
    edge_softness = effect["edge_softness"]
    color_alpha = np.clip((half_width + edge_softness / 2.0 - distance) / edge_softness, 0.0, 1.0)

    # Duplicate dark layer with roughly 25 px feather and a small right/down
    # offset, matching the tutorial's middle-layer shadow construction.
    shadow_distance = np.abs(axis - (center + effect["shadow_offset_px"]))
    shadow_feather = effect["shadow_feather_px"]
    shadow_alpha = np.clip((half_width + shadow_feather - shadow_distance) / shadow_feather, 0.0, 1.0)
    shadow_only = np.clip(shadow_alpha - color_alpha * effect["shadow_overlap"], 0.0, 1.0)

    base = gray_grade(frame, effect["gray_alpha"], effect["gray_beta"]).astype(np.float32)
    base *= (1.0 - effect["shadow_strength"] * shadow_only[..., None])
    result = frame.astype(np.float32) * color_alpha[..., None] + base * (1.0 - color_alpha[..., None])

    # A restrained specular rim gives the passing mask a glass/mirror edge.
    rim = np.clip(1.0 - np.abs(distance - half_width) / effect["rim_width_px"], 0.0, 1.0)
    result += rim[..., None] * np.array(effect["rim_bgr"], dtype=np.float32)
    return np.clip(result, 0, 255).astype(np.uint8)


def render_video(frames: list[np.ndarray], fps: float, ffmpeg: str, output: Path) -> None:
    height, width = frames[0].shape[:2]
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps:.3f}", "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    for frame in frames:
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    if process.wait() != 0:
        raise RuntimeError(stderr[-4000:])


def mux_source_audio(ffmpeg: str, visual: Path, source: Path, source_start: float, duration: float, output: Path) -> None:
    filters = f"[1:a]atrim=start={source_start}:duration={duration},asetpts=PTS-STARTPTS,aresample=48000[aout]"
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(visual), "-i", str(source),
        "-filter_complex", filters, "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--template-config", default="templates/mirror_slide/template.json")
    args = parser.parse_args()

    config_path = Path(args.template_config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    fps = float(config["video"]["fps"])
    size = (int(config["video"]["width"]), int(config["video"]["height"]))
    source_start = float(config["timing"]["source_start"])
    duration = float(config["timing"]["duration"])
    active_start = float(config["timing"]["active_start"])
    active_end = float(config["timing"]["active_end"])
    effect = config["effect"]
    frames = load_frames(Path(args.source).resolve(), source_start, duration, fps, size)
    output_frames: list[np.ndarray] = []
    fade_frames = int(effect["fade_frames"])
    for index, frame in enumerate(frames):
        time = index / fps
        if active_start <= time <= active_end:
            progress = (time - active_start) / (active_end - active_start)
            effected = mirror_slide_frame(frame, progress, effect)
            edge_distance = min(time - active_start, active_end - time)
            mix = np.clip(edge_distance * fps / fade_frames, 0.0, 1.0)
            output = cv2.addWeighted(frame, 1.0 - mix, effected, mix, 0)
        else:
            output = frame
        output_frames.append(output)

    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    output = Path(args.out).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    visual = work / "mirror_slide_visual.mp4"
    render_video(output_frames, fps, args.ffmpeg, visual)
    mux_source_audio(args.ffmpeg, visual, Path(args.source).resolve(), source_start, duration, output)
    print(output)


if __name__ == "__main__":
    main()
