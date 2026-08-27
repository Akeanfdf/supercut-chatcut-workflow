"""Render a focused foreground-hold burn/glitch transition preview.

The outgoing weapon is frozen in screen space and cut out.  The incoming
gameplay starts underneath it, while a procedural burn matte removes the old
weapon.  Cyan energy, exposure, and a very short glitch hide the background
handoff without redrawing the weapon.
"""

from __future__ import annotations

import argparse
import math
import subprocess
import wave
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


def sharpest_tail(frames: list[np.ndarray], count: int = 12) -> np.ndarray:
    tail = frames[-min(count, len(frames)) :]
    return max(
        tail,
        key=lambda f: cv2.Laplacian(cv2.cvtColor(f[int(f.shape[0] * 0.38) :], cv2.COLOR_BGR2GRAY), cv2.CV_64F).var(),
    ).copy()


def weapon_mask(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    small_w = 640
    small_h = round(height * small_w / width)
    small = cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_AREA)
    gc_mask = np.zeros(small.shape[:2], dtype=np.uint8)
    rect = (int(small_w * 0.12), int(small_h * 0.36), int(small_w * 0.86), int(small_h * 0.63))
    bg_model = np.zeros((1, 65), np.float64)
    fg_model = np.zeros((1, 65), np.float64)
    cv2.grabCut(small, gc_mask, rect, bg_model, fg_model, 7, cv2.GC_INIT_WITH_RECT)
    binary = np.where((gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    binary[: int(small_h * 0.35)] = 0

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, 8)
    cleaned = np.zeros_like(binary)
    for label in range(1, count):
        area = stats[label, cv2.CC_STAT_AREA]
        x, y, component_w, component_h = stats[label, :4]
        cx, cy = centroids[label]
        touches_bottom = y + component_h > small_h * 0.80
        weapon_zone = cx > small_w * 0.16 and cy > small_h * 0.48
        if area > 180 and (touches_bottom or weapon_zone):
            cleaned[labels == label] = 255

    # This preview uses a reload frame whose arm enters from the lower-left.
    # Limit GrabCut to the plausible first-person view-model envelope so the
    # adjacent white wall/floor cannot become part of the held foreground.
    envelope = np.zeros_like(cleaned)
    polygon = np.array(
        [
            [0, small_h - 1],
            [0, int(small_h * 0.78)],
            [int(small_w * 0.18), int(small_h * 0.74)],
            [int(small_w * 0.34), int(small_h * 0.66)],
            [int(small_w * 0.44), int(small_h * 0.64)],
            [int(small_w * 0.45), int(small_h * 0.47)],
            [int(small_w * 0.57), int(small_h * 0.44)],
            [int(small_w * 0.64), int(small_h * 0.36)],
            [int(small_w * 0.72), int(small_h * 0.38)],
            [int(small_w * 0.80), int(small_h * 0.48)],
            [int(small_w * 0.91), int(small_h * 0.51)],
            [small_w - 1, int(small_h * 0.59)],
            [small_w - 1, small_h - 1],
        ],
        dtype=np.int32,
    )
    cv2.fillPoly(envelope, [polygon], 255)
    cleaned = cv2.bitwise_and(cleaned, envelope)

    kernel = np.ones((5, 5), np.uint8)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel, iterations=2)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
    cleaned = cv2.GaussianBlur(cleaned, (11, 11), 0)
    return cv2.resize(cleaned, (width, height), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0


def zoom_blur(frame: np.ndarray, strength: float, samples: int = 7) -> np.ndarray:
    if strength <= 0.002:
        return frame
    height, width = frame.shape[:2]
    accum = frame.astype(np.float32)
    total = 1.0
    center = (width / 2.0, height / 2.0)
    for sample in range(1, samples):
        scale = 1.0 + strength * sample / (samples - 1)
        matrix = cv2.getRotationMatrix2D(center, 0, scale)
        warped = cv2.warpAffine(frame, matrix, (width, height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
        weight = 1.0 - 0.55 * sample / samples
        accum += warped.astype(np.float32) * weight
        total += weight
    return np.clip(accum / total, 0, 255).astype(np.uint8)


def screen(base: np.ndarray, light: np.ndarray) -> np.ndarray:
    a = base.astype(np.float32) / 255.0
    b = light.astype(np.float32) / 255.0
    return np.clip((1.0 - (1.0 - a) * (1.0 - b)) * 255.0, 0, 255).astype(np.uint8)


def electric_overlay(shape: tuple[int, int, int], progress: float, rng: np.random.Generator) -> np.ndarray:
    height, width = shape[:2]
    energy = math.exp(-((progress - 0.16) / 0.16) ** 2)
    layer = np.zeros(shape, dtype=np.uint8)
    if energy < 0.02:
        return layer
    origin = np.array([int(width * 0.59), int(height * 0.58)])
    targets = [
        np.array([int(width * 0.05), int(height * 0.20)]),
        np.array([int(width * 0.23), int(height * 0.96)]),
        np.array([int(width * 0.96), int(height * 0.18)]),
        np.array([int(width * 0.97), int(height * 0.78)]),
    ]
    for index, target in enumerate(targets):
        if progress * 8.0 < index * 0.18:
            continue
        points = []
        segments = 11
        normal = np.array([-(target - origin)[1], (target - origin)[0]], dtype=np.float32)
        normal /= max(1.0, np.linalg.norm(normal))
        for j in range(segments + 1):
            t = j / segments
            point = origin * (1 - t) + target * t
            jitter = math.sin(t * math.pi) * rng.normal(0, 18) * normal
            points.append(np.round(point + jitter).astype(np.int32))
        pts = np.array(points).reshape((-1, 1, 2))
        cv2.polylines(layer, [pts], False, (255, 210, 70), max(1, round(2 + 3 * energy)), cv2.LINE_AA)
    bloom = cv2.GaussianBlur(layer, (0, 0), 13)
    return cv2.addWeighted(layer, min(1.0, 0.7 + energy), bloom, 1.8 * energy, 0)


def glitch(frame: np.ndarray, amount: float, rng: np.random.Generator) -> np.ndarray:
    if amount <= 0.01:
        return frame
    result = frame.copy()
    height, width = frame.shape[:2]
    bands = 5 + round(13 * amount)
    for _ in range(bands):
        y = int(rng.integers(0, height - 3))
        band_h = int(rng.integers(3, max(5, round(42 * amount))))
        shift = int(rng.integers(-1, 2) * rng.integers(15, max(17, round(105 * amount))))
        y2 = min(height, y + band_h)
        result[y:y2] = np.roll(result[y:y2], shift, axis=1)
    split = max(1, round(9 * amount))
    blue, green, red = cv2.split(result)
    blue = np.roll(blue, split, axis=1)
    red = np.roll(red, -split, axis=1)
    result = cv2.merge((blue, green, red))
    result[::4] = (result[::4].astype(np.float32) * (1.0 - 0.22 * amount)).astype(np.uint8)
    return result


def make_burn_fields(mask: np.ndarray, seed: int = 2408) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    height, width = mask.shape
    yy, xx = np.mgrid[0:height, 0:width]
    anchor_x, anchor_y = width * 0.79, height * 0.90
    distance = np.sqrt(((xx - anchor_x) / width) ** 2 + ((yy - anchor_y) / height) ** 2)
    rng = np.random.default_rng(seed)
    noise = rng.random((height // 8 + 1, width // 8 + 1)).astype(np.float32)
    noise = cv2.resize(noise, (width, height), interpolation=cv2.INTER_CUBIC)
    noise = cv2.GaussianBlur(noise, (0, 0), 7)
    order = distance + noise * 0.19 + (height - yy) / height * 0.05
    inside = mask > 0.12
    lo, hi = np.percentile(order[inside], [1, 99]) if np.any(inside) else (0.0, 1.0)
    order = np.clip((order - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    return order.astype(np.float32), inside, noise


def save_image(path: Path, image: np.ndarray) -> None:
    """Write through imencode so Windows paths with Chinese text are safe."""
    suffix = path.suffix.lower() or ".png"
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        raise RuntimeError(f"Cannot encode {path}")
    path.write_bytes(encoded.tobytes())


def burn_composite(
    background: np.ndarray,
    cutout: np.ndarray,
    base_mask: np.ndarray,
    order: np.ndarray,
    progress: float,
    rng: np.random.Generator,
) -> np.ndarray:
    q = np.clip((progress - 0.14) / 0.70, 0.0, 1.08)
    threshold = q
    softness = 0.026
    keep = np.clip((order - threshold) / softness * 0.5 + 0.5, 0.0, 1.0) * base_mask
    if q >= 1.02:
        keep[:] = 0
    result = (
        cutout.astype(np.float32) * keep[..., None]
        + background.astype(np.float32) * (1.0 - keep[..., None])
    ).astype(np.uint8)

    if 0.0 < q < 1.03:
        edge = np.clip(1.0 - np.abs(order - threshold) / 0.070, 0.0, 1.0) * base_mask
        hot = np.clip(1.0 - np.abs(order - threshold) / 0.022, 0.0, 1.0) * base_mask
        edge_u8 = (edge * 255).astype(np.uint8)
        glow = cv2.GaussianBlur(edge_u8, (0, 0), 15).astype(np.float32) / 255.0
        fire = np.zeros_like(result, dtype=np.float32)
        fire[..., 0] = 18 * edge + 14 * glow
        fire[..., 1] = 115 * edge + 75 * glow + 125 * hot
        fire[..., 2] = 255 * edge + 165 * glow + 100 * hot

        # Flames rise from the active burn front; their placement follows the
        # gun alpha instead of being a generic full-screen fire overlay.
        flame_alpha = np.zeros_like(edge, dtype=np.float32)
        for offset, weight in [(7, 0.75), (15, 0.55), (27, 0.38), (43, 0.22)]:
            shifted = np.zeros_like(edge)
            shifted[:-offset] = edge[offset:]
            flame_alpha = np.maximum(flame_alpha, shifted * weight)
        texture = rng.random(edge.shape).astype(np.float32)
        texture = cv2.GaussianBlur(texture, (0, 0), 2.2)
        flame_alpha *= np.clip((texture - 0.35) * 2.8, 0.0, 1.0)
        fire[..., 1] += 120 * flame_alpha
        fire[..., 2] += 255 * flame_alpha
        fire_layer = np.clip(fire, 0, 255).astype(np.uint8)
        result = screen(result, fire_layer)

        coords = np.column_stack(np.where(edge > 0.58))
        if len(coords):
            spark_layer = np.zeros_like(result)
            for _ in range(min(24, max(4, len(coords) // 900))):
                y, x = coords[int(rng.integers(0, len(coords)))]
                length = int(rng.integers(8, 28))
                cv2.line(spark_layer, (int(x), int(y)), (int(x + rng.integers(-7, 8)), max(0, int(y - length))), (30, 175, 255), 1, cv2.LINE_AA)
            result = screen(result, cv2.GaussianBlur(spark_layer, (3, 3), 0))
    return result


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


def make_soundtrack(path: Path, duration: float, transition_time: float, rate: int = 48000) -> None:
    count = round(duration * rate)
    t = np.arange(count, dtype=np.float32) / rate
    rng = np.random.default_rng(901)
    audio = np.zeros(count, dtype=np.float32)

    # Rising filtered-noise whoosh.
    envelope = np.exp(-((t - (transition_time + 0.18)) / 0.28) ** 2)
    noise = rng.normal(0, 1, count).astype(np.float32)
    smooth = cv2.GaussianBlur(noise.reshape(1, -1), (0, 0), sigmaX=12).reshape(-1)
    audio += smooth * envelope * 0.33

    # Low impact and a short electrical chirp.
    impact_t = np.maximum(0, t - transition_time)
    impact_env = np.exp(-impact_t * 12) * (t >= transition_time)
    audio += np.sin(2 * math.pi * (74 * impact_t + 23 * impact_t**2)) * impact_env * 0.42
    chirp_start = transition_time + 0.62
    chirp_t = np.maximum(0, t - chirp_start)
    chirp_env = np.exp(-chirp_t * 28) * (t >= chirp_start)
    audio += np.sign(np.sin(2 * math.pi * (120 + 950 * chirp_t) * chirp_t)) * chirp_env * 0.11

    # Small deterministic crackles during the burn window.
    for pulse in np.linspace(transition_time + 0.28, transition_time + 0.80, 12):
        pulse_env = np.exp(-np.maximum(0, t - pulse) * 95) * (t >= pulse)
        audio += rng.uniform(-0.13, 0.13) * pulse_env

    audio = np.tanh(audio * 1.5)
    stereo = np.column_stack((audio, np.roll(audio, 47)))
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())


def mux_audio(
    ffmpeg: str,
    visual: Path,
    outgoing: Path,
    incoming: Path,
    sfx: Path,
    output: Path,
    pre_duration: float,
    incoming_duration: float,
    outgoing_start: float,
) -> None:
    filters = (
        f"[1:a]atrim=start={outgoing_start}:duration={pre_duration},asetpts=PTS-STARTPTS,aresample=48000[a0];"
        f"[2:a]atrim=start=0:duration={incoming_duration},asetpts=PTS-STARTPTS,aresample=48000[a1];"
        f"[a0][a1]concat=n=2:v=0:a=1[game];"
        f"[game][3:a]amix=inputs=2:duration=first:weights='0.78 1.0':normalize=0,alimiter=limit=0.95[aout]"
    )
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(visual), "-i", str(outgoing), "-i", str(incoming), "-i", str(sfx),
        "-filter_complex", filters,
        "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--outgoing", required=True)
    parser.add_argument("--incoming", required=True)
    parser.add_argument("--work-dir", required=True)
    args = parser.parse_args()

    fps = 30.0
    size = (1280, 720)
    pre_duration = 1.05
    transition_duration = 1.00
    post_duration = 1.25
    outgoing_start = 4.58
    incoming_duration = transition_duration + post_duration
    outgoing_path = Path(args.outgoing).resolve()
    incoming_path = Path(args.incoming).resolve()
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    output = Path(args.out).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    outgoing = load_frames(outgoing_path, outgoing_start, pre_duration, fps, size)
    incoming = load_frames(incoming_path, 0.0, incoming_duration, fps, size)
    cutout = sharpest_tail(outgoing)
    base_mask = weapon_mask(cutout)
    order, _, _ = make_burn_fields(base_mask)
    save_image(work / "weapon_freeze.png", cutout)
    save_image(work / "weapon_mask.png", (base_mask * 255).astype(np.uint8))

    frames = list(outgoing)
    transition_count = round(transition_duration * fps)
    rng = np.random.default_rng(7781)
    for index in range(transition_count):
        progress = index / max(1, transition_count - 1)
        after = incoming[index].copy()
        blend = np.clip(progress / 0.16, 0.0, 1.0)
        background = cv2.addWeighted(outgoing[-1], 1.0 - blend, after, blend, 0)
        zoom_strength = 0.075 * math.exp(-((progress - 0.15) / 0.16) ** 2)
        background = zoom_blur(background, zoom_strength)

        grade = math.exp(-((progress - 0.40) / 0.38) ** 2)
        graded = background.astype(np.float32)
        graded[..., 0] *= 1.0 + 0.22 * grade
        graded[..., 1] *= 1.0 + 0.11 * grade
        graded[..., 2] *= 1.0 - 0.12 * grade
        background = np.clip(graded, 0, 255).astype(np.uint8)

        frame = burn_composite(background, cutout, base_mask, order, progress, rng)
        frame = screen(frame, electric_overlay(frame.shape, progress, rng))
        flash = 0.78 * math.exp(-((progress - 0.12) / 0.055) ** 2)
        if flash > 0.01:
            frame = cv2.addWeighted(frame, 1.0 - flash * 0.48, np.full_like(frame, 255), flash * 0.48, 0)
        glitch_amount = math.exp(-((progress - 0.83) / 0.065) ** 2)
        frame = glitch(frame, glitch_amount, rng)
        frames.append(frame)

    frames.extend(incoming[transition_count : transition_count + round(post_duration * fps)])
    visual = work / "transition_preview_visual.mp4"
    sfx = work / "transition_preview_sfx.wav"
    render_video(frames, fps, args.ffmpeg, visual)
    duration = len(frames) / fps
    make_soundtrack(sfx, duration, pre_duration)
    mux_audio(
        args.ffmpeg, visual, outgoing_path, incoming_path, sfx, output,
        pre_duration, incoming_duration, outgoing_start,
    )
    print(output)


if __name__ == "__main__":
    main()
