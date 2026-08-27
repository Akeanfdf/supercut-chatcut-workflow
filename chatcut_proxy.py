"""Materialize source and exact-effect proxies declared by a ChatCut manifest."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import cv2

from effect_runtime import render_effect_transition
from tools.render_layered_plan import load_segment


def _run(command: list[str], label: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{label} failed:\n{result.stderr[-5000:]}")


def _review_size(width: int, height: int) -> tuple[int, int]:
    scale = min(1.0, 1920 / max(1, width), 1080 / max(1, height))
    review_width = max(2, round(width * scale / 2) * 2)
    review_height = max(2, round(height * scale / 2) * 2)
    return review_width, review_height


def _write_video(frames: list, fps: float, ffmpeg: str, output: Path) -> None:
    if not frames:
        raise ValueError("Cannot encode an empty effect proxy")
    height, width = frames[0].shape[:2]
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
        "-r", f"{fps:.6f}", "-i", "-", "-an", "-c:v", "libx264",
        "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output.resolve()),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.stdin is None:
        raise RuntimeError("Could not open FFmpeg stdin for effect proxy")
    for frame in frames:
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    code = process.wait()
    if code:
        raise RuntimeError(f"Effect proxy encode failed:\n{stderr[-5000:]}")


def _source_proxy(source: Path, output: Path, width: int, height: int, fps: float, ffmpeg: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    filter_value = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps:.6f}"
    _run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-i", str(source.resolve()),
            "-map", "0:v:0", "-map", "0:a?", "-vf", filter_value,
            "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output.resolve()),
        ],
        f"Source proxy {source.name}",
    )


def materialize_chatcut_proxies(
    plan: dict[str, Any],
    manifest: dict[str, Any],
    ffmpeg: str,
    force: bool = False,
) -> dict[str, Any]:
    if plan.get("schema") != "supercut-edit-plan-v2":
        raise ValueError("Proxy generation requires supercut-edit-plan-v2")
    canvas = manifest["project"]["canvas"]
    width, height = _review_size(int(canvas["width"]), int(canvas["height"]))
    fps = min(30.0, float(canvas["fps"]))
    results: list[dict[str, Any]] = []
    source_assets = {asset["id"]: asset for asset in manifest.get("assets", []) if asset.get("kind") == "source_proxy"}
    for asset in source_assets.values():
        source = Path(asset["original_path"])
        output = Path(asset["proxy_path"])
        if output.is_file() and not force:
            status = "reused"
        else:
            if not source.is_file():
                raise FileNotFoundError(source)
            _source_proxy(source, output, width, height, fps, ffmpeg)
            status = "created"
        results.append({"kind": "source_proxy", "asset_id": asset["id"], "path": str(output), "status": status})

    shots = plan.get("shots", [])
    index_by_id = {shot["id"]: index for index, shot in enumerate(shots)}
    effect_assets = {
        asset.get("effect_id"): asset
        for asset in manifest.get("assets", [])
        if asset.get("kind") == "exact_effect_proxy"
    }
    for effect in plan.get("effects", []):
        index = index_by_id.get(effect["after_shot_id"])
        if index is None or index + 1 >= len(shots):
            continue
        asset = effect_assets.get(effect["effect_id"])
        # Multiple uses of one effect have distinct assets in the manifest; fall
        # back to the exact item path when effect_id lookup is ambiguous.
        if asset is None or Path(asset["proxy_path"]).stem != effect["id"]:
            asset = next(
                (
                    row for row in manifest.get("assets", [])
                    if row.get("kind") == "exact_effect_proxy" and Path(row["proxy_path"]).stem == effect["id"]
                ),
                None,
            )
        if asset is None:
            raise RuntimeError(f"Missing exact proxy declaration for {effect['id']}")
        output = Path(asset["proxy_path"])
        if output.is_file() and not force:
            status = "reused"
        else:
            previous = load_segment(shots[index], width, height, fps)
            incoming = load_segment(shots[index + 1], width, height, fps)
            count = max(1, round(float(effect["duration"]) * fps))
            frames = render_effect_transition(
                previous,
                incoming,
                count,
                effect["effect_id"],
                effect["principle"],
            )
            _write_video(frames, fps, ffmpeg, output)
            status = "created"
        results.append({"kind": "exact_effect_proxy", "effect_id": effect["id"], "path": str(output), "status": status})

    return {
        "schema": "supercut-chatcut-proxy-report-v1",
        "plan_revision": plan["plan_revision"],
        "review": {"width": width, "height": height, "fps": fps},
        "items": results,
        "all_ready": bool(results) and all(Path(row["path"]).is_file() for row in results),
    }


def save_proxy_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
