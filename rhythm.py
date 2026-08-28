"""Deterministic local rhythm analysis and edit-point alignment for SuperCut.

Observed waveform attacks and inferred musical grids are deliberately stored as
different event classes.  An inferred BPM grid must never be reported as a
measured drum hit.
"""

from __future__ import annotations

import bisect
import copy
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np


RHYTHM_SCHEMA = "supercut-beat-map-v2"
BAND_RANGES = {
    "low": (30.0, 180.0),
    "mid": (150.0, 2500.0),
    "high": (2000.0, 8000.0),
    "air": (6000.0, 12000.0),
}
SYNC_STRENGTH = {"subtle": 0.75, "balanced": 0.52, "intense": 0.32}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_audio(ffmpeg: str, source: Path, sample_rate: int = 22050) -> np.ndarray:
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "f32le",
            "-",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return np.frombuffer(result.stdout, dtype="<f4").copy()


def _robust_normalize(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if not values.size:
        return values
    low, high = np.percentile(values, [10, 95])
    if high <= low + 1e-9:
        return np.zeros_like(values)
    return np.clip((values - low) / (high - low), 0.0, 1.0)


def _frame_features(
    samples: np.ndarray,
    sample_rate: int,
    frame_size: int = 1024,
    hop: int = 256,
) -> tuple[dict[str, np.ndarray], np.ndarray, float]:
    if samples.size < frame_size:
        samples = np.pad(samples, (0, frame_size - samples.size))
    frame_count = 1 + (samples.size - frame_size) // hop
    frequencies = np.fft.rfftfreq(frame_size, 1.0 / sample_rate)
    masks = {
        name: (frequencies >= low) & (frequencies < min(high, sample_rate / 2.0))
        for name, (low, high) in BAND_RANGES.items()
    }
    flux = {name: np.zeros(frame_count, dtype=np.float32) for name in BAND_RANGES}
    rms = np.zeros(frame_count, dtype=np.float32)
    window = np.hanning(frame_size).astype(np.float32)
    previous: np.ndarray | None = None
    for index in range(frame_count):
        frame = samples[index * hop : index * hop + frame_size]
        spectrum = np.abs(np.fft.rfft(frame * window)).astype(np.float32)
        rms[index] = float(np.sqrt(np.mean(frame * frame) + 1e-12))
        if previous is not None:
            positive = np.maximum(spectrum - previous, 0.0)
            for name, mask in masks.items():
                if np.any(mask):
                    flux[name][index] = float(np.mean(positive[mask]))
        previous = spectrum
    kernel = np.ones(3, dtype=np.float32) / 3.0
    normalized = {
        name: _robust_normalize(np.convolve(values, kernel, mode="same"))
        for name, values in flux.items()
    }
    return normalized, _robust_normalize(np.log1p(rms * 100.0)), hop / sample_rate


def _adaptive_threshold(values: np.ndarray, radius: int = 18) -> np.ndarray:
    if values.size < 3:
        return np.full_like(values, 1.0)
    padded = np.pad(values, (radius, radius), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, radius * 2 + 1)
    median = np.median(windows, axis=1)
    deviation = np.median(np.abs(windows - median[:, None]), axis=1)
    return np.clip(median + np.maximum(0.055, 1.6 * deviation), 0.0, 1.0)


def _classify_onset(bands: dict[str, np.ndarray], index: int) -> tuple[str, dict[str, float]]:
    values = {name: float(series[index]) for name, series in bands.items()}
    low, mid, high, air = values["low"], values["mid"], values["high"], values["air"]
    if low >= 0.36 and low >= mid * 1.08 and low >= high:
        kind = "kick"
    elif mid >= 0.36 and mid >= low * 0.88 and mid >= high * 0.88:
        kind = "snare_or_clap"
    elif max(high, air) >= 0.34 and max(high, air) >= mid:
        kind = "high_percussion"
    else:
        kind = "broadband_transient"
    return kind, {name: round(value, 4) for name, value in values.items()}


def _detect_onsets(
    envelope: np.ndarray,
    bands: dict[str, np.ndarray],
    hop_seconds: float,
) -> list[dict[str, Any]]:
    if envelope.size < 3:
        return []
    threshold = _adaptive_threshold(envelope)
    candidates = [
        index
        for index in range(1, envelope.size - 1)
        if envelope[index] >= 0.12
        and envelope[index] >= threshold[index]
        and envelope[index] >= envelope[index - 1]
        and envelope[index] > envelope[index + 1]
    ]
    min_gap = max(1, round(0.075 / hop_seconds))
    selected: list[int] = []
    for index in sorted(candidates, key=lambda value: float(envelope[value]), reverse=True):
        if all(abs(index - kept) >= min_gap for kept in selected):
            selected.append(index)
    selected.sort()
    events: list[dict[str, Any]] = []
    for number, index in enumerate(selected, start=1):
        kind, band_values = _classify_onset(bands, index)
        prominence = max(band_values.values())
        strength = float(envelope[index])
        events.append(
            {
                "id": f"onset-{number:05d}",
                "time": round(index * hop_seconds, 6),
                "type": kind,
                "observed": True,
                "strength": round(strength, 4),
                "confidence": round(min(1.0, 0.55 * strength + 0.45 * prominence), 4),
                "bands": band_values,
            }
        )
    return events


def _tempo_candidates(envelope: np.ndarray, hop_seconds: float) -> list[dict[str, float]]:
    if envelope.size < 16:
        return []
    centered = envelope.astype(np.float64) - float(np.mean(envelope))
    min_lag = max(1, round((60.0 / 200.0) / hop_seconds))
    max_lag = min(len(centered) - 1, round((60.0 / 60.0) / hop_seconds))
    candidates: list[tuple[float, float, int]] = []
    for lag in range(min_lag, max_lag + 1):
        left, right = centered[:-lag], centered[lag:]
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        correlation = float(np.dot(left, right) / denominator) if denominator > 1e-9 else 0.0
        bpm = 60.0 / (lag * hop_seconds)
        candidates.append((correlation, bpm, lag))
    selected: list[dict[str, float]] = []
    for score, bpm, lag in sorted(candidates, reverse=True):
        if all(abs(bpm - row["bpm"]) >= 4.0 for row in selected):
            selected.append({"bpm": round(bpm, 4), "confidence": round(max(0.0, score), 4), "lag": lag})
        if len(selected) == 4:
            break
    return selected


def _track_beats(
    envelope: np.ndarray,
    onsets: list[dict[str, Any]],
    hop_seconds: float,
    duration: float,
) -> tuple[list[dict[str, Any]], list[dict[str, float]], float, float]:
    candidates = _tempo_candidates(envelope, hop_seconds)
    if not candidates:
        return [], [], 0.0, 0.0
    best: tuple[float, dict[str, float], int] | None = None
    for candidate in candidates:
        lag = int(candidate["lag"])
        phase_scores = [float(np.mean(envelope[phase::lag])) for phase in range(lag)]
        phase = int(np.argmax(phase_scores))
        # Prefer an edit-useful mid-tempo interpretation when half/double-time
        # candidates have effectively equal waveform support.
        tempo_bias = 1.06 if 90.0 <= float(candidate["bpm"]) <= 160.0 else 1.0
        score = float(candidate["confidence"]) * (0.6 + phase_scores[phase]) * tempo_bias
        if best is None or score > best[0]:
            best = (score, candidate, phase)
    assert best is not None
    _, selected, phase = best
    bpm = float(selected["bpm"])
    period = 60.0 / bpm
    phase_seconds = phase * hop_seconds
    onset_times = [float(event["time"]) for event in onsets]
    beats: list[dict[str, Any]] = []
    index = 0
    time_value = phase_seconds
    while time_value <= duration + 1e-9:
        position = min(len(envelope) - 1, max(0, round(time_value / hop_seconds)))
        local = envelope[max(0, position - 2) : min(len(envelope), position + 3)]
        strength = float(np.max(local)) if local.size else 0.0
        nearest: dict[str, Any] | None = None
        if onset_times:
            location = bisect.bisect_left(onset_times, time_value)
            choices = [value for value in (location - 1, location) if 0 <= value < len(onsets)]
            if choices:
                nearest = min((onsets[value] for value in choices), key=lambda event: abs(float(event["time"]) - time_value))
                if abs(float(nearest["time"]) - time_value) > min(0.09, period * 0.2):
                    nearest = None
        beats.append(
            {
                "id": f"beat-{index + 1:05d}",
                "index": index + 1,
                "time": round(time_value, 6),
                "observed": False,
                "inference": "tempo_phase_grid",
                "strength": round(strength, 4),
                "observed_onset_id": nearest["id"] if nearest else None,
                "observed_onset_time": nearest["time"] if nearest else None,
            }
        )
        index += 1
        time_value = phase_seconds + index * period

    phase_energy = []
    for bar_phase in range(4):
        values = [float(beat["strength"]) for beat in beats[bar_phase::4]]
        phase_energy.append(float(np.mean(values)) if values else 0.0)
    downbeat_phase = int(np.argmax(phase_energy)) if phase_energy else 0
    ordered = sorted(phase_energy, reverse=True)
    downbeat_confidence = 0.0
    if ordered and ordered[0] > 1e-9:
        downbeat_confidence = max(0.0, min(1.0, (ordered[0] - (ordered[1] if len(ordered) > 1 else 0.0)) / ordered[0]))
    for beat_index, beat in enumerate(beats):
        beat["beat_in_bar"] = ((beat_index - downbeat_phase) % 4) + 1
        beat["downbeat_candidate"] = beat["beat_in_bar"] == 1
        beat["downbeat_confidence"] = round(downbeat_confidence, 4)
    clean_candidates = [{key: value for key, value in row.items() if key != "lag"} for row in candidates]
    return beats, clean_candidates, bpm, downbeat_confidence


def _strong_anchors(onsets: list[dict[str, Any]], beats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not onsets:
        return []
    strengths = np.asarray([float(event["strength"]) for event in onsets], dtype=np.float32)
    threshold = max(0.45, float(np.percentile(strengths, 65)))
    beat_by_onset = {beat.get("observed_onset_id"): beat for beat in beats if beat.get("observed_onset_id")}
    anchors: list[dict[str, Any]] = []
    for event in onsets:
        if float(event["strength"]) < threshold:
            continue
        beat = beat_by_onset.get(event["id"])
        downbeat = bool(beat and beat.get("downbeat_candidate"))
        anchors.append(
            {
                "id": f"anchor-{len(anchors) + 1:05d}",
                "time": event["time"],
                "source_event_id": event["id"],
                "source": "observed_onset",
                "type": event["type"],
                "strength": event["strength"],
                "confidence": event["confidence"],
                "near_inferred_beat": beat is not None,
                "downbeat_candidate": downbeat,
                "downbeat_confidence": beat.get("downbeat_confidence", 0.0) if beat else 0.0,
            }
        )
    return anchors


def _silence_ranges(rms: np.ndarray, hop_seconds: float, duration: float) -> list[dict[str, float]]:
    quiet = rms <= 0.035
    minimum = max(1, round(0.35 / hop_seconds))
    ranges: list[dict[str, float]] = []
    start: int | None = None
    for index, value in enumerate(np.append(quiet, False)):
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start >= minimum:
                ranges.append({"start": round(start * hop_seconds, 6), "end": round(min(duration, index * hop_seconds), 6)})
            start = None
    return ranges


def _sections(rms: np.ndarray, hop_seconds: float, duration: float) -> list[dict[str, Any]]:
    if not rms.size or duration <= 0:
        return []
    window = max(1, round(0.5 / hop_seconds))
    energy = np.asarray([float(np.mean(rms[index : index + window])) for index in range(0, len(rms), window)])
    if not energy.size:
        return []
    smoothed = np.convolve(energy, np.ones(5) / 5.0, mode="same") if energy.size >= 5 else energy
    novelty = np.abs(np.diff(smoothed, prepend=smoothed[0]))
    boundaries = [0.0]
    for index in np.argsort(novelty)[::-1]:
        time_value = float(index * 0.5)
        if 3.0 <= time_value <= duration - 3.0 and all(abs(time_value - value) >= 4.0 for value in boundaries):
            boundaries.append(time_value)
        if len(boundaries) >= 8:
            break
    boundaries = sorted(boundaries) + [duration]
    result: list[dict[str, Any]] = []
    global_high = max(float(np.percentile(smoothed, 70)), 1e-9)
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        lo, hi = int(start / 0.5), max(int(math.ceil(end / 0.5)), int(start / 0.5) + 1)
        segment = smoothed[lo:hi]
        mean_energy = float(np.mean(segment)) if segment.size else 0.0
        if index == 0:
            label = "intro"
        elif index == len(boundaries) - 2:
            label = "release"
        elif mean_energy >= global_high:
            label = "climax"
        elif segment.size > 1 and float(segment[-1]) > float(segment[0]) * 1.08:
            label = "rise"
        else:
            label = "sustain"
        result.append({"index": index + 1, "start": round(start, 6), "end": round(end, 6), "label": label, "energy": round(mean_energy, 4)})
    return result


def _apply_timeline_offset(result: dict[str, Any], offset_seconds: float) -> None:
    result["source"]["timeline_offset_seconds"] = round(float(offset_seconds), 6)
    for key in ("observed_onsets", "inferred_beats", "strong_anchors"):
        for event in result.get(key, []):
            event["timeline_time"] = round(float(event["time"]) - offset_seconds, 6)
    for key in ("sections", "silence_ranges"):
        for event in result.get(key, []):
            event["timeline_start"] = round(float(event["start"]) - offset_seconds, 6)
            event["timeline_end"] = round(float(event["end"]) - offset_seconds, 6)


def analyze_samples(
    samples: np.ndarray,
    sample_rate: int,
    *,
    source_id: str = "in-memory",
    timeline_offset_seconds: float = 0.0,
) -> dict[str, Any]:
    samples = np.asarray(samples, dtype=np.float32)
    duration = samples.size / float(sample_rate) if sample_rate else 0.0
    bands, rms, hop_seconds = _frame_features(samples, sample_rate)
    envelope = _robust_normalize(
        0.38 * bands["low"] + 0.34 * bands["mid"] + 0.20 * bands["high"] + 0.08 * bands["air"]
    )
    onsets = _detect_onsets(envelope, bands, hop_seconds)
    beats, candidates, bpm, downbeat_confidence = _track_beats(envelope, onsets, hop_seconds, duration)
    anchors = _strong_anchors(onsets, beats)
    result = {
        "schema": RHYTHM_SCHEMA,
        "status": "complete" if onsets else "needs_input",
        "source": {
            "path": source_id,
            "sha256": None,
            "duration": round(duration, 6),
        },
        "analysis": {
            "method": "multiband_spectral_flux_v1",
            "sample_rate": sample_rate,
            "hop_seconds": round(hop_seconds, 9),
            "estimated_bpm": round(bpm, 4) if bpm else None,
            "tempo_confidence": candidates[0]["confidence"] if candidates else 0.0,
            "tempo_candidates": candidates,
            "meter_assumption": 4,
            "downbeat_method": "inferred_four_phase_energy",
            "downbeat_confidence": round(downbeat_confidence, 4),
            "observed_onset_count": len(onsets),
            "strong_anchor_count": len(anchors),
        },
        "observed_onsets": onsets,
        "inferred_beats": beats,
        "strong_anchors": anchors,
        "sections": _sections(rms, hop_seconds, duration),
        "silence_ranges": _silence_ranges(rms, hop_seconds, duration),
        "notes": [
            "Observed onsets come from the decoded waveform; inferred beats come from tempo/phase estimation.",
            "Percussion labels and downbeats are heuristic and retain explicit confidence values.",
        ],
    }
    _apply_timeline_offset(result, timeline_offset_seconds)
    return result


def analyze_rhythm(
    source: Path,
    ffmpeg: str,
    *,
    timeline_offset_seconds: float = 0.0,
    sample_rate: int = 22050,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Music file does not exist: {source}")
    result = analyze_samples(
        decode_audio(ffmpeg, source, sample_rate),
        sample_rate,
        source_id=str(source),
        timeline_offset_seconds=timeline_offset_seconds,
    )
    result["source"]["sha256"] = _sha256(source)
    return result


def _fingerprint(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, float]:
    bands, rms, hop_seconds = _frame_features(samples, sample_rate, frame_size=1024, hop=max(1, round(sample_rate * 0.02)))
    channels = np.stack([bands["low"], bands["mid"], bands["high"], rms], axis=0).astype(np.float64)
    channels -= np.mean(channels, axis=1, keepdims=True)
    scale = np.std(channels, axis=1, keepdims=True)
    channels /= np.where(scale > 1e-9, scale, 1.0)
    return channels, hop_seconds


def estimate_audio_offset(reference: Path, music: Path, ffmpeg: str, sample_rate: int = 8000) -> dict[str, Any]:
    reference_samples = decode_audio(ffmpeg, reference.expanduser().resolve(), sample_rate)
    music_samples = decode_audio(ffmpeg, music.expanduser().resolve(), sample_rate)
    reference_features, hop_seconds = _fingerprint(reference_samples, sample_rate)
    music_features, _ = _fingerprint(music_samples, sample_rate)
    length = min(reference_features.shape[1], music_features.shape[1])
    if length < 16:
        return {"status": "needs_input", "offset_seconds": 0.0, "normalized_multiband_correlation": 0.0}
    reference_features = reference_features[:, :length]
    if music_features.shape[1] < length:
        reference_features, music_features = music_features[:, :length], reference_features
    valid_count = music_features.shape[1] - reference_features.shape[1] + 1
    score = np.zeros(valid_count, dtype=np.float64)
    for channel in range(reference_features.shape[0]):
        ref = reference_features[channel]
        src = music_features[channel]
        correlation = np.correlate(src, ref, mode="valid")
        squared = np.concatenate(([0.0], np.cumsum(src * src)))
        local_norm = np.sqrt(np.maximum(1e-9, squared[len(ref):] - squared[:-len(ref)]))
        ref_norm = max(1e-9, float(np.linalg.norm(ref)))
        score += correlation / (local_norm * ref_norm)
    score /= reference_features.shape[0]
    best = int(np.argmax(score))
    confidence = float(np.clip(score[best], -1.0, 1.0))
    return {
        "status": "complete" if confidence >= 0.35 else "needs_input",
        "offset_seconds": round(best * hop_seconds, 6),
        "time_scale": 1.0,
        "normalized_multiband_correlation": round(confidence, 6),
        "same_source_confident": confidence >= 0.55,
    }


def write_rhythm_graph(path: Path, rhythm_map: dict[str, Any], width: int = 1600, height: int = 420) -> None:
    import cv2

    canvas = np.full((height, width, 3), 248, dtype=np.uint8)
    duration = max(0.001, float(rhythm_map.get("source", {}).get("duration", 0.0)))
    cv2.line(canvas, (40, height // 2), (width - 20, height // 2), (205, 202, 194), 1)
    for onset in rhythm_map.get("observed_onsets", []):
        x = 40 + round(float(onset["time"]) / duration * (width - 60))
        strength = float(onset.get("strength", 0.0))
        y = round((height - 90) * min(1.0, strength))
        color = (216, 84, 84) if onset.get("type") == "kick" else (84, 84, 216)
        cv2.line(canvas, (x, height // 2 - y // 2), (x, height // 2 + y // 2), color, 1)
    for anchor in rhythm_map.get("strong_anchors", []):
        x = 40 + round(float(anchor["time"]) / duration * (width - 60))
        cv2.circle(canvas, (x, 34), 3, (84, 84, 216), -1)
    bpm = rhythm_map.get("analysis", {}).get("estimated_bpm")
    cv2.putText(canvas, f"Observed onset waveform | inferred BPM {bpm or 'n/a'}", (40, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (30, 30, 27), 1, cv2.LINE_AA)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix or ".png", canvas)
    if not ok:
        raise RuntimeError(f"Cannot encode rhythm graph: {path}")
    encoded.tofile(str(path))


def save_beat_map(path: Path, rhythm_map: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rhythm_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _transition_duration(shots: list[dict[str, Any]], index: int) -> float:
    if index >= len(shots) - 1 or shots[index].get("transition_after", "hard_cut") == "hard_cut":
        return 0.0
    requested = max(0.0, float(shots[index].get("transition_duration", 0.0)))
    return min(requested, float(shots[index]["duration"]) * 0.45, float(shots[index + 1]["duration"]) * 0.45)


def _boundary_time(shots: list[dict[str, Any]], index: int) -> float:
    elapsed = sum(float(shot["duration"]) for shot in shots[: index + 1])
    elapsed -= sum(_transition_duration(shots, value) for value in range(index))
    return elapsed - _transition_duration(shots, index) / 2.0


def align_plan_to_rhythm(
    plan: dict[str, Any],
    rhythm_map: dict[str, Any],
    *,
    intensity: str = "balanced",
    max_snap_ms: float = 150.0,
    minimum_shot_seconds: float = 0.24,
) -> dict[str, Any]:
    """Return a plan whose internal visual boundaries are snapped to observed attacks."""
    result = copy.deepcopy(plan)
    shots = result.get("shots", [])
    anchors = [
        anchor
        for anchor in rhythm_map.get("strong_anchors", [])
        if float(anchor.get("timeline_time", anchor.get("time", -1.0))) >= 0.0
    ]
    anchors.sort(key=lambda row: float(row.get("timeline_time", row["time"])))
    anchor_times = [float(row.get("timeline_time", row["time"])) for row in anchors]
    threshold = SYNC_STRENGTH.get(intensity, SYNC_STRENGTH["balanced"])
    window = max(0.0, float(max_snap_ms)) / 1000.0
    matched = 0
    deltas: list[float] = []
    used: set[str] = set()
    for index, shot in enumerate(shots[:-1]):
        planned = _boundary_time(shots, index)
        left = bisect.bisect_left(anchor_times, planned - window)
        right = bisect.bisect_right(anchor_times, planned + window)
        candidates = []
        for anchor in anchors[left:right]:
            if anchor["id"] in used or float(anchor.get("strength", 0.0)) < threshold:
                continue
            time_value = float(anchor.get("timeline_time", anchor["time"]))
            delta = time_value - planned
            score = float(anchor.get("strength", 0.0)) - abs(delta) / max(window, 1e-9) * 0.2
            if anchor.get("downbeat_candidate") and float(anchor.get("downbeat_confidence", 0.0)) >= 0.35:
                score += 0.08
            candidates.append((score, abs(delta), delta, anchor))
        selected = max(candidates, default=None, key=lambda row: (row[0], -row[1]))
        shot["planned_boundary"] = round(planned, 6)
        shot["beat_anchor"] = None
        if selected is None:
            shot["final_boundary"] = round(planned, 6)
            shot["cut_reason"] = list(dict.fromkeys([*shot.get("cut_reason", []), "narrative_or_reference_boundary"]))
            continue
        _, _, delta, anchor = selected
        next_shot = shots[index + 1]
        if float(shot["duration"]) + delta < minimum_shot_seconds or float(next_shot["duration"]) - delta < minimum_shot_seconds:
            shot["final_boundary"] = round(planned, 6)
            continue
        shot["duration"] = round(float(shot["duration"]) + delta, 6)
        next_shot["duration"] = round(float(next_shot["duration"]) - delta, 6)
        final = _boundary_time(shots, index)
        adjustment = final - planned
        anchor_time = float(anchor.get("timeline_time", anchor["time"]))
        alignment_error = final - anchor_time
        shot["final_boundary"] = round(final, 6)
        shot["beat_anchor"] = {
            "id": anchor["id"],
            "source_event_id": anchor.get("source_event_id"),
            "source": "observed_onset",
            "type": anchor.get("type"),
            "strength": anchor.get("strength"),
            "confidence": anchor.get("confidence"),
            "timeline_time": round(anchor_time, 6),
            "adjustment_ms": round(adjustment * 1000.0, 3),
            "delta_ms": round(alignment_error * 1000.0, 3),
            "downbeat_candidate": bool(anchor.get("downbeat_candidate")),
        }
        shot["cut_reason"] = list(dict.fromkeys([*shot.get("cut_reason", []), "observed_onset"]))
        used.add(anchor["id"])
        matched += 1
        deltas.append(abs(alignment_error) * 1000.0)
    total = max(0, len(shots) - 1)
    ratio = matched / total if total else 0.0
    result["rhythm_sync"] = {
        "status": "aligned" if matched else "no_reliable_anchor",
        "mode": intensity,
        "anchor_source": "observed_onsets_only",
        "max_snap_ms": float(max_snap_ms),
        "primary_cut_count": total,
        "aligned_primary_cut_count": matched,
        "primary_cut_alignment_ratio": round(ratio, 4),
        "median_cut_offset_ms": round(float(np.median(deltas)), 3) if deltas else None,
        "p95_cut_offset_ms": round(float(np.percentile(deltas, 95)), 3) if deltas else None,
    }
    result["beat_map"] = rhythm_map
    return result
