"""End-to-end smoke test for analysis, planning, transitions, and rendering."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from make_fixture import main as make_fixture  # noqa: E402
from supercut import analyze_reference, build_plan, catalog_sources, render_plan, save_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", required=True)
    args = parser.parse_args()

    make_fixture()
    output = ROOT / "output"
    template = analyze_reference(
        ROOT / "output/test_assets/reference/fixture_reference.mp4",
        output / "test_template.json",
        output / "test_reference_sheet.jpg",
    )
    catalog = catalog_sources(
        ROOT / "output/test_assets/source", output / "test_catalog.json", output / "test_source_sheet.jpg"
    )
    plan = build_plan(template, catalog)
    plan["shots"][0].update(transition_after="dissolve", transition_duration=0.20)
    plan["shots"][1].update(transition_after="whip", transition_duration=0.18)
    save_json(output / "test_plan.json", plan)
    result_path = output / "test_draft.mp4"
    render_plan(plan, result_path, ffmpeg_path=args.ffmpeg)

    cap = cv2.VideoCapture(str(result_path))
    assert cap.isOpened(), "Rendered video cannot be opened"
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    expected = sum(float(shot["duration"]) for shot in plan["shots"]) - 0.20 - 0.18
    actual = frames / fps
    assert abs(fps - 30.0) < 0.01, (fps, frames)
    assert abs(actual - expected) <= 2 / fps, (actual, expected)
    assert (width, height) == (640, 360), (width, height)
    print({"ok": True, "fps": fps, "frames": frames, "duration": round(actual, 3), "expected": expected})


if __name__ == "__main__":
    main()
