"""Render a reviewed plan with layered weapon holds and reconstructed source audio."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import deque
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from effect_runtime import render_effect_transition


def run(command: list[str], label: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed:\n{result.stderr[-5000:]}")


def load_segment(shot: dict, width: int, height: int, fps: float) -> list[np.ndarray]:
    return list(iter_segment_frames(shot, width, height, fps))


def iter_segment_frames(shot: dict, width: int, height: int, fps: float) -> Iterator[np.ndarray]:
    cap = cv2.VideoCapture(shot["source"])
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {shot['source']}")
    source_fps = float(cap.get(cv2.CAP_PROP_FPS) or fps)
    start_frame = round(float(shot["source_start"]) * source_fps)
    target_count = max(1, round(float(shot["duration"]) * fps))
    speed = float(shot.get("speed", 1.0))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    current_source_index = start_frame - 1
    current_frame: np.ndarray | None = None
    try:
        for output_index in range(target_count):
            desired = start_frame + round(output_index * source_fps * speed / fps)
            while current_source_index < desired:
                ok, frame = cap.read()
                if not ok:
                    break
                current_frame = frame
                current_source_index += 1
            if current_frame is None:
                raise RuntimeError(f"Cannot decode shot {shot['index']}")
            yield cv2.resize(current_frame, (width, height), interpolation=cv2.INTER_AREA)
    finally:
        cap.release()


def sharpest_tail(frames: list[np.ndarray], count: int = 12) -> np.ndarray:
    tail = frames[-min(count, len(frames)) :]
    return max(
        tail,
        key=lambda frame: cv2.Laplacian(
            cv2.cvtColor(frame[int(frame.shape[0] * 0.40) :, :], cv2.COLOR_BGR2GRAY), cv2.CV_64F
        ).var(),
    )


def weapon_mask(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    small_w = 640
    small_h = round(height * small_w / width)
    small = cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_AREA)
    mask = np.zeros(small.shape[:2], dtype=np.uint8)
    rect = (int(small_w * 0.12), int(small_h * 0.36), int(small_w * 0.86), int(small_h * 0.63))
    bg_model = np.zeros((1, 65), np.float64)
    fg_model = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(small, mask, rect, bg_model, fg_model, 5, cv2.GC_INIT_WITH_RECT)
        binary = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    except cv2.error:
        binary = np.zeros(small.shape[:2], dtype=np.uint8)

    # Retain meaningful lower-screen components; reject tiny HUD/text islands.
    binary[: int(small_h * 0.35), :] = 0
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, 8)
    cleaned = np.zeros_like(binary)
    for label in range(1, count):
        area = stats[label, cv2.CC_STAT_AREA]
        x, y, component_w, component_h = stats[label, :4]
        center_x, center_y = centroids[label]
        touches_lower = y + component_h >= small_h * 0.76
        in_weapon_zone = center_x >= small_w * 0.20 and center_y >= small_h * 0.48
        if area >= 220 and (touches_lower or in_weapon_zone):
            cleaned[labels == label] = 255
    if np.count_nonzero(cleaned) < small_w * small_h * 0.015:
        # Conservative soft polygon fallback around the usual view-model area.
        polygon = np.array(
            [
                [int(small_w * 0.22), small_h - 1],
                [small_w - 1, small_h - 1],
                [small_w - 1, int(small_h * 0.43)],
                [int(small_w * 0.74), int(small_h * 0.46)],
                [int(small_w * 0.50), int(small_h * 0.58)],
            ],
            dtype=np.int32,
        )
        cv2.fillPoly(cleaned, [polygon], 255)
    kernel = np.ones((5, 5), np.uint8)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel, iterations=2)
    cleaned = cv2.GaussianBlur(cleaned, (21, 21), 0)
    return cv2.resize(cleaned, (width, height), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0


def shifted(frame: np.ndarray, dx: int) -> np.ndarray:
    height, width = frame.shape[:2]
    matrix = np.float32([[1, 0, dx], [0, 1, 0]])
    return cv2.warpAffine(frame, matrix, (width, height), borderMode=cv2.BORDER_REFLECT_101)


def motion_blur(frame: np.ndarray, strength: int) -> np.ndarray:
    kernel = max(1, strength | 1)
    return cv2.blur(frame, (kernel, 1)) if kernel > 1 else frame


def transition_frames(
    previous: list[np.ndarray],
    incoming: list[np.ndarray],
    count: int,
    kind: str,
    layered: bool,
    direction: int,
    effect_id: str | None = None,
    effect_principle: str | None = None,
) -> list[np.ndarray]:
    count = min(count, len(previous), len(incoming))
    if count <= 0:
        return []
    if effect_id and effect_principle:
        return render_effect_transition(
            previous,
            incoming,
            count,
            effect_id=effect_id,
            principle=effect_principle,
            seed=7919 + direction,
        )
    cutout_frame = sharpest_tail(previous)
    alpha_mask = weapon_mask(cutout_frame) if layered else None
    result: list[np.ndarray] = []
    width = previous[0].shape[1]
    for index in range(count):
        alpha = (index + 1) / (count + 1)
        before = previous[-count + index]
        after = incoming[index]
        if kind == "whip" or layered:
            distance = int(width * 0.20)
            before_bg = shifted(before, int(-direction * alpha * distance))
            after_bg = shifted(after, int(direction * (1.0 - alpha) * distance))
            blur = max(3, round(31 * math.sin(math.pi * alpha)))
            background = cv2.addWeighted(
                motion_blur(before_bg, blur), 1.0 - alpha, motion_blur(after_bg, blur), alpha, 0
            )
        else:
            background = cv2.addWeighted(before, 1.0 - alpha, after, alpha, 0)
        if kind == "fade_black":
            background = (background.astype(np.float32) * (1.0 - 0.72 * math.sin(math.pi * alpha))).astype(np.uint8)
        if layered and alpha_mask is not None:
            hold = 1.0 if alpha <= 0.62 else max(0.0, 1.0 - (alpha - 0.62) / 0.38)
            matte = (alpha_mask * hold)[..., None]
            background = (cutout_frame.astype(np.float32) * matte + background.astype(np.float32) * (1.0 - matte)).astype(
                np.uint8
            )
        result.append(background)
    return result


def write_mask_preview(
    shots: list[dict], segments: list[list[np.ndarray]], output: Path
) -> None:
    rows = [index for index, shot in enumerate(shots[:-1]) if shot.get("layering_after") == "foreground_hold_planned"]
    if not rows:
        return
    tile_w, tile_h = 480, 300
    canvas = np.full((len(rows) * tile_h, 4 * tile_w, 3), 24, dtype=np.uint8)
    checker = np.zeros((720, 1280, 3), dtype=np.uint8)
    checker[:] = (75, 75, 75)
    for row, index in enumerate(rows):
        foreground = sharpest_tail(segments[index])
        mask = weapon_mask(foreground)
        cutout = (foreground.astype(np.float32) * mask[..., None] + checker.astype(np.float32) * (1 - mask[..., None])).astype(
            np.uint8
        )
        mask_view = np.repeat((mask * 255).astype(np.uint8)[..., None], 3, axis=2)
        incoming = segments[index + 1][0]
        previews = [foreground, mask_view, cutout, incoming]
        labels = ["source foreground", "auto mask", "cutout preview", "incoming background"]
        for column, (preview, label) in enumerate(zip(previews, labels)):
            preview = cv2.resize(preview, (tile_w, tile_h - 28), interpolation=cv2.INTER_AREA)
            x, y = column * tile_w, row * tile_h
            canvas[y : y + tile_h - 28, x : x + tile_w] = preview
            cv2.putText(
                canvas,
                f"shot {shots[index]['index']} {label}",
                (x + 7, y + tile_h - 9),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (245, 245, 245),
                1,
                cv2.LINE_AA,
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), canvas)


def assemble_visual(
    shots: list[dict],
    segments: list[list[np.ndarray]],
    fps: float,
    ffmpeg: str,
    output: Path,
) -> float:
    timeline = list(segments[0])
    for index in range(1, len(shots)):
        previous_shot = shots[index - 1]
        incoming = segments[index]
        kind = previous_shot.get("transition_after", "hard_cut")
        layered = previous_shot.get("layering_after") == "foreground_hold_planned"
        count = round(float(previous_shot.get("transition_duration", 0.0)) * fps)
        if kind == "hard_cut" or count <= 0:
            timeline.extend(incoming)
            continue
        count = min(count, len(timeline) - 1, len(incoming) - 1)
        transition = transition_frames(
            timeline,
            incoming,
            count,
            kind,
            layered,
            1 if index % 2 else -1,
            effect_id=previous_shot.get("effect_after"),
            effect_principle=previous_shot.get("effect_principle"),
        )
        timeline = timeline[:-count] + transition + incoming[count:]

    final_transition = shots[-1].get("transition_after")
    if final_transition == "fade_black":
        count = min(len(timeline), round(float(shots[-1].get("transition_duration", 0.3)) * fps))
        for index in range(count):
            factor = 1.0 - (index + 1) / count
            timeline[-count + index] = (timeline[-count + index].astype(np.float32) * factor).astype(np.uint8)

    height, width = timeline[0].shape[:2]
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{width}x{height}",
        "-r",
        f"{fps:.3f}",
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output.resolve()),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    for frame in timeline:
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    code = process.wait()
    if code != 0:
        raise RuntimeError(f"Visual render failed:\n{stderr[-5000:]}")
    return len(timeline) / fps


def assemble_visual_streaming(
    shots: list[dict],
    width: int,
    height: int,
    fps: float,
    ffmpeg: str,
    output: Path,
) -> float:
    """Encode a v2 timeline while buffering only transition handles."""
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
        "-r", f"{fps:.3f}", "-i", "-", "-an", "-c:v", "libx264",
        "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output.resolve()),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.stdin is None:
        raise RuntimeError("Could not open FFmpeg stdin for streaming render")
    written = 0
    previous_tail: list[np.ndarray] = []
    try:
        for index, shot in enumerate(shots):
            iterator = iter_segment_frames(shot, width, height, fps)
            incoming_count = 0
            previous_shot = shots[index - 1] if index > 0 else None
            if previous_shot and previous_shot.get("transition_after") != "hard_cut":
                incoming_count = max(0, round(float(previous_shot.get("transition_duration", 0.0)) * fps))
            incoming_head: list[np.ndarray] = []
            for _ in range(incoming_count):
                try:
                    incoming_head.append(next(iterator))
                except StopIteration:
                    break
            if previous_shot and incoming_count > 0:
                count = min(incoming_count, len(previous_tail), len(incoming_head))
                rendered = transition_frames(
                    previous_tail,
                    incoming_head,
                    count,
                    previous_shot.get("transition_after", "dissolve"),
                    previous_shot.get("layering_after") == "foreground_hold_planned",
                    1 if index % 2 else -1,
                    effect_id=previous_shot.get("effect_after"),
                    effect_principle=previous_shot.get("effect_principle"),
                )
                for frame in rendered:
                    process.stdin.write(frame.tobytes())
                    written += 1
            elif previous_tail:
                for frame in previous_tail:
                    process.stdin.write(frame.tobytes())
                    written += 1

            outgoing_count = 0
            if index < len(shots) - 1 and shot.get("transition_after") != "hard_cut":
                outgoing_count = max(0, round(float(shot.get("transition_duration", 0.0)) * fps))
            final_fade_count = 0
            if index == len(shots) - 1 and shot.get("transition_after") == "fade_black":
                final_fade_count = max(1, round(float(shot.get("transition_duration", 0.3)) * fps))
                outgoing_count = final_fade_count
            buffer: deque[np.ndarray] = deque()
            for frame in iterator:
                buffer.append(frame)
                if len(buffer) > outgoing_count:
                    process.stdin.write(buffer.popleft().tobytes())
                    written += 1
            previous_tail = list(buffer)
            if final_fade_count and previous_tail:
                for fade_index, frame in enumerate(previous_tail):
                    factor = 1.0 - (fade_index + 1) / len(previous_tail)
                    process.stdin.write((frame.astype(np.float32) * factor).astype(np.uint8).tobytes())
                    written += 1
                previous_tail = []
        if previous_tail:
            for frame in previous_tail:
                process.stdin.write(frame.tobytes())
                written += 1
    finally:
        process.stdin.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    code = process.wait()
    if code:
        raise RuntimeError(f"Streaming visual render failed:\n{stderr[-5000:]}")
    return written / fps


def assemble_audio(shots: list[dict], fps: float, duration: float, ffmpeg: str, output: Path) -> None:
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "warning"]
    for shot in shots:
        command += ["-ss", str(shot["source_start"]), "-t", str(shot["duration"]), "-i", shot["source"]]
    filters: list[str] = []
    for index, shot in enumerate(shots):
        filters.append(
            f"[{index}:a]atrim=duration={float(shot['duration']):.6f},asetpts=PTS-STARTPTS,"
            f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
        )
    current = "a0"
    for index in range(1, len(shots)):
        previous = shots[index - 1]
        output_label = f"ax{index}"
        transition_duration = float(previous.get("transition_duration", 0.0))
        if previous.get("transition_after") == "hard_cut" or transition_duration <= 0:
            filters.append(f"[{current}][a{index}]concat=n=2:v=0:a=1[{output_label}]")
        else:
            filters.append(
                f"[{current}][a{index}]acrossfade=d={transition_duration:.6f}:c1=tri:c2=tri[{output_label}]"
            )
        current = output_label
    fade_duration = float(shots[-1].get("transition_duration", 0.3))
    fade_start = max(0.0, duration - fade_duration)
    filters.append(f"[{current}]atrim=duration={duration:.6f},afade=t=out:st={fade_start:.6f}:d={fade_duration:.6f}[aout]")
    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output.resolve()),
    ]
    run(command, "Audio assembly")


def all_sources_have_audio(shots: list[dict], ffmpeg: str) -> bool:
    checked: dict[str, bool] = {}
    for shot in shots:
        source = str(shot["source"])
        if source not in checked:
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-i", source],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            checked[source] = "Audio:" in result.stderr
    return bool(checked) and all(checked.values())


def render_reviewed_plan(
    plan: dict,
    ffmpeg: str,
    output: Path,
    mask_preview: Path | None = None,
    preview_only: bool = False,
) -> None:
    shots = plan["shots"]
    width, height, fps = int(plan["output"]["width"]), int(plan["output"]["height"]), float(plan["output"]["fps"])
    print(f"Loading {len(shots)} shots...")
    if mask_preview is not None or preview_only or plan.get("schema") != "supercut-edit-plan-v2":
        segments = [load_segment(shot, width, height, fps) for shot in shots]
        if mask_preview is not None:
            write_mask_preview(shots, segments, mask_preview)
            print(f"Mask preview: {mask_preview}")
        if preview_only:
            return
    else:
        segments = []
    output.parent.mkdir(parents=True, exist_ok=True)
    visual = output.with_name(f"{output.stem}_visual.mp4")
    audio = output.with_name(f"{output.stem}_audio.m4a")
    duration = (
        assemble_visual_streaming(shots, width, height, fps, ffmpeg, visual)
        if plan.get("schema") == "supercut-edit-plan-v2"
        else assemble_visual(shots, segments, fps, ffmpeg, visual)
    )
    print(f"Visual: {duration:.3f}s -> {visual}")
    if all_sources_have_audio(shots, ffmpeg):
        assemble_audio(shots, fps, duration, ffmpeg, audio)
        print(f"Audio -> {audio}")
        run(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
                "-i", str(visual.resolve()), "-i", str(audio.resolve()),
                "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-shortest",
                "-movflags", "+faststart", str(output.resolve()),
            ],
            "Final mux",
        )
    else:
        run(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
                "-i", str(visual.resolve()), "-map", "0:v:0", "-c", "copy",
                "-movflags", "+faststart", str(output.resolve()),
            ],
            "Final visual-only copy",
        )
        print("Audio skipped: one or more source clips have no audio stream")
    print(f"Final: {output.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--mask-preview")
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument("--allow-unapproved-preview", action="store_true")
    args = parser.parse_args()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    if plan.get("schema") == "supercut-edit-plan-v2" and plan.get("review_status") != "approved" and not args.allow_unapproved_preview:
        raise RuntimeError("v2计划尚未通过用户审阅；预览请显式使用 --allow-unapproved-preview")
    render_reviewed_plan(
        plan,
        args.ffmpeg,
        Path(args.out),
        Path(args.mask_preview) if args.mask_preview else None,
        args.preview_only,
    )


if __name__ == "__main__":
    main()
