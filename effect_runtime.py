"""Template-driven transition runtime for every effect in the SuperCut catalog.

The historic project contains a mixture of specialised preview scripts and
catalog-only effect descriptions.  This module is the common execution floor:
every catalog principle maps to a deterministic OpenCV renderer.  Specialised
renderers may replace the generic result later, but planning never has to emit
an effect that the main timeline cannot execute.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import cv2
import numpy as np


Frame = np.ndarray
Handler = Callable[[Frame, Frame, float, str, np.random.Generator], Frame]


SUPPORTED_PRINCIPLES = {
    "foreground_retention",
    "mask_reveal",
    "spatial_transform",
    "fragmentation",
    "digital_glitch",
    "time_manipulation",
    "exposure_luma",
    "rhythm_layout",
    "tactile_reveal",
    "organic_matte",
    "optical_geometry",
    "perspective_motion",
    "editorial_masking",
    "graphic_composition",
}


def _blend(before: Frame, after: Frame, progress: float) -> Frame:
    return cv2.addWeighted(before, 1.0 - progress, after, progress, 0)


def _shift(frame: Frame, dx: float = 0.0, dy: float = 0.0) -> Frame:
    height, width = frame.shape[:2]
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(frame, matrix, (width, height), borderMode=cv2.BORDER_REFLECT_101)


def _zoom(frame: Frame, factor: float) -> Frame:
    height, width = frame.shape[:2]
    factor = max(0.2, factor)
    scaled = cv2.resize(frame, None, fx=factor, fy=factor, interpolation=cv2.INTER_LINEAR)
    scaled_h, scaled_w = scaled.shape[:2]
    if factor >= 1.0:
        x = max(0, (scaled_w - width) // 2)
        y = max(0, (scaled_h - height) // 2)
        return scaled[y : y + height, x : x + width]
    canvas = np.zeros_like(frame)
    x = (width - scaled_w) // 2
    y = (height - scaled_h) // 2
    canvas[y : y + scaled_h, x : x + scaled_w] = scaled
    return canvas


def _soft_mask(mask: np.ndarray, radius: int = 17) -> np.ndarray:
    radius = max(3, radius | 1)
    return cv2.GaussianBlur(mask.astype(np.float32), (radius, radius), 0)


def _composite(before: Frame, after: Frame, mask: np.ndarray) -> Frame:
    alpha = np.clip(mask, 0.0, 1.0)[..., None]
    return np.clip(before.astype(np.float32) * (1.0 - alpha) + after.astype(np.float32) * alpha, 0, 255).astype(np.uint8)


def _foreground_retention(before: Frame, after: Frame, progress: float, effect_id: str, rng: np.random.Generator) -> Frame:
    height, width = before.shape[:2]
    background = _blend(_shift(before, -progress * width * 0.12), _shift(after, (1.0 - progress) * width * 0.12), progress)
    yy, xx = np.mgrid[0:height, 0:width]
    center_x = width * (0.62 if "weapon" in effect_id else 0.5)
    center_y = height * (0.72 if "weapon" in effect_id else 0.55)
    radius_x = width * (0.43 if "weapon" in effect_id else 0.28)
    radius_y = height * (0.38 if "weapon" in effect_id else 0.34)
    mask = (((xx - center_x) / radius_x) ** 2 + ((yy - center_y) / radius_y) ** 2 <= 1.0).astype(np.float32)
    mask = _soft_mask(mask, 25)
    hold = 1.0 if progress < 0.58 else max(0.0, 1.0 - (progress - 0.58) / 0.42)
    result = _composite(background, before, mask * hold)
    if "glow" in effect_id:
        edge = cv2.Laplacian((mask * 255).astype(np.uint8), cv2.CV_32F)
        glow = cv2.GaussianBlur(np.abs(edge), (31, 31), 0)
        result[:, :, 0] = np.clip(result[:, :, 0].astype(np.float32) + glow * 0.35, 0, 255)
        result[:, :, 2] = np.clip(result[:, :, 2].astype(np.float32) + glow * 0.75, 0, 255)
    return result


def _mask_reveal(before: Frame, after: Frame, progress: float, effect_id: str, rng: np.random.Generator) -> Frame:
    height, width = before.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width]
    if "eye" in effect_id:
        aperture = max(0.01, math.sin(progress * math.pi / 2))
        mask = (np.abs(yy - height / 2) <= height * 0.5 * aperture * (0.35 + 0.65 * np.sin(xx / width * math.pi))).astype(np.float32)
    elif "mirror" in effect_id:
        mask = ((xx + yy * 0.55) / (width + height * 0.55) <= progress).astype(np.float32)
    elif "luma" in effect_id:
        luma = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        mask = (luma >= 1.0 - progress).astype(np.float32)
    else:
        distance = np.sqrt(((xx - width / 2) / width) ** 2 + ((yy - height / 2) / height) ** 2)
        mask = (distance <= progress * 0.72).astype(np.float32)
    return _composite(before, after, _soft_mask(mask, 19))


def _spatial_transform(before: Frame, after: Frame, progress: float, effect_id: str, rng: np.random.Generator) -> Frame:
    height, width = before.shape[:2]
    eased = 1.0 - (1.0 - progress) ** 3
    if "camera_sweep" in effect_id:
        left = _shift(before, -eased * width)
        right = _shift(after, (1.0 - eased) * width)
        return _blend(cv2.blur(left, (31, 1)), cv2.blur(right, (31, 1)), progress)
    if "screen_tunnel" in effect_id:
        outer = _zoom(before, 1.0 + progress * 1.3)
        inner = _zoom(after, 0.35 + progress * 0.65)
        return _blend(outer, inner, eased)
    overshoot = 1.0 + 0.22 * math.sin(progress * math.pi)
    return _blend(_zoom(before, overshoot), _zoom(after, 1.22 - 0.22 * eased), eased)


def _fragmentation(before: Frame, after: Frame, progress: float, effect_id: str, rng: np.random.Generator) -> Frame:
    height, width = before.shape[:2]
    rows, columns = (5, 8) if "edge" in effect_id else (7, 9)
    result = after.copy()
    tile_h, tile_w = math.ceil(height / rows), math.ceil(width / columns)
    for row in range(rows):
        for column in range(columns):
            order = ((row * columns + column) * 37 + 11) % (rows * columns)
            threshold = order / max(1, rows * columns - 1)
            if progress < threshold:
                y0, y1 = row * tile_h, min(height, (row + 1) * tile_h)
                x0, x1 = column * tile_w, min(width, (column + 1) * tile_w)
                dx = int((column - columns / 2) * progress * tile_w * 0.35)
                dy = int((row - rows / 2) * progress * tile_h * 0.35)
                patch = before[y0:y1, x0:x1]
                target_y = min(max(0, y0 + dy), height - patch.shape[0])
                target_x = min(max(0, x0 + dx), width - patch.shape[1])
                result[target_y : target_y + patch.shape[0], target_x : target_x + patch.shape[1]] = patch
    return result


def _digital_glitch(before: Frame, after: Frame, progress: float, effect_id: str, rng: np.random.Generator) -> Frame:
    base = _blend(before, after, progress)
    height, width = base.shape[:2]
    strength = math.sin(progress * math.pi)
    result = base.copy()
    bands = 14
    for band in range(bands):
        y0 = band * height // bands
        y1 = (band + 1) * height // bands
        dx = int(rng.integers(-1, 2) * width * 0.045 * strength)
        result[y0:y1] = np.roll(result[y0:y1], dx, axis=1)
    channel_shift = max(1, int(width * 0.012 * strength))
    result[:, :, 2] = np.roll(result[:, :, 2], channel_shift, axis=1)
    result[:, :, 0] = np.roll(result[:, :, 0], -channel_shift, axis=1)
    if "film" in effect_id:
        noise = rng.normal(0, 18 * strength, result.shape).astype(np.float32)
        result = np.clip(result.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    return result


def _time_manipulation(before: Frame, after: Frame, progress: float, effect_id: str, rng: np.random.Generator) -> Frame:
    if "reverse" in effect_id:
        phase = 0.5 - 0.5 * math.cos(progress * math.pi)
        result = _blend(before, after, phase)
        return np.roll(result, int(math.sin(progress * math.pi * 6) * result.shape[1] * 0.018), axis=1)
    if "memory" in effect_id:
        flash = 0.38 * max(0.0, math.sin(progress * math.pi * 8))
        result = _blend(before, after, progress)
        return cv2.addWeighted(result, 1.0 - flash, np.full_like(result, 255), flash, 0)
    # Speed-ramp and freeze-frame effects share a visual hold-and-release cue.
    held = before if progress < 0.42 else after
    mix = 0.0 if progress < 0.42 else min(1.0, (progress - 0.42) / 0.58)
    return _blend(held, after, mix)


def _exposure_luma(before: Frame, after: Frame, progress: float, effect_id: str, rng: np.random.Generator) -> Frame:
    base = _blend(before, after, progress)
    peak = math.sin(progress * math.pi)
    if "shockwave" in effect_id:
        height, width = base.shape[:2]
        yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
        cx, cy = width / 2, height / 2
        dx, dy = xx - cx, yy - cy
        radius = np.sqrt(dx * dx + dy * dy) + 1e-6
        displacement = np.exp(-((radius / max(width, height) - progress * 0.55) ** 2) / 0.002) * 14 * peak
        map_x = (xx + dx / radius * displacement).astype(np.float32)
        map_y = (yy + dy / radius * displacement).astype(np.float32)
        base = cv2.remap(base, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
    return cv2.addWeighted(base, 1.0 - peak * 0.72, np.full_like(base, 255), peak * 0.72, 0)


def _rhythm_layout(before: Frame, after: Frame, progress: float, effect_id: str, rng: np.random.Generator) -> Frame:
    height, width = before.shape[:2]
    result = np.full_like(before, 12)
    if "split" in effect_id or "multiview" in effect_id:
        split = width // 2
        result[:, :split] = cv2.resize(before, (split, height))
        result[:, split:] = cv2.resize(after, (width - split, height))
        divider = max(2, width // 240)
        result[:, split - divider : split + divider] = 245
        return _blend(before, result, min(1.0, progress * 1.8)) if progress < 0.55 else _blend(result, after, (progress - 0.55) / 0.45)
    rows, columns = (3, 3) if "nine" in effect_id else (2, 3)
    for row in range(rows):
        for column in range(columns):
            source = before if (row + column + int(progress * 8)) % 2 == 0 else after
            y0, y1 = row * height // rows, (row + 1) * height // rows
            x0, x1 = column * width // columns, (column + 1) * width // columns
            result[y0:y1, x0:x1] = cv2.resize(source, (x1 - x0, y1 - y0))
    return _blend(result, after, max(0.0, (progress - 0.65) / 0.35))


def _tactile_reveal(before: Frame, after: Frame, progress: float, effect_id: str, rng: np.random.Generator) -> Frame:
    height, width = before.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width]
    if "page_fold" in effect_id:
        boundary = width * (1.0 - progress)
        mask = (xx >= boundary).astype(np.float32)
        result = _composite(before, after, _soft_mask(mask, 15))
        shadow = np.exp(-((xx - boundary) ** 2) / max(1.0, (width * 0.018) ** 2)) * 95
        return np.clip(result.astype(np.float32) - shadow[..., None], 0, 255).astype(np.uint8)
    noise = rng.random((height, width), dtype=np.float32)
    if "paper" in effect_id:
        edge = progress + 0.08 * np.sin(yy / max(1, height) * math.pi * 12)
        mask = (xx / max(1, width) <= edge).astype(np.float32)
    else:
        mask = (noise <= progress).astype(np.float32)
    result = _composite(before, after, _soft_mask(mask, 11))
    if "burn" in effect_id:
        rim = cv2.morphologyEx((mask * 255).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((9, 9), np.uint8))
        result[:, :, 2] = np.clip(result[:, :, 2].astype(np.float32) + rim * 0.8, 0, 255)
    return result


def _organic_matte(before: Frame, after: Frame, progress: float, effect_id: str, rng: np.random.Generator) -> Frame:
    height, width = before.shape[:2]
    low = rng.random((max(2, height // 18), max(2, width // 18)), dtype=np.float32)
    field = cv2.resize(low, (width, height), interpolation=cv2.INTER_CUBIC)
    if "ink" in effect_id:
        yy, xx = np.mgrid[0:height, 0:width]
        radial = np.sqrt(((xx - width * 0.48) / width) ** 2 + ((yy - height * 0.52) / height) ** 2)
        field = 0.52 * field + 0.48 * (1.0 - radial)
    threshold = np.quantile(field, max(0.0, 1.0 - progress))
    mask = (field >= threshold).astype(np.float32)
    return _composite(before, after, _soft_mask(mask, 21))


def _optical_geometry(before: Frame, after: Frame, progress: float, effect_id: str, rng: np.random.Generator) -> Frame:
    if "water" in effect_id:
        base = _blend(before, after, progress)
        height, width = base.shape[:2]
        reflected = cv2.flip(base, 0)
        for y in range(height // 2, height):
            reflected[y] = np.roll(reflected[y], int(math.sin(y * 0.09 + progress * 15) * 10 * math.sin(progress * math.pi)), axis=0)
        base[height // 2 :] = _blend(base[height // 2 :], reflected[height // 2 :], 0.52)
        return base
    base = _blend(before, after, progress)
    height, width = base.shape[:2]
    center = (width // 2, height // 2)
    matrix = cv2.getRotationMatrix2D(center, progress * 135, 1.0 + 0.18 * math.sin(progress * math.pi))
    rotated = cv2.warpAffine(base, matrix, (width, height), borderMode=cv2.BORDER_REFLECT_101)
    left = rotated[:, : width // 2]
    rotated[:, width - left.shape[1] :] = cv2.flip(left, 1)
    return rotated


def _perspective_motion(before: Frame, after: Frame, progress: float, effect_id: str, rng: np.random.Generator) -> Frame:
    before_zoom = _zoom(before, 1.0 + 0.18 * progress)
    after_zoom = _zoom(after, 1.18 - 0.18 * progress)
    result = _blend(before_zoom, after_zoom, progress)
    height, width = result.shape[:2]
    vignette_x = cv2.getGaussianKernel(width, width / 2.8)
    vignette_y = cv2.getGaussianKernel(height, height / 2.8)
    vignette = vignette_y @ vignette_x.T
    vignette /= max(float(vignette.max()), 1e-6)
    strength = 0.22 * math.sin(progress * math.pi)
    return np.clip(result.astype(np.float32) * (1.0 - strength + strength * vignette[..., None]), 0, 255).astype(np.uint8)


def _editorial_masking(before: Frame, after: Frame, progress: float, effect_id: str, rng: np.random.Generator) -> Frame:
    height, width = before.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width]
    boundary = progress * (width * 1.35) - width * 0.18 + np.sin(yy / max(1, height) * math.pi * 4) * width * 0.035
    mask = (xx <= boundary).astype(np.float32)
    return _composite(before, after, _soft_mask(mask, 17))


def _graphic_composition(before: Frame, after: Frame, progress: float, effect_id: str, rng: np.random.Generator) -> Frame:
    height, width = before.shape[:2]
    if "vinyl" in effect_id:
        result = np.zeros_like(before)
        radius = int(min(width, height) * (0.18 + progress * 0.28))
        yy, xx = np.mgrid[0:height, 0:width]
        mask = (((xx - width / 2) ** 2 + (yy - height / 2) ** 2) <= radius * radius).astype(np.float32)
        disc = _zoom(after, 0.55 + progress * 0.45)
        return _composite(result, disc, _soft_mask(mask, 9))
    if "freeze" in effect_id:
        result = _blend(before, after, progress)
        gray = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY)
        edge = cv2.Canny(gray, 80, 180)
        edge = cv2.dilate(edge, np.ones((3, 3), np.uint8))
        result[edge > 0] = (235, 245, 255)
        return result
    if "typography" in effect_id:
        result = _blend(before, after, progress)
        text = "CUT"
        scale = max(0.8, width / 720 * (1.0 + 0.35 * math.sin(progress * math.pi)))
        thickness = max(2, round(scale * 3))
        size, _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, thickness)
        origin = ((width - size[0]) // 2, (height + size[1]) // 2)
        cv2.putText(result, text, origin, cv2.FONT_HERSHEY_DUPLEX, scale, (245, 245, 245), thickness, cv2.LINE_AA)
        return result
    if "card" in effect_id:
        angle = max(0.05, abs(math.cos(progress * math.pi)))
        card = cv2.resize(after if progress >= 0.5 else before, (max(1, int(width * angle)), height))
        result = np.full_like(before, 18)
        x = (width - card.shape[1]) // 2
        result[:, x : x + card.shape[1]] = card
        return result
    if "manga" in effect_id or "photo" in effect_id:
        return _rhythm_layout(before, after, progress, effect_id, rng)
    return _blend(before, after, progress)


HANDLERS: dict[str, Handler] = {
    "foreground_retention": _foreground_retention,
    "mask_reveal": _mask_reveal,
    "spatial_transform": _spatial_transform,
    "fragmentation": _fragmentation,
    "digital_glitch": _digital_glitch,
    "time_manipulation": _time_manipulation,
    "exposure_luma": _exposure_luma,
    "rhythm_layout": _rhythm_layout,
    "tactile_reveal": _tactile_reveal,
    "organic_matte": _organic_matte,
    "optical_geometry": _optical_geometry,
    "perspective_motion": _perspective_motion,
    "editorial_masking": _editorial_masking,
    "graphic_composition": _graphic_composition,
}


def build_effect_registry(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return executable metadata for every supported catalog entry."""
    registry: dict[str, dict[str, Any]] = {}
    for effect in catalog.get("effects", []):
        principle = effect.get("principle", "")
        if principle not in HANDLERS:
            continue
        implementation = effect.get("implementation", {})
        registry[effect["effect_id"]] = {
            "effect_id": effect["effect_id"],
            "principle": principle,
            "execution_tier": implementation.get("runtime_status") or (
                "specialized" if implementation.get("status") == "implemented" else "generic"
            ),
            "renderer_path": implementation.get("renderer_path"),
            "template_path": implementation.get("template_path"),
        }
    return registry


def render_effect_transition(
    previous: list[Frame],
    incoming: list[Frame],
    count: int,
    effect_id: str,
    principle: str,
    seed: int = 7,
) -> list[Frame]:
    """Render a transition with exact frame count and deterministic output."""
    count = min(max(0, int(count)), len(previous), len(incoming))
    if count <= 0:
        return []
    if principle not in HANDLERS:
        raise ValueError(f"Unsupported effect principle: {principle}")
    handler = HANDLERS[principle]
    result: list[Frame] = []
    for index in range(count):
        progress = (index + 1) / (count + 1)
        rng = np.random.default_rng(seed + index * 1009)
        before = previous[-count + index]
        after = incoming[index]
        if before.shape != after.shape:
            after = cv2.resize(after, (before.shape[1], before.shape[0]), interpolation=cv2.INTER_AREA)
        result.append(handler(before, after, progress, effect_id, rng))
    return result
