"""Generate tiny deterministic videos for an end-to-end smoke test."""

from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
FPS = 30
SIZE = (640, 360)


def writer(path: Path) -> cv2.VideoWriter:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, SIZE)
    if not result.isOpened():
        raise RuntimeError(f"Unable to create {path}")
    return result


def patterned_frame(index: int, color: tuple[int, int, int], variant: int) -> np.ndarray:
    frame = np.full((SIZE[1], SIZE[0], 3), color, dtype=np.uint8)
    x = (index * (5 + variant * 3)) % (SIZE[0] + 120) - 60
    y = SIZE[1] // 2 + int(70 * np.sin(index / (8 + variant)))
    cv2.circle(frame, (x, y), 42 + variant * 4, (245, 245, 245), -1)
    cv2.putText(frame, f"FIXTURE {variant + 1}", (25, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (15, 15, 15), 2)
    return frame


def make_reference(path: Path) -> None:
    out = writer(path)
    segments = [
        (30, (40, 70, 210), 0),
        (42, (210, 95, 35), 1),
        (24, (50, 185, 80), 2),
        (36, (185, 55, 160), 1),
    ]
    frame_index = 0
    for count, color, variant in segments:
        for _ in range(count):
            out.write(patterned_frame(frame_index, color, variant))
            frame_index += 1
    out.release()


def make_source(path: Path, color: tuple[int, int, int], variant: int) -> None:
    out = writer(path)
    for index in range(150):
        out.write(patterned_frame(index, color, variant))
    out.release()


def main() -> None:
    make_reference(ROOT / "output/test_assets/reference/fixture_reference.mp4")
    make_source(ROOT / "output/test_assets/source/fixture_slow.mp4", (55, 160, 210), 0)
    make_source(ROOT / "output/test_assets/source/fixture_medium.mp4", (190, 130, 45), 1)
    make_source(ROOT / "output/test_assets/source/fixture_fast.mp4", (70, 185, 100), 2)
    print("Fixture videos created.")


if __name__ == "__main__":
    main()
