"""Render the project-standard memory-flash montage template."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path

import cv2
import numpy as np

from render_burn_glitch_preview import weapon_mask


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


def ease_out_cubic(value: float) -> float:
    return 1.0 - (1.0 - value) ** 3


def transform_snapshot(frame: np.ndarray, progress: float, index: int, effect: dict) -> np.ndarray:
    height, width = frame.shape[:2]
    eased = ease_out_cubic(progress)
    scale = effect["start_scale"] + (effect["end_scale"] - effect["start_scale"]) * eased
    sign = -1.0 if index % 2 == 0 else 1.0
    angle = sign * effect["start_rotation_deg"] * (1.0 - eased)
    shake = effect["shake_px"] * (1.0 - 0.58 * progress)
    offset_x = sign * shake * math.sin(progress * math.pi * 2.6)
    offset_y = shake * 0.28 * math.cos(progress * math.pi * 2.0 + index)
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle, scale)
    matrix[0, 2] += offset_x
    matrix[1, 2] += offset_y
    return cv2.warpAffine(frame, matrix, (width, height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)


def chromatic_aberration(frame: np.ndarray, amount: int) -> np.ndarray:
    if amount <= 0:
        return frame
    blue, green, red = cv2.split(frame)
    blue = np.roll(blue, amount, axis=1)
    red = np.roll(red, -amount, axis=1)
    return cv2.merge((blue, green, red))


def vignette(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    radius = np.sqrt(((xx - width / 2) / (width * 0.72)) ** 2 + ((yy - height / 2) / (height * 0.78)) ** 2)
    gain = np.clip(1.04 - 0.31 * radius**1.7, 0.70, 1.0)
    return np.clip(frame.astype(np.float32) * gain[..., None], 0, 255).astype(np.uint8)


def choose_foreground(frames: list[np.ndarray]) -> np.ndarray:
    height = frames[0].shape[0]
    candidates = frames[len(frames) // 3 : max(len(frames) // 3 + 1, len(frames) - 10)]
    return max(
        candidates,
        key=lambda frame: cv2.Laplacian(
            cv2.cvtColor(frame[int(height * 0.38) :], cv2.COLOR_BGR2GRAY), cv2.CV_64F
        ).var(),
    ).copy()


def foreground_composite(background: np.ndarray, foreground: np.ndarray, mask: np.ndarray, effect: dict) -> np.ndarray:
    mask_u8 = np.clip(mask * 255, 0, 255).astype(np.uint8)
    outline_px = int(effect["foreground_outline_px"])
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (outline_px * 2 + 1, outline_px * 2 + 1))
    dilated = cv2.dilate(mask_u8, kernel)
    outline = np.clip(dilated.astype(np.int16) - mask_u8.astype(np.int16), 0, 255).astype(np.uint8)
    glow = cv2.GaussianBlur(dilated, (0, 0), effect["foreground_glow_px"]).astype(np.float32) / 255.0
    alpha = np.clip(mask * effect["foreground_opacity"], 0.0, 1.0)
    result = background.astype(np.float32)
    result += glow[..., None] * np.array([18.0, 20.0, 24.0], dtype=np.float32)
    outline_alpha = outline.astype(np.float32) / 255.0
    outline_color = np.array(effect["foreground_outline_bgr"], dtype=np.float32)
    result = result * (1.0 - outline_alpha[..., None]) + outline_color * outline_alpha[..., None]
    result = foreground.astype(np.float32) * alpha[..., None] + result * (1.0 - alpha[..., None])
    return np.clip(result, 0, 255).astype(np.uint8)


def save_image(path: Path, image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise RuntimeError(f"Cannot encode {path}")
    path.write_bytes(encoded.tobytes())


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
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--template-config", default="templates/memory_flash/template.json")
    args = parser.parse_args()

    config = json.loads(Path(args.template_config).resolve().read_text(encoding="utf-8"))
    video = config["video"]
    timing = config["timing"]
    effect = config["effect"]
    fps = float(video["fps"])
    size = (int(video["width"]), int(video["height"]))
    source_start = float(timing["source_start"])
    duration = float(timing["duration"])
    active_start = float(timing["active_start"])
    active_end = float(timing["active_end"])
    source = Path(args.source).resolve()
    frames = load_frames(source, source_start, duration, fps, size)

    memory_frames = [frames[min(len(frames) - 1, round(pos * (len(frames) - 1)))] for pos in effect["memory_positions"]]
    foreground = choose_foreground(frames)
    foreground_alpha = weapon_mask(foreground)
    output_frames: list[np.ndarray] = []
    slot_duration = (active_end - active_start) / len(memory_frames)
    fade_frames = int(effect["fade_frames"])

    for frame_index, source_frame in enumerate(frames):
        time = frame_index / fps
        if active_start <= time <= active_end:
            active_time = time - active_start
            slot_float = active_time / slot_duration
            slot = min(len(memory_frames) - 1, int(slot_float))
            local = np.clip(slot_float - slot, 0.0, 1.0)
            background = transform_snapshot(memory_frames[slot], float(local), slot, effect)
            color_shift = max(1, round(effect["chromatic_shift_px"] * (0.72 + 0.28 * math.sin(local * math.pi))))
            background = chromatic_aberration(background, color_shift)
            background = vignette(background)
            background = np.clip(background.astype(np.float32) * effect["background_gain"], 0, 255).astype(np.uint8)
            composed = foreground_composite(background, foreground, foreground_alpha, effect)

            cut_pulse = math.exp(-((local - 0.03) / 0.055) ** 2) * effect["cut_flash_strength"]
            entry_pulse = math.exp(-((active_time - 0.03) / 0.055) ** 2) * effect["entry_flash_strength"]
            flash = min(0.72, cut_pulse + entry_pulse)
            if flash > 0.005:
                composed = cv2.addWeighted(composed, 1.0 - flash, np.full_like(composed, 255), flash, 0)

            edge_distance = min(time - active_start, active_end - time)
            mix = np.clip(edge_distance * fps / fade_frames, 0.0, 1.0)
            output = cv2.addWeighted(source_frame, 1.0 - mix, composed, mix, 0)
        else:
            output = source_frame
        output_frames.append(output)

    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    output = Path(args.out).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    save_image(work / "foreground_freeze.png", foreground)
    save_image(work / "foreground_mask.png", (foreground_alpha * 255).astype(np.uint8))
    visual = work / "memory_flash_visual.mp4"
    render_video(output_frames, fps, args.ffmpeg, visual)
    mux_source_audio(args.ffmpeg, visual, source, source_start, duration, output)
    print(output)


if __name__ == "__main__":
    main()
