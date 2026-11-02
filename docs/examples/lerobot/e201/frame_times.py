"""E201: how long the colour detector takes on frames from a real camera, or from a picture.

0.30.0 moved the colour detector from OpenCV to numpy, which halved the install and made some frames
slower. Those frames were made up: a real lens blurs the one-pixel patterns the worst of them were
built on, and nothing has timed the detector on what a real camera sees. No arm and no model here.

    python frame_times.py --camera 1 --seconds 10
    python frame_times.py --image checkerboard.png

`--camera` needs OpenCV, which `quackd[lerobot]` brings through LeRobot. A picture is read at
640x480, as the frames quackd's detector was timed on were, so its time can be set beside the
camera's.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Iterator
from pathlib import Path

from PIL import Image

from quackd.perception.color_blob import ColorBlobDetector


def camera_frames(index: int, seconds: float, backend: str | None) -> Iterator[Image.Image]:
    """Frames from a webcam by its OpenCV index, as they come, for `seconds`."""
    import cv2

    capture = (
        cv2.VideoCapture(index, cv2.CAP_MSMF) if backend == "msmf" else cv2.VideoCapture(index)
    )
    if not capture.isOpened():
        raise SystemExit(f"camera {index} did not open: lerobot-find-cameras opencv lists them")
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ok, frame = capture.read()
            if ok:
                yield Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    finally:
        capture.release()


def picture_frames(path: Path, repeats: int) -> Iterator[Image.Image]:
    """One picture at 640x480, `repeats` times, since a single timing says little."""
    with Image.open(path) as opened:
        frame = opened.convert("RGB").resize((640, 480), Image.Resampling.LANCZOS)
    for _ in range(repeats):
        yield frame


def report(frames: Iterator[Image.Image]) -> int:
    """Time the detector on each frame on this thread, as a run does, and say what it took."""
    detector = ColorBlobDetector()
    taken: list[float] = []
    found: list[str] = []
    size = (0, 0)
    for frame in frames:
        size = frame.size
        started = time.perf_counter()
        found = [d.label for d in detector.detect(frame)]
        taken.append((time.perf_counter() - started) * 1000)
    if not taken:
        print("no frames came")
        return 1
    taken.sort()
    middle = taken[len(taken) // 2]
    high = taken[int(len(taken) * 0.9)]
    print(f"{len(taken)} frames of {size[0]}x{size[1]}")
    print(f"detector: p50 {middle:.1f} ms, p90 {high:.1f} ms, max {taken[-1]:.1f} ms a frame")
    print(f"in the last frame it saw: {', '.join(found) or 'nothing'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--camera", type=int, help="a webcam's OpenCV index")
    parser.add_argument("--seconds", type=float, default=10.0, help="how long to read the camera")
    parser.add_argument(
        "--backend", choices=["msmf"], help="for a camera that lists and won't open"
    )
    parser.add_argument("--image", type=Path, help="a picture, read at 640x480")
    parser.add_argument("--repeats", type=int, default=30, help="how many times to time a picture")
    args = parser.parse_args()
    if (args.camera is None) == (args.image is None):
        parser.error("give --camera or --image")
    if args.image is not None:
        return report(picture_frames(args.image, args.repeats))
    return report(camera_frames(args.camera, args.seconds, args.backend))


if __name__ == "__main__":
    raise SystemExit(main())
