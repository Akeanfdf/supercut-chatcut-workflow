"""Render representative v2/v3 effects with OpenCV and mux source audio with FFmpeg."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path

import cv2
import numpy as np


def load_frames(path: Path, start: float, count: int, fps: float, size: tuple[int, int]) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {path}")
    native_fps = float(cap.get(cv2.CAP_PROP_FPS) or fps)
    first = round(start * native_fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, first)
    frames: list[np.ndarray] = []
    source_index = first - 1
    current: np.ndarray | None = None
    for output_index in range(count):
        desired = first + round(output_index * native_fps / fps)
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


def smoothstep(value: float) -> float:
    value = float(np.clip(value, 0.0, 1.0))
    return value * value * (3.0 - 2.0 * value)


def scale_center(frame: np.ndarray, scale: float) -> np.ndarray:
    height, width = frame.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), 0.0, scale)
    return cv2.warpAffine(frame, matrix, (width, height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)


def radial_blur(frame: np.ndarray, strength: float, samples: int) -> np.ndarray:
    if strength <= 0.002 or samples <= 1:
        return frame
    accum = np.zeros_like(frame, dtype=np.float32)
    weights = 0.0
    for index in range(samples):
        offset = (index / max(1, samples - 1) - 0.5) * strength
        weight = 1.0 - abs(index / max(1, samples - 1) - 0.5) * 0.8
        accum += scale_center(frame, 1.0 + offset).astype(np.float32) * weight
        weights += weight
    return np.clip(accum / weights, 0, 255).astype(np.uint8)


def elastic_zoom_frame(outgoing: np.ndarray, incoming: np.ndarray, progress: float, effect: dict) -> np.ndarray:
    eased = smoothstep(progress)
    out_scale = 1.0 + (effect["outgoing_scale_end"] - 1.0) * eased
    overshoot = effect["overshoot"] * math.sin(progress * math.pi)
    in_scale = effect["incoming_scale_start"] + (1.0 - effect["incoming_scale_start"]) * eased + overshoot
    out_frame = scale_center(outgoing, out_scale)
    in_frame = scale_center(incoming, max(0.2, in_scale))
    blur_strength = math.sin(progress * math.pi) * 0.09
    out_frame = radial_blur(out_frame, blur_strength, int(effect["radial_blur_samples"]))
    in_frame = radial_blur(in_frame, blur_strength * 0.8, int(effect["radial_blur_samples"]))
    result = cv2.addWeighted(out_frame, 1.0 - eased, in_frame, eased, 0)
    flash = effect["flash_strength"] * math.exp(-((progress - 0.5) / 0.16) ** 2)
    return np.clip(result.astype(np.float32) + 255.0 * flash, 0, 255).astype(np.uint8)


def build_shards(width: int, height: int, effect: dict) -> list[tuple[np.ndarray, tuple[float, float]]]:
    rng = np.random.default_rng(int(effect["seed"]))
    columns = int(effect["columns"])
    rows = int(effect["rows"])
    jitter = float(effect["jitter_px"])
    nodes = np.zeros((rows + 1, columns + 1, 2), dtype=np.float32)
    for row in range(rows + 1):
        for column in range(columns + 1):
            x = width * column / columns
            y = height * row / rows
            if 0 < row < rows and 0 < column < columns:
                x += rng.uniform(-jitter, jitter)
                y += rng.uniform(-jitter, jitter)
            nodes[row, column] = (x, y)
    shards: list[tuple[np.ndarray, tuple[float, float]]] = []
    for row in range(rows):
        for column in range(columns):
            polygon = np.array([
                nodes[row, column], nodes[row, column + 1],
                nodes[row + 1, column + 1], nodes[row + 1, column],
            ], dtype=np.int32)
            mask = np.zeros((height, width), dtype=np.uint8)
            cv2.fillConvexPoly(mask, polygon, 255)
            center = tuple(np.mean(polygon.astype(np.float32), axis=0))
            shards.append((mask, center))
    return shards


def crack_split_frame(outgoing: np.ndarray, incoming: np.ndarray, progress: float, effect: dict,
                      shards: list[tuple[np.ndarray, tuple[float, float]]]) -> np.ndarray:
    height, width = outgoing.shape[:2]
    eased = smoothstep(progress)
    result = incoming.astype(np.float32) * (0.45 + 0.55 * eased)
    center = np.array([width / 2.0, height / 2.0], dtype=np.float32)
    scatter = float(effect["scatter_px"]) * eased * eased
    for mask, shard_center in shards:
        direction = np.array(shard_center, dtype=np.float32) - center
        norm = max(float(np.linalg.norm(direction)), 1.0)
        direction /= norm
        dx = float(direction[0] * scatter)
        dy = float(direction[1] * scatter + 0.22 * scatter * eased)
        matrix = np.float32([[1, 0, dx], [0, 1, dy]])
        moved_frame = cv2.warpAffine(outgoing, matrix, (width, height), borderMode=cv2.BORDER_CONSTANT)
        moved_mask = cv2.warpAffine(mask, matrix, (width, height), borderMode=cv2.BORDER_CONSTANT).astype(np.float32) / 255.0
        alpha = moved_mask * (1.0 - 0.68 * eased)
        result = moved_frame.astype(np.float32) * alpha[..., None] + result * (1.0 - alpha[..., None])
    if progress < 0.42:
        crack = np.zeros((height, width), dtype=np.uint8)
        for mask, _ in shards:
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(crack, contours, -1, 255, int(effect["crack_width_px"]))
        strength = smoothstep(progress / 0.42)
        result *= (1.0 - (crack.astype(np.float32) / 255.0 * 0.78 * strength)[..., None])
    flash = float(effect["flash_strength"]) * math.exp(-((progress - 0.2) / 0.1) ** 2)
    return np.clip(result + 255.0 * flash, 0, 255).astype(np.uint8)


def signal_glitch_frame(outgoing: np.ndarray, incoming: np.ndarray, progress: float, effect: dict, frame_index: int) -> np.ndarray:
    rng = np.random.default_rng(int(effect["seed"]) + frame_index * 97)
    height, width = outgoing.shape[:2]
    alpha = smoothstep(progress)
    base = cv2.addWeighted(outgoing, 1.0 - alpha, incoming, alpha, 0)
    glitched = base.copy()
    band_count = int(effect["bands"])
    edges = np.linspace(0, height, band_count + 1, dtype=int)
    envelope = math.sin(progress * math.pi)
    for band in range(band_count):
        if rng.random() > 0.55:
            continue
        y0, y1 = edges[band], edges[band + 1]
        shift = round(rng.uniform(-1, 1) * float(effect["max_shift_px"]) * envelope)
        glitched[y0:y1] = np.roll(glitched[y0:y1], shift, axis=1)
    shift = round(float(effect["rgb_shift_px"]) * envelope)
    blue, green, red = cv2.split(glitched)
    blue = np.roll(blue, -shift, axis=1)
    red = np.roll(red, shift, axis=1)
    glitched = cv2.merge((blue, green, red)).astype(np.float32)
    noise = rng.normal(0.0, 255.0 * float(effect["noise_strength"]) * envelope, glitched.shape).astype(np.float32)
    glitched += noise
    scanline = np.zeros((height, 1, 1), dtype=np.float32)
    scanline[::2] = -255.0 * float(effect["scanline_strength"]) * envelope
    glitched += scanline
    return np.clip(glitched, 0, 255).astype(np.uint8)


def exposure_flash_frame(outgoing: np.ndarray, incoming: np.ndarray, progress: float, effect: dict, frame_index: int) -> np.ndarray:
    alpha = smoothstep(progress)
    mixed = cv2.addWeighted(outgoing, 1.0 - alpha, incoming, alpha, 0).astype(np.float32)
    peak = math.exp(-((progress - 0.5) / 0.19) ** 2)
    gain = 1.0 + (float(effect["peak_gain"]) - 1.0) * peak
    gray = cv2.cvtColor(mixed.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    threshold = float(effect["luma_threshold"]) - 0.35 * peak
    matte = np.clip((gray - threshold) / max(0.08, 1.0 - threshold), 0.0, 1.0)
    bloom = cv2.GaussianBlur(matte, (0, 0), float(effect["bloom_sigma"]))
    result = mixed * gain + 255.0 * bloom[..., None] * peak * 0.55
    transition_count = max(1, round(0.42 * 30))
    middle = transition_count // 2
    if abs(frame_index - middle) < int(effect["white_hold_frames"]):
        result = result * 0.25 + 255.0 * 0.75
    return np.clip(result, 0, 255).astype(np.uint8)


def page_fold_frame(outgoing: np.ndarray, incoming: np.ndarray, progress: float, effect: dict) -> np.ndarray:
    height, width = outgoing.shape[:2]
    eased = smoothstep(progress)
    boundary = int(width * eased)
    result = outgoing.copy()
    if boundary > 0:
        result[:, :boundary] = incoming[:, :boundary]
    band = max(6, int(float(effect.get("fold_width_ratio", 0.12)) * width * math.sin(progress * math.pi)))
    x0, x1 = max(0, boundary - band), min(width, boundary + band)
    if x1 > x0:
        strip = outgoing[:, max(0, boundary - (x1 - x0)):boundary]
        if strip.size:
            strip = cv2.resize(cv2.flip(strip, 1), (x1 - x0, height), interpolation=cv2.INTER_LINEAR)
            shade = np.linspace(0.35, 1.05, x1 - x0, dtype=np.float32)[None, :, None]
            result[:, x0:x1] = np.clip(strip.astype(np.float32) * shade, 0, 255).astype(np.uint8)
        shadow = np.zeros_like(result[:, x0:x1], dtype=np.float32)
        shadow[:] = -float(effect.get("shadow_strength", 65)) * math.sin(progress * math.pi)
        result[:, x0:x1] = np.clip(result[:, x0:x1].astype(np.float32) + shadow, 0, 255).astype(np.uint8)
    return result


_MATTE_CACHE: dict[tuple[int, int, int], np.ndarray] = {}


def _organic_noise(height: int, width: int, seed: int) -> np.ndarray:
    key = (height, width, seed)
    if key not in _MATTE_CACHE:
        rng = np.random.default_rng(seed)
        small = rng.random((max(8, height // 18), max(8, width // 18))).astype(np.float32)
        noise = cv2.resize(small, (width, height), interpolation=cv2.INTER_CUBIC)
        noise = cv2.GaussianBlur(noise, (0, 0), max(2.0, width / 180.0))
        cv2.normalize(noise, noise, 0.0, 1.0, cv2.NORM_MINMAX)
        _MATTE_CACHE[key] = noise
    return _MATTE_CACHE[key]


def ink_wash_frame(outgoing: np.ndarray, incoming: np.ndarray, progress: float, effect: dict) -> np.ndarray:
    height, width = outgoing.shape[:2]
    noise = _organic_noise(height, width, int(effect.get("seed", 171)))
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    cx = width * float(effect.get("origin_x", 0.52))
    cy = height * float(effect.get("origin_y", 0.5))
    distance = np.sqrt(((xx - cx) / width) ** 2 + ((yy - cy) / height) ** 2)
    field = 1.0 - distance * 1.55 + (noise - 0.5) * float(effect.get("noise_strength", 0.72))
    threshold = 1.05 - smoothstep(progress) * 1.5
    softness = float(effect.get("edge_softness", 0.10))
    matte = np.clip((field - threshold) / max(softness, 0.01), 0.0, 1.0)
    matte = cv2.GaussianBlur(matte, (0, 0), float(effect.get("blur_sigma", 3.0)))
    result = outgoing.astype(np.float32) * (1.0 - matte[..., None]) + incoming.astype(np.float32) * matte[..., None]
    edge = np.clip(1.0 - np.abs(matte - 0.5) * 7.0, 0.0, 1.0)
    result *= (1.0 - edge[..., None] * float(effect.get("ink_edge_darkness", 0.32)))
    return np.clip(result, 0, 255).astype(np.uint8)


def kaleidoscope_frame(outgoing: np.ndarray, incoming: np.ndarray, progress: float, effect: dict) -> np.ndarray:
    height, width = outgoing.shape[:2]
    alpha = smoothstep(progress)
    base = cv2.addWeighted(outgoing, 1.0 - alpha, incoming, alpha, 0)
    angle = float(effect.get("rotation_degrees", 52.0)) * math.sin(progress * math.pi)
    scale = 1.0 + float(effect.get("zoom_strength", 0.22)) * math.sin(progress * math.pi)
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, scale)
    spun = cv2.warpAffine(base, matrix, (width, height), borderMode=cv2.BORDER_REFLECT_101)
    yy, xx = np.mgrid[0:height, 0:width]
    map_x = (width / 2 + np.abs(xx - width / 2)).astype(np.float32)
    map_y = (height / 2 + np.abs(yy - height / 2)).astype(np.float32)
    mirrored = cv2.remap(spun, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
    envelope = math.sin(progress * math.pi)
    return cv2.addWeighted(base, 1.0 - envelope, mirrored, envelope, 0)


def dolly_zoom_frame(outgoing: np.ndarray, incoming: np.ndarray, progress: float, effect: dict) -> np.ndarray:
    eased = smoothstep(progress)
    envelope = math.sin(progress * math.pi)
    old = scale_center(outgoing, 1.0 + float(effect.get("outgoing_zoom", 0.28)) * eased)
    new = scale_center(incoming, float(effect.get("incoming_start_scale", 0.78)) + (1.0 - float(effect.get("incoming_start_scale", 0.78))) * eased)
    background = radial_blur(cv2.addWeighted(old, 1.0 - eased, new, eased, 0), envelope * 0.045, int(effect.get("blur_samples", 5)))
    height, width = outgoing.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    rx = (xx - width * 0.5) / (width * float(effect.get("subject_radius_x", 0.22)))
    ry = (yy - height * float(effect.get("subject_y", 0.46))) / (height * float(effect.get("subject_radius_y", 0.34)))
    lock = np.clip(1.0 - (rx * rx + ry * ry), 0.0, 1.0)
    lock = cv2.GaussianBlur(lock, (0, 0), width / 70.0)
    subject_mix = cv2.addWeighted(outgoing, 1.0 - eased, incoming, eased, 0)
    return np.clip(background.astype(np.float32) * (1.0 - lock[..., None]) + subject_mix.astype(np.float32) * lock[..., None], 0, 255).astype(np.uint8)


def object_occlusion_frame(outgoing: np.ndarray, incoming: np.ndarray, progress: float, effect: dict) -> np.ndarray:
    height, width = outgoing.shape[:2]
    x = int((-0.18 + smoothstep(progress) * 1.36) * width)
    band = int(width * float(effect.get("occluder_width_ratio", 0.20)))
    feather = max(3, int(width * float(effect.get("feather_ratio", 0.018))))
    xx = np.arange(width, dtype=np.float32)
    matte_line = np.clip((x - xx) / feather + 0.5, 0.0, 1.0)
    matte = np.repeat(matte_line[None, :], height, axis=0)
    result = outgoing.astype(np.float32) * (1.0 - matte[..., None]) + incoming.astype(np.float32) * matte[..., None]
    x0, x1 = max(0, x - band // 2), min(width, x + band // 2)
    if x1 > x0:
        slice_mix = cv2.addWeighted(outgoing[:, x0:x1], 0.5, incoming[:, x0:x1], 0.5, 0)
        darkness = float(effect.get("occluder_darkness", 0.42))
        result[:, x0:x1] = slice_mix.astype(np.float32) * (1.0 - darkness)
    return np.clip(result, 0, 255).astype(np.uint8)


def typography_wipe_frame(outgoing: np.ndarray, incoming: np.ndarray, progress: float, effect: dict) -> np.ndarray:
    height, width = outgoing.shape[:2]
    text = str(effect.get("text", "CUT"))
    font = cv2.FONT_HERSHEY_DUPLEX
    base_scale = min(width / 640.0, height / 360.0) * float(effect.get("font_scale", 4.0))
    scale = base_scale * (0.45 + smoothstep(progress) * 1.0)
    thickness = max(2, round(scale * float(effect.get("thickness_ratio", 2.5))))
    mask = np.zeros((height, width), dtype=np.uint8)
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    cv2.putText(mask, text, ((width - tw) // 2, (height + th) // 2), font, scale, 255, thickness, cv2.LINE_AA)
    mask = cv2.GaussianBlur(mask, (0, 0), float(effect.get("feather_sigma", 2.0))).astype(np.float32) / 255.0
    if progress > 0.62:
        mask = np.maximum(mask, smoothstep((progress - 0.62) / 0.38))
    result = outgoing.astype(np.float32) * (1.0 - mask[..., None]) + incoming.astype(np.float32) * mask[..., None]
    return np.clip(result, 0, 255).astype(np.uint8)


def make_sheet(frames: list[np.ndarray], path: Path) -> None:
    indexes = np.linspace(0, len(frames) - 1, 6, dtype=int)
    tiles = [cv2.resize(frames[index], (320, 180), interpolation=cv2.INTER_AREA) for index in indexes]
    sheet = np.hstack(tiles)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), sheet)


def mux_audio(ffmpeg: str, visual: Path, outgoing: Path, incoming: Path, out_path: Path,
              outgoing_start: float, incoming_start: float, pre: float, transition: float, post: float) -> None:
    total = pre + transition + post
    delay_ms = round(pre * 1000)
    filter_complex = (
        f"[1:a]atrim=start={outgoing_start:.6f}:duration={pre + transition:.6f},asetpts=PTS-STARTPTS,"
        f"afade=t=out:st={pre:.6f}:d={transition:.6f}[a0];"
        f"[2:a]atrim=start={incoming_start:.6f}:duration={transition + post:.6f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={transition:.6f},adelay={delay_ms}|{delay_ms}[a1];"
        f"[a0][a1]amix=inputs=2:duration=longest:dropout_transition=0,atrim=0:{total:.6f}[a]"
    )
    command = [
        ffmpeg, "-y", "-i", str(visual), "-i", str(outgoing), "-i", str(incoming),
        "-filter_complex", filter_complex, "-map", "0:v:0", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
        "-shortest", str(out_path),
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--outgoing", required=True)
    parser.add_argument("--incoming", required=True)
    parser.add_argument("--template-config", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    config = json.loads(Path(args.template_config).read_text(encoding="utf-8"))
    video = config["video"]
    timing = config["timing"]
    effect = config["effect"]
    fps = float(video["fps"])
    size = (int(video["width"]), int(video["height"]))
    pre = float(timing["pre_duration"])
    transition = float(timing["transition_duration"])
    post = float(timing["post_duration"])
    pre_count = round(pre * fps)
    transition_count = round(transition * fps)
    post_count = round(post * fps)
    outgoing_path = Path(args.outgoing)
    incoming_path = Path(args.incoming)
    outgoing = load_frames(outgoing_path, float(timing["outgoing_start"]), pre_count + transition_count, fps, size)
    incoming = load_frames(incoming_path, float(timing["incoming_start"]), transition_count + post_count, fps, size)

    mode = config["mode"]
    shards = build_shards(*size, effect) if mode == "crack_split" else []
    timeline = outgoing[:pre_count]
    for index in range(transition_count):
        progress = index / max(1, transition_count - 1)
        old = outgoing[pre_count + index]
        new = incoming[index]
        if mode == "elastic_zoom":
            frame = elastic_zoom_frame(old, new, progress, effect)
        elif mode == "crack_split":
            frame = crack_split_frame(old, new, progress, effect, shards)
        elif mode == "signal_glitch":
            frame = signal_glitch_frame(old, new, progress, effect, index)
        elif mode == "exposure_flash":
            frame = exposure_flash_frame(old, new, progress, effect, index)
        elif mode == "page_fold":
            frame = page_fold_frame(old, new, progress, effect)
        elif mode == "ink_wash_reveal":
            frame = ink_wash_frame(old, new, progress, effect)
        elif mode == "kaleidoscope_cut":
            frame = kaleidoscope_frame(old, new, progress, effect)
        elif mode == "dolly_zoom":
            frame = dolly_zoom_frame(old, new, progress, effect)
        elif mode == "object_occlusion_wipe":
            frame = object_occlusion_frame(old, new, progress, effect)
        elif mode == "typography_wipe":
            frame = typography_wipe_frame(old, new, progress, effect)
        else:
            raise ValueError(f"Unsupported mode: {mode}")
        timeline.append(frame)
    timeline.extend(incoming[transition_count: transition_count + post_count])

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    visual = work_dir / f"{config['template_id']}_visual.mp4"
    writer = cv2.VideoWriter(str(visual), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    if not writer.isOpened():
        raise RuntimeError(f"Cannot create {visual}")
    for frame in timeline:
        writer.write(frame)
    writer.release()
    make_sheet(timeline, work_dir / f"{config['template_id']}_sheet.jpg")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mux_audio(args.ffmpeg, visual, outgoing_path, incoming_path, out_path,
              float(timing["outgoing_start"]), float(timing["incoming_start"]), pre, transition, post)
    print(out_path.resolve())


if __name__ == "__main__":
    main()
